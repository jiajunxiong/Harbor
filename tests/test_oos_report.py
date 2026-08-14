"""OOS HTML research report tests (MVP 3 / SP 3.68).

Covers the self-contained HTML report over the SP 3.66 OOS export — the
prominent research-only banner (显著展示研究性质), the split diagram (切分图), the
OOS net-value chart (OOS 净值), the fold dispersion (折叠离散度), the environment
/ stress performance (环境/压力表现), the coverage scores (覆盖评分), the
limitations (限制) and the conclusion (结论) with no return promise.
"""

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
from harbor.core.oos_dispersion import FoldDispersion, OosDispersionReport
from harbor.core.oos_report import OosReportError, build_report_data, render_oos_report
from harbor.core.validation_domain import ManifestComponent


def _export_dict() -> dict[str, object]:
    """A schema-faithful SP 3.66 OOS export document."""
    return {
        "schema_version": "1.0",
        "run": {"run_id": "validation-1"},
        "frozen_config": {
            "split": {
                "train_start": "2019-01-01",
                "train_end": "2021-12-31",
                "validation_start": "2022-01-01",
                "validation_end": "2022-12-31",
                "test_start": "2023-01-01",
                "test_end": "2026-12-30",
            },
            "rolling": {},
            "budget": {},
            "tuning": {},
        },
        "dataset": {"fingerprint": "dataset-fp", "manifest": {}},
        "trial_log": [],
        "fit_snapshots": [],
        "fold_results": [],
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


def _net_values() -> tuple[dict[str, object], ...]:
    """A small OOS net-value series for the chart."""
    return (
        {"date": "2023-01-01", "total_value": 1_000_000.0},
        {"date": "2024-01-01", "total_value": 1_050_000.0},
        {"date": "2025-01-01", "total_value": 1_020_000.0},
        {"date": "2026-12-30", "total_value": 1_100_000.0},
    )


def _dispersion() -> OosDispersionReport:
    """A fold-dispersion report with three executed and one failed fold."""
    return OosDispersionReport(
        folds=(
            FoldDispersion(
                fold_index=0,
                cumulative_return=0.10,
                max_drawdown=-0.05,
                turnover=0.5,
                coverage_pct=100.0,
                failure_reason=None,
            ),
            FoldDispersion(
                fold_index=1,
                cumulative_return=-0.05,
                max_drawdown=-0.10,
                turnover=0.6,
                coverage_pct=100.0,
                failure_reason=None,
            ),
            FoldDispersion(
                fold_index=2,
                cumulative_return=0.15,
                max_drawdown=-0.04,
                turnover=0.4,
                coverage_pct=100.0,
                failure_reason=None,
            ),
            FoldDispersion(
                fold_index=3,
                cumulative_return=None,
                max_drawdown=None,
                turnover=None,
                coverage_pct=None,
                failure_reason="access denied",
            ),
        ),
        dataset_fingerprint="dataset-fp",
        code_version="test",
        fingerprint="x" * 64,
    )


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


def _render(**overrides: object) -> str:
    """Render the full OOS report with overridable arguments."""
    fields: dict[str, object] = {
        "export_dict": _export_dict(),
        "net_values": _net_values(),
        "dispersion": _dispersion(),
        "environment_segments": _segments(),
        "coverage": _coverage(),
        "limitations": ("limited OOS horizon",),
    }
    fields.update(overrides)
    return render_oos_report(**fields)  # type: ignore[arg-type]


class TestDocumentStructure(unittest.TestCase):
    """The HTML document scaffold (SP 3.68)."""

    def test_document_scaffold(self) -> None:
        text = _render()
        self.assertIn("<!doctype html>", text)
        self.assertIn('<html lang="zh">', text)
        self.assertIn('<meta charset="utf-8">', text)
        self.assertIn("<title>", text)
        self.assertIn("</html>", text)

    def test_default_title(self) -> None:
        self.assertIn("<title>OOS validation report validation-1</title>", _render())

    def test_title_override(self) -> None:
        text = _render(title="My OOS report")
        self.assertIn("<title>My OOS report</title>", text)

    def test_research_banner_is_prominent(self) -> None:
        text = _render()
        # 显著展示研究性质: a bold .research banner at the top.
        self.assertIn('class="research"', text)
        self.assertIn("仅用于研究", text)
        self.assertIn("no promise of future returns", text)

    def test_audit_count_in_header(self) -> None:
        self.assertIn("审计事件 4", _render())


class TestReportSections(unittest.TestCase):
    """Each acceptance section is rendered (SP 3.68)."""

    def test_split_section(self) -> None:
        text = _render()
        self.assertIn('id="split"', text)
        self.assertIn("训练 train", text)
        self.assertIn("验证 validation", text)
        self.assertIn("测试 test (OOS)", text)
        self.assertIn("2019-01-01", text)
        self.assertIn("2026-12-30", text)

    def test_net_value_section(self) -> None:
        text = _render()
        self.assertIn('id="net-values"', text)
        self.assertIn("<svg", text)
        self.assertIn("2023-01-01", text)
        self.assertIn("2026-12-30", text)

    def test_net_value_section_omitted_without_series(self) -> None:
        self.assertNotIn('id="net-values"', _render(net_values=None))

    def test_dispersion_section(self) -> None:
        text = _render()
        self.assertIn('id="dispersion"', text)
        self.assertIn("收益离散度", text)
        self.assertIn("平均收益", text)
        self.assertIn("最差折叠", text)
        self.assertIn("access denied", text)

    def test_dispersion_section_omitted_without_report(self) -> None:
        self.assertNotIn('id="dispersion"', _render(dispersion=None))

    def test_stress_section(self) -> None:
        text = _render()
        self.assertIn('id="stress"', text)
        self.assertIn("cost-stress-2x", text)
        self.assertIn("-1.5", text)

    def test_stress_section_empty_registry(self) -> None:
        export = _export_dict()
        export["stress_results"] = {"registrations": ()}
        text = _render(export_dict=export)
        self.assertIn("无压力情景登记", text)

    def test_environment_section(self) -> None:
        text = _render()
        self.assertIn('id="environment"', text)
        self.assertIn("bull_market", text)
        self.assertIn("5.00%", text)  # strategy_return 0.05

    def test_environment_section_omitted_without_segments(self) -> None:
        self.assertNotIn('id="environment"', _render(environment_segments=None))

    def test_coverage_section(self) -> None:
        text = _render()
        self.assertIn('id="coverage"', text)
        self.assertIn("prices", text)
        self.assertIn("95.00", text)

    def test_coverage_section_omitted_without_coverage(self) -> None:
        self.assertNotIn('id="coverage"', _render(coverage=None))

    def test_limitations_section(self) -> None:
        text = _render()
        self.assertIn('id="limitations"', text)
        self.assertIn("limited OOS horizon", text)

    def test_limitations_empty_note(self) -> None:
        text = _render(limitations=())
        self.assertIn("无未解决限制", text)

    def test_conclusion_section(self) -> None:
        text = _render()
        self.assertIn('id="conclusion"', text)
        self.assertIn("QUALIFIED", text)
        self.assertIn("holdout-1-v2", text)
        self.assertIn("no promise of future returns", text)

    def test_section_order(self) -> None:
        text = _render()
        positions = [
            text.index(f'id="{section}"')
            for section in (
                "split",
                "net-values",
                "dispersion",
                "stress",
                "environment",
                "coverage",
                "limitations",
                "conclusion",
            )
        ]
        self.assertEqual(positions, sorted(positions))


class TestSafetyAndDeterminism(unittest.TestCase):
    """HTML escaping, embedded data and reproducibility (SP 3.68)."""

    def test_dynamic_text_is_escaped(self) -> None:
        text = _render(limitations=("<script>alert(1)</script>",))
        self.assertIn("&lt;script&gt;", text)
        self.assertNotIn("<script>alert(1)", text)

    def test_window_report_data_embedded(self) -> None:
        text = _render()
        self.assertIn("window.REPORT_DATA =", text)
        self.assertIn("validation-1", text)
        self.assertIn("2023-01-01", text)

    def test_chart_json_cannot_break_out(self) -> None:
        text = _render()
        # The embedded JSON must not contain a literal closing script tag.
        self.assertNotIn("</script>", text.split("window.REPORT_DATA")[1].split("</body>")[0])

    def test_deterministic_rendering(self) -> None:
        first = _render()
        second = _render()
        self.assertEqual(first, second)


class TestBuildReportData(unittest.TestCase):
    """The export-document validation (SP 3.68)."""

    def test_build_report_data_extracts_sections(self) -> None:
        data = build_report_data(_export_dict())
        self.assertEqual(data["run_id"], "validation-1")
        self.assertEqual(data["split"]["test_end"], "2026-12-30")
        self.assertEqual(data["conclusion"]["overall"], "QUALIFIED")
        self.assertEqual(data["audit_count"], 4)

    def test_build_report_data_rejects_non_export(self) -> None:
        with self.assertRaises(OosReportError):
            build_report_data({"run": {"run_id": "x"}})


if __name__ == "__main__":
    unittest.main()
