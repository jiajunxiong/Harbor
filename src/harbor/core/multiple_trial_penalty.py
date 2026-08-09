"""Multiple-trials penalty (MVP 3 / SP 3.22).

Searching many parameter trials inflates the best metric through selection
bias, so an out-of-sample conclusion must be penalized for the number of
trials run. This module computes that penalty over the registered trials
(SP 3.18) and the selection context (SP 3.21):

- ``trial_count`` reports 试验数量 (every trial, including failed ones — they
  were searched and influence the researcher's choice).
- ``effective_df`` reports 有效自由度 as the number of DISTINCT trial-input
  fingerprints (SP 3.18 ``trial_fingerprint``): duplicate inputs — the same
  parameters, boundaries, dataset fingerprint, seed and code version — do not
  add independent freedom, so they collapse the effective degrees of freedom.
- ``selection_bias_warning`` reports 选择偏差告警: no warning for a single
  trial; a warning for several trials that names the count, the effective df
  and any duplicates, escalating under a large trial budget.
- ``downgrade`` lowers the conclusion grade 降低结论等级: +1 when the trial
  budget is large (预算较大) and +1 when the best-vs-runner-up metric gap is
  below the significance threshold (差异不显著), capped at 2 levels. SP 3.58
  will apply this downgrade to the pre-registered OOS conclusion.

``compute_trial_penalty`` is a pure, deterministic function of the trials, the
pre-registered :class:`PenaltyConfig` and the metric direction; the result
carries a derived SHA-256 fingerprint so equal inputs replay to an identical
penalty (SP 3.28). Pure core layer: depends on SP 3.18's trial fingerprint and
the SP 3.2 metric direction, never on storage, services or CLI code.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace

from harbor.core.trial_registry import trial_fingerprint
from harbor.core.validation_config import MetricDirection
from harbor.core.validation_domain import ParameterTrial


class MultipleTrialPenaltyError(ValueError):
    """Raised when a trial-penalty rule or outcome is invalid (SP 3.22)."""


@dataclass(frozen=True)
class PenaltyConfig:
    """The pre-registered multiple-trials penalty thresholds (SP 3.22).

    ``large_budget_threshold`` is the trial count at or above which the
    search counts as a large budget (预算较大); ``min_significant_gap`` is the
    minimum best-vs-runner-up metric gap for the difference to count as
    significant (差异不显著 is below it).
    """

    large_budget_threshold: int = 20
    min_significant_gap: float = 0.01

    def __post_init__(self) -> None:
        if self.large_budget_threshold <= 1:
            raise MultipleTrialPenaltyError("large_budget_threshold must be greater than 1.")
        if self.min_significant_gap < 0:
            raise MultipleTrialPenaltyError("min_significant_gap must be non-negative.")

    def readable(self) -> str:
        """Render the penalty thresholds as one line."""
        return (
            f"penalty large-budget-threshold {self.large_budget_threshold} "
            f"min-significant-gap {self.min_significant_gap}"
        )


@dataclass(frozen=True)
class MultipleTrialPenalty:
    """The selection-bias penalty over a set of trials (SP 3.22).

    ``trial_count`` is 试验数量 (all trials, failed included);
    ``effective_df`` is 有效自由度 (distinct input fingerprints); ``duplicates``
    is the number of trials that shared an input with an earlier trial.
    ``selection_bias_warning`` is the 选择偏差告警 message (``None`` for a
    single trial). ``downgrade`` is how many conclusion-grade levels to lower
    (0-2) with ``reasons``; ``best_metric``/``runner_up_metric`` are the two
    best metric values and ``metric_gap`` their absolute difference (set only
    when both exist). ``fingerprint`` is the derived SHA-256 digest.
    """

    trial_count: int
    effective_df: int
    duplicates: int
    downgrade: int
    selection_bias_warning: str | None
    reasons: tuple[str, ...]
    best_metric: float | None
    runner_up_metric: float | None
    metric_gap: float | None
    fingerprint: str

    def __post_init__(self) -> None:
        if self.trial_count < 0:
            raise MultipleTrialPenaltyError("trial_count must be non-negative.")
        if not 0 <= self.effective_df <= self.trial_count:
            raise MultipleTrialPenaltyError("effective_df must lie in [0, trial_count].")
        if self.duplicates != self.trial_count - self.effective_df:
            raise MultipleTrialPenaltyError("duplicates must equal trial_count - effective_df.")
        if not 0 <= self.downgrade <= 2:
            raise MultipleTrialPenaltyError("downgrade must be in [0, 2].")
        if not self.fingerprint:
            raise MultipleTrialPenaltyError("penalty fingerprint must be non-empty.")
        if (self.reasons == ()) != (self.downgrade == 0):
            raise MultipleTrialPenaltyError(
                "reasons must be present exactly when the grade is downgraded."
            )
        gap_present = self.metric_gap is not None
        both_metrics = self.best_metric is not None and self.runner_up_metric is not None
        if gap_present != both_metrics:
            raise MultipleTrialPenaltyError(
                "metric_gap must be set exactly when best and runner-up are set."
            )

    def readable(self) -> str:
        """Render the penalty as one line."""
        warning = (
            "no warning" if self.selection_bias_warning is None else self.selection_bias_warning
        )
        return (
            f"penalty trials {self.trial_count} effective_df {self.effective_df} "
            f"duplicates {self.duplicates} downgrade {self.downgrade}: {warning}"
        )


def compute_trial_penalty(
    trials: Sequence[ParameterTrial],
    *,
    config: PenaltyConfig = PenaltyConfig(),
    direction: MetricDirection = MetricDirection.HIGHER_BETTER,
) -> MultipleTrialPenalty:
    """Compute the multiple-trials penalty over the registered trials.

    Reports the trial count, the effective degrees of freedom (distinct
    SP 3.18 input fingerprints), any duplicates and a selection-bias warning,
    and lowers the conclusion grade (0-2 levels) when the trial budget is
    large or the best-vs-runner-up metric gap is below the significance
    threshold. Failed trials count toward the trial count (they were searched)
    but never toward the best / runner-up metrics.
    """
    trial_count = len(trials)
    effective_df = len({trial_fingerprint(trial) for trial in trials})
    duplicates = trial_count - effective_df
    metrics = sorted(
        (trial.metric for trial in trials if trial.metric is not None),
        reverse=direction is MetricDirection.HIGHER_BETTER,
    )
    best_metric = metrics[0] if metrics else None
    runner_up_metric = metrics[1] if len(metrics) > 1 else None
    metric_gap: float | None = None
    if best_metric is not None and runner_up_metric is not None:
        metric_gap = abs(best_metric - runner_up_metric)
    warning: str | None = None
    if trial_count == 0:
        warning = "no trials recorded"
    elif trial_count > 1:
        parts = [f"selection bias: best of {trial_count} trials (effective df {effective_df})"]
        if duplicates > 0:
            parts.append(f"after {duplicates} duplicate trials")
        parts.append("overstates the expected out-of-sample metric")
        if trial_count >= config.large_budget_threshold:
            parts.append("under a large trial budget")
        warning = " ".join(parts)
    downgrade = 0
    reasons: list[str] = []
    if trial_count > 1 and trial_count >= config.large_budget_threshold:
        downgrade += 1
        reasons.append(f"large trial budget: {trial_count} trials searched")
    if trial_count > 1 and metric_gap is not None and metric_gap < config.min_significant_gap:
        downgrade += 1
        reasons.append(
            f"best vs runner-up gap {metric_gap:.4g} below the significance "
            f"threshold {config.min_significant_gap}"
        )
    downgrade = min(downgrade, 2)
    penalty = MultipleTrialPenalty(
        trial_count=trial_count,
        effective_df=effective_df,
        duplicates=duplicates,
        downgrade=downgrade,
        selection_bias_warning=warning,
        reasons=tuple(reasons),
        best_metric=best_metric,
        runner_up_metric=runner_up_metric,
        metric_gap=metric_gap,
        fingerprint="unfingerprinted",
    )
    return replace(penalty, fingerprint=penalty_fingerprint(penalty))


def penalty_json(penalty: MultipleTrialPenalty) -> str:
    """Return a stable, key-sorted JSON serialization of a penalty record.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style).
    """
    payload: dict[str, object] = {
        "trial_count": penalty.trial_count,
        "effective_df": penalty.effective_df,
        "duplicates": penalty.duplicates,
        "downgrade": penalty.downgrade,
        "selection_bias_warning": penalty.selection_bias_warning,
        "reasons": list(penalty.reasons),
        "best_metric": penalty.best_metric,
        "runner_up_metric": penalty.runner_up_metric,
        "metric_gap": penalty.metric_gap,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def penalty_fingerprint(penalty: MultipleTrialPenalty) -> str:
    """Return the stable SHA-256 fingerprint of a penalty record (SP 3.22).

    Identical penalties always fingerprint identically; the digest excludes
    the derived fingerprint field so it can be re-derived and verified.
    """
    return hashlib.sha256(penalty_json(penalty).encode("utf-8")).hexdigest()
