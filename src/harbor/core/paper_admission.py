"""Strategy registration and admission for the paper loop (MVP 4 / SP 4.3).

Only an MVP 3 ``QUALIFIED`` and versioned strategy may enter the paper
(模拟盘) loop; ``INCONCLUSIVE`` and ``NOT_QUALIFIED`` strategies are rejected
with an explicit reason (SP 4.3 acceptance). The admission decision is an
immutable, timestamped record so the 准入 gate is auditable and replayable.

The conclusion vocabulary is :class:`harbor.core.validation_domain.OOSConclusion`
(SP 3.58); the admission never re-derives a conclusion and never fabricates
evidence — it only reflects what MVP 3 recorded. Pure core logic: depends on
the validation domain and never touches storage, services or CLI code.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum

from harbor.core.validation_domain import OOSConclusion


class AdmissionDecision(StrEnum):
    """Whether a strategy was admitted to the paper loop (SP 4.3)."""

    ADMITTED = "ADMITTED"
    REJECTED = "REJECTED"


class AdmissionRegistryError(ValueError):
    """Raised when an admission registry is built invalidly (SP 4.3)."""


def _now_utc() -> datetime:
    """Return the current UTC time (default admission timestamp)."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class StrategyAdmission:
    """A recorded admission decision for one strategy version (SP 4.3).

    ``source_run_id`` links to the MVP 3 validation run that produced the
    conclusion and ``dataset_fingerprint`` to the frozen dataset it was
    derived from (SP 3.7), so the gate is traceable.
    """

    strategy: str
    strategy_version: str
    decision: AdmissionDecision
    reason: str
    conclusion: OOSConclusion
    admitted_at: datetime
    source_run_id: str | None = None
    dataset_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if not self.strategy or not self.strategy_version:
            raise ValueError("Strategy and strategy version must be non-empty.")
        if not self.reason:
            raise ValueError("Admission reason must be non-empty.")
        if self.admitted_at.tzinfo is None or self.admitted_at.utcoffset() != timedelta(0):
            raise ValueError("Admission timestamp must be UTC-aware (offset 0).")

    def readable(self) -> str:
        """Render the admission as a compact summary."""
        return (
            f"{self.decision.value} {self.strategy}@{self.strategy_version} "
            f"conclusion {self.conclusion.value} at "
            f"{self.admitted_at.isoformat()} ({self.reason})"
        )


def _with_default_timestamp(*, admitted_at: datetime | None) -> datetime:
    return _now_utc() if admitted_at is None else admitted_at


def admit_strategy(
    *,
    strategy: str,
    strategy_version: str,
    conclusion: OOSConclusion,
    source_run_id: str | None = None,
    dataset_fingerprint: str | None = None,
    admitted_at: datetime | None = None,
) -> StrategyAdmission:
    """Admit or reject a strategy based on its MVP 3 conclusion (SP 4.3).

    ``QUALIFIED`` strategies are admitted; ``INCONCLUSIVE`` and
    ``NOT_QUALIFIED`` are rejected with an explicit reason. The decision never
    fabricates a reason or re-derives the conclusion.

    Raises:
        ValueError: If the strategy identity is empty or the timestamp is not
            UTC-aware.
    """
    timestamp = _with_default_timestamp(admitted_at=admitted_at)
    if conclusion is OOSConclusion.QUALIFIED:
        return StrategyAdmission(
            strategy=strategy,
            strategy_version=strategy_version,
            decision=AdmissionDecision.ADMITTED,
            reason=(
                "MVP 3 conclusion is QUALIFIED (SP 3.58); the strategy is "
                "versioned and may enter the paper loop."
            ),
            conclusion=conclusion,
            source_run_id=source_run_id,
            dataset_fingerprint=dataset_fingerprint,
            admitted_at=timestamp,
        )
    if conclusion is OOSConclusion.INCONCLUSIVE:
        return StrategyAdmission(
            strategy=strategy,
            strategy_version=strategy_version,
            decision=AdmissionDecision.REJECTED,
            reason=(
                "MVP 3 conclusion is INCONCLUSIVE: evidence is insufficient "
                "to qualify; the strategy must not enter the paper loop."
            ),
            conclusion=conclusion,
            source_run_id=source_run_id,
            dataset_fingerprint=dataset_fingerprint,
            admitted_at=timestamp,
        )
    return StrategyAdmission(
        strategy=strategy,
        strategy_version=strategy_version,
        decision=AdmissionDecision.REJECTED,
        reason=(
            "MVP 3 conclusion is NOT_QUALIFIED: the strategy fails the "
            "out-of-sample qualification rules and must not enter the paper loop."
        ),
        conclusion=conclusion,
        source_run_id=source_run_id,
        dataset_fingerprint=dataset_fingerprint,
        admitted_at=timestamp,
    )


@dataclass(frozen=True)
class AdmissionRegistry:
    """An immutable registry of strategy admissions (SP 4.3).

    Admissions are kept key-sorted by ``(strategy, strategy_version)`` and
    unique, so the registry is deterministic and replayable. A duplicate or
    out-of-order registration is rejected rather than silently overwritten.
    """

    admissions: tuple[StrategyAdmission, ...] = ()

    def __post_init__(self) -> None:
        keys = [(admission.strategy, admission.strategy_version) for admission in self.admissions]
        if len(set(keys)) != len(keys):
            raise AdmissionRegistryError("Admissions must be unique per strategy version.")
        if keys != sorted(keys):
            raise AdmissionRegistryError("Admissions must be key-sorted.")

    def for_strategy(self, strategy: str, strategy_version: str) -> StrategyAdmission | None:
        """Return the admission for a strategy version (None when absent)."""
        for admission in self.admissions:
            if admission.strategy == strategy and admission.strategy_version == strategy_version:
                return admission
        return None

    def admitted(self) -> tuple[StrategyAdmission, ...]:
        """Return the admitted admissions in key order."""
        return tuple(
            admission
            for admission in self.admissions
            if admission.decision is AdmissionDecision.ADMITTED
        )

    def rejected(self) -> tuple[StrategyAdmission, ...]:
        """Return the rejected admissions in key order."""
        return tuple(
            admission
            for admission in self.admissions
            if admission.decision is AdmissionDecision.REJECTED
        )

    def register(self, admission: StrategyAdmission) -> "AdmissionRegistry":
        """Return a new registry with ``admission`` recorded (SP 4.3)."""
        merged = tuple(
            sorted(
                (*self.admissions, admission),
                key=lambda item: (item.strategy, item.strategy_version),
            )
        )
        return AdmissionRegistry(admissions=merged)

    def readable(self) -> str:
        """Render the registry as a compact summary."""
        return (
            f"admission registry: {len(self.admissions)} recorded "
            f"({len(self.admitted())} admitted, {len(self.rejected())} rejected)"
        )
