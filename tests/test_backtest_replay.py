"""Derived backtest views for the dashboard (MVP 5 / SP 5.21-SP 5.23).

The API-level contract is pinned by ``tests/test_api_contract.py`` with a fake
store. These tests cover the layer underneath it:

* SP 5.21 — the replay consistency view, including what its fingerprint does and
  does not cover, and the sibling comparison.
* SP 5.22 — the report download's media type and filename handling.
* SP 5.23 — the rebasing that makes two runs comparable at all.

The database-backed sibling lookup is exercised in
``tests/test_api_read_store_integration.py``; here the repository is faked so the
shaping, the caveats and the error paths can be asserted without a database.
"""

from __future__ import annotations

import unittest
from datetime import date
from typing import Any
from unittest.mock import MagicMock, patch

from harbor.core.backtest_domain import Currency
from harbor.services.backtest import (
    REPORT_MEDIA_TYPES,
    BacktestReportError,
    _safe_filename_part,
    render_backtest_report,
)
from harbor.services.backtest_analytics import (
    BacktestAnalyticsError,
    comparison_series,
    comparison_series_from_rows,
    run_comparison,
)
from harbor.services.backtest_replay import (
    BacktestReplayError,
    consistency_notes,
    manifest_view,
)


def net_value_row(
    day: int,
    *,
    total: float,
    currency: str = "HKD",
) -> dict[str, Any]:
    """One persisted net-value row with a chosen total."""
    return {
        "as_of_date": date(2026, 1, day),
        "currency": currency,
        "cash": total / 2,
        "securities_value": total / 2,
        "fees_paid": 1.0,
    }


class ComparisonRebasingTests(unittest.TestCase):
    """SP 5.23 — a rebased series is what makes two runs comparable."""

    def test_the_series_starts_at_zero(self) -> None:
        points = comparison_series_from_rows(
            [net_value_row(2, total=1000.0), net_value_row(3, total=1100.0)]
        )

        self.assertAlmostEqual(points[0].cumulative_return, 0.0)

    def test_each_point_is_measured_from_the_runs_own_first_value(self) -> None:
        points = comparison_series_from_rows(
            [
                net_value_row(2, total=1000.0),
                net_value_row(3, total=1500.0),
                net_value_row(4, total=800.0),
            ]
        )

        self.assertAlmostEqual(points[1].cumulative_return, 0.5)
        self.assertAlmostEqual(points[2].cumulative_return, -0.2)

    def test_a_run_starting_at_a_different_scale_is_comparable(self) -> None:
        # The same +50% on 1,000 and on 10,000 must produce the same curve; this
        # is the property that lets an HKD run sit beside a USD run.
        small = comparison_series_from_rows(
            [net_value_row(2, total=1000.0), net_value_row(3, total=1500.0)]
        )
        large = comparison_series_from_rows(
            [net_value_row(2, total=10_000.0), net_value_row(3, total=15_000.0)]
        )

        self.assertAlmostEqual(small[1].cumulative_return, large[1].cumulative_return)

    def test_a_non_positive_start_is_refused_rather_than_rebased(self) -> None:
        with self.assertRaises(BacktestAnalyticsError):
            comparison_series_from_rows(
                [net_value_row(2, total=0.0), net_value_row(3, total=100.0)]
            )

    def test_an_empty_series_is_refused_by_the_pure_helper(self) -> None:
        with self.assertRaises(BacktestAnalyticsError):
            comparison_series(())


class RunComparisonTests(unittest.TestCase):
    """SP 5.23 — one run's entry, including when it has nothing to show."""

    def test_a_healthy_run_carries_curve_dates_and_metrics(self) -> None:
        entry = run_comparison(
            "bt-1",
            [
                net_value_row(2, total=1000.0),
                net_value_row(3, total=1200.0),
                net_value_row(4, total=900.0),
                net_value_row(5, total=1300.0),
            ],
        )

        self.assertTrue(entry.available)
        self.assertEqual(entry.currency, Currency.HKD)
        self.assertEqual(entry.start_date, date(2026, 1, 2))
        self.assertEqual(entry.end_date, date(2026, 1, 5))
        self.assertEqual(entry.point_count, 4)
        self.assertEqual(len(entry.points), 4)
        self.assertIsNone(entry.unavailable_reason)
        self.assertIsNotNone(entry.metrics)
        self.assertAlmostEqual(entry.metrics.cumulative_return, 0.3)

    def test_a_run_without_valuations_is_reported_as_absent_with_a_reason(self) -> None:
        entry = run_comparison("bt-1", [])

        self.assertFalse(entry.available)
        self.assertIsNone(entry.currency)
        self.assertIsNone(entry.start_date)
        self.assertEqual(entry.point_count, 0)
        self.assertEqual(entry.points, ())
        self.assertIsNotNone(entry.unavailable_reason)

    def test_a_curve_is_still_offered_when_a_ratio_is_undefined(self) -> None:
        # A flat curve has a real shape but no Sharpe ratio; the comparison must
        # keep the curve and report the metric reason separately.
        entry = run_comparison(
            "bt-1",
            [
                net_value_row(2, total=1000.0),
                net_value_row(3, total=1000.0),
                net_value_row(4, total=1000.0),
            ],
        )

        self.assertFalse(entry.available)
        self.assertEqual(len(entry.points), 3)
        self.assertIsNotNone(entry.unavailable_reason)

    def test_a_mixed_currency_series_is_refused_rather_than_rebased(self) -> None:
        entry = run_comparison(
            "bt-1",
            [
                net_value_row(2, total=1000.0, currency="HKD"),
                net_value_row(3, total=1100.0, currency="USD"),
            ],
        )

        self.assertFalse(entry.available)
        self.assertIn("currencies", entry.unavailable_reason or "")


class ReportServingTests(unittest.TestCase):
    """SP 5.22 — the download's format, media type and filename."""

    def test_every_format_has_a_media_type_and_extension(self) -> None:
        for report_format, media_type in REPORT_MEDIA_TYPES.items():
            with self.subTest(report_format):
                with patch(
                    "harbor.services.backtest.report_backtest",
                    return_value="rendered",
                ):
                    report = render_backtest_report(
                        connection=MagicMock(), run_id="bt-1", report_format=report_format
                    )

                self.assertEqual(report.content, "rendered")
                self.assertEqual(report.media_type, media_type)
                self.assertEqual(report.filename, f"harbor-backtest-bt-1.{report_format}")

    def test_an_unknown_format_is_refused(self) -> None:
        with self.assertRaises(BacktestReportError):
            render_backtest_report(connection=MagicMock(), run_id="bt-1", report_format="xml")

    def test_a_run_id_cannot_escape_the_filename(self) -> None:
        # The filename lands in a Content-Disposition header, so a separator must
        # not survive; a leading "..\/" cannot walk out of the download name.
        self.assertEqual(_safe_filename_part("../../etc/passwd"), "etc_passwd")
        self.assertEqual(_safe_filename_part(""), "run")
        self.assertEqual(_safe_filename_part("..."), "run")

    def test_a_header_injection_attempt_is_neutralized(self) -> None:
        sanitized = _safe_filename_part('bt-1"\r\nX-Evil: 1')

        for forbidden in ('"', "\r", "\n", "/", "\\", ";"):
            with self.subTest(forbidden):
                self.assertNotIn(forbidden, sanitized)
        self.assertTrue(sanitized.startswith("bt-1"))

    def test_a_normal_run_id_is_left_alone(self) -> None:
        self.assertEqual(
            _safe_filename_part("13d3a15ae60a42e2a213dd6471c6d22f"),
            "13d3a15ae60a42e2a213dd6471c6d22f",
        )

    def test_the_downloaded_name_carries_the_sanitized_id(self) -> None:
        with patch("harbor.services.backtest.report_backtest", return_value="rendered"):
            report = render_backtest_report(
                connection=MagicMock(), run_id="a/b", report_format="csv"
            )

        self.assertEqual(report.filename, "harbor-backtest-a_b.csv")


class ConsistencyNotesTests(unittest.TestCase):
    """SP 5.21 — the caveats that keep a fingerprint from over-claiming."""

    def test_the_notes_name_what_the_fingerprint_covers(self) -> None:
        joined = " ".join(consistency_notes())

        self.assertIn("配置哈希", joined)
        self.assertIn("数据查询边界", joined)

    def test_the_notes_say_unset_inputs_are_not_the_same_as_unused(self) -> None:
        joined = " ".join(consistency_notes())

        self.assertIn("random_seed", joined)
        self.assertIn("未设置", joined)

    def test_the_notes_say_the_fingerprint_does_not_cover_the_data(self) -> None:
        joined = " ".join(consistency_notes())

        self.assertIn("数据内容", joined)
        self.assertIn("数据源补数", joined)


class ManifestViewTests(unittest.TestCase):
    """SP 5.21 — the transport shape of a derived manifest."""

    def test_every_fingerprint_input_is_carried(self) -> None:
        from harbor.core.replay_manifest import DataQueryBoundaries, ReplayManifest

        manifest = ReplayManifest(
            run_id="bt-1",
            config_hash="hash-1",
            code_version="abc1234",
            data_boundaries=DataQueryBoundaries(
                start_date=date(2020, 1, 1),
                end_date=date(2026, 8, 27),
                data_cutoff=date(2026, 8, 27),
            ),
            fx_source=None,
            calendar_version=None,
            random_seed=None,
        )

        view = manifest_view(manifest)

        self.assertEqual(view.fingerprint, manifest.fingerprint())
        self.assertEqual(view.config_hash, "hash-1")
        self.assertEqual(view.data_cutoff, date(2026, 8, 27))
        self.assertIsNone(view.fx_source)

    def test_the_view_is_rejected_for_a_bad_sibling_bound(self) -> None:
        from harbor.services.backtest_replay import build_replay_consistency

        with self.assertRaises(BacktestReplayError):
            build_replay_consistency(connection=MagicMock(), run_id="bt-1", max_siblings=-1)


if __name__ == "__main__":
    unittest.main()
