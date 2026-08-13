"""Tests for the stability-adjudication rules (MVP 3 / SP 3.58).

Covers the pre-registered rule config (version / source / per-dimension
thresholds / fingerprint), the derived robustness signals (validation of every
ratio, spread, count and flag), the five-dimension adjudication
(折叠离散度 / 参数邻域 / 环境分段 / 压力损失 / 覆盖门槛), the aggregation priority
(any FAIL → NOT_QUALIFIED, else any INSUFFICIENT → INCONCLUSIVE, else
QUALIFIED), the auditable conclusion invariants and the re-derivable
fingerprints.
"""

import hashlib
import json
import unittest

from harbor.core.backtest_domain import Market
from harbor.core.stability_rule import (
    StabilityAssessment,
    StabilityConclusion,
    StabilityDimension,
    StabilityRuleConfig,
    StabilityRuleError,
    StabilitySignals,
    StabilityVerdict,
    adjudicate_stability,
    build_stability_rule,
    default_stability_rule,
    stability_fingerprint,
    stability_json,
    stability_rule_config_fingerprint,
    stability_rule_config_json,
)
from harbor.core.validation_domain import OOSConclusion


def _config(**overrides: float) -> StabilityRuleConfig:
    """Build a stability rule from the default thresholds plus overrides."""
    base = default_stability_rule()
    fields: dict[str, object] = {
        "max_fold_spread": base.max_fold_spread,
        "max_neighborhood_cliff_ratio": base.max_neighborhood_cliff_ratio,
        "max_neighborhood_infeasible_ratio": base.max_neighborhood_infeasible_ratio,
        "max_environment_insufficient_ratio": base.max_environment_insufficient_ratio,
        "max_stress_loss_pct": base.max_stress_loss_pct,
    }
    fields.update(overrides)
    return build_stability_rule(
        version=base.version,
        source=base.source,
        **fields,  # type: ignore[arg-type]
    )


def _signals(**overrides: object) -> StabilitySignals:
    """Build all-pass robustness signals plus overrides."""
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


class TestStabilityRuleConfig(unittest.TestCase):
    """The pre-registered stability rule and its validation (SP 3.58)."""

    def test_default_rule_values(self) -> None:
        rule = default_stability_rule()
        self.assertEqual(rule.version, "stability-default")
        self.assertEqual(rule.source, "pre-registered")
        self.assertEqual(rule.max_fold_spread, 0.20)
        self.assertEqual(rule.max_neighborhood_cliff_ratio, 0.50)
        self.assertEqual(rule.max_neighborhood_infeasible_ratio, 0.50)
        self.assertEqual(rule.max_environment_insufficient_ratio, 0.50)
        self.assertEqual(rule.max_stress_loss_pct, 10.0)
        self.assertEqual(len(rule.fingerprint), 64)

    def test_default_rule_fingerprint_stable(self) -> None:
        self.assertEqual(
            default_stability_rule().fingerprint,
            default_stability_rule().fingerprint,
        )

    def test_build_rule_assembles_and_fingerprints(self) -> None:
        rule = _config(max_fold_spread=0.15, max_stress_loss_pct=8.0)
        self.assertEqual(rule.max_fold_spread, 0.15)
        self.assertEqual(rule.max_stress_loss_pct, 8.0)
        self.assertEqual(rule.fingerprint, stability_rule_config_fingerprint(rule))

    def test_rule_readable(self) -> None:
        rule = default_stability_rule()
        text = rule.readable()
        self.assertIn("stability-default", text)
        self.assertIn("0.2", text)
        self.assertIn("10.0", text)

    def test_rule_rejects_empty_version(self) -> None:
        with self.assertRaises(StabilityRuleError):
            build_stability_rule(
                version="",
                max_fold_spread=0.2,
                max_neighborhood_cliff_ratio=0.5,
                max_neighborhood_infeasible_ratio=0.5,
                max_environment_insufficient_ratio=0.5,
                max_stress_loss_pct=10.0,
            )

    def test_rule_rejects_empty_source(self) -> None:
        with self.assertRaises(StabilityRuleError):
            build_stability_rule(
                version="v1",
                source="",
                max_fold_spread=0.2,
                max_neighborhood_cliff_ratio=0.5,
                max_neighborhood_infeasible_ratio=0.5,
                max_environment_insufficient_ratio=0.5,
                max_stress_loss_pct=10.0,
            )

    def test_rule_rejects_negative_fold_spread(self) -> None:
        with self.assertRaises(StabilityRuleError):
            build_stability_rule(
                version="v1",
                max_fold_spread=-0.1,
                max_neighborhood_cliff_ratio=0.5,
                max_neighborhood_infeasible_ratio=0.5,
                max_environment_insufficient_ratio=0.5,
                max_stress_loss_pct=10.0,
            )

    def test_rule_rejects_ratio_below_zero(self) -> None:
        with self.assertRaises(StabilityRuleError):
            _config(max_neighborhood_cliff_ratio=-0.1)

    def test_rule_rejects_ratio_above_one(self) -> None:
        with self.assertRaises(StabilityRuleError):
            _config(max_environment_insufficient_ratio=1.5)

    def test_rule_rejects_negative_stress_loss(self) -> None:
        with self.assertRaises(StabilityRuleError):
            _config(max_stress_loss_pct=-1.0)

    def test_rule_rejects_empty_fingerprint(self) -> None:
        with self.assertRaises(StabilityRuleError):
            StabilityRuleConfig(
                version="v1",
                source="pre-registered",
                max_fold_spread=0.2,
                max_neighborhood_cliff_ratio=0.5,
                max_neighborhood_infeasible_ratio=0.5,
                max_environment_insufficient_ratio=0.5,
                max_stress_loss_pct=10.0,
                fingerprint="",
            )


class TestStabilitySignals(unittest.TestCase):
    """The derived robustness signals and their validation (SP 3.58)."""

    def test_default_signals_are_valid(self) -> None:
        signals = _signals()
        self.assertEqual(signals.market, Market.HK)
        self.assertEqual(signals.fold_count, 4)
        self.assertEqual(signals.fold_spread, 0.10)

    def test_signals_rejects_empty_dataset_fingerprint(self) -> None:
        with self.assertRaises(StabilityRuleError):
            _signals(dataset_fingerprint="")

    def test_signals_rejects_empty_code_version(self) -> None:
        with self.assertRaises(StabilityRuleError):
            _signals(code_version="")

    def test_signals_rejects_negative_fold_spread(self) -> None:
        with self.assertRaises(StabilityRuleError):
            _signals(fold_spread=-0.1)

    def test_signals_rejects_negative_fold_count(self) -> None:
        with self.assertRaises(StabilityRuleError):
            _signals(fold_count=-1)

    def test_signals_rejects_negative_failure_count(self) -> None:
        with self.assertRaises(StabilityRuleError):
            _signals(fold_failure_count=-1)

    def test_signals_rejects_failures_exceeding_folds(self) -> None:
        with self.assertRaises(StabilityRuleError):
            _signals(fold_count=2, fold_failure_count=3)

    def test_signals_rejects_cliff_ratio_out_of_range(self) -> None:
        with self.assertRaises(StabilityRuleError):
            _signals(neighborhood_cliff_ratio=1.5)
        with self.assertRaises(StabilityRuleError):
            _signals(neighborhood_cliff_ratio=-0.1)

    def test_signals_rejects_infeasible_ratio_out_of_range(self) -> None:
        with self.assertRaises(StabilityRuleError):
            _signals(neighborhood_infeasible_ratio=1.2)

    def test_signals_rejects_environment_ratio_out_of_range(self) -> None:
        with self.assertRaises(StabilityRuleError):
            _signals(environment_insufficient_ratio=-0.2)

    def test_signals_rejects_negative_stress_loss(self) -> None:
        with self.assertRaises(StabilityRuleError):
            _signals(max_stress_loss_pct=-0.5)


class TestAdjudicateStability(unittest.TestCase):
    """The five-dimension adjudication and the aggregation priority (SP 3.58)."""

    def _conclusion(self, **overrides: object) -> StabilityConclusion:
        return adjudicate_stability(_signals(**overrides), config=_config())

    def test_all_pass_qualified(self) -> None:
        conclusion = self._conclusion()
        self.assertEqual(conclusion.conclusion, OOSConclusion.QUALIFIED)
        self.assertEqual(len(conclusion.assessments), 5)
        self.assertTrue(all(a.verdict is StabilityVerdict.PASS for a in conclusion.assessments))
        self.assertEqual(conclusion.reasons, ())

    def test_fold_spread_exceeds_downgrades(self) -> None:
        conclusion = self._conclusion(fold_spread=0.30)
        self.assertEqual(conclusion.conclusion, OOSConclusion.NOT_QUALIFIED)
        assessment = conclusion.assessments[0]
        self.assertEqual(assessment.dimension, StabilityDimension.FOLD_DISPERSION)
        self.assertEqual(assessment.verdict, StabilityVerdict.FAIL)
        self.assertIn("exceeds", assessment.detail)
        self.assertIn("exceeds", conclusion.reasons[0])

    def test_fold_failure_downgrades(self) -> None:
        conclusion = self._conclusion(fold_count=4, fold_failure_count=1)
        self.assertEqual(conclusion.conclusion, OOSConclusion.NOT_QUALIFIED)
        self.assertEqual(conclusion.assessments[0].verdict, StabilityVerdict.FAIL)
        self.assertIn("1 of 4", conclusion.assessments[0].detail)

    def test_no_folds_inconclusive(self) -> None:
        conclusion = self._conclusion(fold_count=0)
        self.assertEqual(conclusion.conclusion, OOSConclusion.INCONCLUSIVE)
        self.assertEqual(conclusion.assessments[0].verdict, StabilityVerdict.INSUFFICIENT)
        self.assertIn("no fold", conclusion.reasons[0])

    def test_spread_unavailable_inconclusive(self) -> None:
        conclusion = self._conclusion(fold_count=4, fold_spread=None)
        self.assertEqual(conclusion.conclusion, OOSConclusion.INCONCLUSIVE)
        self.assertEqual(conclusion.assessments[0].verdict, StabilityVerdict.INSUFFICIENT)

    def test_cliff_ratio_exceeds_downgrades(self) -> None:
        conclusion = self._conclusion(neighborhood_cliff_ratio=0.6)
        self.assertEqual(conclusion.conclusion, OOSConclusion.NOT_QUALIFIED)
        assessment = conclusion.assessments[1]
        self.assertEqual(assessment.dimension, StabilityDimension.PARAMETER_NEIGHBORHOOD)
        self.assertEqual(assessment.verdict, StabilityVerdict.FAIL)
        self.assertIn("cliff ratio", assessment.detail)

    def test_infeasible_ratio_exceeds_downgrades(self) -> None:
        conclusion = self._conclusion(neighborhood_infeasible_ratio=0.6)
        self.assertEqual(conclusion.conclusion, OOSConclusion.NOT_QUALIFIED)
        self.assertEqual(conclusion.assessments[1].verdict, StabilityVerdict.FAIL)
        self.assertIn("infeasible ratio", conclusion.assessments[1].detail)

    def test_neighborhood_unavailable_inconclusive(self) -> None:
        conclusion = self._conclusion(
            neighborhood_cliff_ratio=None, neighborhood_infeasible_ratio=None
        )
        self.assertEqual(conclusion.conclusion, OOSConclusion.INCONCLUSIVE)
        self.assertEqual(conclusion.assessments[1].verdict, StabilityVerdict.INSUFFICIENT)

    def test_environment_ratio_exceeds_downgrades(self) -> None:
        conclusion = self._conclusion(environment_insufficient_ratio=0.6)
        self.assertEqual(conclusion.conclusion, OOSConclusion.NOT_QUALIFIED)
        assessment = conclusion.assessments[2]
        self.assertEqual(assessment.dimension, StabilityDimension.ENVIRONMENT_SEGMENTATION)
        self.assertEqual(assessment.verdict, StabilityVerdict.FAIL)
        self.assertIn("environment insufficient", assessment.detail)

    def test_environment_unavailable_inconclusive(self) -> None:
        conclusion = self._conclusion(environment_insufficient_ratio=None)
        self.assertEqual(conclusion.conclusion, OOSConclusion.INCONCLUSIVE)
        self.assertEqual(conclusion.assessments[2].verdict, StabilityVerdict.INSUFFICIENT)

    def test_stress_loss_exceeds_downgrades(self) -> None:
        conclusion = self._conclusion(max_stress_loss_pct=12.0)
        self.assertEqual(conclusion.conclusion, OOSConclusion.NOT_QUALIFIED)
        assessment = conclusion.assessments[3]
        self.assertEqual(assessment.dimension, StabilityDimension.STRESS_LOSS)
        self.assertEqual(assessment.verdict, StabilityVerdict.FAIL)
        self.assertIn("worst stress loss", assessment.detail)

    def test_stress_unquantifiable_downgrades(self) -> None:
        conclusion = self._conclusion(stress_unquantifiable=True)
        self.assertEqual(conclusion.conclusion, OOSConclusion.NOT_QUALIFIED)
        self.assertEqual(conclusion.assessments[3].verdict, StabilityVerdict.FAIL)
        self.assertIn("could not be quantified", conclusion.assessments[3].detail)

    def test_stress_unavailable_inconclusive(self) -> None:
        conclusion = self._conclusion(max_stress_loss_pct=None)
        self.assertEqual(conclusion.conclusion, OOSConclusion.INCONCLUSIVE)
        self.assertEqual(conclusion.assessments[3].verdict, StabilityVerdict.INSUFFICIENT)

    def test_coverage_blocked_downgrades(self) -> None:
        conclusion = self._conclusion(coverage_blocked=True)
        self.assertEqual(conclusion.conclusion, OOSConclusion.NOT_QUALIFIED)
        assessment = conclusion.assessments[4]
        self.assertEqual(assessment.dimension, StabilityDimension.COVERAGE)
        self.assertEqual(assessment.verdict, StabilityVerdict.FAIL)
        self.assertIn("blocked", assessment.detail)

    def test_coverage_passes_without_gate_data(self) -> None:
        conclusion = self._conclusion(coverage_blocked=False)
        self.assertEqual(conclusion.assessments[4].verdict, StabilityVerdict.PASS)

    def test_fail_dominates_insufficient(self) -> None:
        conclusion = self._conclusion(coverage_blocked=True, fold_count=0)
        self.assertEqual(conclusion.conclusion, OOSConclusion.NOT_QUALIFIED)

    def test_all_insufficient_inconclusive(self) -> None:
        conclusion = self._conclusion(
            fold_count=0,
            neighborhood_cliff_ratio=None,
            neighborhood_infeasible_ratio=None,
            environment_insufficient_ratio=None,
            max_stress_loss_pct=None,
        )
        self.assertEqual(conclusion.conclusion, OOSConclusion.INCONCLUSIVE)
        self.assertEqual(
            [a.verdict for a in conclusion.assessments[:4]],
            [StabilityVerdict.INSUFFICIENT] * 4,
        )
        self.assertEqual(conclusion.assessments[4].verdict, StabilityVerdict.PASS)

    def test_assessment_order_is_fixed(self) -> None:
        conclusion = self._conclusion()
        self.assertEqual(
            [a.dimension for a in conclusion.assessments],
            [
                StabilityDimension.FOLD_DISPERSION,
                StabilityDimension.PARAMETER_NEIGHBORHOOD,
                StabilityDimension.ENVIRONMENT_SEGMENTATION,
                StabilityDimension.STRESS_LOSS,
                StabilityDimension.COVERAGE,
            ],
        )

    def test_reasons_capture_fail_and_insufficient_in_order(self) -> None:
        conclusion = self._conclusion(fold_failure_count=1, max_stress_loss_pct=None)
        self.assertEqual(conclusion.conclusion, OOSConclusion.NOT_QUALIFIED)
        self.assertEqual(len(conclusion.reasons), 2)
        self.assertIn("fold(s) failed", conclusion.reasons[0])
        self.assertIn("stress-loss evidence", conclusion.reasons[1])

    def test_conclusion_properties_mirror_signals(self) -> None:
        conclusion = self._conclusion(market=Market.US)
        self.assertEqual(conclusion.market, Market.US)
        self.assertEqual(conclusion.dataset_fingerprint, "dataset-fp")
        self.assertEqual(conclusion.code_version, "test")

    def test_conclusion_readable(self) -> None:
        conclusion = self._conclusion(coverage_blocked=True)
        text = conclusion.readable()
        self.assertIn("NOT_QUALIFIED", text)
        self.assertIn("COVERAGE=FAIL", text)

    def test_assessment_readable(self) -> None:
        conclusion = self._conclusion()
        self.assertIn("COVERAGE", conclusion.assessments[4].readable())

    def test_custom_rule_thresholds_apply(self) -> None:
        conclusion = adjudicate_stability(
            _signals(fold_spread=0.25), config=_config(max_fold_spread=0.30)
        )
        self.assertEqual(conclusion.conclusion, OOSConclusion.QUALIFIED)


class TestStabilityConclusionInvariants(unittest.TestCase):
    """Direct-construction consistency of the auditable conclusion (SP 3.58)."""

    def _base(self, **overrides: object) -> dict[str, object]:
        rule = _config()
        signals = _signals()
        assessments = (
            StabilityAssessment(
                dimension=StabilityDimension.FOLD_DISPERSION,
                verdict=StabilityVerdict.PASS,
                detail="fold return spread 10.00% within 20.00%",
            ),
            StabilityAssessment(
                dimension=StabilityDimension.PARAMETER_NEIGHBORHOOD,
                verdict=StabilityVerdict.PASS,
                detail="parameter neighborhood is stable (no excessive cliffs "
                "or infeasible regions)",
            ),
            StabilityAssessment(
                dimension=StabilityDimension.ENVIRONMENT_SEGMENTATION,
                verdict=StabilityVerdict.PASS,
                detail="environment insufficient ratio 10% within 50%",
            ),
            StabilityAssessment(
                dimension=StabilityDimension.STRESS_LOSS,
                verdict=StabilityVerdict.PASS,
                detail="worst stress loss 3.00% within 10.00%",
            ),
            StabilityAssessment(
                dimension=StabilityDimension.COVERAGE,
                verdict=StabilityVerdict.PASS,
                detail="the coverage gate passed",
            ),
        )
        fields: dict[str, object] = {
            "conclusion": OOSConclusion.QUALIFIED,
            "rule": rule,
            "signals": signals,
            "assessments": tuple(assessments),
            "reasons": (),
            "fingerprint": "fp",
        }
        fields.update(overrides)
        return fields

    def test_conclusion_rejects_empty_assessments(self) -> None:
        with self.assertRaises(StabilityRuleError):
            StabilityConclusion(**self._base(assessments=()))  # type: ignore[arg-type]

    def test_conclusion_rejects_inconsistent_conclusion(self) -> None:
        with self.assertRaises(StabilityRuleError):
            StabilityConclusion(**self._base(conclusion=OOSConclusion.NOT_QUALIFIED))  # type: ignore[arg-type]

    def test_conclusion_rejects_inconsistent_reasons(self) -> None:
        with self.assertRaises(StabilityRuleError):
            StabilityConclusion(**self._base(reasons=("unexpected",)))  # type: ignore[arg-type]

    def test_conclusion_rejects_empty_fingerprint(self) -> None:
        with self.assertRaises(StabilityRuleError):
            StabilityConclusion(**self._base(fingerprint=""))  # type: ignore[arg-type]


class TestStabilityFingerprints(unittest.TestCase):
    """The re-derivable, stable fingerprints of the rule and conclusion."""

    def test_config_fingerprint_rederivable(self) -> None:
        rule = _config()
        digest = hashlib.sha256(stability_rule_config_json(rule).encode("utf-8")).hexdigest()
        self.assertEqual(rule.fingerprint, digest)

    def test_conclusion_fingerprint_rederivable(self) -> None:
        conclusion = adjudicate_stability(_signals(), config=_config())
        digest = hashlib.sha256(stability_json(conclusion).encode("utf-8")).hexdigest()
        self.assertEqual(conclusion.fingerprint, digest)
        self.assertEqual(conclusion.fingerprint, stability_fingerprint(conclusion))

    def test_conclusion_json_excludes_derived_fingerprint(self) -> None:
        conclusion = adjudicate_stability(_signals(), config=_config())
        payload = json.loads(stability_json(conclusion))
        self.assertNotIn("fingerprint", payload)
        self.assertEqual(payload["conclusion"], "QUALIFIED")

    def test_conclusion_json_embeds_signals_and_rule(self) -> None:
        conclusion = adjudicate_stability(_signals(), config=_config())
        payload = json.loads(stability_json(conclusion))
        self.assertEqual(payload["signals"]["market"], "HK")
        self.assertEqual(payload["signals"]["fold_count"], 4)
        self.assertEqual(payload["rule"]["max_stress_loss_pct"], 10.0)
        self.assertEqual(len(payload["assessments"]), 5)

    def test_conclusion_fingerprint_sensitive_to_signals(self) -> None:
        qualified = adjudicate_stability(_signals(), config=_config())
        different = adjudicate_stability(_signals(fold_spread=0.05), config=_config())
        self.assertNotEqual(qualified.fingerprint, different.fingerprint)

    def test_conclusion_fingerprint_sensitive_to_rule(self) -> None:
        default = adjudicate_stability(_signals(), config=_config())
        tightened = adjudicate_stability(_signals(), config=_config(max_fold_spread=0.05))
        self.assertNotEqual(default.fingerprint, tightened.fingerprint)

    def test_default_rule_fingerprint_consistent_across_calls(self) -> None:
        self.assertEqual(
            default_stability_rule().fingerprint,
            _config().fingerprint,
        )


if __name__ == "__main__":
    unittest.main()
