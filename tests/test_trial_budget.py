"""Trial budget and stopping rule tests (MVP 3 / SP 3.17).

Verifies the declared search budget (max trials, random seed, tie-breaking,
early stopping) and its enforcement: a trial is never silently appended once
the budget is exhausted, early stopping is a deterministic function of the
trailing metrics, and best-trial selection resolves ties per the pre-registered
rule.
"""

import unittest
from datetime import date

from pydantic import ValidationError

from harbor.core.trial_budget import (
    BudgetExhaustedError,
    BudgetTracker,
    EarlyStopRule,
    StoppingDecision,
    TieBreaker,
    TrialBudget,
    evaluate_early_stop,
    select_best_trial,
)
from harbor.core.validation_config import MetricDirection
from harbor.core.validation_domain import ParameterTrial


def _trial(trial_id: str, metric: float | None) -> ParameterTrial:
    """Return a minimal valid parameter trial carrying ``metric``."""
    return ParameterTrial(
        trial_id=trial_id,
        parameters=(),
        dataset_fingerprint="f" * 64,
        train_start=date(2019, 1, 1),
        train_end=date(2020, 12, 31),
        validation_start=date(2021, 1, 1),
        validation_end=date(2022, 12, 31),
        seed=42,
        code_version="1.0.0",
        metric=metric,
        failed_reason=None if metric is not None else "failed",
    )


class TieBreakerTests(unittest.TestCase):
    """Verify the three tie-breaking rules."""

    def test_all_rules_are_declared(self) -> None:
        self.assertEqual(
            tuple(TieBreaker),
            (TieBreaker.FIRST, TieBreaker.LAST, TieBreaker.TRIAL_ID),
        )


class EarlyStopRuleTests(unittest.TestCase):
    """Verify the three early-stop rules."""

    def test_all_rules_are_declared(self) -> None:
        self.assertEqual(
            tuple(EarlyStopRule),
            (EarlyStopRule.NONE, EarlyStopRule.NO_IMPROVEMENT, EarlyStopRule.TARGET_METRIC),
        )


class TrialBudgetTests(unittest.TestCase):
    """Verify budget declaration and validation."""

    def test_defaults(self) -> None:
        budget = TrialBudget()
        self.assertEqual(budget.max_trials, 100)
        self.assertEqual(budget.random_seed, 42)
        self.assertEqual(budget.tie_breaker, TieBreaker.FIRST)
        self.assertEqual(budget.early_stop, EarlyStopRule.NONE)

    def test_custom_budget(self) -> None:
        budget = TrialBudget(
            max_trials=20,
            random_seed=7,
            tie_breaker=TieBreaker.TRIAL_ID,
            early_stop=EarlyStopRule.NO_IMPROVEMENT,
            early_stop_trials=5,
        )
        self.assertEqual(budget.max_trials, 20)
        self.assertEqual(budget.random_seed, 7)
        self.assertEqual(budget.tie_breaker, TieBreaker.TRIAL_ID)

    def test_max_trials_must_be_positive(self) -> None:
        with self.assertRaises(ValidationError):
            TrialBudget(max_trials=0)

    def test_no_improvement_requires_window(self) -> None:
        with self.assertRaisesRegex(ValidationError, "requires early_stop_trials"):
            TrialBudget(early_stop=EarlyStopRule.NO_IMPROVEMENT)

    def test_target_metric_requires_target(self) -> None:
        with self.assertRaisesRegex(ValidationError, "requires early_stop_target"):
            TrialBudget(early_stop=EarlyStopRule.TARGET_METRIC)

    def test_budget_is_frozen(self) -> None:
        budget = TrialBudget()
        with self.assertRaises(ValidationError):
            budget.max_trials = 5

    def test_readable(self) -> None:
        budget = TrialBudget(
            max_trials=20,
            random_seed=7,
            early_stop=EarlyStopRule.NO_IMPROVEMENT,
            early_stop_trials=5,
        )
        summary = budget.readable()
        self.assertIn("trial budget 20", summary)
        self.assertIn("seed 7", summary)
        self.assertIn("no_improvement(5)", summary)


class BudgetTrackerTests(unittest.TestCase):
    """Verify the budget is enforced and never silently exceeded."""

    def setUp(self) -> None:
        self.budget = TrialBudget(max_trials=3, random_seed=1)
        self.tracker = BudgetTracker(self.budget)

    def test_initial_state(self) -> None:
        self.assertEqual(self.tracker.used, 0)
        self.assertEqual(self.tracker.remaining, 3)
        self.assertFalse(self.tracker.exhausted)
        self.assertTrue(self.tracker.can_allocate())

    def test_allocate_decrements_remaining(self) -> None:
        after = self.tracker.allocate()
        self.assertEqual(after.used, 1)
        self.assertEqual(after.remaining, 2)

    def test_allocate_is_immutable(self) -> None:
        after = self.tracker.allocate()
        self.assertEqual(self.tracker.used, 0)
        self.assertEqual(after.used, 1)

    def test_allocate_multiple(self) -> None:
        after = self.tracker.allocate(count=2)
        self.assertEqual(after.used, 2)

    def test_exhausted_after_full_allocations(self) -> None:
        tracker = self.tracker.allocate().allocate().allocate()
        self.assertTrue(tracker.exhausted)
        self.assertFalse(tracker.can_allocate())
        self.assertEqual(tracker.remaining, 0)

    def test_allocate_beyond_budget_raises(self) -> None:
        tracker = self.tracker.allocate().allocate().allocate()
        with self.assertRaisesRegex(BudgetExhaustedError, "silently exceeding"):
            tracker.allocate()

    def test_allocate_count_beyond_remaining_raises(self) -> None:
        with self.assertRaises(BudgetExhaustedError):
            self.tracker.allocate(count=5)

    def test_allocate_non_positive_count_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be positive"):
            self.tracker.allocate(count=0)
        with self.assertRaisesRegex(ValueError, "must be positive"):
            self.tracker.allocate(count=-1)

    def test_constructor_rejects_used_beyond_max(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot exceed"):
            BudgetTracker(self.budget, used=4)

    def test_constructor_rejects_negative_used(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-negative"):
            BudgetTracker(self.budget, used=-1)


class EarlyStopTests(unittest.TestCase):
    """Verify early stopping is deterministic from the trailing metrics."""

    def test_none_never_stops(self) -> None:
        budget = TrialBudget(early_stop=EarlyStopRule.NONE)
        decision = evaluate_early_stop(budget, [0.1, 0.2, 0.3])
        self.assertFalse(decision.should_stop)

    def test_empty_metrics_never_stop(self) -> None:
        budget = TrialBudget(early_stop=EarlyStopRule.TARGET_METRIC, early_stop_target=0.5)
        decision = evaluate_early_stop(budget, [])
        self.assertFalse(decision.should_stop)

    def test_target_reached_higher_better_stops(self) -> None:
        budget = TrialBudget(early_stop=EarlyStopRule.TARGET_METRIC, early_stop_target=0.5)
        decision = evaluate_early_stop(
            budget, [0.3, 0.4, 0.5], direction=MetricDirection.HIGHER_BETTER
        )
        self.assertTrue(decision.should_stop)
        self.assertIn("target metric 0.5", decision.reason or "")

    def test_target_not_reached_continues(self) -> None:
        budget = TrialBudget(early_stop=EarlyStopRule.TARGET_METRIC, early_stop_target=0.9)
        decision = evaluate_early_stop(budget, [0.3, 0.4, 0.5])
        self.assertFalse(decision.should_stop)

    def test_target_reached_lower_better_stops(self) -> None:
        budget = TrialBudget(early_stop=EarlyStopRule.TARGET_METRIC, early_stop_target=0.1)
        decision = evaluate_early_stop(
            budget, [0.3, 0.2, 0.1], direction=MetricDirection.LOWER_BETTER
        )
        self.assertTrue(decision.should_stop)

    def test_target_not_reached_lower_better_continues(self) -> None:
        budget = TrialBudget(early_stop=EarlyStopRule.TARGET_METRIC, early_stop_target=0.0)
        decision = evaluate_early_stop(
            budget, [0.3, 0.2, 0.1], direction=MetricDirection.LOWER_BETTER
        )
        self.assertFalse(decision.should_stop)

    def test_no_improvement_needs_more_than_window(self) -> None:
        budget = TrialBudget(early_stop=EarlyStopRule.NO_IMPROVEMENT, early_stop_trials=3)
        self.assertFalse(evaluate_early_stop(budget, [0.1, 0.2, 0.3]).should_stop)

    def test_no_improvement_stops_when_stagnant(self) -> None:
        budget = TrialBudget(early_stop=EarlyStopRule.NO_IMPROVEMENT, early_stop_trials=3)
        # Best prior = 0.5; trailing 0.4, 0.4, 0.3 all fail to improve.
        decision = evaluate_early_stop(budget, [0.3, 0.5, 0.4, 0.4, 0.3])
        self.assertTrue(decision.should_stop)
        self.assertIn("no improvement in the last 3", decision.reason or "")

    def test_no_improvement_continues_when_improved(self) -> None:
        budget = TrialBudget(early_stop=EarlyStopRule.NO_IMPROVEMENT, early_stop_trials=3)
        # Trailing contains 0.6 which improves over best prior 0.5.
        decision = evaluate_early_stop(budget, [0.3, 0.5, 0.4, 0.4, 0.6])
        self.assertFalse(decision.should_stop)

    def test_no_improvement_lower_better_direction(self) -> None:
        budget = TrialBudget(early_stop=EarlyStopRule.NO_IMPROVEMENT, early_stop_trials=2)
        # Best prior (lowest) = 0.2; trailing 0.3, 0.4 do not improve (lower is better).
        decision = evaluate_early_stop(
            budget, [0.5, 0.2, 0.3, 0.4], direction=MetricDirection.LOWER_BETTER
        )
        self.assertTrue(decision.should_stop)

    def test_stopping_decision_readable(self) -> None:
        self.assertEqual(StoppingDecision(False).readable(), "keep searching")
        self.assertIn(
            "stop:",
            StoppingDecision(True, "target reached").readable(),
        )


class SelectionTests(unittest.TestCase):
    """Verify best-trial selection with deterministic tie-breaking."""

    def test_picks_highest_metric(self) -> None:
        best = select_best_trial([_trial("a", 0.3), _trial("b", 0.7), _trial("c", 0.5)])
        self.assertEqual(best.trial_id, "b")

    def test_picks_lowest_metric_lower_better(self) -> None:
        best = select_best_trial(
            [_trial("a", 0.3), _trial("b", 0.1), _trial("c", 0.5)],
            direction=MetricDirection.LOWER_BETTER,
        )
        self.assertEqual(best.trial_id, "b")

    def test_tie_broken_by_first(self) -> None:
        best = select_best_trial(
            [_trial("a", 0.5), _trial("b", 0.5), _trial("c", 0.5)],
            tie_breaker=TieBreaker.FIRST,
        )
        self.assertEqual(best.trial_id, "a")

    def test_tie_broken_by_last(self) -> None:
        best = select_best_trial(
            [_trial("a", 0.5), _trial("b", 0.5), _trial("c", 0.5)],
            tie_breaker=TieBreaker.LAST,
        )
        self.assertEqual(best.trial_id, "c")

    def test_tie_broken_by_trial_id(self) -> None:
        best = select_best_trial(
            [_trial("t2", 0.5), _trial("t10", 0.5), _trial("t1", 0.5)],
            tie_breaker=TieBreaker.TRIAL_ID,
        )
        self.assertEqual(best.trial_id, "t1")

    def test_failed_trials_are_excluded(self) -> None:
        best = select_best_trial(
            [_trial("failed-1", None), _trial("good", 0.4), _trial("failed-2", None)]
        )
        self.assertEqual(best.trial_id, "good")

    def test_failed_trial_does_not_block_better_metric(self) -> None:
        best = select_best_trial([_trial("a", 0.5), _trial("b", None), _trial("c", 0.7)])
        self.assertEqual(best.trial_id, "c")

    def test_empty_trials_raise(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one trial"):
            select_best_trial([])

    def test_all_failed_trials_raise(self) -> None:
        with self.assertRaisesRegex(ValueError, "no trial carries a metric"):
            select_best_trial([_trial("a", None), _trial("b", None)])


if __name__ == "__main__":
    unittest.main()
