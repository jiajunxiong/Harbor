"""Response schemas for the read-only API (MVP 5 / SP 5.9).

Fields are named after the persisted columns so the dashboard maps one-to-one
onto the database, and every timestamp keeps its timezone. ``extra="ignore"``
lets a router feed a whole row in without leaking columns that are not part of
the published contract.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

API_NAME = "harbor-api"
API_VERSION = "v1"


class _Schema(BaseModel):
    """Base for API responses: unknown columns never leak (SP 5.9)."""

    model_config = ConfigDict(extra="ignore")


class ApiInfo(_Schema):
    """Identity and capability of this API instance (SP 5.3)."""

    name: str = API_NAME
    version: str
    api_version: str = API_VERSION
    read_only: bool = True
    auth_required: bool = False


class HealthStatus(_Schema):
    """Liveness of the API and its database dependency."""

    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable"]
    read_only: bool = True
    version: str


class RunCounts(_Schema):
    """Artifact counts backing a run detail view (SP 5.8)."""

    net_value_points: int = 0
    positions: int = 0
    fills: int = 0
    metrics: int = 0
    rejected_trades: int = 0


class BacktestRunSummary(_Schema):
    """One backtest run as shown in the dashboard list."""

    run_id: str
    status: str
    strategy: str
    strategy_version: str
    code_version: str
    config_hash: str
    data_cutoff: date
    started_at: datetime
    finished_at: datetime | None = None
    error_summary: str | None = None
    resume_of: str | None = None


class BacktestRunDetail(BacktestRunSummary):
    """A backtest run plus its redacted configuration and artifact counts."""

    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    counts: RunCounts = Field(default_factory=RunCounts)


class ValidationRunSummary(_Schema):
    """One validation run as shown in the dashboard list."""

    run_id: str
    status: str
    code_version: str
    config_hash: str
    test_set_id: str | None = None
    created_at: datetime
    updated_at: datetime | None = None
    error_summary: str | None = None


class ValidationConclusion(_Schema):
    """A recorded out-of-sample conclusion (SP 3.58).

    Conclusions never carry a return promise, and the limitations travel with
    the verdict so a dashboard cell cannot show the outcome alone.
    """

    conclusion: Literal["QUALIFIED", "NOT_QUALIFIED", "INCONCLUSIVE"]
    rule_version: str
    created_at: datetime
    limitations: list[dict[str, Any]] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)


class ValidationRunDetail(ValidationRunSummary):
    """A validation run plus its frozen fingerprint, conclusion and warnings."""

    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    dataset_fingerprint: str | None = None
    conclusion: ValidationConclusion | None = None
    warning_count: int = 0
    warnings_by_severity: dict[str, int] = Field(default_factory=dict)


class PaperRunSummary(_Schema):
    """One paper run as shown in the dashboard list."""

    run_id: str
    status: str
    strategy: str
    strategy_version: str
    markets: list[str] = Field(default_factory=list)
    base_currency: str
    code_version: str
    dataset_fingerprint: str
    created_at: datetime
    started_at: datetime | None = None
    stopped_at: datetime | None = None


class PaperRunCounts(_Schema):
    """Artifact counts backing a paper-run detail view (SP 5.8)."""

    orders: int = 0
    fills: int = 0
    net_value_points: int = 0
    approvals: int = 0
    circuit_breakers: int = 0
    reconciliation_differences: int = 0


class PaperRunDetail(PaperRunSummary):
    """A paper run plus its redacted configuration and artifact counts."""

    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    counts: PaperRunCounts = Field(default_factory=PaperRunCounts)


class IngestionSummary(_Schema):
    """The most recent ingestion run touching a market."""

    run_id: str
    status: Literal["running", "completed", "failed"]
    source: str
    start_time: datetime
    end_time: datetime | None = None
    records_processed: int = 0


class QualitySummary(_Schema):
    """Data-quality facts the dashboard shows for one market."""

    market: Literal["HK", "US"]
    security_count: int = 0
    bar_count: int = 0
    latest_bar_date: date | None = None
    open_issue_count: int = 0
    issues_by_severity: dict[str, int] = Field(default_factory=dict)
    latest_ingestion: IngestionSummary | None = None
