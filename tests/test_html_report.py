"""HTML research report tests (MVP 2 / SP 2.60).

Verifies that the SP 2.58 artifact renders as a self-contained HTML report with
a summary, an embedded net-value chart (图表数据), key risks, data coverage and
known assumptions, and that it never implies a return promise.
"""

import json
import re
import unittest
from datetime import date, timedelta

from harbor.core.backtest_config import BacktestConfig, MarketQuota
from harbor.core.backtest_domain import (
    BacktestStatus,
    CashBalance,
    Currency,
    Market,
    NetValue,
    OrderSide,
)
from harbor.core.backtest_runner import BacktestTrace, DailyResult
from harbor.core.backtest_state_machine import RunState
from harbor.core.html_report import (
    ReportError,
    build_report_data,
    render_html_report,
)
from harbor.core.result_export import export_run_to_dict
from harbor.core.run_identity import RunIdentity
from harbor.core.valuation import DailyValuation, PositionValue

HKD = Currency.HKD
HK = Market.HK

_DAY = date(2024, 1, 2)
_INITIAL = 100_000.0


def _day(offset: int) -> date:
    return _DAY + timedelta(days=offset)


def _config() -> BacktestConfig:
    return BacktestConfig(
        markets=(Market.HK,),
        market_quotas=(MarketQuota(market=Market.HK, target_count=15, weight=1.0),),
        start_date=_DAY,
        end_date=_day(1),
        base_currency=HKD,
        initial_capital=_INITIAL,
    )


def _identity() -> RunIdentity:
    return RunIdentity(config_hash="abc123", data_cutoff=_DAY, code_version="1.0.0")


def _state() -> RunState:
    return RunState(run_id="r1", status=BacktestStatus.COMPLETED)


def _position_value(price: float, quantity: float = 1_000.0) -> PositionValue:
    return PositionValue(
        market=HK,
        symbol="0001.HK",
        quantity=quantity,
        price=price,
        currency=HKD,
        fx_rate=1.0,
        market_value_quote=quantity * price,
        market_value_base=quantity * price,
        carried_forward=False,
        warning=None,
    )


def _valuation(*, as_of: date, cash: float, price: float, fees: float = 0.0) -> DailyValuation:
    position = _position_value(price)
    return DailyValuation(
        as_of=as_of,
        base_currency=HKD,
        cash=(CashBalance(currency=HKD, amount=cash),),
        position_values=(position,),
        realized_fees=(CashBalance(currency=HKD, amount=fees),),
        fx_pnl=0.0,
        net_value=NetValue(
            as_of_date=as_of,
            currency=HKD,
            cash=cash,
            securities_value=position.market_value_base,
            fees_paid=fees,
        ),
    )


def _result(
    *,
    as_of: date,
    valuation: DailyValuation,
    fills: tuple = (),
    warnings: tuple = (),
) -> DailyResult:
    return DailyResult(
        as_of=as_of,
        valuation=valuation,
        fills=fills,
        dividends=(),
        adjustments=(),
        refused=(),
        warnings=warnings,
    )


def _main_results(warning: str = "") -> tuple[DailyResult, ...]:
    day0 = _result(
        as_of=_day(0),
        valuation=_valuation(as_of=_day(0), cash=_INITIAL - 50_030.0, price=50.0, fees=30.0),
        fills=_main_fill(),
        warnings=(warning,) if warning else (),
    )
    day1 = _result(
        as_of=_day(1),
        valuation=_valuation(as_of=_day(1), cash=_INITIAL - 50_030.0, price=55.0, fees=30.0),
    )
    return (day0, day1)


def _main_fill() -> tuple:
    from harbor.core.backtest_domain import Fill

    return (
        Fill(
            order_ref="r",
            symbol="0001.HK",
            market=HK,
            side=OrderSide.BUY,
            quantity=1_000.0,
            price=50.0,
            currency=HKD,
            trade_date=_DAY,
            fee=30.0,
        ),
    )


def _trace(results: tuple[DailyResult, ...] | None = None) -> BacktestTrace:
    return BacktestTrace(
        run_id="r1",
        config=_config(),
        identity=_identity(),
        state=_state(),
        results=_main_results() if results is None else results,
    )


def _artifact(results: tuple[DailyResult, ...] | None = None) -> dict:
    return export_run_to_dict(trace=_trace(results))


def _extract_report_data(html_text: str) -> dict:
    match = re.search(r"window\.REPORT_DATA = (\{.*?\});", html_text, re.DOTALL)
    assert match is not None
    return json.loads(match.group(1))


class ReportDataTests(unittest.TestCase):
    """Verify the structured report data (SP 2.60)."""

    def test_build_report_data_structure(self) -> None:
        data = build_report_data(_artifact())
        self.assertEqual(data["run_id"], "r1")
        self.assertEqual(data["status"], "COMPLETED")
        self.assertTrue(data["succeeded"])
        self.assertEqual(data["markets"], ["HK"])
        self.assertEqual(data["day_count"], 2)
        self.assertEqual(len(data["net_values"]), 2)
        self.assertEqual(data["net_values"][0]["date"], _DAY.isoformat())
        self.assertAlmostEqual(data["net_values"][1]["total_value"], 104_970.0, places=6)

    def test_invalid_artifact_raises(self) -> None:
        with self.assertRaisesRegex(ReportError, "SP 2.58"):
            build_report_data({})


class HtmlContentTests(unittest.TestCase):
    """Verify the rendered HTML sections (SP 2.60)."""

    def test_html_contains_all_sections(self) -> None:
        text = render_html_report(_artifact())
        self.assertIn("<title>", text)
        self.assertIn("摘要 (Summary)", text)
        self.assertIn("绩效指标 (Performance)", text)
        self.assertIn("主要风险 (Key Risks)", text)
        self.assertIn("数据覆盖 (Data Coverage)", text)
        self.assertIn("已知假设 (Known Assumptions)", text)
        self.assertIn("净值走势 (Net Value Chart)", text)
        self.assertIn("r1", text)

    def test_disclaimer_no_return_promise(self) -> None:
        text = render_html_report(_artifact())
        self.assertIn("不构成投资建议", text)
        self.assertIn("不表示未来收益", text)
        self.assertIn("no promise of future returns", text)

    def test_embedded_chart_data(self) -> None:
        data = _extract_report_data(render_html_report(_artifact()))
        self.assertEqual(data["run_id"], "r1")
        self.assertEqual(len(data["net_values"]), 2)
        self.assertEqual(data["net_values"][0]["date"], _DAY.isoformat())

    def test_svg_chart_present_with_two_points(self) -> None:
        text = render_html_report(_artifact())
        self.assertIn("<svg", text)
        self.assertIn("<polyline", text)

    def test_svg_absent_with_single_point(self) -> None:
        day0 = _result(
            as_of=_day(0),
            valuation=_valuation(as_of=_day(0), cash=_INITIAL, price=50.0),
        )
        text = render_html_report(_artifact((day0,)))
        self.assertNotIn("<svg", text)
        self.assertIn("无法绘制图表", text)

    def test_warnings_rendered(self) -> None:
        text = render_html_report(_artifact(_main_results(warning="low volume")))
        self.assertIn("告警 (Warnings)", text)
        self.assertIn("low volume", text)

    def test_html_escapes_dynamic_content(self) -> None:
        results = _main_results(warning="<script>alert(1)</script>")
        text = render_html_report(_artifact(results))
        self.assertIn("&lt;script&gt;", text)
        self.assertNotIn("<script>alert(1)</script>", text)

    def test_chart_json_cannot_break_script_block(self) -> None:
        text = render_html_report(_artifact())
        # the embedded JSON payload must not contain a raw closing script tag
        data_block = re.search(r"window\.REPORT_DATA = (.*?);\n</script>", text, re.DOTALL)
        assert data_block is not None
        self.assertNotIn("</script>", data_block.group(1))

    def test_custom_title(self) -> None:
        text = render_html_report(_artifact(), title="My Report")
        self.assertIn("<title>My Report</title>", text)


if __name__ == "__main__":
    unittest.main()
