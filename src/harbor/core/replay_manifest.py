"""Replayable manifest (MVP 2 / SP 2.61).

Records everything needed to reproduce a research run as an immutable
:class:`ReplayManifest`: the strategy config hash (SP 2.5), the code version,
the data query boundaries (config date range + data cutoff, SP 2.47), the FX
source (SP 2.12), the calendar version (SP 2.11) and the random seed.

Two runs whose manifests carry the same :meth:`ReplayManifest.fingerprint` are
replay-identical: identical inputs reproduce identical signals, fills, net
values and metrics (SP 2.62 / 2.82). The fingerprint deliberately excludes the
run id, which identifies a specific execution rather than the research inputs.

FX source, calendar version and random seed are recorded verbatim when provided
and omitted (``None``) otherwise — the manifest never fabricates a value the
caller did not supply (never-assume rule).

Pure core logic: depends only on the config and run-identity types; never
touches storage or CLI code.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

from harbor.core.backtest_config import BacktestConfig
from harbor.core.run_identity import RunIdentity


class ReplayManifestError(ValueError):
    """Raised when a replay manifest cannot be built (SP 2.61)."""


@dataclass(frozen=True)
class DataQueryBoundaries:
    """The data query boundaries of a run (数据查询边界, SP 2.47)."""

    start_date: date
    end_date: date
    data_cutoff: date

    def __post_init__(self) -> None:
        if self.end_date < self.start_date:
            raise ReplayManifestError("end_date must be on or after start_date.")

    def readable(self) -> str:
        """Render the boundaries as a compact summary."""
        return (
            f"{self.start_date.isoformat()} .. {self.end_date.isoformat()} "
            f"(cutoff {self.data_cutoff.isoformat()})"
        )


@dataclass(frozen=True)
class ReplayManifest:
    """Everything needed to reproduce one research run (可重放清单, SP 2.61)."""

    run_id: str
    config_hash: str
    code_version: str
    data_boundaries: DataQueryBoundaries
    fx_source: str | None
    calendar_version: str | None
    random_seed: int | None

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ReplayManifestError("run_id must be non-empty.")
        if not self.config_hash:
            raise ReplayManifestError("config_hash must be non-empty.")
        if not self.code_version:
            raise ReplayManifestError("code_version must be non-empty.")
        if self.random_seed is not None and self.random_seed < 0:
            raise ReplayManifestError("random_seed must be non-negative when set.")

    def fingerprint(self) -> str:
        """Return a stable string identifying the replay inputs (SP 2.61).

        Two runs with the same fingerprint are replay-identical. The run id is
        deliberately excluded because it identifies an execution, not the
        research inputs.
        """
        seed = "" if self.random_seed is None else str(self.random_seed)
        return "|".join(
            [
                self.config_hash,
                self.code_version,
                self.data_boundaries.start_date.isoformat(),
                self.data_boundaries.end_date.isoformat(),
                self.data_boundaries.data_cutoff.isoformat(),
                self.fx_source or "",
                self.calendar_version or "",
                seed,
            ]
        )

    def readable(self) -> str:
        """Render the manifest as a human-readable replay checklist."""
        lines = [
            f"replay manifest {self.run_id}:",
            f"  config hash: {self.config_hash}",
            f"  code version: {self.code_version}",
            f"  data boundaries: {self.data_boundaries.readable()}",
            f"  fx source: {self.fx_source or 'n/a'}",
            f"  calendar version: {self.calendar_version or 'n/a'}",
            f"  random seed: {'n/a' if self.random_seed is None else self.random_seed}",
            f"  fingerprint: {self.fingerprint()}",
        ]
        return "\n".join(lines)


def build_replay_manifest(
    *,
    run_id: str,
    config: BacktestConfig,
    identity: RunIdentity,
    fx_source: str | None = None,
    calendar_version: str | None = None,
    random_seed: int | None = None,
) -> ReplayManifest:
    """Build the replay manifest for a run from its config and identity.

    Args:
        run_id: The run id.
        config: The validated configuration snapshot (SP 2.4).
        identity: The run identity carrying config hash, data cutoff and code
            version (SP 2.48).
        fx_source: Optional FX data source label (SP 2.12).
        calendar_version: Optional trading calendar version (SP 2.11).
        random_seed: Optional random seed used by the run, if any.

    Returns:
        A :class:`ReplayManifest` recording all replay inputs.
    """
    return ReplayManifest(
        run_id=run_id,
        config_hash=identity.config_hash,
        code_version=identity.code_version,
        data_boundaries=DataQueryBoundaries(
            start_date=config.start_date,
            end_date=config.end_date,
            data_cutoff=identity.data_cutoff,
        ),
        fx_source=fx_source,
        calendar_version=calendar_version,
        random_seed=random_seed,
    )


def _parse_date(value: Any, label: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ReplayManifestError(f"{label} is not an ISO date: {value!r}.") from exc


def manifest_from_artifact(
    artifact: dict[str, Any],
    *,
    fx_source: str | None = None,
    calendar_version: str | None = None,
    random_seed: int | None = None,
) -> ReplayManifest:
    """Build the replay manifest from an SP 2.58 results artifact.

    Reads the config hash, code version, data cutoff and run id from the run
    metadata, and the date range from the config snapshot.

    Raises:
        ReplayManifestError: If the artifact is not an SP 2.58 artifact or a
            required field is missing or malformed.
    """
    if "run" not in artifact or "config" not in artifact:
        raise ReplayManifestError("Expected an SP 2.58 results artifact with 'run' and 'config'.")
    run = artifact["run"]
    inputs = run["inputs"]
    config = artifact["config"]
    if "start_date" not in config or "end_date" not in config:
        raise ReplayManifestError("The config snapshot is missing start_date/end_date.")
    return ReplayManifest(
        run_id=run["run_id"],
        config_hash=inputs["config_hash"],
        code_version=inputs["code_version"],
        data_boundaries=DataQueryBoundaries(
            start_date=_parse_date(config["start_date"], "config.start_date"),
            end_date=_parse_date(config["end_date"], "config.end_date"),
            data_cutoff=_parse_date(inputs["data_cutoff"], "run.inputs.data_cutoff"),
        ),
        fx_source=fx_source,
        calendar_version=calendar_version,
        random_seed=random_seed,
    )
