"""OOS JSON artifact export tests (MVP 3 / SP 3.66).

Covers the canonical JSON export of the frozen configuration (冻结配置), data
manifest (数据清单), trial log (试验日志), fit snapshots (拟合快照), fold results
(折叠结果), stress results (压力结果), conclusion (结论) and audit events (审计事
件), built from the SP 3.65 conclusion evidence chain.
"""

import json
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone

from harbor.core.backtest_domain import Market
from harbor.core.conclusion_evidence import (
    ConclusionEvidence,
    build_conclusion_evidence,
)
from harbor.core.coverage_scoring import (
    CoverageMeasurement,
    CoverageScore,
    MarketCoverage,
)
from harbor.core.oos_chain import FoldChain, OosChainIntegrity, oos_chain_fingerprint
from harbor.core.oos_conclusion import (
    OosStructuredConclusion,
    build_oos_conclusion,
)
from harbor.core.oos_export import (
    OosExportError,
    export_oos_to_dict,
    export_oos_to_json,
)
from harbor.core.performance_metrics import PerformanceMetrics
from harbor.core.stability_rule import (
    StabilitySignals,
    adjudicate_stability,
    default_stability_rule,
)
from harbor.core.stress_registry import (
    StressScenarioCategory,
    StressScenarioRegistry,
    build_scenario_registration,
    build_stress_registry,
)
from harbor.core.trial_budget import TrialBudget
from harbor.core.validation_domain import EvaluationSplit, ManifestComponent

_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _performance(**overrides: object) -> PerformanceMetrics:
    """SP 3.38 return/risk metrics over the OOS test interval."""
    fields: dict[str, object] = {
        "start_date": date(2023, 1, 1),
        "end_date": date(2026, 12, 30),
        "periods": 1000,
        "cumulative_return": 0.05,
        "annualized_return": 0.20,
        "annualized_volatility": 0.15,
        "max_drawdown": -0.05,
        "sharpe_ratio": 1.2,
        "calmar_ratio": 1.0,
        "downside_deviation": 0.08,
    }
    fields.update(overrides)
    return PerformanceMetrics(**fields)  # type: ignore[arg-type]


def _coverage(**overrides: object) -> MarketCoverage:
    """A full-price HK coverage report (SP 3.9)."""
    fields: dict[str, object] = {
        "market": Market.HK,
        "scores": (
            CoverageScore(
                market=Market.HK,
                item=ManifestComponent.PRICES,
                measurement=CoverageMeasurement(covered=100, denominator=100),
            ),
        ),
    }
    fields.update(overrides)
    return MarketCoverage(**fields)  # type: ignore[arg-type]


def _signals(**overrides: object) -> StabilitySignals:
    """All-pass robustness signals (SP 3.58)."""
    fields: dict[str, object] = {
        "market": Market.HK,
        "dataset_fingerprint": "dataset-fp",
        "code_version": "test",
        "fold_spread": 0.10,
        "fold_count": 4,
        "fold_failure_count": 0,
        "neighborhood_cliff_ratio": 0.10,
        "neighborhood_infeasible_ratio": 0.10,
        "environment_insufficient_ratio": 0.10,
        "max_stress_loss_pct": 3.0,
        "stress_unquantifiable": False,
        "coverage_blocked": False,
    }
    fields.update(overrides)
    return StabilitySignals(**fields)  # type: ignore[arg-type]


def _conclusion(**overrides: object) -> OosStructuredConclusion:
    """A clean structured conclusion (SP 3.64)."""
    fields: dict[str, object] = {
        "version": "conclusion-1.0",
        "source": "pre-registered",
        "market": Market.HK,
        "dataset_fingerprint": "dataset-fp",
        "code_version": "test",
        "performance": _performance(),
        "benchmark_return": 0.02,
        "excess_return": 0.03,
        "coverage": _coverage(),
        "stability": adjudicate_stability(_signals(), config=default_stability_rule()),
        "budget": TrialBudget(max_trials=3, random_seed=42),
        "unresolved_limitations": (),
    }
    fields.update(overrides)
    return build_oos_conclusion(**fields)  # type: ignore[arg-type]


def _split(**overrides: object) -> EvaluationSplit:
    """The SP 3.4 split whose test interval equals the OOS performance."""
    fields: dict[str, object] = {
        "train_start": date(2019, 1, 1),
        "train_end": date(2021, 12, 31),
        "validation_start": date(2022, 1, 1),
        "validation_end": date(2022, 12, 31),
        "test_start": date(2023, 1, 1),
        "test_end": date(2026, 12, 30),
    }
    fields.update(overrides)
    return EvaluationSplit(**fields)  # type: ignore[arg-type]


def _fold_chain(index: int) -> FoldChain:
    """One complete per-fold evidence chain (SP 3.36)."""
    return FoldChain(
        fold_index=index,
        dataset_fingerprint="dataset-fp",
        fit_fingerprint="fit-fp",
        trial_id="trial-1",
        trial_fingerprint="trial-fp",
        run_id=f"oos-run-{index}",
        replay_fingerprint="replay-fp",
        report_artifact_fingerprint="artifact-fp",
    )


def _chains(**overrides: object) -> OosChainIntegrity:
    """The SP 3.36 evidence-chain integrity over four folds."""
    dataset_fingerprint = overrides.get("dataset_fingerprint", "dataset-fp")
    fields: dict[str, object] = {
        "chains": tuple(
            replace(_fold_chain(index), dataset_fingerprint=dataset_fingerprint)
            for index in range(4)
        ),
        "dataset_fingerprint": dataset_fingerprint,
        "code_version": "test",
    }
    fields.update(overrides)
    integrity = OosChainIntegrity(**fields, fingerprint="unfingerprinted")  # type: ignore[arg-type]
    return replace(integrity, fingerprint=oos_chain_fingerprint(integrity))


def _registry(**overrides: object) -> StressScenarioRegistry:
    """One registered cost scenario (SP 3.59)."""
    fields: dict[str, object] = {
        "version": "reg-evidence",
        "source": "pre-registered",
        "registrations": (
            build_scenario_registration(
                category=StressScenarioCategory.COST,
                scenario_id="cost-stress-2x",
                market=Market.HK,
                assumptions=("rates scaled by the pre-registered multiplier",),
                parameters={"multiplier": 2.0},
                dataset_fingerprint="dataset-fp",
                code_version="test",
                baseline_difference=-1.5,
                difference_summary=None,
            ),
        ),
    }
    fields.update(overrides)
    return build_stress_registry(**fields)  # type: ignore[arg-type]


def _evidence(**overrides: object) -> ConclusionEvidence:
    """A fully-linked evidence chain (SP 3.65)."""
    fields: dict[str, object] = {
        "conclusion": _conclusion(),
        "test_set_version": "holdout-1-v2",
        "split": _split(),
        "chains": _chains(),
        "stress_registry": _registry(),
        "warnings": ("single-provider FX",),
    }
    fields.update(overrides)
    return build_conclusion_evidence(**fields)  # type: ignore[arg-type]


def _rolling_payload() -> dict[str, object]:
    """The frozen rolling-window config payload."""
    return {"mode": "EXPANDING", "step_days": 365, "retrain_frequency": "EVERY_FOLD"}


def _tuning_payload() -> dict[str, object]:
    """The frozen tuning config payload."""
    return {"primary_metric": "sharpe", "max_trials": 3}


def _manifest_payload() -> dict[str, object]:
    """The frozen data manifest payload."""
    return {"sources": {"PRICES": "yfinance"}, "cutoff": "2026-12-30"}


def _audit_events() -> tuple[dict[str, object], ...]:
    """The recorded audit events (审计事件)."""
    return (
        {"event": "DATA_READ", "stage": "TEST_LOCKED", "fold": 0},
        {"event": "DATA_READ", "stage": "TEST_LOCKED", "fold": 1},
        {"event": "DATA_READ", "stage": "TEST_LOCKED", "fold": 2},
        {"event": "DATA_READ", "stage": "TEST_LOCKED", "fold": 3},
    )


def _export(**overrides: object) -> dict[str, object]:
    """Export the full OOS state with overridable arguments."""
    fields: dict[str, object] = {
        "run_id": "validation-1",
        "evidence": _evidence(),
        "rolling": _rolling_payload(),
        "tuning": _tuning_payload(),
        "manifest": _manifest_payload(),
        "audit_events": _audit_events(),
    }
    fields.update(overrides)
    return export_oos_to_dict(**fields)  # type: ignore[arg-type]


class TestExportSections(unittest.TestCase):
    """Every acceptance section is exported (SP 3.66)."""

    def test_all_eight_sections_present(self) -> None:
        export = _export()
        self.assertEqual(
            set(export),
            {
                "schema_version",
                "run",
                "frozen_config",
                "dataset",
                "trial_log",
                "fit_snapshots",
                "fold_results",
                "stress_results",
                "conclusion",
                "audit_events",
            },
        )

    def test_schema_version_and_run_id(self) -> None:
        export = _export()
        self.assertEqual(export["schema_version"], "1.0")
        self.assertEqual(export["run"], {"run_id": "validation-1"})

    def test_frozen_config_split(self) -> None:
        split = _export()["frozen_config"]["split"]
        self.assertEqual(split["test_start"], "2023-01-01")
        self.assertEqual(split["test_end"], "2026-12-30")

    def test_frozen_config_rolling(self) -> None:
        rolling = _export()["frozen_config"]["rolling"]
        self.assertEqual(rolling["mode"], "EXPANDING")
        self.assertEqual(rolling["step_days"], 365)

    def test_frozen_config_budget(self) -> None:
        budget = _export()["frozen_config"]["budget"]
        self.assertEqual(budget["max_trials"], 3)
        self.assertEqual(budget["random_seed"], 42)
        self.assertEqual(budget["tie_breaker"], "first")

    def test_frozen_config_tuning(self) -> None:
        tuning = _export()["frozen_config"]["tuning"]
        self.assertEqual(tuning["primary_metric"], "sharpe")

    def test_dataset_fingerprint_and_manifest(self) -> None:
        dataset = _export()["dataset"]
        self.assertEqual(dataset["fingerprint"], "dataset-fp")
        self.assertEqual(dataset["manifest"]["cutoff"], "2026-12-30")

    def test_trial_log(self) -> None:
        trial_log = _export()["trial_log"]
        self.assertEqual(len(trial_log), 4)
        self.assertEqual(
            trial_log[0],
            {
                "fold_index": 0,
                "trial_id": "trial-1",
                "trial_fingerprint": "trial-fp",
            },
        )

    def test_fit_snapshots(self) -> None:
        snapshots = _export()["fit_snapshots"]
        self.assertEqual(len(snapshots), 4)
        self.assertEqual(
            snapshots[1],
            {"fold_index": 1, "fit_fingerprint": "fit-fp"},
        )

    def test_fold_results(self) -> None:
        folds = _export()["fold_results"]
        self.assertEqual(len(folds), 4)
        self.assertEqual(
            folds[3],
            {
                "fold_index": 3,
                "run_id": "oos-run-3",
                "replay_fingerprint": "replay-fp",
                "report_artifact_fingerprint": "artifact-fp",
            },
        )

    def test_stress_results(self) -> None:
        stress = _export()["stress_results"]
        self.assertEqual(stress["version"], "reg-evidence")
        self.assertEqual(len(stress["registrations"]), 1)
        self.assertEqual(stress["registrations"][0]["scenario_id"], "cost-stress-2x")
        self.assertEqual(stress["registrations"][0]["baseline_difference"], -1.5)

    def test_conclusion(self) -> None:
        conclusion = _export()["conclusion"]
        self.assertEqual(conclusion["overall"], "QUALIFIED")
        self.assertEqual(conclusion["test_set_version"], "holdout-1-v2")
        self.assertEqual(conclusion["dataset_fingerprint"], "dataset-fp")
        self.assertEqual(conclusion["code_version"], "test")
        self.assertEqual(len(conclusion["conclusion_fingerprint"]), 64)

    def test_audit_events(self) -> None:
        events = _export()["audit_events"]
        self.assertEqual(len(events), 4)
        self.assertEqual(events[0]["event"], "DATA_READ")
        self.assertEqual(events[0]["stage"], "TEST_LOCKED")


class TestExportValidation(unittest.TestCase):
    """The export rejects empty identifiers (SP 3.66)."""

    def test_rejects_empty_run_id(self) -> None:
        with self.assertRaises(OosExportError):
            _export(run_id="")

    def test_rejects_empty_schema_version(self) -> None:
        with self.assertRaises(OosExportError):
            _export(schema_version="")


class TestExportJson(unittest.TestCase):
    """The deterministic JSON serialization (SP 3.66)."""

    def test_json_is_stable_across_exports(self) -> None:
        kwargs: dict[str, object] = {
            "run_id": "validation-1",
            "evidence": _evidence(),
            "rolling": _rolling_payload(),
            "tuning": _tuning_payload(),
            "manifest": _manifest_payload(),
            "audit_events": _audit_events(),
        }
        first = export_oos_to_json(**kwargs)  # type: ignore[arg-type]
        second = export_oos_to_json(**kwargs)  # type: ignore[arg-type]
        self.assertEqual(first, second)

    def test_json_roundtrips_to_dict(self) -> None:
        kwargs: dict[str, object] = {
            "run_id": "validation-1",
            "evidence": _evidence(),
            "rolling": _rolling_payload(),
            "tuning": _tuning_payload(),
            "manifest": _manifest_payload(),
            "audit_events": _audit_events(),
        }
        text = export_oos_to_json(**kwargs)  # type: ignore[arg-type]
        self.assertEqual(json.loads(text), export_oos_to_dict(**kwargs))  # type: ignore[arg-type]

    def test_json_is_indented(self) -> None:
        text = export_oos_to_json(
            run_id="validation-1",
            evidence=_evidence(),
            rolling=_rolling_payload(),
            tuning=_tuning_payload(),
            manifest=_manifest_payload(),
            audit_events=_audit_events(),
        )
        self.assertIn("\n", text)


class TestExportConsistency(unittest.TestCase):
    """The export always matches the evidence chain it was built from (SP 3.66)."""

    def test_trial_log_matches_chains(self) -> None:
        evidence = _evidence()
        export = _export(evidence=evidence)
        self.assertEqual(
            [entry["trial_id"] for entry in export["trial_log"]],
            list(evidence.trial_ids),
        )

    def test_fold_results_match_run_ids(self) -> None:
        evidence = _evidence()
        export = _export(evidence=evidence)
        self.assertEqual(
            [entry["run_id"] for entry in export["fold_results"]],
            list(evidence.run_ids),
        )

    def test_conclusion_matches_evidence(self) -> None:
        evidence = _evidence()
        conclusion = _export(evidence=evidence)["conclusion"]
        self.assertEqual(conclusion["overall"], evidence.conclusion.overall.value)
        self.assertEqual(conclusion["conclusion_fingerprint"], evidence.conclusion.fingerprint)

    def test_dataset_matches_evidence(self) -> None:
        evidence = _evidence()
        dataset = _export(evidence=evidence)["dataset"]
        self.assertEqual(dataset["fingerprint"], evidence.dataset_fingerprint)


if __name__ == "__main__":
    unittest.main()
