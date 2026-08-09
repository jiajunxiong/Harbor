"""Trial budget and stopping rules (MVP 3 / SP 3.17).

Configures and enforces the parameter-search budget before tuning begins:
the maximum number of trials (预算), the deterministic random seed, the
tie-breaking rule (并列规则) and the early-stopping rule (提前停止规则). The
budget is a hard cap — :meth:`BudgetTracker.allocate` refuses to record a
trial once the budget is exhausted, so no experiment is ever silently
appended beyond the declared budget (预算耗尽后不得静默追加试验).

The tie-breaker makes trial selection deterministic when metrics are equal,
and :func:`select_best_trial` returns the winning trial that SP 3.21's
pre-registered selection rules build upon. Early stopping is a pure function
of the budget and the trailing trial metrics, so a stopped run is replayable
from the same seed and metric sequence.

Frozen Pydantic + frozen dataclasses, matching the SP 3.2 config and SP 3.1
domain conventions; pure core layer, depends only on the validation-config
and validation-domain types.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from harbor.core.validation_config import MetricDirection
from harbor.core.validation_domain import ParameterTrial

_TIE_TOLERANCE = 1e-12


class TrialBudgetError(ValueError):
    """Raised when a trial budget rule is violated (SP 3.17)."""


class BudgetExhaustedError(TrialBudgetError):
    """Raised when a trial would exceed the declared budget (SP 3.17)."""


class TieBreaker(StrEnum):
    """How equal-metric trials are ranked deterministically (SP 3.17)."""

    FIRST = "first"
    LAST = "last"
    TRIAL_ID = "trial_id"


class EarlyStopRule(StrEnum):
    """The supported early-stopping rules (SP 3.17)."""

    NONE = "none"
    NO_IMPROVEMENT = "no_improvement"
    TARGET_METRIC = "target_metric"


class TrialBudget(BaseModel):
    """The declared search budget and stopping rules (SP 3.17).

    ``max_trials`` is a hard cap; ``random_seed`` makes the search
    deterministic; ``tie_breaker`` fixes how equal metrics rank;
    ``early_stop`` (with its parameters) decides when a search may stop
    before the budget is fully spent. ``NO_IMPROVEMENT`` requires
    ``early_stop_trials``; ``TARGET_METRIC`` requires ``early_stop_target``.
    """

    model_config = ConfigDict(frozen=True)

    max_trials: int = Field(default=100, gt=0, description="最大试验数")
    random_seed: int = Field(default=42, description="确定性随机种子")
    tie_breaker: TieBreaker = TieBreaker.FIRST
    early_stop: EarlyStopRule = EarlyStopRule.NONE
    early_stop_trials: int | None = Field(default=None, gt=0, description="提前停止所需连续试验数")
    early_stop_target: float | None = None

    @model_validator(mode="after")
    def _validate_stopping(self) -> "TrialBudget":
        """Require the settings a chosen early-stop rule needs (SP 3.17)."""
        if self.early_stop is EarlyStopRule.NO_IMPROVEMENT and self.early_stop_trials is None:
            raise ValueError("the NO_IMPROVEMENT early stop rule requires early_stop_trials.")
        if self.early_stop is EarlyStopRule.TARGET_METRIC and self.early_stop_target is None:
            raise ValueError("the TARGET_METRIC early stop rule requires early_stop_target.")
        return self

    def readable(self) -> str:
        """Render the budget as one line."""
        stopping = self.early_stop.value
        if self.early_stop is EarlyStopRule.NO_IMPROVEMENT:
            stopping = f"no_improvement({self.early_stop_trials})"
        elif self.early_stop is EarlyStopRule.TARGET_METRIC:
            stopping = f"target_metric({self.early_stop_target})"
        return (
            f"trial budget {self.max_trials} seed {self.random_seed} "
            f"tie {self.tie_breaker.value} stop {stopping}"
        )


@dataclass(frozen=True)
class BudgetTracker:
    """Immutable counter enforcing the declared budget (SP 3.17).

    ``used`` counts the trials recorded so far; every allocation returns a
    NEW tracker so the budget history is replayable. Once ``used`` reaches
    ``max_trials`` no further trial can be allocated — a tuner calling
    :meth:`allocate` after the budget is exhausted gets
    :class:`BudgetExhaustedError` instead of silently exceeding the cap.
    """

    budget: TrialBudget
    used: int = 0

    def __post_init__(self) -> None:
        if self.used < 0:
            raise ValueError("used trials must be non-negative.")
        if self.used > self.budget.max_trials:
            raise ValueError("used trials cannot exceed max_trials.")

    @property
    def remaining(self) -> int:
        """Number of trials still allocatable."""
        return self.budget.max_trials - self.used

    @property
    def exhausted(self) -> bool:
        """Whether the budget is fully spent."""
        return self.used >= self.budget.max_trials

    def can_allocate(self) -> bool:
        """Whether at least one more trial can be allocated."""
        return self.used < self.budget.max_trials

    def allocate(self, *, count: int = 1) -> "BudgetTracker":
        """Record ``count`` trials, refusing to exceed the budget.

        Raises:
            ValueError: If ``count`` is not positive.
            BudgetExhaustedError: If the budget would be exceeded — a trial
                is never silently appended beyond the declared cap.
        """
        if count <= 0:
            raise ValueError("trial count must be positive.")
        if self.used + count > self.budget.max_trials:
            raise BudgetExhaustedError(
                f"trial budget exhausted: {self.used}/{self.budget.max_trials} "
                f"used; cannot allocate {count} more without silently "
                "exceeding the declared budget."
            )
        return replace(self, used=self.used + count)


@dataclass(frozen=True)
class StoppingDecision:
    """The outcome of an early-stopping evaluation (SP 3.17)."""

    should_stop: bool
    reason: str | None = None

    def readable(self) -> str:
        """Render the decision as one line."""
        if not self.should_stop:
            return "keep searching"
        return f"stop: {self.reason or 'early stop rule triggered'}"


def _better(metric: float, reference: float, direction: MetricDirection) -> bool:
    """Whether ``metric`` strictly improves on ``reference`` for ``direction``."""
    if direction is MetricDirection.HIGHER_BETTER:
        return metric > reference
    return metric < reference


def _is_tie(metric: float, reference: float) -> bool:
    """Whether two metrics are equal within tolerance."""
    return abs(metric - reference) <= _TIE_TOLERANCE


def evaluate_early_stop(
    budget: TrialBudget,
    metrics: Sequence[float],
    *,
    direction: MetricDirection = MetricDirection.HIGHER_BETTER,
) -> StoppingDecision:
    """Decide whether to stop early from the trailing trial metrics (SP 3.17).

    ``NONE`` never stops early. ``TARGET_METRIC`` stops once the latest
    metric reaches (or, for ``LOWER_BETTER``, falls to) the configured
    target. ``NO_IMPROVEMENT`` stops once the last ``early_stop_trials``
    metrics all failed to improve over the best prior metric.

    Raises:
        TrialBudgetError: If the budget's chosen rule is missing a required
            setting (defensive; the model validator already forbids this).
    """
    if budget.early_stop is EarlyStopRule.NONE:
        return StoppingDecision(should_stop=False)
    if not metrics:
        return StoppingDecision(should_stop=False)
    latest = metrics[-1]
    if budget.early_stop is EarlyStopRule.TARGET_METRIC:
        if budget.early_stop_target is None:
            raise TrialBudgetError("the TARGET_METRIC early stop rule requires early_stop_target.")
        target = budget.early_stop_target
        reached = (
            latest >= target if direction is MetricDirection.HIGHER_BETTER else latest <= target
        )
        if reached:
            return StoppingDecision(
                should_stop=True, reason=f"target metric {target} reached with {latest}."
            )
        return StoppingDecision(should_stop=False)
    window = budget.early_stop_trials
    if window is None:
        raise TrialBudgetError("the NO_IMPROVEMENT early stop rule requires early_stop_trials.")
    if len(metrics) <= window:
        return StoppingDecision(should_stop=False)
    prior = metrics[:-window]
    trailing = metrics[-window:]
    best_prior = _best(prior, direction)
    if any(_better(metric, best_prior, direction) for metric in trailing):
        return StoppingDecision(should_stop=False)
    return StoppingDecision(should_stop=True, reason=f"no improvement in the last {window} trials.")


def _best(metrics: Sequence[float], direction: MetricDirection) -> float:
    """Return the best metric for ``direction`` (metrics is non-empty)."""
    best = metrics[0]
    for metric in metrics[1:]:
        if _better(metric, best, direction):
            best = metric
    return best


def _tie_pick(
    tie_breaker: TieBreaker,
    current: ParameterTrial,
    candidate: ParameterTrial,
) -> ParameterTrial:
    """Return the preferred of two equal-metric trials for ``tie_breaker``."""
    if tie_breaker is TieBreaker.FIRST:
        return current
    if tie_breaker is TieBreaker.LAST:
        return candidate
    if candidate.trial_id < current.trial_id:
        return candidate
    return current


def select_best_trial(
    trials: Sequence[ParameterTrial],
    *,
    direction: MetricDirection = MetricDirection.HIGHER_BETTER,
    tie_breaker: TieBreaker = TieBreaker.FIRST,
) -> ParameterTrial:
    """Select the best trial by metric with deterministic tie-breaking.

    Failed trials (no metric) are excluded; with only failed trials the
    selection raises. Equal metrics are resolved by ``tie_breaker``: ``FIRST``
    keeps the earliest trial, ``LAST`` takes the latest, ``TRIAL_ID`` picks
    the lexicographically smallest id. This is the deterministic core that
    SP 3.21's pre-registered selection rules build on (never the test set).

    Raises:
        ValueError: If no trial is supplied or none carries a metric.
    """
    if not trials:
        raise ValueError("at least one trial is required for selection.")
    eligible = [trial for trial in trials if trial.metric is not None]
    if not eligible:
        raise ValueError("no trial carries a metric for selection.")
    best = eligible[0]
    best_metric: float | None = best.metric
    for candidate in eligible[1:]:
        candidate_metric = candidate.metric
        if candidate_metric is None:
            continue
        if best_metric is None:
            best = candidate
            best_metric = candidate_metric
            continue
        if _better(candidate_metric, best_metric, direction):
            best = candidate
            best_metric = candidate_metric
        elif _is_tie(candidate_metric, best_metric):
            best = _tie_pick(tie_breaker, best, candidate)
    return best
