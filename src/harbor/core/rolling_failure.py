"""Rolling backtest failure recovery (MVP 3 / SP 3.43).

A single failed fold preserves its diagnostics and, per configuration, sets
the overall status to failed or not-qualified; it is forbidden to omit failed
folds and continue aggregation (单一折叠失败保留诊断并按配置将整体状态置为失败或
不合格；禁止省略失败折叠后继续汇总).

The SP 3.35 :class:`~harbor.core.rolling_oos.RollingOosRun` records every
fold's outcome — executed folds carry a run id + replay manifest, and
non-executed folds carry a ``failure_reason`` (access denied, outside the
registered holdout, no selected candidate, ...). SP 3.43 recovers from those
failures:

- **Diagnostics preserved** (保留诊断): every failed fold's index and failure
  reason is collected in fold order into :class:`FoldFailureDiagnostics` —
  never silently dropped.
- **Overall status per config** (按配置置为失败或不合格): the
  :class:`RollingFailurePolicy` picks the terminal SP 3.13 status applied when
  any fold fails — ``FAILED`` or ``NOT_QUALIFIED``.
- **Aggregation forbidden** (禁止省略失败折叠后继续汇总): ``aggregation_allowed``
  is false whenever a fold failed, and
  :func:`require_aggregation_allowed` raises :class:`RollingFailureError` so a
  failed fold is never omitted to keep concatenation (SP 3.37) or metrics
  (SP 3.38) running on a partial path.

:func:`check_rolling_failures` returns the non-raising recovery outcome;
:func:`require_aggregation_allowed` enforces the no-omit rule. The recovery is
a value record with a re-derivable SHA-256 fingerprint for auditability.

Pure core layer: depends only on the SP 3.35 run and the SP 3.13 status enum,
never on storage, services or CLI.
"""

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, replace
from enum import StrEnum

from harbor.core.rolling_oos import RollingOosRun
from harbor.core.validation_domain import ValidationStatus


class RollingFailureError(ValueError):
    """Raised when failed folds would be omitted to continue aggregation (SP 3.43)."""


class FailureSeverity(StrEnum):
    """The configured overall status applied when a fold fails (SP 3.43)."""

    FAILED = "failed"
    NOT_QUALIFIED = "not_qualified"


#: The SP 3.13 terminal status applied per configured severity.
_SEVERITY_STATUS: dict[FailureSeverity, ValidationStatus] = {
    FailureSeverity.FAILED: ValidationStatus.FAILED,
    FailureSeverity.NOT_QUALIFIED: ValidationStatus.NOT_QUALIFIED,
}


@dataclass(frozen=True)
class RollingFailurePolicy:
    """The failure-recovery configuration (SP 3.43).

    ``severity`` selects which overall status the validation run is set to
    when any fold fails: ``FAILED`` (a failed run is never silently resumed,
    SP 3.13) or ``NOT_QUALIFIED`` (research conclusion blocked). A single
    failed fold is enough to trigger it — failed folds are never tolerated
    and never omitted.
    """

    severity: FailureSeverity = FailureSeverity.FAILED

    def readable(self) -> str:
        """Render the policy as one line."""
        return f"rolling failure policy: {self.severity.value} on any fold failure"


@dataclass(frozen=True)
class FoldFailureDiagnostics:
    """Preserved diagnostics of one failed fold (SP 3.43).

    ``fold_index`` identifies the failed fold and ``failure_reason`` is the
    diagnostic recorded by the SP 3.35 run (why the fold did not execute).
    """

    fold_index: int
    failure_reason: str

    def __post_init__(self) -> None:
        if self.fold_index < 0:
            raise RollingFailureError("fold index must be non-negative.")
        if not self.failure_reason:
            raise RollingFailureError("a failed fold must carry a failure reason.")

    def readable(self) -> str:
        """Render the diagnostics as one line."""
        return f"fold {self.fold_index}: {self.failure_reason}"


@dataclass(frozen=True)
class RollingFailureRecovery:
    """The failure-recovery outcome for a rolling OOS run (SP 3.43).

    ``failed_folds`` preserves every failed fold's diagnostics in fold order
    (empty when no fold failed); ``overall_status`` is the configured SP 3.13
    terminal status (``None`` when nothing failed); ``aggregation_allowed`` is
    false exactly when a fold failed — omitting it to continue aggregating is
    forbidden; ``reason`` explains the outcome (``None`` when clean);
    ``fingerprint`` is the re-derivable SHA-256 digest.
    """

    failed_folds: tuple[FoldFailureDiagnostics, ...]
    policy: RollingFailurePolicy
    overall_status: ValidationStatus | None
    aggregation_allowed: bool
    reason: str | None
    fingerprint: str

    def __post_init__(self) -> None:
        previous: int | None = None
        for diagnostics in self.failed_folds:
            if previous is not None and diagnostics.fold_index <= previous:
                raise RollingFailureError("failed folds must be ordered by fold index.")
            previous = diagnostics.fold_index
        if self.aggregation_allowed != (not self.failed_folds):
            raise RollingFailureError("aggregation is allowed exactly when no fold failed.")
        if not self.failed_folds:
            if self.overall_status is not None:
                raise RollingFailureError("a clean run must not carry an overall status.")
            if self.reason is not None:
                raise RollingFailureError("a clean run must not carry a reason.")
        else:
            expected = _SEVERITY_STATUS[self.policy.severity]
            if self.overall_status is not expected:
                raise RollingFailureError("the overall status must match the configured severity.")
            if not self.reason:
                raise RollingFailureError("a failed run must carry a reason.")
        if not self.fingerprint:
            raise RollingFailureError("failure recovery fingerprint must be non-empty.")

    def __len__(self) -> int:
        return len(self.failed_folds)

    def __iter__(self) -> Iterator[FoldFailureDiagnostics]:
        return iter(self.failed_folds)

    def __getitem__(self, index: int) -> FoldFailureDiagnostics:
        return self.failed_folds[index]

    @property
    def failed_count(self) -> int:
        """Number of failed folds."""
        return len(self.failed_folds)

    def readable(self) -> str:
        """Render the recovery as one line."""
        if not self.failed_folds:
            return f"all folds executed; aggregation allowed fp {self.fingerprint}"
        status = self.overall_status.value if self.overall_status is not None else "n/a"
        return (
            f"{self.failed_count} fold(s) failed; overall {status}; "
            f"aggregation forbidden fp {self.fingerprint}"
        )


def check_rolling_failures(
    oos_run: RollingOosRun,
    *,
    policy: RollingFailurePolicy | None = None,
) -> RollingFailureRecovery:
    """Recover the rolling OOS run's fold failures (SP 3.43, non-raising).

    Preserves every failed fold's diagnostics in fold order and applies the
    configured overall status (FAILED / NOT_QUALIFIED) when any fold failed.
    A run with no failures stays aggregation-allowed with no overall status.
    """
    effective = policy if policy is not None else RollingFailurePolicy()
    diagnostics = tuple(
        FoldFailureDiagnostics(
            fold_index=result.validation.fold.fold_index,
            failure_reason=result.failure_reason,
        )
        for result in oos_run.results
        if result.failure_reason is not None
    )
    if not diagnostics:
        recovery = RollingFailureRecovery(
            failed_folds=(),
            policy=effective,
            overall_status=None,
            aggregation_allowed=True,
            reason=None,
            fingerprint="unfingerprinted",
        )
    else:
        status = _SEVERITY_STATUS[effective.severity]
        reason = (
            f"{len(diagnostics)} of {len(oos_run.results)} fold(s) failed; "
            f"overall status set to {status.value}; omitting failed folds and "
            "continuing aggregation is forbidden."
        )
        recovery = RollingFailureRecovery(
            failed_folds=diagnostics,
            policy=effective,
            overall_status=status,
            aggregation_allowed=False,
            reason=reason,
            fingerprint="unfingerprinted",
        )
    return replace(recovery, fingerprint=rolling_failure_fingerprint(recovery))


def require_aggregation_allowed(
    recovery: RollingFailureRecovery,
) -> RollingFailureRecovery:
    """Enforce the no-omit rule (SP 3.43, raising).

    Raises :class:`RollingFailureError` when a fold failed — omitting failed
    folds and continuing aggregation is forbidden; the failed folds' preserved
    diagnostics are named. Otherwise returns the recovery unchanged.
    """
    if not recovery.aggregation_allowed:
        details = "; ".join(diagnostics.readable() for diagnostics in recovery.failed_folds)
        raise RollingFailureError(
            f"omitting failed folds and continuing aggregation is forbidden (SP 3.43): {details}"
        )
    return recovery


def rolling_failure_json(recovery: RollingFailureRecovery) -> str:
    """Return a stable, key-sorted JSON serialization of a recovery.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style).
    """
    payload: dict[str, object] = {
        "aggregation_allowed": recovery.aggregation_allowed,
        "overall_status": (
            recovery.overall_status.value if recovery.overall_status is not None else None
        ),
        "policy": {"severity": recovery.policy.severity.value},
        "failed_folds": [
            {
                "fold_index": diagnostics.fold_index,
                "failure_reason": diagnostics.failure_reason,
            }
            for diagnostics in recovery.failed_folds
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def rolling_failure_fingerprint(recovery: RollingFailureRecovery) -> str:
    """Return the stable SHA-256 fingerprint of a recovery (SP 3.43)."""
    return hashlib.sha256(rolling_failure_json(recovery).encode("utf-8")).hexdigest()


__all__: tuple[str, ...] = (
    "FailureSeverity",
    "FoldFailureDiagnostics",
    "RollingFailureError",
    "RollingFailurePolicy",
    "RollingFailureRecovery",
    "check_rolling_failures",
    "require_aggregation_allowed",
    "rolling_failure_fingerprint",
    "rolling_failure_json",
)
