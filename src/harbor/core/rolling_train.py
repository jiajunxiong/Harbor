"""Rolling training orchestration (MVP 3 / SP 3.33).

For every fold of an SP 3.31 :class:`FoldSequence`, orchestrates the
per-fold training workflow: each fold fits ONLY on its own training interval
(每个折叠只在其训练区间拟合, SP 3.19) and selects its parameters under the
pre-registered rules (按预注册规则选择参数, SP 3.21).

The data-dependent steps are injected so the core layer stays pure:
- ``fit_factory(train_start, train_end) -> TrainingFit`` fits a persistable
  snapshot over a training interval; the orchestrator verifies the returned
  snapshot is confined to the fold's training window (never validation/test)
  and rejects a fit that leaks forward.
- ``evaluate(fold, parameters) -> float`` returns the validation metric for a
  candidate parameter set on a fold.

Each fold registers its candidate trials through the SP 3.18 trial registry,
which structurally binds every trial to the fold's train / validation
boundaries (a trial can never carry different boundaries), then selects the
best candidate with the SP 3.17/3.21 pre-registered rules. The whole run is an
immutable, fingerprinted value so the rolling training is replayable
(SP 3.46).

Pure core layer: depends only on the SP 3.18/3.19/3.21/3.31 modules and the
validation/backtest domain types, never on storage, services or CLI.
"""

import hashlib
import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date

from harbor.core.backtest_domain import Market
from harbor.core.candidate_selection import (
    CandidateSelection,
    TrialValidationResult,
    rules_from_tuning,
    select_candidate,
)
from harbor.core.parameter_constraints import ParameterConstraint
from harbor.core.parameter_space import ParameterSpace
from harbor.core.rolling_window import FoldSequence
from harbor.core.training_fit import (
    TrainingFit,
    TrainingFitError,
    require_fit_within_training,
)
from harbor.core.trial_budget import TrialBudget
from harbor.core.trial_registry import build_trial_registry, trial_fingerprint
from harbor.core.validation_config import TuningConfig
from harbor.core.validation_domain import EvaluationSplit, ParameterTrial, WalkForwardFold


class RollingTrainError(ValueError):
    """Raised when rolling training cannot be orchestrated (SP 3.33)."""


@dataclass(frozen=True)
class FoldTrainingResult:
    """One fold's fit, trials and selection (SP 3.33).

    ``fold`` is the SP 3.31 fold, ``fit`` the training-period snapshot fitted
    on exactly the fold's training interval (SP 3.19), ``trials`` the
    candidate trials registered against the fold's train / validation bounds
    (SP 3.18) and ``selection`` the pre-registered-rules selection (SP 3.21).
    """

    fold: WalkForwardFold
    fit: TrainingFit
    trials: tuple[ParameterTrial, ...]
    selection: CandidateSelection

    def __post_init__(self) -> None:
        if self.fit.dataset_fingerprint != self.fold.dataset_fingerprint:
            raise RollingTrainError(
                f"fold {self.fold.fold_index} fit dataset fingerprint does not match "
                "the fold's frozen dataset fingerprint."
            )
        split = EvaluationSplit(
            train_start=self.fold.train_start,
            train_end=self.fold.train_end,
            validation_start=self.fold.validation_start,
            validation_end=self.fold.validation_end,
            test_start=self.fold.test_start,
            test_end=self.fold.test_end,
        )
        try:
            require_fit_within_training(self.fit, split)
        except TrainingFitError as exc:
            raise RollingTrainError(
                f"fold {self.fold.fold_index} fit is not confined to its training interval: {exc}"
            ) from exc
        for trial in self.trials:
            if trial.dataset_fingerprint != self.fold.dataset_fingerprint:
                raise RollingTrainError(
                    f"fold {self.fold.fold_index} trial {trial.trial_id} dataset "
                    "fingerprint does not match the fold's frozen dataset fingerprint."
                )
            if (
                trial.train_start != self.fold.train_start
                or trial.train_end != self.fold.train_end
                or trial.validation_start != self.fold.validation_start
                or trial.validation_end != self.fold.validation_end
            ):
                raise RollingTrainError(
                    f"fold {self.fold.fold_index} trial {trial.trial_id} carries "
                    "boundaries outside the fold's train / validation interval."
                )
        if self.selection.selected is not None and not any(
            trial.trial_id == self.selection.selected.trial_id for trial in self.trials
        ):
            raise RollingTrainError(
                f"fold {self.fold.fold_index} selected trial "
                f"{self.selection.selected.trial_id} is not among the fold's trials."
            )

    def readable(self) -> str:
        """Render the fold's training outcome as one line."""
        selected = (
            self.selection.selected.trial_id if self.selection.selected is not None else "none"
        )
        return (
            f"fold {self.fold.fold_index} fit "
            f"{self.fit.fit_start.isoformat()}..{self.fit.fit_end.isoformat()} "
            f"trials {len(self.trials)} selected {selected}"
        )


@dataclass(frozen=True)
class RollingTrainRun:
    """The auditable rolling-training orchestration result (SP 3.33).

    One :class:`FoldTrainingResult` per fold, ordered by ``fold_index`` from
    0. ``market`` is the market the trials were registered for,
    ``dataset_fingerprint`` / ``code_version`` the frozen data context and
    ``fingerprint`` the derived SHA-256 digest of the whole run.
    """

    results: tuple[FoldTrainingResult, ...]
    market: Market
    dataset_fingerprint: str
    code_version: str
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.results:
            raise RollingTrainError("a rolling training run requires at least one fold.")
        if not self.dataset_fingerprint:
            raise RollingTrainError("dataset fingerprint must be non-empty.")
        if not self.code_version:
            raise RollingTrainError("code version must be non-empty.")
        for index, result in enumerate(self.results):
            if result.fold.fold_index != index:
                raise RollingTrainError(
                    f"result {index} must carry fold_index {index}, got {result.fold.fold_index}."
                )
            if result.fit.dataset_fingerprint != self.dataset_fingerprint:
                raise RollingTrainError(
                    f"fold {index} fit dataset fingerprint does not match the run."
                )
        if not self.fingerprint:
            raise RollingTrainError("rolling training run fingerprint must be non-empty.")

    def __len__(self) -> int:
        return len(self.results)

    def __iter__(self) -> Iterator[FoldTrainingResult]:
        return iter(self.results)

    def __getitem__(self, index: int) -> FoldTrainingResult:
        return self.results[index]

    @property
    def folds(self) -> tuple[WalkForwardFold, ...]:
        """The folds this run trained on, in fold order."""
        return tuple(result.fold for result in self.results)

    def selection_for(self, fold_index: int) -> CandidateSelection | None:
        """Return the selection for ``fold_index``, or ``None`` when absent."""
        for result in self.results:
            if result.fold.fold_index == fold_index:
                return result.selection
        return None

    def readable(self) -> str:
        """Render the rolling training run as one line."""
        return (
            f"{len(self.results)} folds trained on {self.market.value} "
            f"dataset {self.dataset_fingerprint[:12]} code {self.code_version} "
            f"fp {self.fingerprint}"
        )


def run_rolling_training(
    sequence: FoldSequence,
    *,
    space: ParameterSpace,
    budget: TrialBudget,
    market: Market,
    dataset_fingerprint: str,
    code_version: str,
    tuning: TuningConfig,
    candidate_parameter_sets: Sequence[Mapping[str, object]],
    fit_factory: Callable[[date, date], TrainingFit],
    evaluate: Callable[[WalkForwardFold, Mapping[str, object]], float],
    constraints: Sequence[ParameterConstraint] = (),
    validation_samples: int = 1,
    trial_prefix: str = "fold-trial",
) -> RollingTrainRun:
    """Orchestrate per-fold training and selection for a fold sequence.

    Every fold fits only on its training interval (the injected ``fit_factory``
    is called with the fold's ``train_start``/``train_end`` and the returned
    snapshot must stay within those bounds), registers its candidate trials
    against the fold's train / validation bounds, and selects the best
    candidate under the pre-registered SP 3.21 rules derived from ``tuning``.
    """
    if not dataset_fingerprint:
        raise RollingTrainError("dataset fingerprint must be non-empty.")
    if not code_version:
        raise RollingTrainError("code version must be non-empty.")
    if not trial_prefix:
        raise RollingTrainError("trial prefix must be non-empty.")
    if not candidate_parameter_sets:
        raise RollingTrainError("at least one candidate parameter set is required.")
    if validation_samples <= 0:
        raise RollingTrainError("validation samples must be positive.")
    if any(fold.dataset_fingerprint != dataset_fingerprint for fold in sequence.folds):
        raise RollingTrainError("the run dataset fingerprint does not match the fold sequence.")

    rules = rules_from_tuning(tuning)
    results: list[FoldTrainingResult] = []
    for index, fold in enumerate(sequence.folds):
        split = EvaluationSplit(
            train_start=fold.train_start,
            train_end=fold.train_end,
            validation_start=fold.validation_start,
            validation_end=fold.validation_end,
            test_start=fold.test_start,
            test_end=fold.test_end,
        )
        fit = fit_factory(fold.train_start, fold.train_end)
        try:
            require_fit_within_training(fit, split)
        except TrainingFitError as exc:
            raise RollingTrainError(
                f"fold {index} fit is not confined to its training interval: {exc}"
            ) from exc

        registry = build_trial_registry(
            space=space,
            budget=budget,
            dataset_fingerprint=dataset_fingerprint,
            code_version=code_version,
            market=market,
            train_start=fold.train_start,
            train_end=fold.train_end,
            validation_start=fold.validation_start,
            validation_end=fold.validation_end,
            seed=tuning.random_seed,
            constraints=constraints,
            trial_prefix=f"{trial_prefix}-{index}",
        )
        for parameters in candidate_parameter_sets:
            metric = evaluate(fold, parameters)
            registry, _ = registry.register(parameters, metric=metric)
        trials = registry.trials
        trial_results = {
            trial.trial_id: TrialValidationResult(
                trial_id=trial.trial_id,
                metric_name=tuning.primary_metric,
                validation_samples=validation_samples,
            )
            for trial in trials
        }
        selection = select_candidate(trials, rules=rules, results=trial_results)
        results.append(
            FoldTrainingResult(
                fold=fold,
                fit=fit,
                trials=trials,
                selection=selection,
            )
        )

    run = RollingTrainRun(
        results=tuple(results),
        market=market,
        dataset_fingerprint=dataset_fingerprint,
        code_version=code_version,
        fingerprint="unfingerprinted",
    )
    return replace(run, fingerprint=rolling_train_fingerprint(run))


def rolling_train_json(run: RollingTrainRun) -> str:
    """Return a stable, key-sorted JSON serialization of a rolling train run.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style).
    """
    payload: dict[str, object] = {
        "market": run.market.value,
        "dataset_fingerprint": run.dataset_fingerprint,
        "code_version": run.code_version,
        "results": [
            {
                "fold_index": result.fold.fold_index,
                "fit_fingerprint": result.fit.fingerprint,
                "selection_fingerprint": result.selection.fingerprint,
                "selected": (
                    {
                        "trial_id": result.selection.selected.trial_id,
                        "metric": result.selection.selected.metric,
                    }
                    if result.selection.selected is not None
                    else None
                ),
                "trials": [
                    {
                        "trial_id": trial.trial_id,
                        "trial_fingerprint": trial_fingerprint(trial),
                        "metric": trial.metric,
                    }
                    for trial in result.trials
                ],
            }
            for result in run.results
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def rolling_train_fingerprint(run: RollingTrainRun) -> str:
    """Return the stable SHA-256 fingerprint of a rolling train run (SP 3.33)."""
    return hashlib.sha256(rolling_train_json(run).encode("utf-8")).hexdigest()
