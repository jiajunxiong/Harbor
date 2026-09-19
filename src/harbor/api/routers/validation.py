"""Validation run endpoints (read-only, MVP 5 / SP 5.2).

A validation detail carries the frozen dataset fingerprint and the recorded
out-of-sample conclusion **together with its limitations**, so a dashboard
cannot render a verdict without the caveats that belong to it (SP 3.58).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from harbor.api.deps import get_read_store
from harbor.api.errors import ApiError
from harbor.api.pagination import Page, PageParams, build_page, page_params
from harbor.api.read_store import ReadStore
from harbor.api.redaction import redact_document
from harbor.api.schemas import ValidationRunDetail, ValidationRunSummary
from harbor.api.security import require_readonly

router = APIRouter(
    prefix="/validations",
    tags=["validations"],
    dependencies=[Depends(require_readonly)],
)


@router.get("", response_model=Page[ValidationRunSummary], summary="List validation runs")
def list_validation_runs(
    params: PageParams = Depends(page_params),
    store: ReadStore = Depends(get_read_store),
) -> Page[ValidationRunSummary]:
    """Return one page of validation runs, newest first."""
    rows, total = store.list_validation_runs(limit=params.limit, offset=params.offset)
    items = [ValidationRunSummary.model_validate(row) for row in rows]
    return build_page(items, total=total, params=params)


@router.get("/{run_id}", response_model=ValidationRunDetail, summary="Show one validation run")
def show_validation_run(
    run_id: str,
    store: ReadStore = Depends(get_read_store),
) -> ValidationRunDetail:
    """Return a validation run with its fingerprint, conclusion and warnings."""
    row = store.get_validation_run(run_id)
    if row is None:
        raise ApiError(
            status_code=404,
            code="validation_run_not_found",
            detail=f"No validation run {run_id!r} exists.",
        )
    manifest = store.get_validation_manifest(run_id)
    warning_count, warnings_by_severity = store.validation_warning_stats(run_id)
    payload = redact_document(row)
    payload["dataset_fingerprint"] = manifest.get("fingerprint") if manifest else None
    payload["conclusion"] = store.get_validation_conclusion(run_id)
    payload["warning_count"] = warning_count
    payload["warnings_by_severity"] = warnings_by_severity
    return ValidationRunDetail.model_validate(payload)
