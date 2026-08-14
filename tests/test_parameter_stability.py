"""Parameter-stability tests (MVP 3 / SP 3.61, TEST-ONLY).

Consolidated regression suite over the parameter neighborhood (参数邻域敏感性,
SP 3.57) and the stability adjudication rules (稳定性判定规则, SP 3.58), covering
the four acceptance dimensions:

- 邻域边界 (neighborhood boundaries): the finite grid respects the declared
  bounds — a step that would cross ``[min, max]`` is dropped, so a selection
  near a boundary yields fewer neighbors; INTEGER neighbors stay integers (the
  SP 3.57 fix) and no evaluated value ever leaves the declared range.
- 预算约束 (budget constraints): the neighborhood is a pre-registered FINITE
  grid bounded by ``steps`` per side (never an unbounded re-search), its size is
  deterministic and it never exceeds ``2*steps`` neighbours per parameter — so
  the stability analysis cannot blow a trial budget.
- 二次选参拒绝 (no re-selection): even when neighbours improve the metric or the
  weight-sum constraint makes regions infeasible, the selected trial is never
  replaced and the report states "no re-selection".
- 高离散度导致的降级结论 (high dispersion -> degraded conclusion): the SP 3.58
  rule downgrades a high fold-return spread or an excessive neighborhood cliff
  ratio to ``NOT_QUALIFIED``, and the reason is auditable.
"""

import unittest
from collections import defaultdict
from datetime import date, datetime, timezone

from harbor.core.backtest_domain import Market
from harbor.core.parameter_constraints import (
    ConstraintKind,
    ParameterConstraint,
    constraint,
)
from harbor.core.parameter_neighborhood import (
    NeighborhoodSensitivityReport,
    build_neighborhood_config,
    compute_parameter_neighborhood,
    default_neighborhood_config,
    no_reselection_statement,
)
from harbor.core.parameter_space import (
    ParameterDomain,
    ParameterKind,
    ParameterSpace,
    build_parameter_space,
    declare_parameter,
)
from harbor.core.stability_rule import (
    StabilityDimension,
    StabilitySignals,
    StabilityVerdict,
    adjudicate_stability,
    default_stability_rule,
)
from harbor.core.validation_domain import (
    OOSConclusion,
    Parameter,
    ParameterTrial,
)

_FINGERPRINT = "f" * 64
_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _space() -> ParameterSpace:
    """The three-parameter stepped space (two weights + one window)."""
    return build_parameter_space(
        declare_parameter(
            name="cash_weight",
            kind=ParameterKind.FACTOR_WEIGHT,
            domain=ParameterDomain.CONTINUOUS,
            minimum=0.0,
            maximum=1.0,
            step=0.05,
            default=0.05,
            markets=(Market.HK, Market.US),
        ),
        declare_parameter(
            name="factor_weight",
            kind=ParameterKind.FACTOR_WEIGHT,
            domain=ParameterDomain.CONTINUOUS,
            minimum=0.0,
            maximum=1.0,
            step=0.05,
            default=0.95,
            markets=(Market.HK, Market.US),
        ),
        declare_parameter(
            name="lookback",
            kind=ParameterKind.WINDOW,
            domain=ParameterDomain.INTEGER,
            minimum=60,
            maximum=504,
            step=24,
            default=252,
            markets=(Market.HK, Market.US),
        ),
    )


def _trial(**overrides: object) -> ParameterTrial:
    """A selected parameter trial with the reference metric."""
    fields: dict[str, object] = {
        "trial_id": "trial-1",
        "parameters": (
            Parameter(name="cash_weight", value=0.05),
            Parameter(name="factor_weight", value=0.95),
            Parameter(name="lookback", value=252),
        ),
        "dataset_fingerprint": _FINGERPRINT,
        "train_start": date(2019, 1, 1),
        "train_end": date(2021, 12, 31),
        "validation_start": date(2022, 1, 1),
        "validation_end": date(2022, 12, 31),
        "seed": 42,
        "code_version": "1.0.0",
        "metric": 0.252,
    }
    fields.update(overrides)
    return ParameterTrial(**fields)  # type: ignore[arg-type]


def _evaluate(parameters: dict[str, object]) -> float:
    """Deterministic metric: cliffs below 228, plateau around 252, else up."""
    lookback = int(parameters["lookback"])
    if lookback <= 228:
        return 0.18
    if 240 <= lookback <= 264:
        return 0.252
    return 0.30


def _weight_sum_constraint() -> ParameterConstraint:
    """The cash + factor weights must sum to one."""
    return constraint(
        "weight-sum",
        ConstraintKind.SUM_TO_TARGET,
        "cash_weight",
        "factor_weight",
        target=1.0,
    )


def _report(**overrides: object) -> NeighborhoodSensitivityReport:
    """Compute the default neighborhood with overridable arguments."""
    fields: dict[str, object] = {
        "selected": _trial(),
        "config": default_neighborhood_config(),
        "space": _space(),
        "market": Market.US,
        "evaluate": _evaluate,
        "constraints": (),
    }
    fields.update(overrides)
    return compute_parameter_neighborhood(**fields)  # type: ignore[arg-type]


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


class NeighborhoodBoundaryTests(unittest.TestCase):
    """The finite grid respects the declared bounds (邻域边界, SP 3.61)."""

    def test_grid_clips_at_upper_bound(self) -> None:
        selected = _trial(
            parameters=(
                Parameter(name="cash_weight", value=0.05),
                Parameter(name="factor_weight", value=0.95),
                Parameter(name="lookback", value=504),
            )
        )
        report = _report(selected=selected)
        # The +step lookback neighbours (528/552) cross max 504 and are dropped.
        self.assertEqual(report.point_count, 8)
        lookback_points = [p for p in report.points if p.parameter_name == "lookback"]
        self.assertEqual({p.offset_steps for p in lookback_points}, {-1, -2})
        self.assertTrue(all(p.offset_steps < 0 for p in lookback_points))

    def test_grid_clips_at_lower_bound(self) -> None:
        selected = _trial(
            parameters=(
                Parameter(name="cash_weight", value=0.05),
                Parameter(name="factor_weight", value=0.95),
                Parameter(name="lookback", value=60),
            )
        )
        report = _report(selected=selected)
        # The -step lookback neighbours (36/12) cross min 60 and are dropped.
        self.assertEqual(report.point_count, 8)
        lookback_points = [p for p in report.points if p.parameter_name == "lookback"]
        self.assertEqual({p.offset_steps for p in lookback_points}, {1, 2})
        self.assertTrue(all(p.offset_steps > 0 for p in lookback_points))

    def test_no_neighbor_exceeds_declared_bounds(self) -> None:
        space = _space()
        for point in _report():
            for parameter in point.parameters:
                declared = space.require_declared(parameter.name)
                if declared.minimum is not None:
                    self.assertGreaterEqual(parameter.value, declared.minimum)
                if declared.maximum is not None:
                    self.assertLessEqual(parameter.value, declared.maximum)

    def test_integer_neighbors_stay_integers(self) -> None:
        report = _report()
        self.assertTrue(report.points)
        for point in report.points:
            for parameter in point.parameters:
                if parameter.name == "lookback":
                    self.assertIsInstance(parameter.value, int)


class NeighborhoodBudgetTests(unittest.TestCase):
    """The grid is a finite, bounded, deterministic grid (预算约束, SP 3.61)."""

    def test_grid_size_is_deterministic(self) -> None:
        self.assertEqual(_report().point_count, _report().point_count)
        self.assertEqual(_report().point_count, 10)

    def test_grid_is_bounded_by_steps(self) -> None:
        config = build_neighborhood_config(
            version="neighborhood-1",
            steps=1,
            plateau_tolerance=0.01,
            cliff_threshold=0.05,
        )
        self.assertEqual(_report(config=config).point_count, 6)
        self.assertEqual(_report().point_count, 10)

    def test_grid_never_exceeds_two_steps_per_parameter(self) -> None:
        report = _report()
        by_parameter: defaultdict[str, int] = defaultdict(int)
        for point in report.points:
            by_parameter[point.parameter_name] += 1
        self.assertEqual(set(by_parameter), {"cash_weight", "factor_weight", "lookback"})
        for name, count in by_parameter.items():
            self.assertLessEqual(count, 2 * default_neighborhood_config().steps, name)
        self.assertLessEqual(report.point_count, 3 * 2 * default_neighborhood_config().steps)


class NoReselectionTests(unittest.TestCase):
    """The neighborhood never re-selects parameters (二次选参拒绝, SP 3.61)."""

    def test_no_reselection_statement(self) -> None:
        self.assertIn("does not re-select", no_reselection_statement())

    def test_report_readable_asserts_no_reselection(self) -> None:
        self.assertIn("no re-selection", _report().readable())

    def test_improving_neighbors_do_not_reselect(self) -> None:
        report = _report()
        self.assertGreater(report.improvement_count, 0)
        self.assertEqual(report.trial_id, "trial-1")
        self.assertEqual(report.selected_metric, 0.252)
        self.assertEqual([p.value for p in report.selected_parameters], [0.05, 0.95, 252])

    def test_input_selected_trial_not_mutated(self) -> None:
        selected = _trial()
        _report(selected=selected)
        self.assertEqual(selected.trial_id, "trial-1")
        self.assertEqual(selected.metric, 0.252)

    def test_constraint_infeasible_regions_preserved_not_reselected(self) -> None:
        report = _report(constraints=(_weight_sum_constraint(),))
        # The six weight-sum neighbours are recorded as infeasible (never
        # silently dropped) and none of them replaces the selected trial.
        self.assertEqual(report.infeasible_count, 6)
        self.assertEqual(report.trial_id, "trial-1")


class StabilityConclusionIntegrationTests(unittest.TestCase):
    """High dispersion / cliffs degrade the SP 3.58 conclusion (SP 3.61)."""

    def test_low_dispersion_stable_neighborhood_qualified(self) -> None:
        conclusion = adjudicate_stability(_signals(), config=default_stability_rule())
        self.assertEqual(conclusion.conclusion, OOSConclusion.QUALIFIED)

    def test_high_fold_dispersion_downgrades_to_not_qualified(self) -> None:
        conclusion = adjudicate_stability(
            _signals(fold_spread=0.30), config=default_stability_rule()
        )
        self.assertEqual(conclusion.conclusion, OOSConclusion.NOT_QUALIFIED)
        dispersion = conclusion.assessments[0]
        self.assertEqual(dispersion.dimension, StabilityDimension.FOLD_DISPERSION)
        self.assertEqual(dispersion.verdict, StabilityVerdict.FAIL)
        self.assertIn("exceeds", dispersion.detail)

    def test_high_neighborhood_cliff_downgrades_to_not_qualified(self) -> None:
        conclusion = adjudicate_stability(
            _signals(neighborhood_cliff_ratio=0.60), config=default_stability_rule()
        )
        self.assertEqual(conclusion.conclusion, OOSConclusion.NOT_QUALIFIED)
        neighborhood = conclusion.assessments[1]
        self.assertEqual(neighborhood.dimension, StabilityDimension.PARAMETER_NEIGHBORHOOD)
        self.assertEqual(neighborhood.verdict, StabilityVerdict.FAIL)

    def test_neighborhood_cliff_ratio_feeds_conclusion(self) -> None:
        # A cliff-heavy neighborhood: every neighbor is a cliff (0.10 vs 0.252).
        report = _report(evaluate=lambda parameters: 0.10)
        self.assertEqual(report.cliff_count, report.point_count)
        signals = _signals(
            fold_spread=0.10,
            neighborhood_cliff_ratio=report.cliff_count / report.point_count,
        )
        conclusion = adjudicate_stability(signals, config=default_stability_rule())
        self.assertEqual(conclusion.conclusion, OOSConclusion.NOT_QUALIFIED)
        self.assertEqual(conclusion.assessments[1].verdict, StabilityVerdict.FAIL)

    def test_stable_neighborhood_keeps_conclusion_qualified(self) -> None:
        report = _report()
        signals = _signals(
            neighborhood_cliff_ratio=report.cliff_count / report.point_count,
            neighborhood_infeasible_ratio=report.infeasible_count / report.point_count,
        )
        conclusion = adjudicate_stability(signals, config=default_stability_rule())
        self.assertEqual(conclusion.conclusion, OOSConclusion.QUALIFIED)

    def test_high_dispersion_reason_is_auditable(self) -> None:
        conclusion = adjudicate_stability(
            _signals(fold_spread=0.30), config=default_stability_rule()
        )
        self.assertIn("fold return spread 30.00% exceeds 20.00%", conclusion.reasons[0])


if __name__ == "__main__":
    unittest.main()
