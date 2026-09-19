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
    """Identity and capability of this API instance (SP 5.3).

    ``report_formats`` is published here rather than duplicated in the frontend
    so a client cannot offer a download the API would reject (SP 5.22).
    """

    name: str = API_NAME
    version: str
    api_version: str = API_VERSION
    read_only: bool = True
    auth_required: bool = False
    report_formats: list[str] = Field(default_factory=list)


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


class BacktestRunFilters(_Schema):
    """The filter and sort values a client may use on the run list (SP 5.13).

    The statuses and strategies are the ones actually present in the table, so a
    menu built from this cannot offer a filter that matches nothing. The sort
    fields are the server's allow-list, so the UI never has to guess which
    columns can be ordered.
    """

    statuses: list[str] = Field(default_factory=list)
    strategies: list[str] = Field(default_factory=list)
    sort_fields: list[str] = Field(default_factory=list)
    sort_orders: list[str] = Field(default_factory=list)


class NetValuePoint(_Schema):
    """One daily net-value snapshot as persisted (SP 2.7)."""

    as_of_date: date
    currency: str
    cash: float
    securities_value: float
    fees_paid: float


class NetValueSeries(_Schema):
    """A run's net-value curve, possibly subsampled for display (SP 5.15).

    ``point_count`` is the full series length and ``returned_count`` what this
    response carries, so a client can state plainly that a curve is a subsample
    instead of presenting a smoothed line as the raw series. Metrics and
    drawdown intervals are computed by the server on the *full* series.

    A run that failed before its first valuation has no series at all, which is
    a legitimate state rather than an error: ``point_count`` is then 0.
    """

    run_id: str
    currency: str | None = None
    point_count: int = 0
    returned_count: int = 0
    downsampled: bool = False
    first_date: date | None = None
    last_date: date | None = None
    points: list[NetValuePoint] = Field(default_factory=list)


class PerformanceMetricsView(_Schema):
    """Return and risk metrics over a run's net values (SP 2.53, SP 5.16)."""

    start_date: date
    end_date: date
    periods: int
    cumulative_return: float
    annualized_return: float
    annualized_volatility: float
    max_drawdown: float
    sharpe_ratio: float
    calmar_ratio: float
    downside_deviation: float


class BacktestMetricsResponse(_Schema):
    """A run's metrics, or an explicit statement that they do not exist.

    A failed run has no net values and therefore no metrics, and a degenerate
    series (for example one with zero volatility) has no Sharpe ratio. Both are
    reported as ``available: false`` with the reason, so the dashboard can show
    *why* rather than an empty card or a fabricated zero.
    """

    run_id: str
    source: Literal["persisted_net_values"] = "persisted_net_values"
    available: bool = False
    unavailable_reason: str | None = None
    currency: str | None = None
    metrics: PerformanceMetricsView | None = None


class DrawdownEventView(_Schema):
    """One threshold-triggered drawdown interval (SP 2.56, SP 5.17).

    ``position_detail_available`` is false because position-level values and FX
    P&L are not persisted, so the event cannot carry the trough's holdings or
    exposure. The interval, depth and recovery date are exact.
    """

    threshold: float
    start_date: date
    peak_date: date
    peak_value: float
    trough_date: date
    trough_value: float
    depth: float
    recovered_date: date | None = None
    position_detail_available: bool = False


class BacktestDrawdownResponse(_Schema):
    """A run's drawdown intervals, or an explicit statement that none exist (SP 5.17)."""

    run_id: str
    available: bool = False
    unavailable_reason: str | None = None
    currency: str | None = None
    thresholds: list[float] = Field(default_factory=list)
    events: list[DrawdownEventView] = Field(default_factory=list)


class FillRow(_Schema):
    """One executed order (成交) as persisted (SP 2.7)."""

    trade_date: date
    market: str
    symbol: str
    side: str
    quantity: float
    price: float
    fee: float
    currency: str
    order_ref: str


class FillPage(_Schema):
    """A page of a run's fills plus the filtered total (SP 5.18)."""

    run_id: str
    items: list[FillRow] = Field(default_factory=list)
    total: int = 0
    limit: int = 0
    offset: int = 0
    next_offset: int | None = None


class RejectedTradeRow(_Schema):
    """One refused trade with its reason (SP 2.41, SP 2.7)."""

    market: str
    symbol: str
    side: str | None = None
    quantity: float | None = None
    reason: str
    order_ref: str | None = None


class RejectionReasonCount(_Schema):
    """How often one refusal reason occurred."""

    reason: str
    count: int


class RejectedTradeResponse(_Schema):
    """A page of refused trades plus the full-set reason distribution (SP 5.18).

    The distribution is aggregated over every matching row, not over the page,
    so a chart of it cannot describe only the rows that happen to be on screen.
    """

    run_id: str
    items: list[RejectedTradeRow] = Field(default_factory=list)
    total: int = 0
    limit: int = 0
    offset: int = 0
    next_offset: int | None = None
    reasons: list[RejectionReasonCount] = Field(default_factory=list)


# -- SP 5.21: replay manifest and consistency ---------------------------


class ReplayManifestView(_Schema):
    """A run's replay manifest (SP 2.61), with its input fingerprint.

    ``fx_source``, ``calendar_version`` and ``random_seed`` are unset for a
    backtest run because the columns are not persisted — the dashboard says so
    rather than presenting an unset value as a fact about the run.
    """

    run_id: str
    config_hash: str
    code_version: str
    start_date: date
    end_date: date
    data_cutoff: date
    fx_source: str | None = None
    calendar_version: str | None = None
    random_seed: int | None = None
    fingerprint: str


class ConsistencyIssueView(_Schema):
    """One located difference between two runs' results (SP 2.62)."""

    section: str
    location: str
    expected: str
    actual: str


class SiblingConsistencyView(_Schema):
    """Whether another run with the same inputs produced the same results.

    ``consistent`` is the SP 2.62 verdict over the result sections only; two runs
    that both produced nothing agree there trivially, so ``same_status`` and
    ``outcome_agrees`` are reported separately rather than letting a vacuous
    agreement read as a match.
    """

    run_id: str
    status: str
    same_status: bool = False
    consistent: bool
    outcome_agrees: bool = False
    difference_count: int = 0
    #: Capped at the server's limit; ``difference_count`` is always exact.
    differences: list[ConsistencyIssueView] = Field(default_factory=list)


class BacktestReplayResponse(_Schema):
    """A run's replay manifest plus the runs claiming the same inputs (SP 5.21)."""

    run_id: str
    manifest: ReplayManifestView
    siblings: list[SiblingConsistencyView] = Field(default_factory=list)
    #: How many runs share these inputs, so a truncated list is visible as such.
    sibling_total: int = 0
    truncated: bool = False
    notes: list[str] = Field(default_factory=list)


# -- SP 5.23: multi-run comparison --------------------------------------


class ComparisonPointView(_Schema):
    """One rebased point of a run's curve (dimensionless, so runs are comparable)."""

    as_of_date: date
    cumulative_return: float


class RunComparisonView(_Schema):
    """One run's stance in a multi-run comparison (SP 5.23).

    ``points`` is a cumulative *return* series rather than a net-value series:
    net values carry a currency and an initial capital, so plotting an HKD run
    against a USD run would compare amounts that mean different things.
    """

    run_id: str
    status: str
    strategy: str
    strategy_version: str
    code_version: str
    data_cutoff: date
    currency: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    point_count: int = 0
    #: Scoreable metrics exist; the curve can still be present when they do not.
    available: bool = False
    unavailable_reason: str | None = None
    metrics: PerformanceMetricsView | None = None
    points: list[ComparisonPointView] = Field(default_factory=list)


class BacktestComparisonResponse(_Schema):
    """Several runs' curves and metrics side by side, with the caveats (SP 5.23)."""

    runs: list[RunComparisonView] = Field(default_factory=list)
    #: Reasons the runs are not strictly like-for-like (currency, range, code).
    warnings: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
