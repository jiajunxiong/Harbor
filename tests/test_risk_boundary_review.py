"""Risk boundary review (MVP 2 / SP 2.89).

Confirms two risk boundaries hold before release:

- 回测输出不含收益承诺 (no return promises): every user-facing backtest output —
  the HTML report (SP 2.60), the research docs and the example strategy configs —
  carries the research-only disclaimer (不构成投资建议 / 不表示未来收益或回撤), and
  the exported artifact / trace contain no return-promise language;
- 不产生经纪订单或任何外部交易副作用 (no broker orders / no external trading side
  effects): the entire backtest path (core ``backtest*.py``, the runner, the
  backtest service and the CLI) imports no broker SDK, no order-placement call,
  no webhook and no network client — a backtest only reads research data and
  writes local results (SP 2.67-2.87).

The review is DB-free and scans the actual source files, so it runs everywhere.
"""

import json
import unittest
from datetime import date
from pathlib import Path

from harbor.core.backtest_config import (
    BacktestConfig,
    MarketQuota,
    RebalanceFrequency,
    RiskConfig,
)
from harbor.core.backtest_domain import Currency, Market, Order
from harbor.core.backtest_interfaces import DailyQuote
from harbor.core.backtest_runner import MockUniverse, run_end_to_end_backtest
from harbor.core.html_report import render_html_report
from harbor.core.result_export import export_run_to_dict
from harbor.core.target_weight import TargetWeightConfig, WeightingMethod
from harbor.core.trading_calendar import MarketTradingCalendar

HKD = Currency.HKD
USD = Currency.USD
HK = Market.HK
US = Market.US

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CORE = _REPO_ROOT / "src" / "harbor" / "core"
_SERVICES = _REPO_ROOT / "src" / "harbor" / "services"
_CLI = _REPO_ROOT / "src" / "harbor" / "cli.py"

_BACKTEST_CORE_FILES = tuple(sorted(_CORE.glob("backtest*.py")))
_BACKTEST_PATH_FILES = _BACKTEST_CORE_FILES + (
    _SERVICES / "backtest.py",
    _CLI,
)

# Broker SDKs / order-placement / webhook / HTTP-trading markers (side effects).
_FORBIDDEN_SIDE_EFFECT_MARKERS = (
    "place_order(",
    "send_order(",
    "submit_order(",
    "placeorder(",
    "webhook",
    "import alpaca",
    "from alpaca",
    "ib_insync",
    "import futu",
    "from futu",
    "shinny",
    "easyquotation",
    "vnpy",
    "import ctp",
)
# Network clients: importing one into the backtest path would allow external calls.
_FORBIDDEN_NETWORK_IMPORTS = (
    "import requests",
    "from requests",
    "import httpx",
    "from httpx",
    "import aiohttp",
    "from aiohttp",
    "import urllib",
    "from urllib",
    "import socket",
    "from socket",
    "import subprocess",
    "from subprocess",
)

_RETURN_PROMISE_PHRASES = ("guaranteed", "保证收益", "稳赚", "必然收益", "will return")

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


def _acceptance_universe() -> MockUniverse:
    """The fixed cross-market Mock universe used for the output audits."""
    quotes: dict[tuple[Market, str], dict[date, DailyQuote]] = {}
    for symbol, price in {"0001.HK": 50.0, "0002.HK": 20.0}.items():
        quotes[(HK, symbol)] = {day: _quote(HK, symbol, day, price) for day in _DAYS}
    for symbol, price in {"AAPL": 100.0, "MSFT": 200.0}.items():
        quotes[(US, symbol)] = {day: _quote(US, symbol, day, price) for day in _DAYS}
    return MockUniverse(
        calendar=MarketTradingCalendar({HK: frozenset(), US: frozenset()}),
        quotes=quotes,
        fx_rates={(USD, HKD): {day: 7.8 for day in _DAYS}},
        selections={
            (HK, _DAYS[0]): ("0001.HK",),
            (US, _DAYS[0]): ("AAPL",),
        },
    )


def _acceptance_config() -> BacktestConfig:
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


def _acceptance_artifact() -> dict[str, object]:
    trace = run_end_to_end_backtest(
        run_id="risk-review",
        config=_acceptance_config(),
        universe=_acceptance_universe(),
        code_version="1.0.0",
        weighting=TargetWeightConfig(
            method=WeightingMethod.EQUAL,
            cash_weight=0.05,
            decimal_places=4,
        ),
    )
    return export_run_to_dict(trace=trace, schema_version="1.0")


class NoReturnPromiseTests(unittest.TestCase):
    """Backtest outputs never promise future returns (SP 2.89)."""

    def test_html_report_carries_research_disclaimer(self) -> None:
        html = render_html_report(_acceptance_artifact())
        self.assertIn("不构成投资建议", html)
        self.assertIn("不表示未来收益或回撤", html)

    def test_research_docs_carry_disclaimer(self) -> None:
        for relative in (
            "README.md",
            "docs/backtest_limitations.md",
            "docs/backtest_default_parameters.md",
            "docs/mvp2_acceptance_record.md",
        ):
            with self.subTest(doc=relative):
                text = (_REPO_ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("不构成投资建议", text)

    def test_performance_baseline_makes_no_commitment(self) -> None:
        text = (_REPO_ROOT / "docs" / "performance_baseline.md").read_text(encoding="utf-8")
        self.assertIn("不构成", text)

    def test_example_configs_carry_disclaimer(self) -> None:
        for path in sorted((_REPO_ROOT / "examples" / "configs").iterdir()):
            if path.suffix not in (".yaml", ".json"):
                continue
            with self.subTest(config=path.name):
                text = path.read_text(encoding="utf-8")
                # YAML headers carry the full Chinese disclaimer; the JSON twin
                # (comment-free) states research-only intent in its description.
                self.assertIn("research", text)
                if path.suffix == ".yaml":
                    self.assertIn("不构成投资建议", text)

    def test_exported_artifact_contains_no_return_promise(self) -> None:
        artifact = json.dumps(_acceptance_artifact(), ensure_ascii=False)
        for phrase in _RETURN_PROMISE_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, artifact)


class NoBrokerOrExternalSideEffectTests(unittest.TestCase):
    """The backtest path never creates broker orders or external side effects."""

    def test_backtest_path_has_no_broker_or_order_placement(self) -> None:
        for path in _BACKTEST_PATH_FILES:
            text = path.read_text(encoding="utf-8")
            for marker in _FORBIDDEN_SIDE_EFFECT_MARKERS:
                with self.subTest(file=path.name, marker=marker):
                    self.assertNotIn(marker, text)

    def test_backtest_path_imports_no_network_client(self) -> None:
        for path in _BACKTEST_PATH_FILES:
            text = path.read_text(encoding="utf-8")
            for marker in _FORBIDDEN_NETWORK_IMPORTS:
                with self.subTest(file=path.name, marker=marker):
                    self.assertNotIn(marker, text)

    def test_order_domain_is_a_pure_value(self) -> None:
        """The simulated Order carries no action that would send it anywhere."""
        self.assertTrue(getattr(Order, "__dataclass_params__").frozen)
        for action in (
            "place",
            "place_order",
            "send",
            "send_order",
            "submit",
            "submit_order",
            "execute",
        ):
            with self.subTest(action=action):
                self.assertFalse(hasattr(Order, action))


if __name__ == "__main__":
    unittest.main()
