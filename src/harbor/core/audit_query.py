"""Research audit query (MVP 2 / SP 2.66).

Assembles a run's audit record — configuration, input range, artifacts and
failure reasons — keyed by run id, from the persisted run master record
(SP 2.6) and, when available, the SP 2.58 results artifact. The query layer
(SP 2.68 CLI) loads the record via the backtest repository and the artifact
via the result export, then calls :func:`build_run_audit`.

Pure core logic: only stdlib and the SP 2.2 domain types; never touches
storage or CLI code.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from types import MappingProxyType
from typing import Any

from harbor.core.backtest_domain import BacktestStatus, Currency, Market

_RESULT_SECTIONS = (
    "net_values",
    "positions",
    "trades",
    "dividends",
    "corporate_actions",
    "refused",
)


class AuditError(ValueError):
    """Raised when a run audit cannot be assembled (SP 2.66)."""


@dataclass(frozen=True)
class RunRecord:
    """A pure-core mirror of the persisted ``backtest_runs`` master row (SP 2.6)."""

    run_id: str
    config_hash: str
    config_snapshot: dict[str, Any]
    strategy: str
    strategy_version: str
    code_version: str
    data_cutoff: date
    status: BacktestStatus
    started_at: datetime
    finished_at: datetime | None = None
    error_summary: str | None = None

    def __post_init__(self) -> None:
        if not self.run_id:
            raise ValueError("Run record run_id must be non-empty.")
        if not self.config_hash:
            raise ValueError("Run record config_hash must be non-empty.")
        if not self.code_version:
            raise ValueError("Run record code_version must be non-empty.")
        if self.started_at.tzinfo is None:
            raise ValueError("Run record started_at must be timezone-aware.")
        if self.finished_at is not None and self.finished_at.tzinfo is None:
            raise ValueError("Run record finished_at must be timezone-aware.")


def _parse_date(value: Any, label: str) -> date | None:
    """Return an ISO date from a config value, or ``None`` when absent."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise AuditError(f"Invalid {label} {value!r} in the configuration snapshot.") from exc


def _parse_currency(value: Any) -> Currency | None:
    """Return a currency from a config value, or ``None`` when absent."""
    if value is None:
        return None
    try:
        return Currency(str(value))
    except ValueError as exc:
        raise AuditError(f"Unknown base currency {value!r} in the configuration snapshot.") from exc


def _parse_config(
    config: Mapping[str, Any],
) -> tuple[tuple[Market, ...], date | None, date | None, Currency | None, float | None]:
    """Extract markets, date range, base currency and initial capital from a config dict."""
    markets = tuple(Market(quota["market"]) for quota in config.get("market_quotas", ()))
    start = _parse_date(config.get("start_date"), "start_date")
    end = _parse_date(config.get("end_date"), "end_date")
    base = _parse_currency(config.get("base_currency"))
    initial = config.get("initial_capital")
    return (
        markets,
        start,
        end,
        base,
        (float(initial) if isinstance(initial, (int, float)) else None),
    )


@dataclass(frozen=True)
class RunAudit:
    """The audit record for one run: configuration, input range, artifacts, failure."""

    run_id: str
    status: BacktestStatus
    config_hash: str
    strategy: str
    strategy_version: str
    code_version: str
    markets: tuple[Market, ...]
    start_date: date | None
    end_date: date | None
    data_cutoff: date
    base_currency: Currency | None
    initial_capital: float | None
    started_at: datetime
    finished_at: datetime | None
    error_summary: str | None
    day_count: int | None
    reconciliation_failures: tuple[str, ...]
    warnings: tuple[Any, ...]
    artifact_present: bool
    result_counts: MappingProxyType[str, int]

    @property
    def failed(self) -> bool:
        """Whether the run ended in a failure state."""
        return self.status is BacktestStatus.FAILED

    @property
    def failure_reason(self) -> str | None:
        """Return the run's failure reason, else the first reconciliation failure."""
        if self.error_summary:
            return self.error_summary
        if self.reconciliation_failures:
            return self.reconciliation_failures[0]
        return None

    def readable(self) -> str:
        """Render the audit as a human-readable research summary."""
        markets = ", ".join(market.value for market in self.markets) or "—"
        start = self.start_date.isoformat() if self.start_date else "—"
        end = self.end_date.isoformat() if self.end_date else "—"
        base = self.base_currency.value if self.base_currency is not None else "—"
        initial = "—" if self.initial_capital is None else f"{self.initial_capital:,.2f}"
        finished = self.finished_at.isoformat() if self.finished_at is not None else "—"
        lines = [
            f"Run audit {self.run_id}:",
            f"  status: {self.status.value}",
            f"  strategy: {self.strategy} v{self.strategy_version}",
            f"  code version: {self.code_version}",
            f"  config hash: {self.config_hash}",
            f"  markets: {markets}",
            f"  input range: {start} -> {end} (cutoff {self.data_cutoff.isoformat()})",
            f"  base currency: {base}",
            f"  initial capital: {initial}",
            f"  started: {self.started_at.isoformat()}",
            f"  finished: {finished}",
            f"  artifacts: {'present' if self.artifact_present else 'absent'}",
        ]
        if self.artifact_present:
            counts = ", ".join(f"{key}={count}" for key, count in self.result_counts.items())
            lines.append(f"  result counts: {counts}")
            if self.day_count is not None:
                lines.append(f"  day count: {self.day_count}")
        if self.reconciliation_failures:
            lines.append("  reconciliation failures:")
            for failure in self.reconciliation_failures:
                lines.append(f"    - {failure}")
        if self.warnings:
            lines.append(f"  warnings ({len(self.warnings)}):")
            for warning in self.warnings:
                if isinstance(warning, Mapping):
                    lines.append(f"    - {warning.get('date')}: {warning.get('message')}")
                else:
                    lines.append(f"    - {warning}")
        reason = self.failure_reason
        if reason is not None:
            lines.append(f"  failure reason: {reason}")
        return "\n".join(lines)


def build_run_audit(
    record: RunRecord,
    *,
    artifact: dict[str, Any] | None = None,
) -> RunAudit:
    """Assemble the run audit from the persisted record and optional artifact.

    Args:
        record: The persisted run master record (SP 2.6).
        artifact: The SP 2.58 results artifact, when available.

    Returns:
        A :class:`RunAudit` combining the configuration, input range, artifact
        results and failure reasons.

    Raises:
        AuditError: If the artifact is not an SP 2.58 results artifact, or its
            run id does not match the record's run id.
    """
    if artifact is not None:
        if "run" not in artifact or "config" not in artifact or "net_values" not in artifact:
            raise AuditError("Expected an SP 2.58 results artifact.")
        artifact_run = artifact["run"]
        if artifact_run.get("run_id") != record.run_id:
            raise AuditError(
                f"Artifact run id {artifact_run.get('run_id')!r} does not match "
                f"record run id {record.run_id!r}."
            )
        config = artifact["config"]
        day_count = (
            int(artifact_run["day_count"]) if artifact_run.get("day_count") is not None else None
        )
        reconciliation_failures = tuple(artifact_run.get("reconciliation_failures", ()))
        warnings = tuple(artifact.get("warnings", ()))
        result_counts = MappingProxyType(
            {key: len(artifact.get(key, ())) for key in _RESULT_SECTIONS}
        )
    else:
        config = record.config_snapshot
        day_count = None
        reconciliation_failures = ()
        warnings = ()
        result_counts = MappingProxyType({key: 0 for key in _RESULT_SECTIONS})

    markets, start, end, base, initial = _parse_config(config)
    return RunAudit(
        run_id=record.run_id,
        status=record.status,
        config_hash=record.config_hash,
        strategy=record.strategy,
        strategy_version=record.strategy_version,
        code_version=record.code_version,
        markets=markets,
        start_date=start,
        end_date=end,
        data_cutoff=record.data_cutoff,
        base_currency=base,
        initial_capital=initial,
        started_at=record.started_at,
        finished_at=record.finished_at,
        error_summary=record.error_summary,
        day_count=day_count,
        reconciliation_failures=reconciliation_failures,
        warnings=warnings,
        artifact_present=artifact is not None,
        result_counts=result_counts,
    )
