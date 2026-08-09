"""Fold out-of-sample execution (MVP 3 / SP 3.35).

Runs the MVP 2 engine on each fold's subsequent unseen interval (该折叠后续未
见区间 = the fold's test / out-of-sample segment) using the parameters selected
by SP 3.33, and preserves the full backtest run and its replay manifest
(保留完整回测运行和回放清单, SP 2.61).

Every fold's OOS execution is gated by the SP 3.24 test-access guard: the
fold's test interval is part of the guarded holdout, so the run authorizes a
``DATA_READ`` on the fold's test segment (audited) and only executes when the
guard grants it at ``TEST_LOCKED`` / ``EVALUATED``. A fold denied access, a
fold whose OOS interval falls outside the registered holdout, or a fold with
no selected candidate is recorded as NOT executed with its failure reason —
never silently omitted (SP 3.43). The executed fold's ``run_id`` is written
back onto the fold (SP 3.1 ``WalkForwardFold.run_id``) and its replay manifest
is preserved.

The MVP 2 engine execution is data-dependent and injected as
``run_engine(fold, selected) -> OosRunOutcome`` so the core layer stays pure.

Pure core layer: depends only on the SP 3.24/3.33/3.34 modules and the MVP 2
run identity / replay types, never on storage, services or CLI.
"""

import hashlib
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from datetime import datetime

from harbor.core.replay_manifest import ReplayManifest
from harbor.core.rolling_validate import FoldValidationResult, RollingValidationRun
from harbor.core.test_access_guard import (
    AccessGuard,
    AccessKind,
    access_audit_fingerprint,
)
from harbor.core.validation_domain import ParameterTrial, ValidationStatus, WalkForwardFold


class RollingOosError(ValueError):
    """Raised when a fold's out-of-sample execution record is invalid (SP 3.35)."""


@dataclass(frozen=True)
class OosRunOutcome:
    """The MVP 2 engine outcome for one fold's out-of-sample segment.

    ``run_id`` identifies the full backtest run (persisted via the SP 3.12
    ``validation_folds.backtest_run_id`` link) and ``replay_manifest`` is the
    SP 2.61 replay manifest recording every input needed to reproduce it.
    """

    run_id: str
    replay_manifest: ReplayManifest

    def __post_init__(self) -> None:
        if not self.run_id:
            raise RollingOosError("run id must be non-empty.")
        if self.replay_manifest.run_id != self.run_id:
            raise RollingOosError("replay manifest run id must match the OOS run id.")


@dataclass(frozen=True)
class FoldOosResult:
    """One fold's out-of-sample execution record (SP 3.35).

    ``validation`` links back to the SP 3.34 validation result; ``run_id`` is
    the MVP 2 run id (``None`` when not executed), ``replay_manifest`` the
    preserved SP 2.61 manifest and ``failure_reason`` why the fold was not
    executed (only set when not executed).
    """

    validation: FoldValidationResult
    run_id: str | None
    replay_manifest: ReplayManifest | None
    failure_reason: str | None

    def __post_init__(self) -> None:
        if self.run_id is None:
            if self.replay_manifest is not None:
                raise RollingOosError("a non-executed fold must not carry a replay manifest.")
            if self.failure_reason is None:
                raise RollingOosError("a non-executed fold must carry a failure reason.")
            return
        if self.replay_manifest is None:
            raise RollingOosError("an executed fold must carry a replay manifest.")
        if self.failure_reason is not None:
            raise RollingOosError("an executed fold must not carry a failure reason.")
        if self.replay_manifest.run_id != self.run_id:
            raise RollingOosError("replay manifest run id must match the fold run id.")
        fold = self.fold
        if self.replay_manifest.data_boundaries.start_date > fold.test_start:
            raise RollingOosError("replay manifest does not cover the fold's out-of-sample start.")
        if self.replay_manifest.data_boundaries.end_date < fold.test_end:
            raise RollingOosError("replay manifest does not cover the fold's out-of-sample end.")

    @property
    def fold(self) -> WalkForwardFold:
        """The fold with its MVP 2 ``run_id`` populated."""
        return replace(self.validation.fold, run_id=self.run_id)

    @property
    def executed(self) -> bool:
        """Whether the fold's OOS segment was executed."""
        return self.run_id is not None

    def readable(self) -> str:
        """Render the fold's OOS outcome as one line."""
        if not self.executed:
            return f"fold {self.validation.fold.fold_index} OOS NOT executed: {self.failure_reason}"
        manifest = self.replay_manifest
        assert manifest is not None
        return (
            f"fold {self.validation.fold.fold_index} OOS run {self.run_id} "
            f"replay fp {manifest.fingerprint()}"
        )


@dataclass(frozen=True)
class RollingOosRun:
    """The auditable rolling out-of-sample execution result (SP 3.35).

    One :class:`FoldOosResult` per fold, ordered by ``fold_index`` from 0.
    ``access_guard`` is the SP 3.24 guard after every fold's audited access
    attempt; ``dataset_fingerprint`` / ``code_version`` are inherited from the
    SP 3.34 validation run and ``fingerprint`` is the derived SHA-256 digest.
    """

    results: tuple[FoldOosResult, ...]
    access_guard: AccessGuard
    dataset_fingerprint: str
    code_version: str
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.results:
            raise RollingOosError("a rolling OOS run requires at least one fold.")
        if not self.dataset_fingerprint:
            raise RollingOosError("dataset fingerprint must be non-empty.")
        if not self.code_version:
            raise RollingOosError("code version must be non-empty.")
        for index, result in enumerate(self.results):
            if result.validation.fold.fold_index != index:
                raise RollingOosError(
                    f"result {index} must carry fold_index {index}, "
                    f"got {result.validation.fold.fold_index}."
                )
            if result.validation.application.dataset_fingerprint != self.dataset_fingerprint:
                raise RollingOosError(
                    f"fold {index} application dataset fingerprint does not match the run."
                )
        if not self.fingerprint:
            raise RollingOosError("rolling OOS run fingerprint must be non-empty.")

    def __len__(self) -> int:
        return len(self.results)

    def __iter__(self) -> Iterator[FoldOosResult]:
        return iter(self.results)

    def __getitem__(self, index: int) -> FoldOosResult:
        return self.results[index]

    def oos_for(self, fold_index: int) -> FoldOosResult | None:
        """Return the OOS result for ``fold_index``, or ``None``."""
        for result in self.results:
            if result.validation.fold.fold_index == fold_index:
                return result
        return None

    @property
    def executed_count(self) -> int:
        """Number of folds whose OOS segment executed."""
        return sum(1 for result in self.results if result.executed)

    @property
    def all_executed(self) -> bool:
        """Whether every fold's OOS segment executed."""
        return all(result.executed for result in self.results)

    def readable(self) -> str:
        """Render the rolling OOS run as one line."""
        return (
            f"{self.executed_count}/{len(self.results)} folds executed on "
            f"dataset {self.dataset_fingerprint[:12]} code {self.code_version} "
            f"fp {self.fingerprint}"
        )


def run_rolling_oos(
    validation_run: RollingValidationRun,
    *,
    guard: AccessGuard,
    current_stage: ValidationStatus,
    run_engine: Callable[[WalkForwardFold, ParameterTrial], OosRunOutcome],
    requested_at: datetime | None = None,
) -> RollingOosRun:
    """Execute each fold's out-of-sample segment with its selected parameters.

    Every fold first authorizes a ``DATA_READ`` of its test interval through
    the SP 3.24 guard (audited); a denial records the fold as not executed.
    Executed folds run the injected MVP 2 engine with the SP 3.33 selected
    trial and preserve the run id + replay manifest.
    """
    results: list[FoldOosResult] = []
    for index, fold_result in enumerate(validation_run.results):
        fold = fold_result.fold
        guard, decision = guard.authorize(
            AccessKind.DATA_READ,
            current_stage=current_stage,
            requested_at=requested_at,
        )
        if not decision.granted:
            results.append(
                FoldOosResult(
                    validation=fold_result,
                    run_id=None,
                    replay_manifest=None,
                    failure_reason=decision.reason,
                )
            )
            continue
        registration = guard.registration
        if registration is not None and registration.split is not None:
            holdout = registration.split
            if fold.test_start < holdout.test_start or fold.test_end > holdout.test_end:
                results.append(
                    FoldOosResult(
                        validation=fold_result,
                        run_id=None,
                        replay_manifest=None,
                        failure_reason=(
                            f"fold {index} OOS interval "
                            f"{fold.test_start.isoformat()}..{fold.test_end.isoformat()} "
                            "lies outside the registered holdout test interval "
                            f"{holdout.test_start.isoformat()}..{holdout.test_end.isoformat()}."
                        ),
                    )
                )
                continue
        selected = fold_result.training.selection.selected
        if selected is None:
            results.append(
                FoldOosResult(
                    validation=fold_result,
                    run_id=None,
                    replay_manifest=None,
                    failure_reason=(
                        f"fold {index} has no selected candidate; the OOS segment "
                        "cannot run without selected parameters."
                    ),
                )
            )
            continue
        outcome = run_engine(fold, selected)
        results.append(
            FoldOosResult(
                validation=fold_result,
                run_id=outcome.run_id,
                replay_manifest=outcome.replay_manifest,
                failure_reason=None,
            )
        )

    run = RollingOosRun(
        results=tuple(results),
        access_guard=guard,
        dataset_fingerprint=validation_run.dataset_fingerprint,
        code_version=validation_run.code_version,
        fingerprint="unfingerprinted",
    )
    return replace(run, fingerprint=rolling_oos_fingerprint(run))


def rolling_oos_json(run: RollingOosRun) -> str:
    """Return a stable, key-sorted JSON serialization of a rolling OOS run.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style).
    """
    payload: dict[str, object] = {
        "dataset_fingerprint": run.dataset_fingerprint,
        "code_version": run.code_version,
        "access_audit_fingerprint": access_audit_fingerprint(run.access_guard),
        "results": [
            {
                "fold_index": result.validation.fold.fold_index,
                "run_id": result.run_id,
                "replay_fingerprint": (
                    result.replay_manifest.fingerprint()
                    if result.replay_manifest is not None
                    else None
                ),
                "failure_reason": result.failure_reason,
            }
            for result in run.results
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def rolling_oos_fingerprint(run: RollingOosRun) -> str:
    """Return the stable SHA-256 fingerprint of a rolling OOS run (SP 3.35)."""
    return hashlib.sha256(rolling_oos_json(run).encode("utf-8")).hexdigest()
