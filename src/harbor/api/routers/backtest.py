"""Backtest run endpoints (read-only, MVP 5 / SP 5.2, SP 5.13-5.18).

Every route here is a GET over persisted state. The list's filters feed both the
page and the count, so ``total`` always describes the filtered set rather than
the whole table. Derived views — the net-value curve, performance metrics and
drawdown intervals — are produced by reusing the MVP 2 core functions
(:mod:`harbor.services.backtest_analytics`) instead of reimplementing the maths,
so a dashboard number cannot disagree with a CLI report.

Two things this module refuses to do: it never fabricates an analysis for a run
that has no data (it reports ``available: false`` with the reason), and it never
lets a display subsample influence a reported number (downsampling applies to
the chart payload only).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date

from fastapi import APIRouter, Depends, Query, Response

from harbor.api.deps import get_read_store
from harbor.api.downsample import largest_triangle_three_buckets
from harbor.api.errors import ApiError
from harbor.api.pagination import (
    Page,
    PageBounds,
    PageParams,
    build_page,
    page_bounds,
    page_params,
)
from harbor.api.read_store import ReadStore, Row
from harbor.api.redaction import redact_document
from harbor.api.schemas import (
    BacktestComparisonResponse,
    BacktestDrawdownResponse,
    BacktestMetricsResponse,
    BacktestReplayResponse,
    BacktestRunDetail,
    BacktestRunFilters,
    BacktestRunSummary,
    ComparisonPointView,
    ConsistencyIssueView,
    DrawdownEventView,
    FillPage,
    FillRow,
    NetValuePoint,
    NetValueSeries,
    PerformanceMetricsView,
    RejectedTradeResponse,
    RejectedTradeRow,
    RejectionReasonCount,
    ReplayManifestView,
    RunComparisonView,
    RunCounts,
    SiblingConsistencyView,
)
from harbor.api.security import require_readonly
from harbor.core.backtest_domain import BacktestStatus, Currency, Market, NetValue
from harbor.core.drawdown_events import DrawdownConfig, DrawdownError
from harbor.core.performance_metrics import MetricsError
from harbor.services.backtest import REPORT_FORMATS, REPORT_MEDIA_TYPES
from harbor.services.backtest_analytics import (
    BacktestAnalyticsError,
    DrawdownInterval,
    RunComparison,
    drawdown_events_from_net_values,
    metrics_from_net_values,
    net_values_from_rows,
    run_comparison,
)
from harbor.services.backtest_replay import MAX_SIBLINGS, BacktestReplayError
from harbor.storage.backtest_repositories import RUN_SORT_FIELDS, RUN_SORT_ORDERS

#: The list's default order, matching the pre-5.13 contract (newest first).
DEFAULT_RUN_SORT = "started_at"

_RUN_STATUSES = frozenset(status.value for status in BacktestStatus)

# Authentication runs as a router-level dependency so it is enforced *before*
# the per-request database connection is opened (SP 5.4).
router = APIRouter(
    prefix="/backtests",
    tags=["backtests"],
    dependencies=[Depends(require_readonly)],
)


@dataclass(frozen=True)
class RunQuery:
    """The run list's validated page, filters and sort (SP 5.13)."""

    page: PageParams
    status: str | None
    strategy: str | None
    data_cutoff_from: date | None
    data_cutoff_to: date | None

    @property
    def sort(self) -> str:
        """The sort field to apply, defaulting to the newest-first contract."""
        return self.page.sort or DEFAULT_RUN_SORT


def run_query(
    page: PageParams = Depends(page_params),
    status: str | None = Query(default=None, description="Exact run status to filter on."),
    strategy: str | None = Query(default=None, description="Exact strategy name to filter on."),
    data_cutoff_from: date | None = Query(
        default=None, description="Earliest data cutoff to include (inclusive)."
    ),
    data_cutoff_to: date | None = Query(
        default=None, description="Latest data cutoff to include (inclusive)."
    ),
) -> RunQuery:
    """Validate the run list's filters and sort field against what is supported.

    A mistyped status or sort field is a clear ``422`` rather than a silently
    empty page, which a reader would otherwise mistake for "there are no runs".

    Raises:
        ApiError: 422 for an unknown sort field, status or inverted date range.
    """
    if page.sort is not None and page.sort not in RUN_SORT_FIELDS:
        raise ApiError(
            status_code=422,
            code="invalid_sort",
            detail=f"sort must be one of {', '.join(RUN_SORT_FIELDS)}; got {page.sort!r}.",
        )
    if status is not None and status not in _RUN_STATUSES:
        raise ApiError(
            status_code=422,
            code="invalid_status",
            detail=f"status must be one of {', '.join(sorted(_RUN_STATUSES))}; got {status!r}.",
        )
    if (
        data_cutoff_from is not None
        and data_cutoff_to is not None
        and data_cutoff_from > data_cutoff_to
    ):
        raise ApiError(
            status_code=422,
            code="invalid_date_range",
            detail="data_cutoff_from must not be later than data_cutoff_to.",
        )
    return RunQuery(
        page=page,
        status=status,
        strategy=strategy,
        data_cutoff_from=data_cutoff_from,
        data_cutoff_to=data_cutoff_to,
    )


def _require_run(store: ReadStore, run_id: str) -> Row:
    """Return a run row, or raise the canonical 404 (SP 5.7)."""
    row = store.get_backtest_run(run_id)
    if row is None:
        raise ApiError(
            status_code=404,
            code="backtest_run_not_found",
            detail=f"No backtest run {run_id!r} exists.",
        )
    return row


#: Reason reported when a run has net values missing entirely (a failed run).
NO_NET_VALUES_REASON = "This run has no persisted net values."


def _usable_series(rows: list[Row]) -> tuple[NetValue, ...] | None:
    """Rebuild a run's net-value series, or return ``None`` when there is none.

    An empty series is a legitimate state of a run — a failed backtest never
    reaches its first valuation — so it is reported as absent. An *ambiguous*
    series is a data condition the client cannot act on, so it is refused loudly
    rather than charted. Sharing this between the curve and the derived
    endpoints keeps them from disagreeing about whether the data is usable.

    Raises:
        ApiError: 422 when the series exists but cannot be represented.
    """
    if not rows:
        return None
    try:
        return net_values_from_rows(rows)
    except BacktestAnalyticsError as error:
        raise ApiError(
            status_code=422,
            code="net_value_series_unusable",
            detail=str(error),
        ) from error


@router.get("", response_model=Page[BacktestRunSummary], summary="List backtest runs")
def list_backtest_runs(
    query: RunQuery = Depends(run_query),
    store: ReadStore = Depends(get_read_store),
) -> Page[BacktestRunSummary]:
    """Return one page of backtest runs, filtered and sorted (SP 5.13)."""
    rows, total = store.list_backtest_runs(
        limit=query.page.limit,
        offset=query.page.offset,
        status=query.status,
        strategy=query.strategy,
        data_cutoff_from=query.data_cutoff_from,
        data_cutoff_to=query.data_cutoff_to,
        sort=query.sort,
        order=query.page.order.value,
    )
    items = [BacktestRunSummary.model_validate(row) for row in rows]
    return build_page(items, total=total, params=query.page)


# Declared before ``/{run_id}`` so the literal path wins the match.
@router.get(
    "/filters",
    response_model=BacktestRunFilters,
    summary="Filter and sort values the run list accepts",
)
def read_run_filters(store: ReadStore = Depends(get_read_store)) -> BacktestRunFilters:
    """Return the filter values present in the data and the sort allow-list (SP 5.13)."""
    statuses, strategies = store.run_filter_options()
    return BacktestRunFilters(
        statuses=statuses,
        strategies=strategies,
        sort_fields=list(RUN_SORT_FIELDS),
        sort_orders=list(RUN_SORT_ORDERS),
    )


# Declared before ``/{run_id}`` for the same reason as ``/filters``: a literal
# path must win the match, or ``compare`` would be read as a run id.
@router.get(
    "/compare",
    response_model=BacktestComparisonResponse,
    summary="Compare several runs' curves and metrics",
)
def compare_backtest_runs(
    run_ids: str = Query(
        ...,
        description="Comma-separated backtest run ids to compare (2 to 5, unique).",
    ),
    store: ReadStore = Depends(get_read_store),
) -> BacktestComparisonResponse:
    """Return several runs' rebased curves and metrics side by side (SP 5.23).

    The curves are cumulative returns measured from each run's own first net
    value — the same baseline SP 5.16 uses — because a return is dimensionless
    while a net value is not: laying an HKD run beside a USD run would compare
    amounts that mean different things, and converting them would need an FX
    rate this data does not carry. Anything that makes the comparison less
    like-for-like (currencies, date ranges, data cutoffs, code versions) is
    reported as a warning instead of being silently averaged over.

    Runs are deliberately not subsampled here: a chart of several curves cannot
    show a subsample honestly without also aligning the runs, so the number of
    runs is capped instead and every point is real.
    """
    requested = [run_id.strip() for run_id in run_ids.split(",") if run_id.strip()]
    if len(requested) < 2:
        raise ApiError(
            status_code=422,
            code="too_few_runs",
            detail="At least two run ids are required to compare.",
        )
    if len(requested) > MAX_COMPARISON_RUNS:
        raise ApiError(
            status_code=422,
            code="too_many_runs",
            detail=f"At most {MAX_COMPARISON_RUNS} runs can be compared at once.",
        )
    duplicates = sorted({run_id for run_id in requested if requested.count(run_id) > 1})
    if duplicates:
        raise ApiError(
            status_code=422,
            code="duplicate_run_ids",
            detail=f"Each run may appear once; repeated: {', '.join(duplicates)}.",
        )

    rows_by_run: list[Row] = []
    for run_id in requested:
        rows_by_run.append(_require_run(store, run_id))

    comparisons = [run_comparison(run_id, store.list_net_values(run_id)) for run_id in requested]
    return BacktestComparisonResponse(
        runs=[
            _comparison_view(row, comparison)
            for row, comparison in zip(rows_by_run, comparisons, strict=True)
        ],
        warnings=list(_comparison_warnings(comparisons)),
        notes=list(COMPARISON_NOTES),
    )


#: How many runs one comparison request may ask for.
MAX_COMPARISON_RUNS = 5

COMPARISON_NOTES = (
    "曲线以各运行自身首个净值点为基准，归一为累计收益；基准与 SP 5.16 的累计收益一致，"
    "因此不同本金规模可以直接比较曲线形状与幅度。",
    "不提供跨币种金额比较：本项目数据没有汇率表，任何隐式 1:1 换算都会被拒绝，"
    "所以比较的是收益率而不是净值金额。",
    "单指标最优不等于策略更优；样本期、市场与标的不同时，指标之间不具备可比性。",
)


def _comparison_view(row: Row, comparison: RunComparison) -> RunComparisonView:
    """Render one run's comparison entry as the published schema."""
    return RunComparisonView(
        run_id=comparison.run_id,
        status=str(row["status"]),
        strategy=str(row["strategy"]),
        strategy_version=str(row["strategy_version"]),
        code_version=str(row["code_version"]),
        data_cutoff=row["data_cutoff"],
        currency=comparison.currency.value if comparison.currency is not None else None,
        start_date=comparison.start_date,
        end_date=comparison.end_date,
        point_count=comparison.point_count,
        available=comparison.available,
        unavailable_reason=comparison.unavailable_reason,
        metrics=(
            PerformanceMetricsView.model_validate(asdict(comparison.metrics))
            if comparison.metrics is not None
            else None
        ),
        points=[
            ComparisonPointView(
                as_of_date=point.as_of_date, cumulative_return=point.cumulative_return
            )
            for point in comparison.points
        ],
    )


def _currency_label(currency: Currency | None) -> str:
    """Render a run's currency for a warning message."""
    return currency.value if currency is not None else "未记录"


def _comparison_warnings(comparisons: Sequence[RunComparison]) -> tuple[str, ...]:
    """Report what makes these runs not strictly like-for-like (SP 5.23).

    Each difference is stated as a fact about the selection. A reader comparing
    runs across currencies, periods or code versions is doing something
    legitimate, but the page must not let them believe the runs are equivalent.
    """
    warnings: list[str] = []

    currencies = sorted({_currency_label(item.currency) for item in comparisons})
    if len(currencies) > 1:
        warnings.append(
            f"所选运行的基准币种不同（{'、'.join(currencies)}）；曲线为累计收益，"
            "币种不同时不可据此比较金额规模。"
        )

    ranges = sorted(
        {
            f"{item.start_date}~{item.end_date}"
            for item in comparisons
            if item.start_date is not None and item.end_date is not None
        }
    )
    if len(ranges) > 1:
        warnings.append(
            f"所选运行的时间区间不同（{'、'.join(ranges)}）；横轴为真实日期，未做区间对齐。"
        )

    missing = [item.run_id for item in comparisons if not item.points]
    if missing:
        warnings.append(
            f"{len(missing)} 个运行没有可绘制的净值序列（{'、'.join(missing)}）；"
            "它们只出现在列表与指标中，不在曲线上。"
        )

    no_metrics = [item.run_id for item in comparisons if item.metrics is None and item.points]
    if no_metrics:
        warnings.append(
            f"{len(no_metrics)} 个运行有净值曲线但缺少可计算指标（{'、'.join(no_metrics)}）；"
            "对应指标为空原因见该运行详情。"
        )
    return tuple(warnings)


@router.get("/{run_id}", response_model=BacktestRunDetail, summary="Show one backtest run")
def show_backtest_run(
    run_id: str,
    store: ReadStore = Depends(get_read_store),
) -> BacktestRunDetail:
    """Return one backtest run with its redacted config and artifact counts (SP 5.14)."""
    row = _require_run(store, run_id)
    payload = redact_document(row)
    payload["counts"] = RunCounts(**store.backtest_run_stats(run_id))
    return BacktestRunDetail.model_validate(payload)


@router.get(
    "/{run_id}/net-values",
    response_model=NetValueSeries,
    summary="A run's net-value curve",
)
def read_net_values(
    run_id: str,
    max_points: int | None = Query(
        default=None,
        ge=3,
        le=5000,
        description=(
            "Subsample the curve for display when it is longer than this. "
            "Never affects reported metrics or drawdown intervals."
        ),
    ),
    store: ReadStore = Depends(get_read_store),
) -> NetValueSeries:
    """Return a run's net-value curve for charting (SP 5.15).

    A run that failed before its first valuation has no curve; that is reported
    as an empty series rather than as an error, because it is a legitimate
    state of a run (SP 5.14 shows the failure reason).
    """
    _require_run(store, run_id)
    snapshots = _usable_series(store.list_net_values(run_id))
    if snapshots is None:
        return NetValueSeries(run_id=run_id)

    full_count = len(snapshots)
    selected = list(snapshots)

    if max_points is not None and full_count > max_points:
        # LTTB picks existing points, so the subsample is a subset of the real
        # series rather than an interpolation of it.
        by_ordinal = {snapshot.as_of_date.toordinal(): snapshot for snapshot in snapshots}
        points = [
            (float(snapshot.as_of_date.toordinal()), snapshot.total_value) for snapshot in snapshots
        ]
        selected = [
            by_ordinal[int(x)] for x, _value in largest_triangle_three_buckets(points, max_points)
        ]

    return NetValueSeries(
        run_id=run_id,
        currency=snapshots[0].currency.value,
        point_count=full_count,
        returned_count=len(selected),
        downsampled=len(selected) < full_count,
        first_date=snapshots[0].as_of_date,
        last_date=snapshots[-1].as_of_date,
        points=[
            NetValuePoint(
                as_of_date=snapshot.as_of_date,
                currency=snapshot.currency.value,
                cash=snapshot.cash,
                securities_value=snapshot.securities_value,
                fees_paid=snapshot.fees_paid,
            )
            for snapshot in selected
        ],
    )


@router.get(
    "/{run_id}/metrics",
    response_model=BacktestMetricsResponse,
    summary="Performance metrics computed from persisted net values",
)
def read_metrics(
    run_id: str,
    store: ReadStore = Depends(get_read_store),
) -> BacktestMetricsResponse:
    """Return a run's return and risk metrics (SP 5.16).

    The numbers come from the same core function the CLI report uses, over the
    persisted net values — the metrics table is not populated by the backtest
    service, so recomputing from net values is what keeps this honest and
    identical to ``harbor-cli backtest show``.

    A degenerate series (too short, or zero volatility) has no Sharpe ratio;
    that is reported as ``available: false`` with the reason rather than as a
    fabricated zero.
    """
    _require_run(store, run_id)
    snapshots = _usable_series(store.list_net_values(run_id))
    if snapshots is None:
        return BacktestMetricsResponse(
            run_id=run_id, available=False, unavailable_reason=NO_NET_VALUES_REASON
        )
    currency = snapshots[0].currency.value
    try:
        metrics = metrics_from_net_values(snapshots)
    except MetricsError as error:
        return BacktestMetricsResponse(
            run_id=run_id,
            available=False,
            unavailable_reason=str(error),
            currency=currency,
        )
    return BacktestMetricsResponse(
        run_id=run_id,
        available=True,
        currency=currency,
        metrics=PerformanceMetricsView.model_validate(asdict(metrics)),
    )


@router.get(
    "/{run_id}/drawdowns",
    response_model=BacktestDrawdownResponse,
    summary="Drawdown intervals at the configured thresholds",
)
def read_drawdowns(
    run_id: str,
    store: ReadStore = Depends(get_read_store),
) -> BacktestDrawdownResponse:
    """Return a run's 5% / 8% / 10% drawdown intervals (SP 5.17).

    Thresholds, depth and recovery dates come from the CLI's own core function.
    The trough's holdings and exposure are not persisted, so
    ``position_detail_available`` is false on every event rather than the field
    being filled with a guess.
    """
    _require_run(store, run_id)
    snapshots = _usable_series(store.list_net_values(run_id))
    if snapshots is None:
        return BacktestDrawdownResponse(
            run_id=run_id, available=False, unavailable_reason=NO_NET_VALUES_REASON
        )
    config = DrawdownConfig()
    try:
        intervals = drawdown_events_from_net_values(snapshots, config=config)
    except DrawdownError as error:
        return BacktestDrawdownResponse(
            run_id=run_id,
            available=False,
            unavailable_reason=str(error),
            currency=snapshots[0].currency.value,
        )
    return BacktestDrawdownResponse(
        run_id=run_id,
        available=True,
        currency=snapshots[0].currency.value,
        thresholds=list(config.thresholds),
        events=[_drawdown_event(interval) for interval in intervals],
    )


@router.get(
    "/{run_id}/replay",
    response_model=BacktestReplayResponse,
    summary="A run's replay manifest and its agreement with runs sharing its inputs",
)
def read_replay_consistency(
    run_id: str,
    store: ReadStore = Depends(get_read_store),
) -> BacktestReplayResponse:
    """Return a run's replay manifest and the sibling consistency check (SP 5.21).

    The manifest is *derived* from the persisted run and config snapshot rather
    than stored, so what is shown is what the data still supports. Sibling runs
    are those recording the same config hash, code version and data cutoff —
    runs that claim to be the same experiment — and each is compared against
    this one with the CLI's own consistency check.

    A fingerprint alone cannot prove two runs are identical: it covers the
    configuration, not the data the runs actually read. Both facts are therefore
    reported, and a disagreement is described as a difference to investigate
    rather than a failure.
    """
    _require_run(store, run_id)
    try:
        consistency = store.replay_consistency(run_id, max_siblings=MAX_SIBLINGS)
    except BacktestReplayError as error:
        raise ApiError(
            status_code=422,
            code="replay_manifest_unavailable",
            detail=str(error),
        ) from error
    manifest = consistency.manifest
    return BacktestReplayResponse(
        run_id=run_id,
        manifest=ReplayManifestView(
            run_id=manifest.run_id,
            config_hash=manifest.config_hash,
            code_version=manifest.code_version,
            start_date=manifest.start_date,
            end_date=manifest.end_date,
            data_cutoff=manifest.data_cutoff,
            fx_source=manifest.fx_source,
            calendar_version=manifest.calendar_version,
            random_seed=manifest.random_seed,
            fingerprint=manifest.fingerprint,
        ),
        siblings=[
            SiblingConsistencyView(
                run_id=sibling.run_id,
                status=sibling.status,
                same_status=sibling.same_status,
                consistent=sibling.consistent,
                outcome_agrees=sibling.outcome_agrees,
                difference_count=sibling.difference_count,
                differences=[
                    ConsistencyIssueView(
                        section=issue.section,
                        location=issue.location,
                        expected=issue.expected,
                        actual=issue.actual,
                    )
                    for issue in sibling.differences
                ],
            )
            for sibling in consistency.siblings
        ],
        sibling_total=consistency.sibling_total,
        truncated=consistency.truncated,
        notes=list(consistency.notes),
    )


@router.get(
    "/{run_id}/report",
    summary="Download a run's research report",
    response_class=Response,
    responses={
        200: {
            "content": {media_type: {} for media_type in sorted(REPORT_MEDIA_TYPES.values())},
            "description": "The rendered report in the requested format.",
        }
    },
)
def download_report(
    run_id: str,
    format: str = Query(
        default="json",
        description="Report format; see report_formats in /api/v1/version.",
    ),
    store: ReadStore = Depends(get_read_store),
) -> Response:
    """Render a run's report server-side and return it as a download (SP 5.22).

    The document is produced by the same renderer ``harbor-cli backtest report``
    writes, so a downloaded file and a terminal report cannot disagree about a
    number. Rendering happens on the server: the browser never recomputes a
    figure, and the only formats offered are the ones this API declares in
    ``/api/v1/version``.
    """
    if format not in REPORT_FORMATS:
        raise ApiError(
            status_code=422,
            code="invalid_report_format",
            detail=(
                f"format must be one of {', '.join(REPORT_FORMATS)}; got {format!r}. "
                "The supported formats are published at /api/v1/version."
            ),
        )
    _require_run(store, run_id)
    report = store.render_report(run_id, format)
    return Response(
        content=report.content,
        media_type=report.media_type,
        headers={"Content-Disposition": f'attachment; filename="{report.filename}"'},
    )


def _drawdown_event(interval: DrawdownInterval) -> DrawdownEventView:
    """Render one analytics drawdown interval as the published schema."""
    return DrawdownEventView(
        threshold=interval.threshold,
        start_date=interval.start_date,
        peak_date=interval.peak_date,
        peak_value=interval.peak_value,
        trough_date=interval.trough_date,
        trough_value=interval.trough_value,
        depth=interval.depth,
        recovered_date=interval.recovered_date,
    )


@router.get(
    "/{run_id}/fills",
    response_model=FillPage,
    summary="A run's filled orders",
)
def read_fills(
    run_id: str,
    bounds: PageBounds = Depends(page_bounds),
    market: Market | None = Query(default=None, description="Restrict to one market."),
    symbol: str | None = Query(default=None, max_length=32, description="Restrict to one symbol."),
    store: ReadStore = Depends(get_read_store),
) -> FillPage:
    """Return one page of a run's fills, optionally by market and symbol (SP 5.18)."""
    _require_run(store, run_id)
    rows, total = store.list_fills(
        run_id,
        limit=bounds.limit,
        offset=bounds.offset,
        market=market.value if market is not None else None,
        symbol=symbol,
    )
    consumed = min(bounds.offset + len(rows), total)
    return FillPage(
        run_id=run_id,
        items=[FillRow.model_validate(row) for row in rows],
        total=total,
        limit=bounds.limit,
        offset=bounds.offset,
        next_offset=consumed if consumed < total else None,
    )


@router.get(
    "/{run_id}/rejected-trades",
    response_model=RejectedTradeResponse,
    summary="A run's refused trades and why they were refused",
)
def read_rejected_trades(
    run_id: str,
    bounds: PageBounds = Depends(page_bounds),
    market: Market | None = Query(default=None, description="Restrict to one market."),
    symbol: str | None = Query(default=None, max_length=32, description="Restrict to one symbol."),
    store: ReadStore = Depends(get_read_store),
) -> RejectedTradeResponse:
    """Return a page of refused trades plus the full-set reason distribution (SP 5.18).

    The distribution is aggregated in the database over every matching row, not
    over the page, so charting it cannot misrepresent the whole run as the part
    that happens to be on screen.
    """
    _require_run(store, run_id)
    market_value = market.value if market is not None else None
    rows, total = store.list_rejected_trades(
        run_id,
        limit=bounds.limit,
        offset=bounds.offset,
        market=market_value,
        symbol=symbol,
    )
    consumed = min(bounds.offset + len(rows), total)
    return RejectedTradeResponse(
        run_id=run_id,
        items=[RejectedTradeRow.model_validate(row) for row in rows],
        total=total,
        limit=bounds.limit,
        offset=bounds.offset,
        next_offset=consumed if consumed < total else None,
        reasons=[
            RejectionReasonCount(reason=reason, count=count)
            for reason, count in store.rejected_reason_counts(
                run_id, market=market_value, symbol=symbol
            )
        ],
    )
