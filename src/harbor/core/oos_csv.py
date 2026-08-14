"""OOS CSV export (MVP 3 / SP 3.67).

Exports the OOS validation state (the SP 3.66 JSON document) as CSV with
stable, fixed column order (字段稳定): folds (折叠), trials (试验), environment
performance (环境表现), stress differences (压力差异), coverage scores (覆盖评分)
and conclusion evidence (结论证据).

The folds / trials / stress-difference / conclusion rows are read from the SP
3.66 export document; the environment and coverage rows are built from the SP
3.50 environment segments and the SP 3.9 per-market coverage when supplied
(otherwise the table renders its header only). Every row is prefixed with the
validation run id and missing fields render as empty cells, matching the
SP 2.59 CSV conventions.

Pure core layer: depends only on the SP 3.66 export payload, the SP 3.50
environment segments and the SP 3.9 coverage; never touches storage, services
or CLI.
"""

import csv
import io
from collections.abc import Mapping, Sequence
from typing import Any

from harbor.core.coverage_scoring import MarketCoverage
from harbor.core.environment_segmented import EnvironmentSegmentedPerformance


class OosCsvError(ValueError):
    """Raised when an OOS CSV export is invalid (SP 3.67)."""


_FIELDS: dict[str, tuple[str, ...]] = {
    "folds": (
        "fold_index",
        "run_id",
        "replay_fingerprint",
        "report_artifact_fingerprint",
    ),
    "trials": ("fold_index", "trial_id", "trial_fingerprint"),
    "environment": (
        "dimension",
        "regime_name",
        "day_count",
        "sufficient",
        "strategy_return",
        "benchmark_return",
        "excess_return",
        "turnover",
        "coverage_pct",
    ),
    "stress_differences": (
        "category",
        "scenario_id",
        "market",
        "baseline_difference",
        "difference_summary",
    ),
    "coverage": ("market", "item", "coverage_pct", "gap"),
    "conclusion_evidence": (
        "overall",
        "test_set_version",
        "dataset_fingerprint",
        "code_version",
        "conclusion_fingerprint",
    ),
}


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
    """Render rows as CSV with a leading ``validation_run_id`` column (SP 3.67)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["validation_run_id", *fields])
    for row in rows:
        writer.writerow([run_id, *(_cell(row.get(field)) for field in fields)])
    return buffer.getvalue()


def _environment_rows(
    segments: EnvironmentSegmentedPerformance,
) -> list[dict[str, object]]:
    """One row per SP 3.50 environment segment."""
    return [
        {
            "dimension": segment.dimension.value,
            "regime_name": segment.regime_name,
            "day_count": segment.day_count,
            "sufficient": segment.sufficient,
            "strategy_return": segment.strategy_return,
            "benchmark_return": segment.benchmark_return,
            "excess_return": segment.excess_return,
            "turnover": segment.turnover,
            "coverage_pct": segment.coverage_pct,
        }
        for segment in segments.segments
    ]


def _coverage_rows(coverage: MarketCoverage) -> list[dict[str, object]]:
    """One row per SP 3.9 coverage score."""
    return [
        {
            "market": score.market.value,
            "item": score.item.value,
            "coverage_pct": score.coverage_pct,
            "gap": score.measurement.gap,
        }
        for score in coverage.scores
    ]


def export_oos_table(
    export_dict: Mapping[str, Any],
    *,
    table: str,
    environment_segments: EnvironmentSegmentedPerformance | None = None,
    coverage: MarketCoverage | None = None,
) -> str:
    """Render one OOS table as CSV with the ``validation_run_id`` prefix.

    Args:
        export_dict: The SP 3.66 OOS JSON export document.
        table: One of ``folds`` / ``trials`` / ``environment`` /
            ``stress_differences`` / ``coverage`` / ``conclusion_evidence``.
        environment_segments: The SP 3.50 segments (required for the
            ``environment`` table, else header-only).
        coverage: The SP 3.9 coverage (required for the ``coverage`` table,
            else header-only).

    Raises:
        OosCsvError: If ``table`` is not a known OOS table.
    """
    if table not in _FIELDS:
        raise OosCsvError(f"Unknown CSV table {table!r}; expected one of {sorted(_FIELDS)}.")
    run_id = export_dict["run"]["run_id"]
    if table == "folds":
        rows = export_dict["fold_results"]
    elif table == "trials":
        rows = export_dict["trial_log"]
    elif table == "environment":
        rows = _environment_rows(environment_segments) if environment_segments is not None else []
    elif table == "stress_differences":
        rows = export_dict["stress_results"]["registrations"]
    elif table == "coverage":
        rows = _coverage_rows(coverage) if coverage is not None else []
    else:  # conclusion_evidence
        rows = [export_dict["conclusion"]]
    return rows_to_csv(run_id=run_id, rows=rows, fields=_FIELDS[table])


def export_oos_csvs(
    export_dict: Mapping[str, Any],
    *,
    environment_segments: EnvironmentSegmentedPerformance | None = None,
    coverage: MarketCoverage | None = None,
) -> dict[str, str]:
    """Render every OOS table as CSV (SP 3.67)."""
    return {
        table: export_oos_table(
            export_dict,
            table=table,
            environment_segments=environment_segments,
            coverage=coverage,
        )
        for table in _FIELDS
    }


def export_folds_csv(export_dict: Mapping[str, Any]) -> str:
    """Render the folds (折叠) CSV."""
    return export_oos_table(export_dict, table="folds")


def export_trials_csv(export_dict: Mapping[str, Any]) -> str:
    """Render the trials (试验) CSV."""
    return export_oos_table(export_dict, table="trials")


def export_stress_differences_csv(export_dict: Mapping[str, Any]) -> str:
    """Render the stress differences (压力差异) CSV."""
    return export_oos_table(export_dict, table="stress_differences")


def export_conclusion_evidence_csv(export_dict: Mapping[str, Any]) -> str:
    """Render the conclusion evidence (结论证据) CSV."""
    return export_oos_table(export_dict, table="conclusion_evidence")


__all__: tuple[str, ...] = (
    "OosCsvError",
    "rows_to_csv",
    "export_oos_table",
    "export_oos_csvs",
    "export_folds_csv",
    "export_trials_csv",
    "export_stress_differences_csv",
    "export_conclusion_evidence_csv",
)
