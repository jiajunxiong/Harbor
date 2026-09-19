"""SQL read-store integration tests (MVP 5 / SP 5.8, SP 5.13-5.18).

The contract tests drive the API through a fake :class:`ReadStore`, which is
exactly what makes them fast — and exactly why they cannot catch a bug in the SQL
adapter. One such bug shipped and was only caught by running the API against the
real database: ``SqlReadStore.list_net_values`` unpacked rows with
``.mappings()`` *and* ``row._mapping``, which raises ``AttributeError`` at
runtime while the fake happily returned the canned rows it was given.

This module closes that gap: it exercises the real adapter against a real
PostgreSQL so a wrong SQLAlchemy result API (``.all()`` vs ``.mappings()``,
missing ordering, a filter that never reaches the WHERE clause) fails here
instead of in front of a user.

The tests are **read-only** on purpose. They never truncate, seed or migrate, so
pointing ``HARBOR_TEST_DATABASE_URL`` at a working database is safe.
"""

from __future__ import annotations

import os
import unittest

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from harbor.api.read_store import SqlReadStore
from harbor.services.backtest import REPORT_FORMATS, BacktestReportError

TEST_DATABASE_URL = os.environ.get("HARBOR_TEST_DATABASE_URL")

_SKIP_REASON = "Set HARBOR_TEST_DATABASE_URL to run the SQL read-store integration tests."


def _engine() -> Engine:
    assert TEST_DATABASE_URL is not None
    return create_engine(TEST_DATABASE_URL)


@unittest.skipUnless(TEST_DATABASE_URL, _SKIP_REASON)
class SqlReadStoreIntegrationTests(unittest.TestCase):
    """Exercise the SQL adapter's real result handling and query building."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = _engine()
        with cls.engine.connect() as connection:
            store = SqlReadStore(connection)
            rows, _total = store.list_backtest_runs(limit=1, offset=0)
            cls.run_id = str(rows[0]["run_id"]) if rows else None
        if cls.run_id is None:
            raise unittest.SkipTest("The database has no backtest runs to read.")

    def setUp(self) -> None:
        self.connection = self.engine.connect()
        self.addCleanup(self.connection.close)
        self.store = SqlReadStore(self.connection)

    def test_run_rows_are_plain_mappings(self) -> None:
        rows, total = self.store.list_backtest_runs(limit=5, offset=0)
        self.assertGreaterEqual(total, 1)
        self.assertTrue(rows)
        for row in rows:
            self.assertIsInstance(row, dict)
            self.assertIn("run_id", row)
            self.assertIn("status", row)
            self.assertIn("data_cutoff", row)

    def test_net_values_come_back_ordered_and_as_mappings(self) -> None:
        rows = self.store.list_net_values(self.run_id)
        self.assertTrue(rows)
        for row in rows:
            self.assertIsInstance(row, dict)
            self.assertIn("currency", row)
            self.assertIn("securities_value", row)
        dates = [row["as_of_date"] for row in rows]
        self.assertEqual(dates, sorted(dates), "the net-value series must be oldest-first")

    def test_filters_reach_the_query_and_the_count(self) -> None:
        everything, _total = self.store.list_backtest_runs(limit=1, offset=0)
        status = str(everything[0]["status"])
        filtered, total = self.store.list_backtest_runs(limit=50, offset=0, status=status)
        self.assertGreaterEqual(total, 1)
        self.assertEqual({str(row["status"]) for row in filtered}, {status})

        filtered, none_total = self.store.list_backtest_runs(limit=50, offset=0, status="NOPE")
        self.assertEqual(none_total, 0)
        self.assertEqual(filtered, [])

    def test_sorting_is_honoured(self) -> None:
        ascending, _total = self.store.list_backtest_runs(
            limit=50, offset=0, sort="run_id", order="asc"
        )
        ids = [str(row["run_id"]) for row in ascending]
        self.assertEqual(ids, sorted(ids))

        descending, _total = self.store.list_backtest_runs(
            limit=50, offset=0, sort="run_id", order="desc"
        )
        self.assertEqual([str(row["run_id"]) for row in descending], sorted(ids, reverse=True))

    def test_run_scoped_trades_do_not_require_a_market(self) -> None:
        fills, fill_total = self.store.list_fills(self.run_id, limit=2, offset=0)
        self.assertGreaterEqual(fill_total, len(fills))
        for row in fills:
            self.assertIsInstance(row, dict)
            self.assertIn("trade_date", row)

        rejected, rejected_total = self.store.list_rejected_trades(self.run_id, limit=5, offset=0)
        self.assertGreaterEqual(rejected_total, len(rejected))
        reasons = self.store.rejected_reason_counts(self.run_id)
        self.assertEqual(sum(count for _reason, count in reasons), rejected_total)

    def test_filter_options_come_back_as_lists(self) -> None:
        statuses, strategies = self.store.run_filter_options()
        self.assertIsInstance(statuses, list)
        self.assertIsInstance(strategies, list)
        self.assertEqual(statuses, sorted(statuses))

    def test_a_missing_run_is_reported_as_absent(self) -> None:
        self.assertIsNone(self.store.get_backtest_run("no-such-run-id"))

    def test_replay_consistency_derives_a_manifest_from_real_rows(self) -> None:
        consistency = self.store.replay_consistency(self.run_id)

        self.assertEqual(consistency.manifest.run_id, self.run_id)
        # ``fingerprint()`` is the SP 2.61 *composite key* (config hash | code
        # version | boundaries | ...), not a digest, so it is asserted as a
        # composition rather than as a fixed-length hash.
        fingerprint = consistency.manifest.fingerprint
        self.assertTrue(fingerprint)
        self.assertIn(consistency.manifest.config_hash, fingerprint)
        self.assertIn(consistency.manifest.code_version, fingerprint)
        # Every compared sibling must genuinely share the derived inputs; if the
        # SQL predicate were dropped, this is what would catch it.
        subject = self.store.get_backtest_run(self.run_id)
        assert subject is not None
        for sibling in consistency.siblings:
            self.assertNotEqual(sibling.run_id, self.run_id)
            row = self.store.get_backtest_run(sibling.run_id)
            assert row is not None
            self.assertEqual(row["config_hash"], subject["config_hash"])
            self.assertEqual(row["code_version"], subject["code_version"])
            self.assertEqual(row["data_cutoff"], subject["data_cutoff"])
            self.assertGreaterEqual(sibling.difference_count, len(sibling.differences))

    def test_the_sibling_list_respects_its_bound_and_counts_the_rest(self) -> None:
        for limit in (0, 1):
            with self.subTest(limit):
                consistency = self.store.replay_consistency(self.run_id, max_siblings=limit)

                self.assertLessEqual(len(consistency.siblings), limit)
                self.assertEqual(
                    consistency.truncated,
                    consistency.sibling_total > len(consistency.siblings),
                )

    def test_sibling_groups_are_the_ones_the_data_contains(self) -> None:
        # Compare the store's answer against an independent scan of the table, so
        # the sibling predicate is verified rather than assumed.
        rows, _total = self.store.list_backtest_runs(limit=200, offset=0)
        subject = next(row for row in rows if str(row["run_id"]) == self.run_id)
        expected = {
            str(row["run_id"])
            for row in rows
            if str(row["run_id"]) != self.run_id
            and row["config_hash"] == subject["config_hash"]
            and row["code_version"] == subject["code_version"]
            and row["data_cutoff"] == subject["data_cutoff"]
        }
        consistency = self.store.replay_consistency(self.run_id, max_siblings=50)

        self.assertEqual(consistency.sibling_total, len(expected))
        self.assertEqual({sibling.run_id for sibling in consistency.siblings}, expected)

    def test_reports_render_in_every_published_format(self) -> None:
        import json

        for report_format in REPORT_FORMATS:
            with self.subTest(report_format):
                report = self.store.render_report(self.run_id, report_format)

                self.assertEqual(report.report_format, report_format)
                self.assertTrue(report.content.strip())
                self.assertTrue(report.filename.endswith(f".{report_format}"))
                if report_format == "json":
                    body = json.loads(report.content)
                    self.assertEqual(body["run"]["run_id"], self.run_id)
                if report_format == "html":
                    self.assertIn(self.run_id, report.content)
                    self.assertIn("<html", report.content)

    def test_an_unknown_report_format_is_refused(self) -> None:
        with self.assertRaises(BacktestReportError):
            self.store.render_report(self.run_id, "xml")
