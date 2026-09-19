"""Paper run endpoints (read-only, MVP 5 / SP 5.2).

These expose the simulated-account loop for monitoring only. The API can never
start, stop, signal or approve a paper run (SP 5.3): those commands stay in the
CLI, where they are authenticated by the operator's own session.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from harbor.api.deps import get_read_store
from harbor.api.errors import ApiError
from harbor.api.pagination import Page, PageParams, build_page, page_params
from harbor.api.read_store import ReadStore
from harbor.api.redaction import redact_document
from harbor.api.schemas import PaperRunCounts, PaperRunDetail, PaperRunSummary
from harbor.api.security import require_readonly

router = APIRouter(
    prefix="/paper-runs",
    tags=["paper"],
    dependencies=[Depends(require_readonly)],
)


@router.get("", response_model=Page[PaperRunSummary], summary="List paper runs")
def list_paper_runs(
    params: PageParams = Depends(page_params),
    store: ReadStore = Depends(get_read_store),
) -> Page[PaperRunSummary]:
    """Return one page of paper runs, newest first."""
    rows, total = store.list_paper_runs(limit=params.limit, offset=params.offset)
    items = [PaperRunSummary.model_validate(row) for row in rows]
    return build_page(items, total=total, params=params)


@router.get("/{run_id}", response_model=PaperRunDetail, summary="Show one paper run")
def show_paper_run(
    run_id: str,
    store: ReadStore = Depends(get_read_store),
) -> PaperRunDetail:
    """Return one paper run with its redacted config and artifact counts."""
    row = store.get_paper_run(run_id)
    if row is None:
        raise ApiError(
            status_code=404,
            code="paper_run_not_found",
            detail=f"No paper run {run_id!r} exists.",
        )
    payload = redact_document(row)
    payload["counts"] = PaperRunCounts(**store.paper_run_stats(run_id))
    return PaperRunDetail.model_validate(payload)
