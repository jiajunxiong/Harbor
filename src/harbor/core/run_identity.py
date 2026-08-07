"""Idempotent run semantics (MVP 2 / SP 2.48).

Two backtest runs with the same configuration hash (SP 2.5), data cutoff date
and code version are recognized as the same research run. By default an
existing run that already produced results is reused rather than overwritten,
so re-running an identical request is idempotent: it returns the existing run
instead of silently replacing its results.

Pure core logic: depends only on the domain types, the configuration and the
config hash (SP 2.5); never touches storage or CLI code.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from harbor.core.backtest_config import BacktestConfig
from harbor.core.backtest_config_loader import config_hash
from harbor.core.backtest_domain import BacktestStatus


class RunAction(StrEnum):
    """What to do with a requested run (SP 2.48)."""

    REUSE = "reuse"
    NEW = "new"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class RunIdentity:
    """Stable identity of a research run (SP 2.48).

    Two runs with the same config hash, data cutoff and code version are the
    same research run.
    """

    config_hash: str
    data_cutoff: date
    code_version: str

    def __post_init__(self) -> None:
        if not self.config_hash:
            raise ValueError("config_hash must be non-empty.")
        if not self.code_version:
            raise ValueError("code_version must be non-empty.")

    def fingerprint(self) -> str:
        """Return a stable, comparable string for this identity."""
        return f"{self.config_hash}:{self.data_cutoff.isoformat()}:{self.code_version}"

    def __str__(self) -> str:
        return self.fingerprint()


@dataclass(frozen=True)
class ExistingRun:
    """An already-recorded run with its identity and status (SP 2.48)."""

    run_id: str
    status: BacktestStatus
    identity: RunIdentity


@dataclass(frozen=True)
class RunResolution:
    """The idempotency decision for a requested run (SP 2.48)."""

    action: RunAction
    existing_run_id: str | None = None
    reason: str = ""

    def readable(self) -> str:
        """Render the resolution as a human-readable summary."""
        if self.existing_run_id is not None:
            return f"{self.action.value} (existing run {self.existing_run_id}): {self.reason}"
        return f"{self.action.value}: {self.reason}"


def run_identity(
    *,
    config_hash: str,
    data_cutoff: date,
    code_version: str,
) -> RunIdentity:
    """Build a run identity from its three identifying inputs (SP 2.48).

    Args:
        config_hash: The SP 2.5 SHA-256 of the canonical configuration JSON.
        data_cutoff: The data cutoff date of the run (数据截止).
        code_version: The code version of the run.

    Raises:
        ValueError: If ``config_hash`` or ``code_version`` is empty.
    """
    return RunIdentity(
        config_hash=config_hash,
        data_cutoff=data_cutoff,
        code_version=code_version,
    )


def identity_from_config(
    *,
    config: BacktestConfig,
    data_cutoff: date,
    code_version: str,
) -> RunIdentity:
    """Build a run identity from a configuration (SP 2.48).

    The configuration hash comes from SP 2.5 (:func:`config_hash`), so an
    identical configuration yields an identical identity.
    """
    return RunIdentity(
        config_hash=config_hash(config),
        data_cutoff=data_cutoff,
        code_version=code_version,
    )


def resolve_run(
    *,
    requested: RunIdentity,
    existing: Sequence[ExistingRun],
    overwrite: bool = False,
) -> RunResolution:
    """Decide whether to reuse, create, or refuse a requested run (SP 2.48).

    By default (``overwrite=False``), an existing run with the same identity
    that already completed is reused and never overwritten; an identical run
    that is still in progress or failed is a CONFLICT (not silently
    overwritten — SP 2.70). With ``overwrite=True`` the request always creates
    a new run.

    Args:
        requested: The identity of the requested run.
        existing: The runs already recorded for this research.
        overwrite: Whether to allow replacing existing results.

    Returns:
        A :class:`RunResolution` describing the decision.
    """
    matches = [run for run in existing if run.identity.fingerprint() == requested.fingerprint()]
    if not matches or overwrite:
        reason = (
            "creating a new run (overwrite requested)."
            if overwrite and matches
            else "no identical run recorded; creating a new run."
        )
        return RunResolution(action=RunAction.NEW, existing_run_id=None, reason=reason)
    for run in matches:
        if run.status is BacktestStatus.COMPLETED:
            return RunResolution(
                action=RunAction.REUSE,
                existing_run_id=run.run_id,
                reason=(
                    f"identical run {run.run_id} already completed; "
                    "reusing instead of overwriting (SP 2.48)."
                ),
            )
    run = matches[0]
    return RunResolution(
        action=RunAction.CONFLICT,
        existing_run_id=run.run_id,
        reason=(
            f"identical run {run.run_id} exists with status {run.status.value}; "
            "not overwriting by default (SP 2.48)."
        ),
    )
