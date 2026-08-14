"""Out-of-sample conclusion model tests (MVP 3 / SP 3.64).

Covers the structured conclusion that aggregates performance (性能), risk
(风险), coverage (覆盖), stability (稳定性), the trial budget (试验预算) and the
unresolved limitations (未解决限制); the aggregate OOS conclusion derivation;
and the hard requirement that the conclusion contains NO return promise
(结论不含收益承诺).
"""

import dataclasses
import hashlib
import json
import math
import unittest
from datetime import date, datetime, timezone

from harbor.core.backtest_domain import Market
from harbor.core.coverage_scoring import (
    CoverageMeasurement,
    CoverageScore,
    MarketCoverage,
)
from harbor.core.oos_conclusion import (
    OosConclusionError,
    OosStructuredConclusion,
    build_oos_conclusion,
    no_return_promise_statement,
    oos_conclusion_fingerprint,
    oos_conclusion_json,
)
from harbor.core.performance_metrics import PerformanceMetrics
from harbor.core.stability_rule import (
    StabilityConclusion,
    StabilitySignals,
    adjudicate_stability,
    default_stability_rule,
)
from harbor.core.trial_budget import TrialBudget
from harbor.core.validation_domain import ManifestComponent, OOSConclusion

_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _performance(**overrides: object) -> PerformanceMetrics:
    """SP 3.38 return/risk metrics with overridable fields."""
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
    """A full-price HK coverage report (SP 3.9) with overridable fields."""
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
    """All-pass robustness signals plus overrides (SP 3.58)."""
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


def _stability(**overrides: object) -> StabilityConclusion:
    """A qualified stability conclusion (SP 3.58) with overridable arguments."""
    fields: dict[str, object] = {
        "signals": _signals(),
        "config": default_stability_rule(),
    }
    fields.update(overrides)
    return adjudicate_stability(**fields)  # type: ignore[arg-type]


def _budget(**overrides: object) -> TrialBudget:
    """A declared trial budget (SP 3.17) with overridable fields."""
    fields: dict[str, object] = {"max_trials": 3, "random_seed": 42}
    fields.update(overrides)
    return TrialBudget(**fields)  # type: ignore[arg-type]


def _conclusion(**overrides: object) -> OosStructuredConclusion:
    """A clean structured conclusion with overridable arguments."""
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
        "stability": _stability(),
        "budget": _budget(),
        "unresolved_limitations": (),
    }
    fields.update(overrides)
    return build_oos_conclusion(**fields)  # type: ignore[arg-type]


class TestOosStructuredConclusion(unittest.TestCase):
    """Construction, market consistency and validation (SP 3.64)."""

    def test_build_assembles_and_fingerprints(self) -> None:
        conclusion = _conclusion()
        self.assertEqual(conclusion.version, "conclusion-1.0")
        self.assertEqual(conclusion.source, "pre-registered")
        self.assertIs(conclusion.market, Market.HK)
        self.assertEqual(conclusion.dataset_fingerprint, "dataset-fp")
        self.assertEqual(conclusion.code_version, "test")
        self.assertEqual(len(conclusion.fingerprint), 64)

    def test_default_version_source(self) -> None:
        conclusion = _conclusion()
        self.assertEqual(conclusion.version, "conclusion-1.0")
        self.assertEqual(conclusion.source, "pre-registered")

    def test_rejects_empty_version(self) -> None:
        with self.assertRaises(OosConclusionError):
            _conclusion(version="")

    def test_rejects_empty_source(self) -> None:
        with self.assertRaises(OosConclusionError):
            _conclusion(source="")

    def test_rejects_empty_dataset_fingerprint(self) -> None:
        with self.assertRaises(OosConclusionError):
            _conclusion(dataset_fingerprint="")

    def test_rejects_empty_code_version(self) -> None:
        with self.assertRaises(OosConclusionError):
            _conclusion(code_version="")

    def test_rejects_coverage_market_mismatch(self) -> None:
        us_coverage = _coverage(
            market=Market.US,
            scores=(
                CoverageScore(
                    market=Market.US,
                    item=ManifestComponent.PRICES,
                    measurement=CoverageMeasurement(covered=100, denominator=100),
                ),
            ),
        )
        with self.assertRaises(OosConclusionError):
            _conclusion(coverage=us_coverage)

    def test_rejects_stability_market_mismatch(self) -> None:
        us_stability = _stability(signals=_signals(market=Market.US))
        with self.assertRaises(OosConclusionError):
            _conclusion(stability=us_stability)

    def test_rejects_non_finite_benchmark_return(self) -> None:
        with self.assertRaises(OosConclusionError):
            _conclusion(benchmark_return=math.nan)

    def test_rejects_non_finite_excess_return(self) -> None:
        with self.assertRaises(OosConclusionError):
            _conclusion(excess_return=math.inf)

    def test_rejects_empty_limitation_string(self) -> None:
        with self.assertRaises(OosConclusionError):
            _conclusion(unresolved_limitations=("",))

    def test_rejects_empty_fingerprint(self) -> None:
        conclusion = _conclusion()
        with self.assertRaises(OosConclusionError):
            OosStructuredConclusion(
                version=conclusion.version,
                source=conclusion.source,
                market=conclusion.market,
                dataset_fingerprint=conclusion.dataset_fingerprint,
                code_version=conclusion.code_version,
                performance=conclusion.performance,
                benchmark_return=conclusion.benchmark_return,
                excess_return=conclusion.excess_return,
                coverage=conclusion.coverage,
                stability=conclusion.stability,
                budget=conclusion.budget,
                unresolved_limitations=conclusion.unresolved_limitations,
                fingerprint="",
            )


class TestOverallAggregation(unittest.TestCase):
    """The aggregate SP 3.1 OOS conclusion (SP 3.64)."""

    def test_qualified_stability_no_limitations_qualified(self) -> None:
        self.assertIs(_conclusion().overall, OOSConclusion.QUALIFIED)

    def test_not_qualified_stability_dominates(self) -> None:
        conclusion = _conclusion(stability=_stability(signals=_signals(fold_spread=0.30)))
        self.assertIs(conclusion.overall, OOSConclusion.NOT_QUALIFIED)

    def test_inconclusive_stability_is_inconclusive(self) -> None:
        conclusion = _conclusion(stability=_stability(signals=_signals(fold_count=0)))
        self.assertIs(conclusion.overall, OOSConclusion.INCONCLUSIVE)

    def test_limitation_downgrades_qualified(self) -> None:
        conclusion = _conclusion(
            unresolved_limitations=("limited OOS horizon for regime segments",)
        )
        self.assertIs(conclusion.overall, OOSConclusion.INCONCLUSIVE)
        self.assertEqual(conclusion.limitation_count, 1)

    def test_multiple_limitations(self) -> None:
        conclusion = _conclusion(
            unresolved_limitations=(
                "limited OOS horizon",
                "single-provider FX",
            )
        )
        self.assertIs(conclusion.overall, OOSConclusion.INCONCLUSIVE)
        self.assertEqual(conclusion.limitation_count, 2)

    def test_not_qualified_dominates_limitations(self) -> None:
        conclusion = _conclusion(
            stability=_stability(signals=_signals(fold_spread=0.30)),
            unresolved_limitations=("limited OOS horizon",),
        )
        self.assertIs(conclusion.overall, OOSConclusion.NOT_QUALIFIED)

    def test_inconclusive_with_limitations(self) -> None:
        conclusion = _conclusion(
            stability=_stability(signals=_signals(fold_count=0)),
            unresolved_limitations=("limited OOS horizon",),
        )
        self.assertIs(conclusion.overall, OOSConclusion.INCONCLUSIVE)


class TestNoReturnPromise(unittest.TestCase):
    """The conclusion contains no return promise (结论不含收益承诺, SP 3.64)."""

    def test_no_return_promise_statement(self) -> None:
        text = no_return_promise_statement()
        self.assertIn("no projection", text)
        self.assertIn("promise of future returns", text)

    def test_readable_contains_no_return_promise(self) -> None:
        self.assertIn("no return promise", _conclusion().readable())

    def test_no_projected_return_field(self) -> None:
        field_names = {field.name for field in dataclasses.fields(OosStructuredConclusion)}
        for forbidden in (
            "projected_return",
            "expected_return",
            "forward_return",
            "future_return",
        ):
            self.assertNotIn(forbidden, field_names)

    def test_readable_contains_aggregates(self) -> None:
        conclusion = _conclusion()
        text = conclusion.readable()
        self.assertIn("QUALIFIED", text)
        self.assertIn("HK", text)
        self.assertIn("coverage 100.0%", text)
        self.assertIn("budget 3", text)
        self.assertIn("limitations 0", text)


class TestDerivedProperties(unittest.TestCase):
    """The coverage, budget and limitation accessors (SP 3.64)."""

    def test_overall_coverage_pct(self) -> None:
        conclusion = _conclusion()
        self.assertEqual(conclusion.overall_coverage_pct, 100.0)
        self.assertEqual(conclusion.overall_coverage_pct, conclusion.coverage.overall_pct)

    def test_max_trials(self) -> None:
        conclusion = _conclusion(budget=_budget(max_trials=5))
        self.assertEqual(conclusion.max_trials, 5)

    def test_limitation_count_property(self) -> None:
        self.assertEqual(_conclusion().limitation_count, 0)
        self.assertEqual(_conclusion(unresolved_limitations=("a", "b")).limitation_count, 2)


class TestOosConclusionFingerprints(unittest.TestCase):
    """The re-derivable, stable fingerprints of the conclusion (SP 3.64)."""

    def test_fingerprint_rederivable(self) -> None:
        conclusion = _conclusion()
        digest = hashlib.sha256(oos_conclusion_json(conclusion).encode("utf-8")).hexdigest()
        self.assertEqual(conclusion.fingerprint, digest)
        self.assertEqual(conclusion.fingerprint, oos_conclusion_fingerprint(conclusion))

    def test_json_excludes_derived_fingerprint(self) -> None:
        payload = json.loads(oos_conclusion_json(_conclusion()))
        self.assertNotIn("fingerprint", payload)
        self.assertEqual(payload["overall"], "QUALIFIED")
        self.assertEqual(payload["market"], "HK")

    def test_json_embeds_aggregates(self) -> None:
        conclusion = _conclusion(unresolved_limitations=("limited OOS horizon",))
        payload = json.loads(oos_conclusion_json(conclusion))
        self.assertEqual(payload["performance"]["cumulative_return"], 0.05)
        self.assertEqual(payload["coverage"]["overall_pct"], 100.0)
        self.assertEqual(payload["stability"]["conclusion"], "QUALIFIED")
        self.assertEqual(payload["budget"]["max_trials"], 3)
        self.assertEqual(payload["unresolved_limitations"], ["limited OOS horizon"])

    def test_fingerprint_sensitive_to_limitation(self) -> None:
        clean = _conclusion()
        limited = _conclusion(unresolved_limitations=("limited OOS horizon",))
        self.assertNotEqual(clean.fingerprint, limited.fingerprint)

    def test_fingerprint_sensitive_to_performance(self) -> None:
        base = _conclusion()
        different = _conclusion(performance=_performance(cumulative_return=0.08))
        self.assertNotEqual(base.fingerprint, different.fingerprint)

    def test_fingerprint_sensitive_to_budget(self) -> None:
        base = _conclusion()
        different = _conclusion(budget=_budget(max_trials=5))
        self.assertNotEqual(base.fingerprint, different.fingerprint)

    def test_fingerprint_stable_across_identical_builds(self) -> None:
        self.assertEqual(_conclusion().fingerprint, _conclusion().fingerprint)


if __name__ == "__main__":
    unittest.main()
