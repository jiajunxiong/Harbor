"""Candidate parameter selection rules (MVP 3 / SP 3.21).

Parameter selection for out-of-sample validation must follow PRE-REGISTERED
rules and can never be driven by test-set performance. This module provides
the selection gate over the registered trials (SP 3.18):

- :class:`SelectionRules` is the pre-registered rule set: ONE primary metric
  name (预注册的单一主指标), its direction, a deterministic tie breaker
  (SP 3.17), a minimum validation-sample count (最低样本数) and any risk
  constraints (风险约束). It is built from the SP 3.2 ``TuningConfig`` and the
  SP 3.17 ``TieBreaker``, so the same rules object is fixed before tuning.
- :class:`RiskConstraint` caps one risk metric (e.g. max drawdown); a
  candidate that violates it — or whose risk is unmeasured (``None``) — is
  excluded rather than silently selected.
- :func:`select_candidate` filters the trials: failed trials (no metric),
  trials without a validation result, trials whose recorded metric is not the
  pre-registered primary metric, trials below the minimum validation-sample
  count and trials violating a risk constraint are each recorded as excluded;
  the best eligible trial is then picked by the primary metric with the
  pre-registered direction and tie breaker (SP 3.17 ``select_best_trial``).
  The selection API only ever reads the validation primary metric recorded on
  each trial — no test-set metric exists in the interface, so selecting by
  test performance is structurally impossible.
- :class:`CandidateSelection` is the auditable, fingerprinted outcome: the
  rules applied, the winning trial (or none) and every exclusion with its
  reason, so equal inputs replay to an identical selection (SP 3.28).

Pure core layer: depends on the SP 3.2 tuning config, the SP 3.17 trial
budget types and the SP 3.1 trial domain, never on storage, services or CLI.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

from harbor.core.trial_budget import TieBreaker, select_best_trial
from harbor.core.validation_config import MetricDirection, TuningConfig
from harbor.core.validation_domain import ParameterTrial


class CandidateSelectionError(ValueError):
    """Raised when a selection rule or outcome is invalid (SP 3.21)."""


@dataclass(frozen=True)
class RiskConstraint:
    """One pre-registered risk constraint on a candidate (SP 3.21).

    ``metric`` names a risk outcome (e.g. ``"max_drawdown_pct"``) and
    ``maximum`` caps it; a value above the cap, or an unmeasured ``None``
    value, fails :meth:`check` so the candidate is excluded.
    """

    metric: str
    maximum: float
    description: str

    def __post_init__(self) -> None:
        if not self.metric:
            raise CandidateSelectionError("risk metric must be non-empty.")
        if not self.description:
            raise CandidateSelectionError("risk description must be non-empty.")

    def check(self, value: float | None) -> bool:
        """Return whether ``value`` satisfies the upper-bound constraint."""
        return value is not None and value <= self.maximum

    def readable(self) -> str:
        """Render the constraint as one line."""
        return f"{self.metric} <= {self.maximum} ({self.description})"


@dataclass(frozen=True)
class SelectionRules:
    """The pre-registered candidate-selection rules (SP 3.21).

    ``primary_metric`` is the single pre-registered metric every candidate is
    ranked on; ``direction`` says whether higher or lower is better;
    ``tie_breaker`` resolves equal metrics deterministically (SP 3.17);
    ``min_validation_samples`` is the minimum validation sample count a
    candidate must reach (最低样本数); ``risk_constraints`` cap the risk
    outcomes a candidate must satisfy (风险约束).
    """

    primary_metric: str
    direction: MetricDirection = MetricDirection.HIGHER_BETTER
    tie_breaker: TieBreaker = TieBreaker.FIRST
    min_validation_samples: int = 1
    risk_constraints: tuple[RiskConstraint, ...] = ()

    def __post_init__(self) -> None:
        if not self.primary_metric.strip():
            raise CandidateSelectionError("primary metric must be non-empty.")
        if self.min_validation_samples <= 0:
            raise CandidateSelectionError("min_validation_samples must be positive.")
        metrics = [constraint.metric for constraint in self.risk_constraints]
        if len(set(metrics)) != len(metrics):
            raise CandidateSelectionError("risk constraint metrics must be unique.")

    def readable(self) -> str:
        """Render the rules as one line."""
        constraints = ", ".join(constraint.metric for constraint in self.risk_constraints) or "none"
        return (
            f"selection by {self.primary_metric} {self.direction.value} "
            f"tie {self.tie_breaker.value} min-samples "
            f"{self.min_validation_samples} risk [{constraints}]"
        )


@dataclass(frozen=True)
class TrialValidationResult:
    """A trial's validation-period outcome used for selection (SP 3.21).

    ``metric_name`` names the metric recorded in the trial's ``metric`` so the
    selection can verify it is exactly the pre-registered primary metric;
    ``validation_samples`` is the validation sample count (最低样本数 gate);
    ``risk`` maps risk-metric names to their values (``None`` = unmeasured).
    """

    trial_id: str
    metric_name: str
    validation_samples: int
    risk: Mapping[str, float | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.trial_id:
            raise CandidateSelectionError("trial_id must be non-empty.")
        if not self.metric_name:
            raise CandidateSelectionError("metric_name must be non-empty.")
        if self.validation_samples <= 0:
            raise CandidateSelectionError("validation_samples must be positive.")

    def readable(self) -> str:
        """Render the validation result as one line."""
        return f"trial {self.trial_id} metric {self.metric_name} samples {self.validation_samples}"


@dataclass(frozen=True)
class ExcludedCandidate:
    """One trial excluded from selection with the reason (SP 3.21)."""

    trial_id: str
    reason: str

    def __post_init__(self) -> None:
        if not self.trial_id:
            raise CandidateSelectionError("trial_id must be non-empty.")
        if not self.reason:
            raise CandidateSelectionError("reason must be non-empty.")

    def readable(self) -> str:
        """Render the exclusion as one line."""
        return f"{self.trial_id}: {self.reason}"


@dataclass(frozen=True)
class CandidateSelection:
    """The auditable selection outcome over registered trials (SP 3.21).

    ``rules`` are the pre-registered rules applied; ``selected`` is the
    winning trial or ``None`` when every candidate was excluded; ``excluded``
    records every ineligible trial with its reason. ``fingerprint`` is the
    derived SHA-256 digest and is excluded from its own digest so it can be
    re-derived and verified (SP 3.28).
    """

    rules: SelectionRules
    selected: ParameterTrial | None
    excluded: tuple[ExcludedCandidate, ...]
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.fingerprint:
            raise CandidateSelectionError("selection fingerprint must be non-empty.")

    def readable(self) -> str:
        """Render the selection outcome as one line."""
        if self.selected is None:
            selected = "none"
        else:
            metric = "n/a" if self.selected.metric is None else f"{self.selected.metric:.4g}"
            selected = f"{self.selected.trial_id} metric {metric}"
        return (
            f"candidate selection by {self.rules.primary_metric}: "
            f"selected {selected}; excluded {len(self.excluded)}"
        )


def _first_violation(
    constraints: Sequence[RiskConstraint],
    risk: Mapping[str, float | None],
) -> str | None:
    """Return the first risk-constraint violation message, or ``None``."""
    for constraint in constraints:
        value = risk.get(constraint.metric)
        if not constraint.check(value):
            display = "unmeasured" if value is None else f"{value:.4g}"
            return f"risk constraint '{constraint.readable()}' violated (value {display})"
    return None


def select_candidate(
    trials: Sequence[ParameterTrial],
    *,
    rules: SelectionRules,
    results: Mapping[str, TrialValidationResult],
) -> CandidateSelection:
    """Select the best candidate trial under the pre-registered rules.

    Each trial is eligible only if it carries the pre-registered primary
    metric (never the test set), has a validation result, records exactly the
    pre-registered metric name, reaches the minimum validation-sample count
    and satisfies every risk constraint; every ineligible trial is recorded
    as excluded. The best eligible trial is picked by the primary metric with
    the pre-registered direction and tie breaker (SP 3.17), or ``None`` when
    nothing is eligible.
    """
    excluded: list[ExcludedCandidate] = []
    eligible: list[ParameterTrial] = []
    for trial in trials:
        if trial.metric is None:
            excluded.append(
                ExcludedCandidate(trial.trial_id, "failed trial carries no primary metric")
            )
            continue
        result = results.get(trial.trial_id)
        if result is None:
            excluded.append(ExcludedCandidate(trial.trial_id, "no validation result recorded"))
            continue
        if result.metric_name != rules.primary_metric:
            excluded.append(
                ExcludedCandidate(
                    trial.trial_id,
                    f"records metric {result.metric_name!r}, pre-registered "
                    f"primary metric is {rules.primary_metric!r}",
                )
            )
            continue
        if result.validation_samples < rules.min_validation_samples:
            excluded.append(
                ExcludedCandidate(
                    trial.trial_id,
                    f"validation samples {result.validation_samples} below the "
                    f"pre-registered minimum {rules.min_validation_samples}",
                )
            )
            continue
        violation = _first_violation(rules.risk_constraints, result.risk)
        if violation is not None:
            excluded.append(ExcludedCandidate(trial.trial_id, violation))
            continue
        eligible.append(trial)
    selected: ParameterTrial | None = None
    if eligible:
        selected = select_best_trial(
            eligible,
            direction=rules.direction,
            tie_breaker=rules.tie_breaker,
        )
    selection = CandidateSelection(
        rules=rules,
        selected=selected,
        excluded=tuple(excluded),
        fingerprint="unfingerprinted",
    )
    return replace(selection, fingerprint=selection_fingerprint(selection))


def rules_from_tuning(
    tuning: TuningConfig,
    *,
    tie_breaker: TieBreaker = TieBreaker.FIRST,
    risk_constraints: Sequence[RiskConstraint] = (),
) -> SelectionRules:
    """Build pre-registered selection rules from the SP 3.2 tuning config.

    The primary metric, direction and minimum validation-sample count are
    taken from the pre-registered ``TuningConfig``; the tie breaker and risk
    constraints default deterministically unless supplied.
    """
    return SelectionRules(
        primary_metric=tuning.primary_metric,
        direction=tuning.metric_direction,
        tie_breaker=tie_breaker,
        min_validation_samples=tuning.min_validation_days,
        risk_constraints=tuple(risk_constraints),
    )


def selection_json(selection: CandidateSelection) -> str:
    """Return a stable, key-sorted JSON serialization of a selection outcome.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style).
    """
    selected: dict[str, object] | None = None
    if selection.selected is not None:
        trial = selection.selected
        selected = {
            "trial_id": trial.trial_id,
            "metric": trial.metric,
            "parameters": {parameter.name: parameter.value for parameter in trial.parameters},
            "dataset_fingerprint": trial.dataset_fingerprint,
            "seed": trial.seed,
            "code_version": trial.code_version,
            "train_start": trial.train_start.isoformat(),
            "train_end": trial.train_end.isoformat(),
            "validation_start": trial.validation_start.isoformat(),
            "validation_end": trial.validation_end.isoformat(),
        }
    payload: dict[str, object] = {
        "rules": {
            "primary_metric": selection.rules.primary_metric,
            "direction": selection.rules.direction.value,
            "tie_breaker": selection.rules.tie_breaker.value,
            "min_validation_samples": selection.rules.min_validation_samples,
            "risk_constraints": [
                {
                    "metric": constraint.metric,
                    "maximum": constraint.maximum,
                    "description": constraint.description,
                }
                for constraint in selection.rules.risk_constraints
            ],
        },
        "selected": selected,
        "excluded": [
            {"trial_id": excluded.trial_id, "reason": excluded.reason}
            for excluded in selection.excluded
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def selection_fingerprint(selection: CandidateSelection) -> str:
    """Return the stable SHA-256 fingerprint of a selection outcome (SP 3.21).

    Identical selections always fingerprint identically; the digest excludes
    the derived fingerprint field so it can be re-derived and verified.
    """
    return hashlib.sha256(selection_json(selection).encode("utf-8")).hexdigest()
