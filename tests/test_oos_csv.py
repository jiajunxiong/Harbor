"""OOS CSV export tests (MVP 3 / SP 3.67).

Covers the stable-field CSV export of folds (折叠), trials (试验), environment
performance (环境表现), stress differences (压力差异), coverage scores (覆盖评分)
and conclusion evidence (结论证据) from the SP 3.66 JSON export document, with
the ``validation_run_id`` prefix and empty cells for missing values.
"""

import csv
import io
import unittest

from harbor.core.backtest_domain import Market
from harbor.core.coverage_scoring import (
    CoverageMeasurement,
    CoverageScore,
    MarketCoverage,
)
from harbor.core.environment_segmented import (
    EnvironmentDimension,
    EnvironmentSegmentedPerformance,
    EnvironmentSegmentPerformance,
)
from harbor.core.oos_csv import (
    OosCsvError,
    export_conclusion_evidence_csv,
    export_folds_csv,
    export_oos_csvs,
    export_oos_table,
    export_stress_differences_csv,
    export_trials_csv,
    rows_to_csv,
)
from harbor.core.validation_domain import ManifestComponent


def _export_dict() -> dict[str, object]:
    """A schema-faithful SP 3.66 OOS export document."""
    return {
        "schema_version": "1.0",
        "run": {"run_id": "validation-1"},
        "frozen_config": {"split": {}, "rolling": {}, "budget": {}, "tuning": {}},
        "dataset": {"fingerprint": "dataset-fp", "manifest": {}},
        "trial_log": [
            {
                "fold_index": index,
                "trial_id": "trial-1",
                "trial_fingerprint": "trial-fp",
            }
            for index in range(4)
        ],
        "fit_snapshots": [{"fold_index": index, "fit_fingerprint": "fit-fp"} for index in range(4)],
        "fold_results": [
            {
                "fold_index": index,
                "run_id": f"oos-run-{index}",
                "replay_fingerprint": "replay-fp",
                "report_artifact_fingerprint": "artifact-fp",
            }
            for index in range(4)
        ],
        "stress_results": {
            "version": "reg-evidence",
            "source": "pre-registered",
            "registrations": [
                {
                    "category": "cost",
                    "scenario_id": "cost-stress-2x",
                    "market": "HK",
                    "assumptions": ["rates scaled"],
                    "parameters": {"multiplier": 2.0},
                    "dataset_fingerprint": "dataset-fp",
                    "code_version": "test",
                    "baseline_difference": -1.5,
                    "difference_summary": None,
                }
            ],
        },
        "conclusion": {
            "overall": "QUALIFIED",
            "conclusion_fingerprint": "c" * 64,
            "test_set_version": "holdout-1-v2",
            "dataset_fingerprint": "dataset-fp",
            "code_version": "test",
        },
        "audit_events": [
            {"event": "DATA_READ", "stage": "TEST_LOCKED", "fold": index} for index in range(4)
        ],
    }


def _segments() -> EnvironmentSegmentedPerformance:
    """One sufficient bull-market segment (SP 3.50)."""
    return EnvironmentSegmentedPerformance(
        segments=(
            EnvironmentSegmentPerformance(
                dimension=EnvironmentDimension.TREND,
                regime_name="bull_market",
                day_count=100,
                sufficient=True,
                insufficient_reason=None,
                strategy_return=0.05,
                strategy_drawdown=-0.02,
                strategy_volatility=0.15,
                strategy_sharpe=1.0,
                benchmark_return=0.03,
                excess_return=0.02,
                turnover=0.5,
                costs=0.001,
                coverage_pct=95.0,
            ),
        ),
        definition_version="env-1",
        definition_fingerprint="env-fp",
        dataset_fingerprint="dataset-fp",
        code_version="test",
        min_samples=20,
        fingerprint="x" * 64,
    )


def _coverage() -> MarketCoverage:
    """One 95% price score (SP 3.9)."""
    return MarketCoverage(
        market=Market.HK,
        scores=(
            CoverageScore(
                market=Market.HK,
                item=ManifestComponent.PRICES,
                measurement=CoverageMeasurement(covered=95, denominator=100),
            ),
        ),
    )


def _rows(text: str) -> list[list[str]]:
    """Parse CSV text into rows."""
    return list(csv.reader(io.StringIO(text)))


class TestTableHeaders(unittest.TestCase):
    """Every table has the fixed, stable field order (字段稳定, SP 3.67)."""

    def test_folds_header(self) -> None:
        header = _rows(export_folds_csv(_export_dict()))[0]
        self.assertEqual(
            header,
            [
                "validation_run_id",
                "fold_index",
                "run_id",
                "replay_fingerprint",
                "report_artifact_fingerprint",
            ],
        )

    def test_trials_header(self) -> None:
        header = _rows(export_trials_csv(_export_dict()))[0]
        self.assertEqual(
            header,
            ["validation_run_id", "fold_index", "trial_id", "trial_fingerprint"],
        )

    def test_environment_header(self) -> None:
        header = _rows(
            export_oos_table(_export_dict(), table="environment", environment_segments=_segments())
        )[0]
        self.assertEqual(
            header,
            [
                "validation_run_id",
                "dimension",
                "regime_name",
                "day_count",
                "sufficient",
                "strategy_return",
                "benchmark_return",
                "excess_return",
                "turnover",
                "coverage_pct",
            ],
        )

    def test_stress_differences_header(self) -> None:
        header = _rows(export_stress_differences_csv(_export_dict()))[0]
        self.assertEqual(
            header,
            [
                "validation_run_id",
                "category",
                "scenario_id",
                "market",
                "baseline_difference",
                "difference_summary",
            ],
        )

    def test_coverage_header(self) -> None:
        header = _rows(export_oos_table(_export_dict(), table="coverage", coverage=_coverage()))[0]
        self.assertEqual(
            header,
            ["validation_run_id", "market", "item", "coverage_pct", "gap"],
        )

    def test_conclusion_evidence_header(self) -> None:
        header = _rows(export_conclusion_evidence_csv(_export_dict()))[0]
        self.assertEqual(
            header,
            [
                "validation_run_id",
                "overall",
                "test_set_version",
                "dataset_fingerprint",
                "code_version",
                "conclusion_fingerprint",
            ],
        )


class TestTableRows(unittest.TestCase):
    """The row content of each table (SP 3.67)."""

    def test_folds_rows(self) -> None:
        rows = _rows(export_folds_csv(_export_dict()))
        self.assertEqual(len(rows), 5)
        self.assertEqual(
            rows[1],
            ["validation-1", "0", "oos-run-0", "replay-fp", "artifact-fp"],
        )
        self.assertEqual(rows[4][1], "3")

    def test_trials_rows(self) -> None:
        rows = _rows(export_trials_csv(_export_dict()))
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[2], ["validation-1", "1", "trial-1", "trial-fp"])

    def test_stress_differences_rows(self) -> None:
        rows = _rows(export_stress_differences_csv(_export_dict()))
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            rows[1],
            ["validation-1", "cost", "cost-stress-2x", "HK", "-1.5", ""],
        )

    def test_conclusion_evidence_rows(self) -> None:
        rows = _rows(export_conclusion_evidence_csv(_export_dict()))
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            rows[1],
            ["validation-1", "QUALIFIED", "holdout-1-v2", "dataset-fp", "test", "c" * 64],
        )

    def test_environment_rows_with_segments(self) -> None:
        rows = _rows(
            export_oos_table(_export_dict(), table="environment", environment_segments=_segments())
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            rows[1],
            [
                "validation-1",
                "trend",
                "bull_market",
                "100",
                "true",
                "0.05",
                "0.03",
                "0.02",
                "0.5",
                "95.0",
            ],
        )

    def test_environment_rows_without_segments_header_only(self) -> None:
        rows = _rows(export_oos_table(_export_dict(), table="environment"))
        self.assertEqual(len(rows), 1)

    def test_coverage_rows_with_coverage(self) -> None:
        rows = _rows(export_oos_table(_export_dict(), table="coverage", coverage=_coverage()))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1], ["validation-1", "HK", "prices", "95.0", ""])

    def test_coverage_rows_without_coverage_header_only(self) -> None:
        rows = _rows(export_oos_table(_export_dict(), table="coverage"))
        self.assertEqual(len(rows), 1)


class TestExportAllAndValidation(unittest.TestCase):
    """The all-tables export and error handling (SP 3.67)."""

    def test_export_oos_csvs_has_all_six_tables(self) -> None:
        export = export_oos_csvs(
            _export_dict(),
            environment_segments=_segments(),
            coverage=_coverage(),
        )
        self.assertEqual(
            set(export),
            {
                "folds",
                "trials",
                "environment",
                "stress_differences",
                "coverage",
                "conclusion_evidence",
            },
        )

    def test_unknown_table_raises(self) -> None:
        with self.assertRaises(OosCsvError):
            export_oos_table(_export_dict(), table="unknown")

    def test_thin_wrappers_match_table_export(self) -> None:
        export_dict = _export_dict()
        self.assertEqual(
            export_folds_csv(export_dict), export_oos_table(export_dict, table="folds")
        )
        self.assertEqual(
            export_trials_csv(export_dict), export_oos_table(export_dict, table="trials")
        )
        self.assertEqual(
            export_stress_differences_csv(export_dict),
            export_oos_table(export_dict, table="stress_differences"),
        )
        self.assertEqual(
            export_conclusion_evidence_csv(export_dict),
            export_oos_table(export_dict, table="conclusion_evidence"),
        )

    def test_deterministic_export(self) -> None:
        export_dict = _export_dict()
        first = export_oos_csvs(export_dict, environment_segments=_segments(), coverage=_coverage())
        second = export_oos_csvs(
            export_dict, environment_segments=_segments(), coverage=_coverage()
        )
        self.assertEqual(first, second)


class TestRowsToCsv(unittest.TestCase):
    """The low-level CSV rendering rules (SP 3.67)."""

    def test_missing_field_renders_empty(self) -> None:
        text = rows_to_csv(
            run_id="r",
            rows=({"a": 1}, {"a": 2, "b": 3}),
            fields=("a", "b"),
        )
        rows = _rows(text)
        self.assertEqual(rows[0], ["validation_run_id", "a", "b"])
        self.assertEqual(rows[1], ["r", "1", ""])
        self.assertEqual(rows[2], ["r", "2", "3"])

    def test_bool_and_none_cells(self) -> None:
        text = rows_to_csv(
            run_id="r",
            rows=({"flag": True, "none": None, "other": False},),
            fields=("flag", "none", "other"),
        )
        self.assertEqual(_rows(text)[1], ["r", "true", "", "false"])

    def test_run_id_written_into_every_row(self) -> None:
        text = rows_to_csv(
            run_id="validation-1",
            rows=({"a": 1}, {"a": 2}),
            fields=("a",),
        )
        for row in _rows(text)[1:]:
            self.assertEqual(row[0], "validation-1")


if __name__ == "__main__":
    unittest.main()
