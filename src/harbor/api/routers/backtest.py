"""Backtest run endpoints (read-only, MVP 5 / SP 5.2).

The list is always paginated and always ordered newest-first, so the
dashboard can page through a large history without unbounded queries (SP 5.6).
The detail view redacts the configuration snapshot before it leaves the
process (SP 5.5).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from harbor.api.deps import get_read_store
from harbor.api.errors import ApiError
from harbor.api.pagination import Page, PageParams, build_page, page_params
from harbor.api.read_store import ReadStore
from harbor.api.redaction import redact_document
from harbor.api.schemas import BacktestRunDetail, BacktestRunSummary, RunCounts
from harbor.api.security import require_readonly

# Authentication runs as a router-level dependency so it is enforced *before*
# the per-request database connection is opened (SP 5.4).
router = APIRouter(
    prefix="/backtests",
    tags=["backtests"],
    dependencies=[Depends(require_readonly)],
)


@router.get("", response_model=Page[BacktestRunSummary], summary="List backtest runs")
def list_backtest_runs(
    params: PageParams = Depends(page_params),
    store: ReadStore = Depends(get_read_store),
) -> Page[BacktestRunSummary]:
    """Return one page of backtest runs, newest first."""
    rows, total = store.list_backtest_runs(limit=params.limit, offset=params.offset)
    items = [BacktestRunSummary.model_validate(row) for row in rows]
    return build_page(items, total=total, params=params)


@router.get("/{run_id}", response_model=BacktestRunDetail, summary="Show one backtest run")
def show_backtest_run(
    run_id: str,
    store: ReadStore = Depends(get_read_store),
) -> BacktestRunDetail:
    """Return one backtest run with its redacted config and artifact counts."""
    row = store.get_backtest_run(run_id)
    if row is None:
        raise ApiError(
            status_code=404,
            code="backtest_run_not_found",
            detail=f"No backtest run {run_id!r} exists.",
        )
    payload = redact_document(row)
    payload["counts"] = RunCounts(**store.backtest_run_stats(run_id))
    return BacktestRunDetail.model_validate(payload)
