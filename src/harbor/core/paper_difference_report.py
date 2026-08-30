"""Difference comparison report (MVP 4 / SP 4.72 / 4.82).

Quantifies the paper-vs-assumption differences by market, rebalance date and
symbol into an exportable comparison table (差异对照报告, SP 4.72). The report
rows have a stable, fixed column order (字段稳定) and export as JSON or CSV
(SP 4.82) so a single market or rebalance cycle can never be hidden inside an
aggregate — every row keeps its attribution.

The JSON export uses the canonical serialization (sorted keys, compact
separators) matching the fingerprint pipeline, so exporting and re-importing
never changes the report's identity (SP 4.81).

Pure core logic: depends on the difference metrics report; never touches
storage or CLI code.
"""

import csv
import io
import json

from harbor.core.paper_difference_metrics import (
    DifferenceMetric,
    DifferenceMetricsReport,
)


class DifferenceReportError(ValueError):
    """Raised when a difference report cannot be exported (SP 4.72)."""


_DIFFERENCE_REPORT_FIELDS: tuple[str, ...] = (
    "paper_run_id",
    "market",
    "rebalance_date",
    "symbol",
    "kind",
    "assumed",
    "actual",
    "difference",
    "relative_difference",
)


def _metric_row(paper_run_id: str, metric: DifferenceMetric) -> dict[str, object]:
    """Map one metric to a report row (SP 4.72)."""
    return {
        "paper_run_id": paper_run_id,
        "market": metric.market.value,
        "rebalance_date": metric.rebalance_date.isoformat(),
        "symbol": metric.symbol,
        "kind": metric.kind.value,
        "assumed": metric.assumed,
        "actual": metric.actual,
        "difference": metric.difference,
        "relative_difference": metric.relative_difference,
    }


def difference_report_rows(report: DifferenceMetricsReport) -> list[dict[str, object]]:
    """Map the report to table rows in a stable column order (SP 4.72)."""
    return [_metric_row(report.paper_run_id, metric) for metric in report.metrics]


def difference_report_json(report: DifferenceMetricsReport) -> str:
    """Export the report as canonical JSON (SP 4.82)."""
    payload: dict[str, object] = {
        "paper_run_id": report.paper_run_id,
        "metrics": [
            {key: row[key] for key in _DIFFERENCE_REPORT_FIELDS[1:]}
            for row in difference_report_rows(report)
        ],
        "unmatched": [
            [market.value, symbol, day.isoformat()] for market, symbol, day in report.unmatched
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def difference_report_csv(report: DifferenceMetricsReport) -> str:
    """Export the report as CSV with a fixed header (SP 4.82)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(_DIFFERENCE_REPORT_FIELDS)
    for row in difference_report_rows(report):
        writer.writerow([_cell(row[field]) for field in _DIFFERENCE_REPORT_FIELDS])
    return buffer.getvalue()


def _cell(value: object) -> object:
    """Render a cell value for CSV (dates as ISO strings, None as empty)."""
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
