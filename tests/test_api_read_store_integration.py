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
