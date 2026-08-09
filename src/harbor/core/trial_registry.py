"""Trial registration and fingerprinting (MVP 3 / SP 3.18).

Registers every parameter trial with the full audit record the acceptance
requires — the validated parameters, the train / validation boundaries, the
frozen dataset fingerprint (SP 3.7), the random seed, the code version and
the resulting metric or failure reason — and fingerprints it so the same
inputs replay to the same trial (SP 3.28).

:class:`TrialRegistry` is an immutable controller: ``register`` validates the
parameter set through the SP 3.15/3.16 space and constraint gates, enforces
the SP 3.17 trial budget (a trial beyond the cap raises
:class:`BudgetExhaustedError`, never silently appended), builds an SP 3.1
:class:`~harbor.core.validation_domain.ParameterTrial` and returns a NEW
registry plus the trial, so the registration history is replayable. The
recorded trial is exactly what SP 3.12's ``validation_trials`` table persists.

:func:`trial_fingerprint` hashes the trial's INPUTS (parameters, boundaries,
dataset fingerprint, seed, code version) and deliberately excludes the
``trial_id`` (execution identity) and the metric / failure reason (outputs),
so equal inputs always produce the same fingerprint.

Pure core layer; depends on the parameter-space, parameter-constraint,
trial-budget and validation-domain types, never on storage.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date

from harbor.core.backtest_domain import Market
from harbor.core.parameter_constraints import (
    ParameterConstraint,
    validate_parameter_set,
)
from harbor.core.parameter_space import ParameterSpace
from harbor.core.trial_budget import BudgetTracker, TrialBudget
from harbor.core.validation_domain import ParameterTrial


class TrialRegistrationError(ValueError):
    """Raised when a trial cannot be registered (SP 3.18)."""


def trial_fingerprint(trial: ParameterTrial) -> str:
    """Return a stable SHA-256 fingerprint of a trial's inputs (SP 3.18).

    Covers the validated parameters (key-sorted), the train / validation
    boundaries, the frozen dataset fingerprint, the random seed and the code
    version. ``trial_id`` is excluded (it identifies an execution, not the
    inputs) and the metric / failure reason are excluded (they are outputs),
    so two trials with equal inputs replay to the same fingerprint.
    """
    payload: dict[str, object] = {
        "parameters": {parameter.name: parameter.value for parameter in trial.parameters},
        "dataset_fingerprint": trial.dataset_fingerprint,
        "train_start": trial.train_start.isoformat(),
        "train_end": trial.train_end.isoformat(),
        "validation_start": trial.validation_start.isoformat(),
        "validation_end": trial.validation_end.isoformat(),
        "seed": trial.seed,
        "code_version": trial.code_version,
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class TrialRegistry:
    """Immutable registry that records parameter trials (SP 3.18).

    Carries the frozen dataset context (fingerprint, code version, seed, the
    train / validation boundaries), the parameter space and constraints
    (SP 3.15/3.16) and the trial budget (SP 3.17). Every ``register`` call
    validates the parameters, enforces the budget, records a
    :class:`ParameterTrial` and returns a new registry, so the trial history
    is replayable and never silently exceeds the declared budget.
    """

    space: ParameterSpace
    budget: TrialBudget
    dataset_fingerprint: str
    code_version: str
    market: Market
    train_start: date
    train_end: date
    validation_start: date
    validation_end: date
    seed: int
    constraints: tuple[ParameterConstraint, ...] = ()
    trial_prefix: str = "trial"
    trials: tuple[ParameterTrial, ...] = ()

    def __post_init__(self) -> None:
        if not self.dataset_fingerprint:
            raise TrialRegistrationError("dataset fingerprint must be non-empty.")
        if not self.code_version:
            raise TrialRegistrationError("code version must be non-empty.")
        if not self.trial_prefix:
            raise TrialRegistrationError("trial prefix must be non-empty.")
        if self.train_end >= self.validation_start:
            raise TrialRegistrationError("training must end strictly before validation starts.")
        if self.validation_start > self.validation_end:
            raise TrialRegistrationError("validation range must not be reversed.")

    @property
    def used(self) -> int:
        """Number of registered trials."""
        return len(self.trials)

    @property
    def remaining(self) -> int:
        """Number of trials still allocatable within the budget."""
        return self.budget.max_trials - len(self.trials)

    @property
    def exhausted(self) -> bool:
        """Whether the declared budget is fully spent."""
        return len(self.trials) >= self.budget.max_trials

    def register(
        self,
        parameters: Mapping[str, object],
        *,
        metric: float | None = None,
        failed_reason: str | None = None,
    ) -> tuple["TrialRegistry", ParameterTrial]:
        """Validate, budget-check and record one parameter trial (SP 3.18).

        The parameter set is validated through
        :func:`~harbor.core.parameter_constraints.validate_parameter_set`
        (SP 3.16), the budget is enforced via the SP 3.17 tracker, and the
        resulting :class:`ParameterTrial` records the frozen context plus the
        metric or failure reason. ``metric`` and ``failed_reason`` are
        mutually exclusive (a failed trial carries no metric), enforced by
        :class:`ParameterTrial` itself.

        Returns:
            A ``(new_registry, trial)`` pair — the original registry is
            unchanged.

        Raises:
            BudgetExhaustedError: If the declared budget is exhausted.
            UndeclaredParameterError / ParameterSpaceError / ...: From the
                SP 3.16 parameter-set validation.
        """
        validated = validate_parameter_set(
            self.space,
            parameters,
            market=self.market,
            constraints=self.constraints,
        )
        tracker = BudgetTracker(self.budget, used=len(self.trials))
        tracker.allocate()
        trial_id = f"{self.trial_prefix}-{len(self.trials) + 1}"
        trial = ParameterTrial(
            trial_id=trial_id,
            parameters=validated,
            dataset_fingerprint=self.dataset_fingerprint,
            train_start=self.train_start,
            train_end=self.train_end,
            validation_start=self.validation_start,
            validation_end=self.validation_end,
            seed=self.seed,
            code_version=self.code_version,
            metric=metric,
            failed_reason=failed_reason,
        )
        new_registry = replace(
            self,
            trials=self.trials + (trial,),
        )
        return new_registry, trial

    def readable(self) -> str:
        """Render the registry and its recorded trials as lines."""
        lines = [
            f"trial registry {self.used}/{self.budget.max_trials} "
            f"fingerprint {self.dataset_fingerprint[:12]} code {self.code_version}"
        ]
        for trial in self.trials:
            lines.append(f"  {trial.readable()}")
        return "\n".join(lines)


def build_trial_registry(
    *,
    space: ParameterSpace,
    budget: TrialBudget,
    dataset_fingerprint: str,
    code_version: str,
    market: Market,
    train_start: date,
    train_end: date,
    validation_start: date,
    validation_end: date,
    seed: int,
    constraints: Sequence[ParameterConstraint] = (),
    trial_prefix: str = "trial",
) -> TrialRegistry:
    """Assemble a trial registry over a frozen dataset context (SP 3.18)."""
    return TrialRegistry(
        space=space,
        budget=budget,
        dataset_fingerprint=dataset_fingerprint,
        code_version=code_version,
        market=market,
        train_start=train_start,
        train_end=train_end,
        validation_start=validation_start,
        validation_end=validation_end,
        seed=seed,
        constraints=tuple(constraints),
        trial_prefix=trial_prefix,
    )
