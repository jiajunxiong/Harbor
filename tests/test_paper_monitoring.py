"""Paper monitoring and period summary tests (MVP 4 / SP 4.80).

Covers the daily snapshot (SP 4.75) and the daily / weekly / monthly summary
generation and query (SP 4.76).
"""

import unittest
from datetime import date

from harbor.core.backtest_domain import CashBalance, Currency, Market, NetValue
from harbor.core.paper_drawdown import DrawdownTier
from harbor.core.paper_monitoring import (
    PaperDailySnapshot,
    PaperMonitoringError,
    PaperPeriodSummary,
    SummaryPeriod,
    build_paper_daily_snapshot,
    build_period_summary,
    query_period_summaries,
    summary_rows,
)
from harbor.core.paper_reconciliation import PaperReconciliationResult
from harbor.core.paper_valuation import PaperPositionValue, PaperValuation

_TRADE = date(2026, 1, 2)


def _net_value(day: date, total: float) -> NetValue:
    return NetValue(
        as_of_date=day,
        currency=Currency.HKD,
        cash=total,
        securities_value=0.0,
        fees_paid=0.0,
    )


def _valuation(day: date, total: float, single_pct: float = 0.0) -> PaperValuation:
    position_values = ()
    if single_pct > 0:
        position_values = (
            PaperPositionValue(
                market=Market.HK,
                symbol="0001.HK",
                quantity=1.0,
                price=total * single_pct,
                currency=Currency.HKD,
                fx_rate=1.0,
                market_value_quote=total * single_pct,
                market_value_base=total * single_pct,
            ),
        )
    net = NetValue(
        as_of_date=day,
        currency=Currency.HKD,
        cash=total - (total * single_pct),
        securities_value=total * single_pct,
        fees_paid=0.0,
    )
    return PaperValuation(
        as_of=day,
        base_currency=Currency.HKD,
        cash=(CashBalance(Currency.HKD, total - (total * single_pct)),),
        position_values=position_values,
        fees_paid=(),
        cash_base=total - (total * single_pct),
        securities_base=total * single_pct,
        fees_base=0.0,
        net_value=net,
        missing_prices=(),
    )


class DailySnapshotTests(unittest.TestCase):
    """The daily monitoring snapshot (SP 4.75 / 4.80)."""

    def test_build_snapshot(self) -> None:
        snapshot = build_paper_daily_snapshot(
            paper_run_id="paper-1",
            as_of=_TRADE,
            valuation=_valuation(_TRADE, 1_000_000.0, single_pct=0.2),
            net_values=(_net_value(date(2026, 1, 1), 1_100_000.0), _net_value(_TRADE, 1_000_000.0)),
        )
        self.assertIsInstance(snapshot, PaperDailySnapshot)
        self.assertAlmostEqual(snapshot.net_value, 1_000_000.0)
        self.assertAlmostEqual(snapshot.drawdown, 100_000.0 / 1_100_000.0)
        # 9.09% drawdown maps to the 8% DEFEND tier
        self.assertEqual(snapshot.drawdown_tier, DrawdownTier.DEFEND)
        self.assertAlmostEqual(snapshot.max_single_stock_pct, 0.2)
        self.assertTrue(snapshot.reconciled)

    def test_drawdown_tier_from_series(self) -> None:
        snapshot = build_paper_daily_snapshot(
            paper_run_id="paper-1",
            as_of=_TRADE,
            valuation=_valuation(_TRADE, 900_000.0),
            net_values=(
                _net_value(date(2026, 1, 1), 1_000_000.0),
                _net_value(_TRADE, 900_000.0),
            ),
        )
        self.assertAlmostEqual(snapshot.drawdown, 0.10)
        self.assertEqual(snapshot.drawdown_tier, DrawdownTier.CIRCUIT)

    def test_reconciliation_state(self) -> None:
        ok = build_paper_daily_snapshot(
            paper_run_id="paper-1",
            as_of=_TRADE,
            valuation=_valuation(_TRADE, 1_000_000.0),
            net_values=(_net_value(_TRADE, 1_000_000.0),),
            reconciliation=PaperReconciliationResult(as_of=_TRADE, differences=()),
        )
        self.assertTrue(ok.reconciled)
        mismatch = build_paper_daily_snapshot(
            paper_run_id="paper-1",
            as_of=_TRADE,
            valuation=_valuation(_TRADE, 1_000_000.0),
            net_values=(_net_value(_TRADE, 1_000_000.0),),
            differences=(),
        )
        self.assertTrue(mismatch.reconciled)

    def test_invalid_snapshot_rejected(self) -> None:
        with self.assertRaises(PaperMonitoringError):
            build_paper_daily_snapshot(
                paper_run_id="",
                as_of=_TRADE,
                valuation=_valuation(_TRADE, 1_000_000.0),
                net_values=(_net_value(_TRADE, 1_000_000.0),),
            )


class PeriodSummaryTests(unittest.TestCase):
    """The period summaries (SP 4.76 / 4.80)."""

    def _snapshots(self) -> tuple[PaperDailySnapshot, ...]:
        return (
            build_paper_daily_snapshot(
                paper_run_id="paper-1",
                as_of=date(2026, 1, 1),
                valuation=_valuation(date(2026, 1, 1), 1_000_000.0),
                net_values=(_net_value(date(2026, 1, 1), 1_000_000.0),),
            ),
            build_paper_daily_snapshot(
                paper_run_id="paper-1",
                as_of=date(2026, 1, 2),
                valuation=_valuation(date(2026, 1, 2), 990_000.0),
                net_values=(
                    _net_value(date(2026, 1, 1), 1_000_000.0),
                    _net_value(date(2026, 1, 2), 990_000.0),
                ),
            ),
        )

    def test_build_period_summary(self) -> None:
        summary = build_period_summary(
            paper_run_id="paper-1",
            period=SummaryPeriod.WEEKLY,
            start=date(2026, 1, 1),
            end=date(2026, 1, 7),
            snapshots=self._snapshots(),
            fills_count=3,
            difference_count=2,
            alert_count=1,
            approval_count=1,
        )
        self.assertIsInstance(summary, PaperPeriodSummary)
        self.assertEqual(summary.snapshot_count, 2)
        self.assertAlmostEqual(summary.start_net_value, 1_000_000.0)
        self.assertAlmostEqual(summary.end_net_value, 990_000.0)
        self.assertAlmostEqual(summary.max_drawdown, 0.01)
        self.assertEqual(summary.fills_count, 3)
        self.assertEqual(summary.approval_count, 1)
        self.assertEqual(summary.reconciled_days, 2)

    def test_out_of_range_snapshots_excluded(self) -> None:
        summary = build_period_summary(
            paper_run_id="paper-1",
            period=SummaryPeriod.DAILY,
            start=date(2026, 1, 2),
            end=date(2026, 1, 2),
            snapshots=self._snapshots(),
        )
        self.assertEqual(summary.snapshot_count, 1)
        self.assertAlmostEqual(summary.start_net_value, 990_000.0)
        self.assertAlmostEqual(summary.end_net_value, 990_000.0)

    def test_empty_period(self) -> None:
        summary = build_period_summary(
            paper_run_id="paper-1",
            period=SummaryPeriod.MONTHLY,
            start=date(2026, 2, 1),
            end=date(2026, 2, 28),
            snapshots=self._snapshots(),
        )
        self.assertEqual(summary.snapshot_count, 0)
        self.assertIsNone(summary.start_net_value)
        self.assertIsNone(summary.end_net_value)

    def test_invalid_period_rejected(self) -> None:
        with self.assertRaises(PaperMonitoringError):
            build_period_summary(
                paper_run_id="paper-1",
                period=SummaryPeriod.WEEKLY,
                start=date(2026, 1, 2),
                end=date(2026, 1, 1),
                snapshots=(),
            )

    def test_summary_rows(self) -> None:
        summary = build_period_summary(
            paper_run_id="paper-1",
            period=SummaryPeriod.DAILY,
            start=date(2026, 1, 1),
            end=date(2026, 1, 1),
            snapshots=self._snapshots(),
        )
        row = summary_rows(summary)
        self.assertEqual(row["paper_run_id"], "paper-1")
        self.assertEqual(row["period"], "daily")
        self.assertEqual(row["start"], "2026-01-01")

    def test_query_period_summaries(self) -> None:
        daily = build_period_summary(
            paper_run_id="paper-1",
            period=SummaryPeriod.DAILY,
            start=date(2026, 1, 1),
            end=date(2026, 1, 1),
            snapshots=(),
        )
        monthly = build_period_summary(
            paper_run_id="paper-1",
            period=SummaryPeriod.MONTHLY,
            start=date(2026, 1, 1),
            end=date(2026, 1, 31),
            snapshots=(),
        )
        other = build_period_summary(
            paper_run_id="paper-2",
            period=SummaryPeriod.DAILY,
            start=date(2026, 1, 1),
            end=date(2026, 1, 1),
            snapshots=(),
        )
        self.assertEqual(
            len(query_period_summaries((daily, monthly, other), paper_run_id="paper-1")),
            2,
        )
        self.assertEqual(
            len(query_period_summaries((daily, monthly, other), period=SummaryPeriod.DAILY)),
            2,
        )
        self.assertEqual(
            len(
                query_period_summaries(
                    (daily, monthly, other),
                    paper_run_id="paper-2",
                    period=SummaryPeriod.MONTHLY,
                )
            ),
            0,
        )

    def test_readable(self) -> None:
        summary = build_period_summary(
            paper_run_id="paper-1",
            period=SummaryPeriod.WEEKLY,
            start=date(2026, 1, 1),
            end=date(2026, 1, 7),
            snapshots=self._snapshots(),
        )
        self.assertIn("weekly summary 2026-01-01..2026-01-07", summary.readable())
