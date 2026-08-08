"""CSV export (MVP 2 / SP 2.59).

Renders the SP 2.58 results artifact as stable CSV documents for net values
(净值), trades (交易), positions (持仓), dividends, corporate actions, refused
orders, warnings, metrics (指标) and factor snapshots (因子快照).

Every table has a fixed, documented column order (字段稳定) and every CSV
carries the run id in its first ``backtest_run_id`` column, so each row can be
traced back to the research run (SP 2.7). Dates are ISO-8601, enums are their
string values, ``None`` renders as an empty cell, and booleans as
``true``/``false``. Nested mapping values are flattened with dot-joined keys in
the metrics CSV; list values are JSON-encoded so the column set never varies.

The output is research-only (不构成投资建议) and deterministic for SP 2.61
replayability.

Pure core logic: only stdlib (csv, io, json) and the core artifact types; never
touches storage or CLI code.
"""

import csv
import io
import json
from collections.abc import Mapping, Sequence
from typing import Any

from harbor.core.factor_snapshot import FactorSnapshot


class CsvExportError(ValueError):
    """Raised when a CSV cannot be rendered (SP 2.59)."""


_FIELDS: dict[str, tuple[str, ...]] = {
    "net_values": (
        "date",
        "currency",
        "cash",
        "securities_value",
        "fees_paid",
        "total_value",
        "fx_pnl",
    ),
    "positions": (
        "date",
        "market",
        "symbol",
        "quantity",
        "price",
        "currency",
        "fx_rate",
        "market_value_quote",
        "market_value_base",
        "carried_forward",
    ),
    "trades": (
        "date",
        "order_ref",
        "market",
        "symbol",
        "side",
        "quantity",
        "price",
        "currency",
        "fee",
        "notional",
    ),
    "dividends": (
        "date",
        "market",
        "symbol",
        "currency",
        "entitlement_date",
        "payment_date",
        "quantity",
        "per_share",
        "gross_amount",
        "is_special",
    ),
    "corporate_actions": (
        "date",
        "market",
        "symbol",
        "action_id",
        "action_type",
        "old_quantity",
        "new_quantity",
        "cash_amount",
    ),
    "refused": ("date", "market", "symbol", "side", "quantity", "reason"),
    "warnings": ("date", "message"),
}

_FACTOR_FIELDS: tuple[str, ...] = (
    "as_of",
    "market",
    "symbol",
    "raw_values",
    "availability_dates",
    "standardized_scores",
    "composite_score",
    "rank",
    "selected",
    "exclusion_reason",
)

_METRIC_GROUPS = ("performance", "trade_stats", "exposure", "drawdown", "attribution")


def _cell(value: Any) -> str:
    """Render one value as a stable CSV cell."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def rows_to_csv(
    *,
    run_id: str,
    rows: Sequence[Mapping[str, Any]],
    fields: Sequence[str],
) -> str:
    """Render rows as CSV with a leading ``backtest_run_id`` column (SP 2.59).

    Args:
        run_id: The backtest run id written into every row.
        rows: The rows to export; missing fields render as empty cells.
        fields: The fixed, stable column order (字段稳定).
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["backtest_run_id", *fields])
    for row in rows:
        writer.writerow([run_id, *(_cell(row.get(field)) for field in fields)])
    return buffer.getvalue()


def export_table_csv(artifact: dict[str, Any], *, table: str) -> str:
    """Render one artifact table as CSV with ``backtest_run_id`` (SP 2.59).

    Raises:
        CsvExportError: If ``table`` is not a known artifact table.
    """
    if table not in _FIELDS:
        raise CsvExportError(f"Unknown CSV table {table!r}; expected one of {sorted(_FIELDS)}.")
    return rows_to_csv(
        run_id=artifact["run"]["run_id"],
        rows=artifact[table],
        fields=_FIELDS[table],
    )


def export_net_values_csv(artifact: dict[str, Any]) -> str:
    """Render the net-value (净值) CSV."""
    return export_table_csv(artifact, table="net_values")


def export_trades_csv(artifact: dict[str, Any]) -> str:
    """Render the trades (交易) CSV."""
    return export_table_csv(artifact, table="trades")


def export_positions_csv(artifact: dict[str, Any]) -> str:
    """Render the positions (持仓) CSV."""
    return export_table_csv(artifact, table="positions")


def export_dividends_csv(artifact: dict[str, Any]) -> str:
    """Render the dividends CSV."""
    return export_table_csv(artifact, table="dividends")


def export_corporate_actions_csv(artifact: dict[str, Any]) -> str:
    """Render the corporate-actions CSV."""
    return export_table_csv(artifact, table="corporate_actions")


def export_refused_csv(artifact: dict[str, Any]) -> str:
    """Render the refused-orders CSV."""
    return export_table_csv(artifact, table="refused")


def export_warnings_csv(artifact: dict[str, Any]) -> str:
    """Render the warnings (告警) CSV."""
    return export_table_csv(artifact, table="warnings")


def _flatten(mapping: Mapping[str, Any], prefix: str) -> list[tuple[str, Any]]:
    """Flatten a nested mapping into ``(dot.joined.field, value)`` pairs."""
    rows: list[tuple[str, Any]] = []
    for key, value in mapping.items():
        field = f"{prefix}.{key}" if prefix else key
        if isinstance(value, Mapping):
            rows.extend(_flatten(value, field))
        else:
            rows.append((field, value))
    return rows


def export_metrics_csv(artifact: dict[str, Any]) -> str:
    """Render the metrics (指标) CSV in stable long format.

    Columns are ``backtest_run_id, group, field, value``. Nested mappings are
    flattened with dot-joined keys (e.g. ``attribution.totals.price_return``);
    list values (exposure points, drawdown events, attribution days) are
    JSON-encoded so the column set never varies.
    """
    run_id = artifact["run"]["run_id"]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["backtest_run_id", "group", "field", "value"])
    metrics = artifact["metrics"]
    for group in _METRIC_GROUPS:
        section = metrics.get(group)
        if section is None:
            continue
        for field, value in _flatten(section, ""):
            rendered: str
            if isinstance(value, list):
                rendered = json.dumps(value, sort_keys=True)
            else:
                rendered = _cell(value)
            writer.writerow([run_id, group, field, rendered])
    return buffer.getvalue()


def export_factor_snapshot_csv(
    snapshots: Sequence[FactorSnapshot],
    *,
    run_id: str,
) -> str:
    """Render the factor snapshots (因子快照) CSV.

    One row per snapshot entry. The factor-value mappings are JSON-encoded so
    the column set stays stable regardless of which factors were scored.
    """
    rows: list[dict[str, Any]] = []
    for snapshot in snapshots:
        for entry in snapshot.entries:
            rows.append(
                {
                    "as_of": snapshot.as_of.isoformat(),
                    "market": entry.market.value,
                    "symbol": entry.symbol,
                    "raw_values": json.dumps(dict(entry.raw_values), sort_keys=True),
                    "availability_dates": json.dumps(
                        {name: day.isoformat() for name, day in entry.availability_dates},
                        sort_keys=True,
                    ),
                    "standardized_scores": json.dumps(
                        dict(entry.standardized_scores), sort_keys=True
                    ),
                    "composite_score": entry.composite_score,
                    "rank": entry.rank,
                    "selected": entry.selected,
                    "exclusion_reason": entry.exclusion_reason,
                }
            )
    return rows_to_csv(run_id=run_id, rows=rows, fields=_FACTOR_FIELDS)


def export_all_csvs(
    artifact: dict[str, Any],
    *,
    factor_snapshots: Sequence[FactorSnapshot] = (),
) -> dict[str, str]:
    """Render every CSV for a run artifact (SP 2.59).

    Args:
        artifact: The SP 2.58 results artifact.
        factor_snapshots: Optional SP 2.28 snapshots to include.

    Returns:
        A mapping of table name to CSV document for all artifact tables, the
        metrics, and the factor snapshots.
    """
    run_id = artifact["run"]["run_id"]
    result: dict[str, str] = {}
    for table in _FIELDS:
        result[table] = export_table_csv(artifact, table=table)
    result["metrics"] = export_metrics_csv(artifact)
    result["factor_snapshots"] = export_factor_snapshot_csv(factor_snapshots, run_id=run_id)
    return result
