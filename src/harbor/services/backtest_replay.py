"""Replay manifest and consistency views for a backtest run (MVP 5 / SP 5.21).

A backtest run's replay manifest (SP 2.61) is *derived*, not stored: it is
rebuilt from the persisted run row and config snapshot. This module wraps that
derivation and pairs it with the SP 2.62 consistency check, so the dashboard can
show two things the CLI already guarantees — the fingerprint of the inputs a run
claims, and whether other runs claiming the same inputs produced the same
results.

Three things it records rather than hides:

* The fingerprint covers the config hash, the code version and the data query
  boundaries. It does **not** cover the data itself, so two runs can share a
  fingerprint while having run on different data (a provider backfill is the
  usual cause). A disagreement between siblings therefore points at the data,
  not at the configuration — which is why the view reports both the fingerprint
  and the comparison instead of declaring "identical" on the fingerprint alone.
* ``fx_source``, ``calendar_version`` and ``random_seed`` are not persisted for
  backtest runs, so they are recorded as unset. They do participate in the
  fingerprint when a caller supplies them, so "unset" is not the same claim as
  "no FX was used".
* A sibling whose artifact cannot be derived is reported as inconsistent with
  the reason, rather than dropped. Silently omitting it would make the remaining
  siblings look like the whole set.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import Connection

from harbor.core.consistency_check import ConsistencyIssue, compare_artifacts
from harbor.core.replay_manifest import (
    ReplayManifest,
    ReplayManifestError,
    manifest_from_artifact,
)
from harbor.services.backtest import BacktestReportError, build_report_artifact
from harbor.storage.backtest_repositories import BacktestRepository

#: How many sibling runs to compare; the total is reported so a bound is visible.
MAX_SIBLINGS = 5

#: How many located differences to carry per sibling; the count is always exact.
MAX_DIFFERENCES = 10


class BacktestReplayError(ValueError):
    """Raised when a run's replay manifest cannot be derived (SP 5.21)."""


@dataclass(frozen=True)
class ManifestView:
    """A run's replay manifest, flattened for transport (SP 5.21)."""

    run_id: str
    config_hash: str
    code_version: str
    start_date: date
    end_date: date
    data_cutoff: date
    fx_source: str | None
    calendar_version: str | None
    random_seed: int | None
    fingerprint: str


@dataclass(frozen=True)
class SiblingConsistency:
    """Whether another run with the same inputs produced the same results.

    ``consistent`` is the SP 2.62 verdict and covers the result sections only
    (net values, trades, positions, metrics). Two runs that both produced nothing
    agree on those sections trivially, so the recorded status is kept and
    combined into ``outcome_agrees``: a completed run and a failed run sharing an
    input fingerprint are the interesting case, and calling them "consistent"
    without that qualifier would hide it.
    """

    run_id: str
    status: str
    same_status: bool
    consistent: bool
    outcome_agrees: bool
    difference_count: int
    differences: tuple[ConsistencyIssue, ...]


@dataclass(frozen=True)
class ReplayConsistency:
    """A run's manifest and its agreement with the runs sharing its inputs."""

    run_id: str
    manifest: ManifestView
    siblings: tuple[SiblingConsistency, ...]
    sibling_total: int
    notes: tuple[str, ...]

    @property
    def truncated(self) -> bool:
        """Whether some siblings were left uncompared."""
        return self.sibling_total > len(self.siblings)

    @property
    def all_consistent(self) -> bool:
        """Whether every compared sibling agrees on its results (SP 2.62)."""
        return all(sibling.consistent for sibling in self.siblings)

    @property
    def all_outcomes_agree(self) -> bool:
        """Whether every compared sibling also recorded the same status."""
        return all(sibling.outcome_agrees for sibling in self.siblings)


def manifest_view(manifest: ReplayManifest) -> ManifestView:
    """Flatten a replay manifest into its transport shape (SP 5.21)."""
    return ManifestView(
        run_id=manifest.run_id,
        config_hash=manifest.config_hash,
        code_version=manifest.code_version,
        start_date=manifest.data_boundaries.start_date,
        end_date=manifest.data_boundaries.end_date,
        data_cutoff=manifest.data_boundaries.data_cutoff,
        fx_source=manifest.fx_source,
        calendar_version=manifest.calendar_version,
        random_seed=manifest.random_seed,
        fingerprint=manifest.fingerprint(),
    )


def consistency_notes() -> tuple[str, ...]:
    """The caveats a reader needs to interpret a fingerprint (SP 5.21)."""
    return (
        "重放清单由已落库数据推导，非单独存储；指纹覆盖配置哈希、代码版本与数据查询边界。",
        "backtest_runs 未持久化 fx_source / calendar_version / random_seed，故记录为未设置；"
        "「未设置」不等于「未使用汇率」。",
        "指纹不覆盖数据内容本身：两次运行可能指纹相同而数据已不同（例如数据源补数之后）。"
        "因此兄弟运行结果不一致时应先核对数据，而不是先怀疑配置。",
        "结果区一致（SP 2.62）只覆盖净值 / 成交 / 持仓 / 指标四个区段；"
        "两次运行都未产生结果时它们当然一致，因此运行状态是否相同单独列出。",
    )


def _artifact_for(connection: Connection, run_id: str) -> dict[str, Any]:
    """Rebuild a run's SP 2.58-shaped artifact, or raise a replay error."""
    try:
        return build_report_artifact(connection=connection, run_id=run_id)
    except BacktestReportError as error:
        raise BacktestReplayError(str(error)) from error


def derive_manifest(*, connection: Connection, run_id: str) -> ReplayManifest:
    """Derive a run's replay manifest from persisted rows (SP 5.21).

    Raises:
        BacktestReplayError: If the run is missing or its manifest cannot be
            derived (a malformed config snapshot, for example).
    """
    artifact = _artifact_for(connection, run_id)
    try:
        return manifest_from_artifact(artifact)
    except ReplayManifestError as error:
        raise BacktestReplayError(
            f"Cannot derive a replay manifest for run {run_id!r}: {error}"
        ) from error


def _compare_sibling(
    connection: Connection,
    artifact: dict[str, Any],
    sibling_run_id: str,
    status: str,
    max_differences: int,
) -> SiblingConsistency:
    """Compare one sibling run's results against the subject run's."""
    try:
        sibling_artifact = _artifact_for(connection, sibling_run_id)
    except BacktestReplayError as error:
        # Keep the sibling visible as a disagreement with its reason: dropping it
        # would leave a partial set looking complete.
        return SiblingConsistency(
            run_id=sibling_run_id,
            status=status,
            same_status=status == str(artifact["run"]["status"]),
            consistent=False,
            outcome_agrees=False,
            difference_count=1,
            differences=(
                ConsistencyIssue(
                    section="run",
                    location="artifact",
                    expected="a derivable result artifact",
                    actual=str(error),
                ),
            ),
        )
    report = compare_artifacts(artifact, sibling_artifact)
    same_status = status == str(artifact["run"]["status"])
    return SiblingConsistency(
        run_id=sibling_run_id,
        status=status,
        same_status=same_status,
        consistent=report.consistent,
        outcome_agrees=report.consistent and same_status,
        difference_count=len(report.issues),
        differences=report.issues[:max_differences],
    )


def build_replay_consistency(
    *,
    connection: Connection,
    run_id: str,
    max_siblings: int = MAX_SIBLINGS,
    max_differences: int = MAX_DIFFERENCES,
) -> ReplayConsistency:
    """Derive a run's manifest and check it against runs sharing its inputs (SP 5.21).

    Args:
        connection: The database connection.
        run_id: The backtest run id.
        max_siblings: How many sibling runs to compare.
        max_differences: How many located differences to carry per sibling.

    Returns:
        The manifest, the per-sibling comparison and the interpretation notes.

    Raises:
        BacktestReplayError: If the run is missing or its manifest is underivable.
    """
    if max_siblings < 0:
        raise BacktestReplayError("max_siblings must not be negative.")
    artifact = _artifact_for(connection, run_id)
    try:
        manifest = manifest_from_artifact(artifact)
    except ReplayManifestError as error:
        raise BacktestReplayError(
            f"Cannot derive a replay manifest for run {run_id!r}: {error}"
        ) from error

    repository = BacktestRepository(connection)
    # Named explicitly rather than spread from a dict: a ``**dict[str, object]``
    # spread erases the parameter types and defeats the type checker.
    rows = [
        dict(row._mapping)
        for row in connection.execute(
            repository.list_runs_sharing_inputs(
                config_hash=manifest.config_hash,
                code_version=manifest.code_version,
                data_cutoff=manifest.data_boundaries.data_cutoff,
                exclude_run_id=run_id,
                limit=max_siblings,
            )
        ).all()
    ]
    total = int(
        connection.execute(
            repository.count_runs_sharing_inputs(
                config_hash=manifest.config_hash,
                code_version=manifest.code_version,
                data_cutoff=manifest.data_boundaries.data_cutoff,
                exclude_run_id=run_id,
            )
        ).scalar_one()
    )

    siblings = tuple(
        _compare_sibling(
            connection,
            artifact,
            str(row["run_id"]),
            str(row["status"]),
            max_differences,
        )
        for row in rows
    )
    return ReplayConsistency(
        run_id=run_id,
        manifest=manifest_view(manifest),
        siblings=siblings,
        sibling_total=total,
        notes=consistency_notes(),
    )
