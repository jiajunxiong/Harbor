"""Conclusion evidence chain tests (MVP 3 / SP 3.65).

Covers the evidence chain that links every OOS conclusion to its test-set
version (测试集版本), dataset fingerprint (数据集指纹), split (切分), the fold /
trial / MVP 2 run evidence chains (折叠 / 试验 / MVP 2 运行, SP 3.36), the
registered stress results (压力结果, SP 3.59) and the warnings (告警), with
cross-validation that every link points back to the conclusion's frozen inputs.
"""

import hashlib
import json
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone

from harbor.core.backtest_domain import Market
from harbor.core.conclusion_evidence import (
    ConclusionEvidence,
    ConclusionEvidenceError,
    build_conclusion_evidence,
    conclusion_evidence_fingerprint,
    conclusion_evidence_json,
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
_FINGERPRINT = "f" * 64


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
    """A fully-linked evidence chain with overridable arguments."""
    fields: dict[str, object] = {
        "conclusion": _conclusion(),
        "test_set_version": "holdout-1-v2",
        "split": _split(),
        "chains": _chains(),
        "stress_registry": _registry(),
        "warnings": (),
    }
    fields.update(overrides)
    return build_conclusion_evidence(**fields)  # type: ignore[arg-type]


class TestConclusionEvidence(unittest.TestCase):
    """Construction, cross-validation and accessors (SP 3.65)."""

    def test_build_assembles_and_fingerprints(self) -> None:
        evidence = _evidence()
        self.assertEqual(evidence.test_set_version, "holdout-1-v2")
        self.assertEqual(evidence.dataset_fingerprint, "dataset-fp")
        self.assertEqual(evidence.fold_count, 4)
        self.assertEqual(len(evidence.fingerprint), 64)

    def test_dataset_fingerprint_defaults_to_conclusion(self) -> None:
        evidence = _evidence()
        self.assertEqual(evidence.dataset_fingerprint, evidence.conclusion.dataset_fingerprint)

    def test_rejects_empty_test_set_version(self) -> None:
        with self.assertRaises(ConclusionEvidenceError):
            _evidence(test_set_version="")

    def test_rejects_empty_dataset_fingerprint(self) -> None:
        with self.assertRaises(ConclusionEvidenceError):
            _evidence(dataset_fingerprint="")

    def test_rejects_dataset_fingerprint_mismatch(self) -> None:
        with self.assertRaises(ConclusionEvidenceError):
            _evidence(dataset_fingerprint="other-fp")

    def test_rejects_chains_dataset_mismatch(self) -> None:
        with self.assertRaises(ConclusionEvidenceError):
            _evidence(chains=_chains(dataset_fingerprint="other-fp"))

    def test_rejects_chains_code_version_mismatch(self) -> None:
        with self.assertRaises(ConclusionEvidenceError):
            _evidence(chains=_chains(code_version="other"))

    def test_rejects_split_start_mismatch(self) -> None:
        with self.assertRaises(ConclusionEvidenceError):
            _evidence(split=_split(test_start=date(2023, 1, 2)))

    def test_rejects_split_end_mismatch(self) -> None:
        with self.assertRaises(ConclusionEvidenceError):
            _evidence(split=_split(test_end=date(2026, 12, 29)))

    def test_rejects_empty_warning_string(self) -> None:
        with self.assertRaises(ConclusionEvidenceError):
            _evidence(warnings=("",))

    def test_rejects_empty_fingerprint(self) -> None:
        evidence = _evidence()
        with self.assertRaises(ConclusionEvidenceError):
            ConclusionEvidence(
                conclusion=evidence.conclusion,
                test_set_version=evidence.test_set_version,
                dataset_fingerprint=evidence.dataset_fingerprint,
                split=evidence.split,
                chains=evidence.chains,
                stress_registry=evidence.stress_registry,
                warnings=evidence.warnings,
                fingerprint="",
            )

    def test_every_fold_chain_is_complete(self) -> None:
        evidence = _evidence()
        self.assertTrue(all(chain.complete for chain in evidence.chains))


class TestEvidenceAccessors(unittest.TestCase):
    """The fold / trial / run / warning accessors (SP 3.65)."""

    def test_trial_ids_in_fold_order(self) -> None:
        evidence = _evidence()
        self.assertEqual(evidence.trial_ids, ("trial-1",) * 4)

    def test_run_ids_in_fold_order(self) -> None:
        evidence = _evidence()
        self.assertEqual(evidence.run_ids, ("oos-run-0", "oos-run-1", "oos-run-2", "oos-run-3"))

    def test_warning_count(self) -> None:
        self.assertEqual(_evidence().warning_count, 0)
        self.assertEqual(_evidence(warnings=("w1", "w2")).warning_count, 2)

    def test_stress_scenario_count(self) -> None:
        evidence = _evidence()
        self.assertEqual(evidence.stress_scenario_count, 1)
        self.assertEqual(evidence.stress_scenario_count, evidence.stress_registry.count)

    def test_readable(self) -> None:
        evidence = _evidence(warnings=("single-provider FX",))
        text = evidence.readable()
        self.assertIn("QUALIFIED", text)
        self.assertIn("holdout-1-v2", text)
        self.assertIn("4 fold(s)", text)
        self.assertIn("4 trial(s)", text)
        self.assertIn("4 run(s)", text)
        self.assertIn("1 stress scenario(s)", text)
        self.assertIn("1 warning(s)", text)


class TestEvidenceFingerprints(unittest.TestCase):
    """The re-derivable, stable fingerprints of the evidence chain (SP 3.65)."""

    def test_fingerprint_rederivable(self) -> None:
        evidence = _evidence()
        digest = hashlib.sha256(conclusion_evidence_json(evidence).encode("utf-8")).hexdigest()
        self.assertEqual(evidence.fingerprint, digest)
        self.assertEqual(evidence.fingerprint, conclusion_evidence_fingerprint(evidence))

    def test_json_excludes_derived_fingerprint(self) -> None:
        payload = json.loads(conclusion_evidence_json(_evidence()))
        self.assertNotIn("fingerprint", payload)
        self.assertEqual(payload["test_set_version"], "holdout-1-v2")

    def test_json_embeds_all_links(self) -> None:
        evidence = _evidence(warnings=("single-provider FX",))
        payload = json.loads(conclusion_evidence_json(evidence))
        self.assertEqual(payload["conclusion_overall"], "QUALIFIED")
        self.assertEqual(payload["dataset_fingerprint"], "dataset-fp")
        self.assertEqual(payload["split"]["test_start"], "2023-01-01")
        self.assertEqual(payload["split"]["test_end"], "2026-12-30")
        self.assertEqual(payload["chains_code_version"], "test")
        self.assertTrue(payload["chains_fingerprint"])
        self.assertTrue(payload["stress_registry_fingerprint"])
        self.assertEqual(payload["warnings"], ["single-provider FX"])

    def test_fingerprint_sensitive_to_test_set_version(self) -> None:
        base = _evidence()
        different = _evidence(test_set_version="holdout-1-v3")
        self.assertNotEqual(base.fingerprint, different.fingerprint)

    def test_fingerprint_sensitive_to_warnings(self) -> None:
        base = _evidence()
        different = _evidence(warnings=("single-provider FX",))
        self.assertNotEqual(base.fingerprint, different.fingerprint)

    def test_fingerprint_sensitive_to_chains(self) -> None:
        base = _evidence()
        altered = _chains(
            chains=tuple(replace(_fold_chain(index), run_id=f"run-{index}") for index in range(4))
        )
        self.assertNotEqual(base.fingerprint, _evidence(chains=altered).fingerprint)

    def test_fingerprint_sensitive_to_conclusion(self) -> None:
        base = _evidence()
        different = _evidence(
            conclusion=_conclusion(unresolved_limitations=("limited OOS horizon",))
        )
        self.assertNotEqual(base.fingerprint, different.fingerprint)

    def test_fingerprint_stable_across_identical_builds(self) -> None:
        self.assertEqual(_evidence().fingerprint, _evidence().fingerprint)


if __name__ == "__main__":
    unittest.main()
