"""Backtest limitation documentation tests (MVP 2 / SP 2.74).

Verifies ``docs/backtest_limitations.md`` documents all seven required
limitation areas (data coverage, historical stock pool, corporate actions, FX,
calendar, suspension valuation and benchmark data) and that it states the
research-only disclaimer. Also cross-checks two limitation claims against real
code behaviour: the calendar's illustrative default holidays (SP 2.11) and the
survivorship-bias risk surfacing in the stock pool (SP 2.10, which SP 2.65
correlates into the report).
"""

import unittest
from datetime import date
from pathlib import Path

from harbor.core.backtest_domain import Market
from harbor.core.stock_pool import StockPoolMembership, evaluate_stock_pool
from harbor.core.trading_calendar import DEFAULT_HOLIDAYS

_DOC_PATH = Path(__file__).resolve().parents[1] / "docs" / "backtest_limitations.md"

_REQUIRED_TOPICS = (
    "数据覆盖",
    "历史股票池",
    "企业行动",
    "汇率",
    "日历",
    "停牌估值",
    "基准",
)


class BacktestLimitationDocumentationTests(unittest.TestCase):
    """Verify the limitations document covers every required area."""

    def setUp(self) -> None:
        self.doc = _DOC_PATH.read_text(encoding="utf-8")

    def test_doc_exists(self) -> None:
        self.assertTrue(_DOC_PATH.is_file())

    def test_doc_covers_all_required_topics(self) -> None:
        for topic in _REQUIRED_TOPICS:
            with self.subTest(topic=topic):
                self.assertIn(topic, self.doc)

    def test_doc_states_research_only(self) -> None:
        self.assertIn("不构成投资建议", self.doc)
        self.assertIn("仅用于研究", self.doc)

    def test_doc_mentions_fx_refusal(self) -> None:
        self.assertIn("1:1", self.doc)

    def test_doc_mentions_survivorship_bias(self) -> None:
        self.assertIn("幸存者", self.doc)

    def test_doc_mentions_illustrative_calendar(self) -> None:
        self.assertIn("DEFAULT_HOLIDAYS", self.doc)
        self.assertIn("示例性", self.doc)

    def test_doc_cross_references_surfacing_mechanisms(self) -> None:
        for marker in ("run_precheck", "correlate_quality", "last_price"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.doc)


class CalendarHolidayLimitationTests(unittest.TestCase):
    """Verify the calendar's default holidays are illustrative only (SP 2.11)."""

    def test_default_holidays_are_defined(self) -> None:
        self.assertTrue(DEFAULT_HOLIDAYS)
        for market in (Market.HK, Market.US):
            self.assertIn(market, DEFAULT_HOLIDAYS)


class SurvivorshipRiskSurfacingTests(unittest.TestCase):
    """Verify the stock pool surfaces survivorship-bias risk (SP 2.10)."""

    def _pool(self, *, historical_known: bool):
        membership = StockPoolMembership(
            symbol="AAPL",
            market=Market.US,
            effective_date=date(2020, 1, 1),
            expiry_date=None,
            source="test",
        )
        return evaluate_stock_pool(
            market=Market.US,
            as_of=date(2024, 1, 1),
            memberships=(membership,),
            source="test",
            historical_known=historical_known,
        )

    def test_unknown_history_flags_risk(self) -> None:
        pool = self._pool(historical_known=False)
        self.assertTrue(pool.survivorship_bias_risk)
        self.assertEqual(pool.risk_reason, "source does not guarantee historical constituents")

    def test_known_history_without_gaps_is_clean(self) -> None:
        pool = self._pool(historical_known=True)
        self.assertFalse(pool.survivorship_bias_risk)
        self.assertIsNone(pool.risk_reason)


if __name__ == "__main__":
    unittest.main()
