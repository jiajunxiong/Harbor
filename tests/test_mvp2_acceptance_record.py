"""MVP 2 acceptance record tests (MVP 2 / SP 2.87).

Verifies that the acceptance record (``docs/mvp2_acceptance_record.md``)
固化了 命令 / 数据版本 / 运行 ID / 结果摘要 / 已知限制 so it is ready for MVP 3
review, and that the recorded data version and result summary stay truthful:
the documented config hash is recomputed from the same strategy configuration
and compared against the record, and the documented run is replayed to confirm
it reproduces the recorded status and final net value (SP 2.82). Any drift in
the acceptance config or engine output fails the record.
"""

import unittest
from datetime import date
from pathlib import Path

from harbor.core.backtest_config import (
    BacktestConfig,
    MarketQuota,
    RebalanceFrequency,
    RiskConfig,
)
from harbor.core.backtest_config_loader import config_hash
from harbor.core.backtest_domain import Currency, Market
from harbor.core.backtest_interfaces import DailyQuote
from harbor.core.backtest_runner import MockUniverse, run_end_to_end_backtest
from harbor.core.target_weight import TargetWeightConfig, WeightingMethod
from harbor.core.trading_calendar import MarketTradingCalendar

HKD = Currency.HKD
USD = Currency.USD
HK = Market.HK
US = Market.US

_DOC = Path(__file__).resolve().parents[1] / "docs" / "mvp2_acceptance_record.md"

# Values recorded in docs/mvp2_acceptance_record.md (kept in sync, SP 2.87).
_RECORDED_CONFIG_HASH = "c7f761bcee1e689c141ccc79ff62d9649b9c47aa27aecedb8acd9c8fa160b825"
_RECORDED_RUN_ID = "mvp2-acceptance-001"
_RECORDED_STATUS = "COMPLETED"
_RECORDED_FINAL_NET_VALUE = 999010.33
_RECORDED_CUTOFF = date(2024, 1, 8)

_DAYS = (
    date(2024, 1, 2),
    date(2024, 1, 3),
    date(2024, 1, 4),
    date(2024, 1, 5),
    date(2024, 1, 8),
)


def _quote(market: Market, symbol: str, day: date, close: float) -> DailyQuote:
    return DailyQuote(
        market=market,
        symbol=symbol,
        day=day,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000_000,
        adjusted_close=close,
    )


def _acceptance_config() -> BacktestConfig:
    """The cross-market acceptance configuration recorded in the doc."""
    return BacktestConfig(
        markets=(HK, US),
        market_quotas=(
            MarketQuota(market=HK, target_count=1, weight=0.5),
            MarketQuota(market=US, target_count=1, weight=0.5),
        ),
        start_date=_DAYS[0],
        end_date=_DAYS[-1],
        base_currency=HKD,
        rebalance_frequency=RebalanceFrequency.QUARTERLY,
        initial_capital=1_000_000.0,
        risk=RiskConfig(max_position_pct=1.0, max_market_pct=1.0, min_cash_pct=0.0),
    )


def _acceptance_universe() -> MockUniverse:
    """The fixed Mock universe recorded in the doc (SP 2.82 reproducible)."""
    quotes: dict[tuple[Market, str], dict[date, DailyQuote]] = {}
    for symbol, price in {"0001.HK": 50.0, "0002.HK": 20.0}.items():
        quotes[(HK, symbol)] = {day: _quote(HK, symbol, day, price) for day in _DAYS}
    for symbol, price in {"AAPL": 100.0, "MSFT": 200.0}.items():
        quotes[(US, symbol)] = {day: _quote(US, symbol, day, price) for day in _DAYS}
    fx_rates = {(USD, HKD): {day: 7.8 for day in _DAYS}}
    return MockUniverse(
        calendar=MarketTradingCalendar({HK: frozenset(), US: frozenset()}),
        quotes=quotes,
        fx_rates=fx_rates,
        selections={
            (HK, _DAYS[0]): ("0001.HK",),
            (US, _DAYS[0]): ("AAPL",),
        },
    )


class AcceptanceRecordDocumentationTests(unittest.TestCase):
    """The record exists and covers the SP 2.87 acceptance dimensions."""

    def test_documentation_covers_acceptance_dimensions(self) -> None:
        self.assertTrue(_DOC.is_file())
        text = _DOC.read_text(encoding="utf-8")
        for marker in (
            "命令",
            "数据版本",
            "config_hash",
            "运行 ID",
            _RECORDED_RUN_ID,
            "结果摘要",
            "已知限制",
            "MVP 3",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, text)

    def test_recorded_values_are_stated_in_documentation(self) -> None:
        text = _DOC.read_text(encoding="utf-8")
        self.assertIn(_RECORDED_CONFIG_HASH, text)
        self.assertIn(_RECORDED_STATUS, text)
        self.assertIn(str(_RECORDED_FINAL_NET_VALUE), text)
        self.assertIn("2024-01-08", text)
        self.assertIn("不构成投资建议", text)


class AcceptanceRecordTruthfulnessTests(unittest.TestCase):
    """The recorded data version and result summary match the engine."""

    def test_recorded_config_hash_matches_the_acceptance_config(self) -> None:
        """Anti-drift: the documented data version equals the current config hash."""
        self.assertEqual(config_hash(_acceptance_config()), _RECORDED_CONFIG_HASH)

    def test_recorded_run_reproduces_the_recorded_result(self) -> None:
        """The documented run replays to the recorded status and net value (SP 2.82)."""
        trace = run_end_to_end_backtest(
            run_id=_RECORDED_RUN_ID,
            config=_acceptance_config(),
            universe=_acceptance_universe(),
            data_cutoff=_RECORDED_CUTOFF,
            code_version="1.0.0",
            weighting=TargetWeightConfig(
                method=WeightingMethod.EQUAL,
                cash_weight=0.05,
                decimal_places=4,
            ),
        )
        self.assertEqual(trace.state.status.value, _RECORDED_STATUS)
        self.assertEqual(trace.reconcile_all(), ())
        self.assertAlmostEqual(
            trace.results[-1].valuation.net_value.total_value,
            _RECORDED_FINAL_NET_VALUE,
            places=2,
        )
        self.assertEqual(len(trace.results), 5)


if __name__ == "__main__":
    unittest.main()
