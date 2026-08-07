"""Results JSON artifacts (MVP 2 / SP 2.58).

Exports one backtest run (SP 2.51 :class:`~harbor.core.backtest_runner.BacktestTrace`)
as a stable, replayable JSON document containing:

- run metadata (运行元数据): run id, status, success flag, code version, config
  hash, data cutoff and data range (SP 2.48 identity);
- the validated configuration snapshot (SP 2.4 / 2.5);
- net values (净值), positions (持仓), trades (交易), dividends, corporate
  actions, refused orders and warnings, all with stable field names;
- optional pre-computed research metrics (SP 2.53–2.57): performance, trade
  stats, exposure, drawdown events and attribution.

Every date is rendered ISO-8601 and every enum as its string value, so the
document is JSON-safe and deterministic (``sort_keys`` keeps repeated exports
byte-identical for SP 2.61 replayability). The analysis is research-only
(不构成投资建议); the document carries no return promise and no broker orders.

Pure core logic: only stdlib (json, dataclasses) and the core types; never
touches storage or CLI code.
"""

import dataclasses
import json
from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum
from typing import Any, cast

from harbor.core.attribution import AttributionReport
from harbor.core.backtest_runner import BacktestTrace, DailyResult
from harbor.core.drawdown_events import DrawdownSeries
from harbor.core.exposure import ExposurePoint, ExposureSeries
from harbor.core.performance_metrics import PerformanceMetrics
from harbor.core.trade_metrics import TradeStats


class ExportError(ValueError):
    """Raised when a value cannot be serialized to JSON (SP 2.58)."""


def _json_key(key: object) -> str:
    """Return a JSON-safe string key for an arbitrary mapping key."""
    if isinstance(key, Enum):
        return str(key.value)
    return str(key)


def _json_safe(value: object) -> Any:
    """Recursively convert a value into JSON-safe primitives (SP 2.58).

    Handles ``date``/``datetime`` (ISO-8601), enums (string value), mappings
    (including :class:`MappingProxyType`), dataclasses, and sequences. Anything
    else raises :class:`ExportError` rather than silently stringifying it.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {_json_key(key): _json_safe(item) for key, item in value.items()}
    if dataclasses.is_dataclass(value):
        return {
            field.name: _json_safe(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }
    if isinstance(value, (tuple, list, frozenset, set)):
        return [_json_safe(item) for item in value]
    raise ExportError(f"Cannot serialize value of type {type(value).__name__!r} to JSON.")


def _net_value_entries(results: tuple[DailyResult, ...]) -> list[dict[str, Any]]:
    """Return the day-by-day net-value rows (净值)."""
    entries: list[dict[str, Any]] = []
    for result in results:
        net = result.valuation.net_value
        entries.append(
            {
                "date": result.as_of.isoformat(),
                "currency": net.currency.value,
                "cash": net.cash,
                "securities_value": net.securities_value,
                "fees_paid": net.fees_paid,
                "total_value": net.total_value,
                "fx_pnl": result.valuation.fx_pnl,
            }
        )
    return entries


def _position_entries(results: tuple[DailyResult, ...]) -> list[dict[str, Any]]:
    """Return the day-by-day position rows (持仓)."""
    entries: list[dict[str, Any]] = []
    for result in results:
        for position in result.valuation.position_values:
            entries.append(
                {
                    "date": result.as_of.isoformat(),
                    "market": position.market.value,
                    "symbol": position.symbol,
                    "quantity": position.quantity,
                    "price": position.price,
                    "currency": position.currency.value,
                    "fx_rate": position.fx_rate,
                    "market_value_quote": position.market_value_quote,
                    "market_value_base": position.market_value_base,
                    "carried_forward": position.carried_forward,
                }
            )
    return entries


def _trade_entries(results: tuple[DailyResult, ...]) -> list[dict[str, Any]]:
    """Return the executed-trade rows (交易)."""
    entries: list[dict[str, Any]] = []
    for result in results:
        for fill in result.fills:
            entries.append(
                {
                    "date": result.as_of.isoformat(),
                    "order_ref": fill.order_ref,
                    "market": fill.market.value,
                    "symbol": fill.symbol,
                    "side": fill.side.value,
                    "quantity": fill.quantity,
                    "price": fill.price,
                    "currency": fill.currency.value,
                    "fee": fill.fee,
                    "notional": fill.notional,
                }
            )
    return entries


def _dividend_entries(results: tuple[DailyResult, ...]) -> list[dict[str, Any]]:
    """Return the dividend rows credited to the ledger (SP 2.43)."""
    entries: list[dict[str, Any]] = []
    for result in results:
        for dividend in result.dividends:
            entries.append(
                {
                    "date": result.as_of.isoformat(),
                    "market": dividend.market.value,
                    "symbol": dividend.symbol,
                    "currency": dividend.currency.value,
                    "entitlement_date": dividend.entitlement_date.isoformat(),
                    "payment_date": dividend.payment_date.isoformat(),
                    "quantity": dividend.quantity,
                    "per_share": dividend.per_share,
                    "gross_amount": dividend.gross_amount,
                    "is_special": dividend.is_special,
                }
            )
    return entries


def _corporate_action_entries(results: tuple[DailyResult, ...]) -> list[dict[str, Any]]:
    """Return the applied corporate-action rows (SP 2.44)."""
    entries: list[dict[str, Any]] = []
    for result in results:
        for adjustment in result.adjustments:
            entries.append(
                {
                    "date": result.as_of.isoformat(),
                    "market": adjustment.market.value,
                    "symbol": adjustment.symbol,
                    "action_id": adjustment.action_id,
                    "action_type": adjustment.action_type.value,
                    "old_quantity": adjustment.old_quantity,
                    "new_quantity": adjustment.new_quantity,
                    "cash_amount": adjustment.cash_amount,
                }
            )
    return entries


def _refused_entries(results: tuple[DailyResult, ...]) -> list[dict[str, Any]]:
    """Return the rejected-trade trail rows (SP 2.41)."""
    entries: list[dict[str, Any]] = []
    for result in results:
        for refused in result.refused:
            entries.append(
                {
                    "date": refused.day.isoformat(),
                    "market": refused.order.market.value,
                    "symbol": refused.order.symbol,
                    "side": refused.order.side.value,
                    "quantity": refused.order.quantity,
                    "reason": refused.reason,
                }
            )
    return entries


def _warning_entries(results: tuple[DailyResult, ...]) -> list[dict[str, Any]]:
    """Return the accumulated warning rows (告警)."""
    return [
        {"date": result.as_of.isoformat(), "message": message}
        for result in results
        for message in result.warnings
    ]


def _exposure_point_compact(point: ExposurePoint | None) -> dict[str, Any] | None:
    """Render one exposure point without bloating the artifact."""
    if point is None:
        return None
    return {
        "as_of": point.as_of.isoformat(),
        "market_exposure": {market.value: value for market, value in point.market_exposure.items()},
        "currency_exposure": {
            currency.value: value for currency, value in point.currency_exposure.items()
        },
        "symbol_exposure": [
            {"market": market.value, "symbol": symbol, "exposure": value}
            for (market, symbol), value in point.symbol_exposure.items()
        ],
    }


def _performance_section(metrics: PerformanceMetrics | None) -> dict[str, Any] | None:
    """Serialize SP 2.53 performance metrics (指标)."""
    if metrics is None:
        return None
    return cast(dict[str, Any], _json_safe(metrics))


def _trade_stats_section(stats: TradeStats | None) -> dict[str, Any] | None:
    """Serialize SP 2.54 trade statistics (指标)."""
    if stats is None:
        return None
    return cast(dict[str, Any], _json_safe(stats))


def _exposure_section(series: ExposureSeries | None) -> dict[str, Any] | None:
    """Serialize the SP 2.55 exposure series (暴露)."""
    if series is None:
        return None
    return {
        "days": len(series.points),
        "points": [
            {
                "as_of": point.as_of.isoformat(),
                "total_value": point.total_value,
                "cash_exposure": point.cash_exposure,
                "market_exposure": {
                    market.value: value for market, value in point.market_exposure.items()
                },
                "currency_exposure": {
                    currency.value: value for currency, value in point.currency_exposure.items()
                },
                "symbol_exposure": [
                    {"market": market.value, "symbol": symbol, "exposure": value}
                    for (market, symbol), value in point.symbol_exposure.items()
                ],
                "industry_exposure": (
                    dict(point.industry_exposure) if point.industry_exposure is not None else None
                ),
            }
            for point in series.points
        ],
    }


def _drawdown_section(series: DrawdownSeries | None) -> dict[str, Any] | None:
    """Serialize the SP 2.56 drawdown events (回撤事件)."""
    if series is None:
        return None
    events: list[dict[str, Any]] = []
    for event in series.events:
        events.append(
            {
                "threshold": event.threshold,
                "start_date": event.start_date.isoformat(),
                "peak_date": event.peak_date.isoformat(),
                "peak_value": event.peak_value,
                "trough_date": event.trough_date.isoformat(),
                "trough_value": event.trough_value,
                "depth": event.depth,
                "recovered_date": (
                    event.recovered_date.isoformat() if event.recovered_date is not None else None
                ),
                "positions": [
                    {
                        "market": position.market.value,
                        "symbol": position.symbol,
                        "quantity": position.quantity,
                        "price": position.price,
                        "currency": position.currency.value,
                        "market_value_base": position.market_value_base,
                    }
                    for position in event.trough_valuation.position_values
                ],
                "exposure": _exposure_point_compact(event.trough_exposure),
            }
        )
    return {"thresholds": list(series.config.thresholds), "events": events}


def _attribution_section(report: AttributionReport | None) -> dict[str, Any] | None:
    """Serialize the SP 2.57 attribution report (归因)."""
    if report is None:
        return None
    return {
        "base_currency": report.base_currency.value,
        "initial_capital": report.initial_capital,
        "tolerance": report.tolerance,
        "reconciled": report.reconciled,
        "totals": {
            "net_value_change": report.total_net_value_change,
            "price_return": report.total_price_return,
            "dividends": report.total_dividends,
            "corporate_actions": report.total_corporate_actions,
            "trading_costs": report.total_trading_costs,
            "fx_impact": report.total_fx_impact,
            "gap": report.total_gap,
        },
        "days": [_json_safe(day) for day in report.days],
    }


def export_run_to_dict(
    *,
    trace: BacktestTrace,
    performance: PerformanceMetrics | None = None,
    trade_stats: TradeStats | None = None,
    exposure: ExposureSeries | None = None,
    drawdown: DrawdownSeries | None = None,
    attribution: AttributionReport | None = None,
    schema_version: str = "1.0",
) -> dict[str, Any]:
    """Assemble the run's results into a JSON-safe artifact (SP 2.58).

    Args:
        trace: The end-to-end run outcome (SP 2.51).
        performance: Optional SP 2.53 metrics.
        trade_stats: Optional SP 2.54 trade statistics.
        exposure: Optional SP 2.55 exposure series.
        drawdown: Optional SP 2.56 drawdown events.
        attribution: Optional SP 2.57 attribution report.
        schema_version: The artifact schema version.

    Returns:
        A JSON-safe dictionary with stable, documented fields.

    Raises:
        ExportError: If any value cannot be serialized to JSON.
    """
    results = trace.results
    start_date = results[0].as_of.isoformat() if results else None
    end_date = results[-1].as_of.isoformat() if results else None
    return {
        "schema_version": schema_version,
        "run": {
            "run_id": trace.run_id,
            "status": trace.state.status.value,
            "succeeded": trace.succeeded,
            "inputs": {
                "code_version": trace.identity.code_version,
                "config_hash": trace.identity.config_hash,
                "data_cutoff": trace.identity.data_cutoff.isoformat(),
                "data_range_start": start_date,
                "data_range_end": end_date,
            },
            "base_currency": trace.config.base_currency.value,
            "initial_capital": trace.config.initial_capital,
            "day_count": len(results),
            "reconciliation_failures": list(trace.reconcile_all()),
        },
        "config": trace.config.model_dump(mode="json"),
        "metrics": {
            "performance": _performance_section(performance),
            "trade_stats": _trade_stats_section(trade_stats),
            "exposure": _exposure_section(exposure),
            "drawdown": _drawdown_section(drawdown),
            "attribution": _attribution_section(attribution),
        },
        "net_values": _net_value_entries(results),
        "positions": _position_entries(results),
        "trades": _trade_entries(results),
        "dividends": _dividend_entries(results),
        "corporate_actions": _corporate_action_entries(results),
        "refused": _refused_entries(results),
        "warnings": _warning_entries(results),
    }


def export_run_to_json(
    *,
    trace: BacktestTrace,
    performance: PerformanceMetrics | None = None,
    trade_stats: TradeStats | None = None,
    exposure: ExposureSeries | None = None,
    drawdown: DrawdownSeries | None = None,
    attribution: AttributionReport | None = None,
    schema_version: str = "1.0",
    indent: int = 2,
) -> str:
    """Return the run's results as a deterministic JSON document (SP 2.58).

    ``sort_keys`` keeps repeated exports of the same run byte-identical
    (SP 2.61 replayability).
    """
    data = export_run_to_dict(
        trace=trace,
        performance=performance,
        trade_stats=trade_stats,
        exposure=exposure,
        drawdown=drawdown,
        attribution=attribution,
        schema_version=schema_version,
    )
    return json.dumps(data, indent=indent, sort_keys=True, ensure_ascii=False)
