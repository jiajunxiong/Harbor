"""Validation run endpoints (read-only, MVP 5 / SP 5.2, SP 5.26-SP 5.36).

A validation detail carries the frozen dataset fingerprint and the recorded
out-of-sample conclusion **together with its limitations**, so a dashboard
cannot render a verdict without the caveats that belong to it (SP 3.58).

Three rules shape the Stage 3 endpoints:

* **absent data is stated, not filled in.** Six of the eight SP 3.12 tables have
  no writer anywhere in the code base, so trials, folds, stress results and the
  conclusion are frequently empty. Each of those endpoints answers
  ``available: false`` with the reason and the command that would produce the
  rows, instead of a zero that reads like a measurement (SP 5.29 / SP 5.31 /
  SP 5.32).
* **derived numbers come from the same core functions the CLI uses.** Coverage is
  measured by :func:`harbor.services.validation_dataset.build_profile` — the
  function ``harbor-cli validation freeze`` runs — so a dashboard percentage and
  a frozen fingerprint cannot disagree about what the data contains (SP 5.30).
* **the anti-misreading notices travel with the verdict** (SP 5.35).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query, Response

from harbor.api.deps import get_read_store
from harbor.api.errors import ApiError
from harbor.api.pagination import Page, PageParams, build_page, page_params
from harbor.api.read_store import ReadStore, Row
from harbor.api.redaction import redact_document
from harbor.api.schemas import (
    CoverageItemView,
    LifecycleEventView,
    ValidationArtifactCounts,
    ValidationCoverageResponse,
    ValidationEventsResponse,
    ValidationFoldsResponse,
    ValidationFoldView,
    ValidationRunDetail,
    ValidationRunSummary,
    ValidationSplitResponse,
    ValidationSplitView,
    ValidationStressResponse,
    ValidationStressView,
    ValidationTrialsResponse,
    ValidationTrialView,
    ValidationWarningsResponse,
    ValidationWarningView,
)
from harbor.api.security import require_readonly
from harbor.services.report_export import REPORT_MEDIA_TYPES
from harbor.services.validation import VALIDATION_REPORT_FORMATS
from harbor.services.validation_dataset import DatasetProfile

router = APIRouter(
    prefix="/validations",
    tags=["validations"],
    dependencies=[Depends(require_readonly)],
)

# -- anti-misreading notices (SP 5.35) -------------------------------------

#: A test set is unlocked once; re-tuning against it turns a validation into a
#: fit, which is why the state machine has no path back (SP 3.13).
NOTICE_TEST_SET_ONCE = (
    "测试集只解锁一次：评测后不得为调参再次解锁"
    "（状态机 DRAFT -> DATA_FROZEN -> TUNING -> TEST_LOCKED -> EVALUATED 不可回退）"
)

#: The notice that matters most to a reader of a run that "passed".
NOTICE_NO_RETUNE = (
    "调参后用同一切分重测得到的通过不算通过：参数试验必须在冻结切分上完成，"
    "超过预算的调参会使结论失效（SP 3.58）"
)

#: ``INCONCLUSIVE`` is a verdict about evidence, not a soft pass.
NOTICE_INCONCLUSIVE = (
    "INCONCLUSIVE 不等于通过：证据不足时结论为待定，需补足证据后重新评测（SP 3.58）"
)

#: Shown while no conclusion exists, so an empty verdict is not read as a pass.
NOTICE_NO_CONCLUSION = (
    "该运行尚未产生结论：结论只由验证流水线评测写入，"
    "当前没有 validation_conclusions 记录（SP 5.27 部分交付）"
)

#: The frozen split is the input to every later stage.
NOTICE_SPLIT_FROZEN = (
    "切分在冻结时写入 validation_splits 并带 split_hash，之后不随配置变更（SP 3.12）"
)

#: Why the coverage percentages are measured at request time.
NOTICE_COVERAGE_MEASURED_NOW = (
    "覆盖百分比为本次请求实测（冻结时只落库了成分区间与门槛警告）；"
    "两个指纹一致即表示冻结后数据未变，不一致即为漂移（SP 5.30）"
)

#: Why a passing item leaves no warning row.
NOTICE_WARNINGS_ARE_FAILURES = (
    "警告只记录未通过门槛的覆盖项；通过项不会写入警告行，"
    "因此「没有警告」不等于「覆盖良好」，覆盖情况请看 /coverage（SP 5.30 / SP 5.33）"
)

#: Why a warning can read "warning" while the coverage table says not_qualified.
NOTICE_WARNING_SEVERITY_LEVELS = (
    "警告表的 severity 只存 warning / error 两档（数据库 CHECK 约束）；"
    "门槛的第三档 not_qualified 记录在 context.severity 中，"
    "覆盖表按门槛原值展示，两处判定实际一致（SP 3.10）"
)

#: Why the audit trail has a null predecessor.
NOTICE_EVENT_ORIGIN = (
    "审计事件按时间累积；创建事件的 from_status 为空，因为该运行没有前置状态，"
    "冻结时间取自 DATA_FROZEN 事件（SP 5.26 / SP 5.33）"
)

#: Reasons for the artifacts the pipeline has not written yet. They name the
#: missing writer so a reader can tell "not run" apart from "run and empty".
TRIALS_UNAVAILABLE_REASON = (
    "该运行没有参数试验记录：validation_trials 的写入者是参数搜索流水线，"
    "当前代码库中尚无调用方（SP 5.29 部分交付）"
)
FOLDS_UNAVAILABLE_REASON = (
    "该运行没有折叠记录：validation_folds 的写入者是滚动验证流水线，"
    "当前代码库中尚无调用方（SP 5.32 部分交付）"
)
STRESS_UNAVAILABLE_REASON = (
    "该运行没有压力情景记录：validation_stress_results 的写入者是压力流水线，"
    "当前代码库中尚无调用方（SP 5.31 部分交付）"
)

#: Notes that belong to every trial list.
TUNING_NOTES = (
    "参数试验在训练/验证切分上进行，测试集不参与调参（SP 3.5）。",
    "重新调参会生成新的运行：既有运行的结果与结论保持不变（SP 3.13）。",
)

#: Notes that belong to every stress list.
STRESS_NOTES = (
    "压力情景是在历史数据上施加已登记假设后重跑的结果，不是对未来亏损的预测（SP 3.59）。",
    "未登记的情景不得进入结论；情景假设与差异一起展示，缺假设的差异数字无法解释（SP 3.59）。",
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


def _require_run(store: ReadStore, run_id: str) -> Row:
    """Return a validation run row, or raise the published 404 (SP 5.2).

    Raises:
        ApiError: 404 when no run exists for the id.
    """
    row = store.get_validation_run(run_id)
    if row is None:
        raise ApiError(
            status_code=404,
            code="validation_run_not_found",
            detail=f"No validation run {run_id!r} exists.",
        )
    return row


def _frozen_at(events: list[Row]) -> datetime | None:
    """The moment a run's dataset was frozen, from its event log (SP 5.26).

    The run row keeps only its last ``updated_at``, so the freeze time would be
    lost without the event that recorded the transition.
    """
    for event in events:
        if str(event.get("to_status")) == "DATA_FROZEN":
            recorded_at = event.get("recorded_at")
            return recorded_at if isinstance(recorded_at, datetime) else None
    return None


def _misreading_notices(conclusion: dict[str, Any] | None) -> list[str]:
    """The SP 5.35 notices that apply to a run's current state.

    The two about the test set and about re-tuning always apply: they describe
    rules that hold for every run, not just the ones that happened to finish.
    """
    notices = [NOTICE_TEST_SET_ONCE, NOTICE_NO_RETUNE]
    if conclusion is None:
        notices.append(NOTICE_NO_CONCLUSION)
    elif str(conclusion.get("conclusion")) == "INCONCLUSIVE":
        notices.append(NOTICE_INCONCLUSIVE)
    return notices


@router.get("/{run_id}", response_model=ValidationRunDetail, summary="Show one validation run")
def show_validation_run(
    run_id: str,
    store: ReadStore = Depends(get_read_store),
) -> ValidationRunDetail:
    """Return a validation run with its fingerprint, conclusion and events (SP 5.26, SP 5.27)."""
    row = _require_run(store, run_id)
    manifest = store.get_validation_manifest(run_id)
    conclusion = store.get_validation_conclusion(run_id)
    warning_count, warnings_by_severity = store.validation_warning_stats(run_id)
    events = store.list_validation_events(run_id)
    payload = redact_document(row)
    payload["dataset_fingerprint"] = manifest.get("fingerprint") if manifest else None
    payload["frozen_at"] = _frozen_at(events)
    payload["conclusion"] = conclusion
    payload["warning_count"] = warning_count
    payload["warnings_by_severity"] = warnings_by_severity
    payload["counts"] = ValidationArtifactCounts.model_validate(
        store.validation_artifact_counts(run_id)
    )
    payload["notices"] = _misreading_notices(conclusion)
    return ValidationRunDetail.model_validate(payload)


@router.get(
    "/{run_id}/split",
    response_model=ValidationSplitResponse,
    summary="A run's frozen split",
)
def read_validation_split(
    run_id: str,
    store: ReadStore = Depends(get_read_store),
) -> ValidationSplitResponse:
    """Return the frozen train / validation / test boundaries (SP 5.28)."""
    row = _require_run(store, run_id)
    split = store.get_validation_split(run_id)
    notes = [NOTICE_SPLIT_FROZEN]
    if row.get("test_set_id"):
        notes.append(
            "该运行已登记 test_set_id（测试集已解锁）；同一测试集不得再用于调参（SP 3.13）"
        )
    return ValidationSplitResponse(
        run_id=run_id,
        status=str(row.get("status", "")),
        available=split is not None,
        unavailable_reason=None if split is not None else "该运行没有冻结切分记录（SP 3.12）。",
        split=ValidationSplitView.model_validate(split) if split is not None else None,
        notes=notes,
    )


def _coverage_items(profile: DatasetProfile) -> list[CoverageItemView]:
    """Flatten a profile's per-market gate outcomes into published rows (SP 5.30).

    Every market keeps its own item rows rather than being averaged into one
    number: a market that is fully covered and a market that is not must not
    cancel each other out.
    """
    items: list[CoverageItemView] = []
    for market_coverage in profile.coverage:
        gate = profile.gate_for(market_coverage.market)
        verdicts = {result.item: result for result in gate.results} if gate is not None else {}
        for score in market_coverage.scores:
            verdict = verdicts.get(score.item)
            items.append(
                CoverageItemView(
                    market=market_coverage.market.value,
                    item=score.item.value,
                    covered=score.measurement.covered,
                    denominator=score.measurement.denominator,
                    coverage_pct=score.coverage_pct,
                    severity=(verdict.severity.value if verdict and verdict.severity else None),
                    reason=verdict.reason if verdict else None,
                    gap=score.measurement.gap,
                )
            )
    return items


@router.get(
    "/{run_id}/coverage",
    response_model=ValidationCoverageResponse,
    summary="Coverage and gates for a frozen run",
)
def read_validation_coverage(
    run_id: str,
    store: ReadStore = Depends(get_read_store),
) -> ValidationCoverageResponse:
    """Measure what the stored data covers for this run's frozen window (SP 5.30).

    The measurement is taken now, by the same function ``validation freeze``
    runs, because the percentages themselves were never persisted; the frozen
    manifest and the recorded warnings stay the immutable record of the freeze.
    Comparing the two fingerprints is what turns this endpoint into a drift
    check.
    """
    _require_run(store, run_id)
    manifest = store.get_validation_manifest(run_id)
    notes = [NOTICE_COVERAGE_MEASURED_NOW]
    if manifest is None:
        notes.append("该运行尚未冻结数据集：请先执行 harbor-cli validation freeze（SP 3.70）。")
    frozen_fingerprint = manifest.get("fingerprint") if manifest else None
    profile = store.validation_dataset_profile(run_id)
    if profile is None:
        return ValidationCoverageResponse(
            run_id=run_id,
            available=False,
            unavailable_reason=(
                "无法测量覆盖：该运行的 config_snapshot 无法重建为验证配置（SP 3.69）。"
            ),
            measured_at=datetime.now(timezone.utc),
            frozen_fingerprint=frozen_fingerprint,
            notes=notes,
        )
    return ValidationCoverageResponse(
        run_id=run_id,
        available=True,
        measured_at=datetime.now(timezone.utc),
        frozen_fingerprint=frozen_fingerprint,
        current_fingerprint=profile.fingerprint,
        fingerprint_matches=(
            None if frozen_fingerprint is None else frozen_fingerprint == profile.fingerprint
        ),
        markets=[market.value for market in profile.markets],
        items=_coverage_items(profile),
        notes=notes + list(profile.notes),
    )


@router.get(
    "/{run_id}/warnings",
    response_model=ValidationWarningsResponse,
    summary="A run's recorded coverage warnings",
)
def read_validation_warnings(
    run_id: str,
    store: ReadStore = Depends(get_read_store),
) -> ValidationWarningsResponse:
    """Return the coverage warnings recorded when the dataset was frozen (SP 5.30, SP 5.33)."""
    _require_run(store, run_id)
    rows = store.list_validation_warnings(run_id)
    _, by_severity = store.validation_warning_stats(run_id)
    return ValidationWarningsResponse(
        run_id=run_id,
        warning_count=len(rows),
        warnings_by_severity=by_severity,
        items=[ValidationWarningView.model_validate(row) for row in rows],
        notes=[NOTICE_WARNINGS_ARE_FAILURES, NOTICE_WARNING_SEVERITY_LEVELS],
    )


@router.get(
    "/{run_id}/events",
    response_model=ValidationEventsResponse,
    summary="A run's lifecycle events",
)
def read_validation_events(
    run_id: str,
    store: ReadStore = Depends(get_read_store),
) -> ValidationEventsResponse:
    """Return a run's state transitions, oldest first (SP 5.26, SP 5.33)."""
    _require_run(store, run_id)
    rows = store.list_validation_events(run_id)
    return ValidationEventsResponse(
        run_id=run_id,
        event_count=len(rows),
        frozen_at=_frozen_at(rows),
        events=[
            LifecycleEventView(
                from_status=row.get("from_status"),
                to_status=str(row.get("to_status", "")),
                reason=row.get("reason"),
                recorded_at=row["recorded_at"],
            )
            for row in rows
        ],
        notes=[NOTICE_EVENT_ORIGIN],
    )


@router.get(
    "/{run_id}/trials",
    response_model=ValidationTrialsResponse,
    summary="A run's parameter trials",
)
def read_validation_trials(
    run_id: str,
    store: ReadStore = Depends(get_read_store),
) -> ValidationTrialsResponse:
    """Return the parameter trials recorded for a run (SP 5.29)."""
    _require_run(store, run_id)
    rows = store.list_validation_trials(run_id)
    return ValidationTrialsResponse(
        run_id=run_id,
        available=bool(rows),
        unavailable_reason=None if rows else TRIALS_UNAVAILABLE_REASON,
        trial_count=len(rows),
        trials=[ValidationTrialView.model_validate(row) for row in rows],
        notes=list(TUNING_NOTES),
    )


@router.get(
    "/{run_id}/folds",
    response_model=ValidationFoldsResponse,
    summary="A run's out-of-sample folds",
)
def read_validation_folds(
    run_id: str,
    store: ReadStore = Depends(get_read_store),
) -> ValidationFoldsResponse:
    """Return the walk-forward folds recorded for a run (SP 5.32)."""
    _require_run(store, run_id)
    rows = store.list_validation_folds(run_id)
    return ValidationFoldsResponse(
        run_id=run_id,
        available=bool(rows),
        unavailable_reason=None if rows else FOLDS_UNAVAILABLE_REASON,
        fold_count=len(rows),
        folds=[ValidationFoldView.model_validate(row) for row in rows],
        notes=[NOTICE_NO_RETUNE],
    )


@router.get(
    "/{run_id}/stress",
    response_model=ValidationStressResponse,
    summary="A run's stress-scenario results",
)
def read_validation_stress(
    run_id: str,
    store: ReadStore = Depends(get_read_store),
) -> ValidationStressResponse:
    """Return the stress-scenario results recorded for a run (SP 5.31)."""
    _require_run(store, run_id)
    rows = store.list_validation_stress(run_id)
    return ValidationStressResponse(
        run_id=run_id,
        available=bool(rows),
        unavailable_reason=None if rows else STRESS_UNAVAILABLE_REASON,
        stress_count=len(rows),
        results=[ValidationStressView.model_validate(row) for row in rows],
        notes=list(STRESS_NOTES),
    )


@router.get(
    "/{run_id}/report",
    summary="Download a run's validation report",
    response_class=Response,
    responses={
        200: {
            "content": {media_type: {} for media_type in sorted(REPORT_MEDIA_TYPES.values())},
            "description": "The rendered report in the requested format.",
        }
    },
)
def download_validation_report(
    run_id: str,
    format: str = Query(
        default="json",
        description="Report format; see validation_report_formats in /api/v1/version.",
    ),
    store: ReadStore = Depends(get_read_store),
) -> Response:
    """Render a run's report server-side and return it as a download (SP 5.34).

    The document is produced by the same renderer ``harbor-cli validation report``
    writes, so a downloaded file and a terminal report cannot disagree — including
    about which sections the database could not supply.
    """
    if format not in VALIDATION_REPORT_FORMATS:
        raise ApiError(
            status_code=422,
            code="invalid_report_format",
            detail=(
                f"format must be one of {', '.join(VALIDATION_REPORT_FORMATS)}; got {format!r}. "
                "The supported formats are published at /api/v1/version."
            ),
        )
    _require_run(store, run_id)
    report = store.render_validation_report(run_id, format)
    return Response(
        content=report.content,
        media_type=report.media_type,
        headers={"Content-Disposition": f'attachment; filename="{report.filename}"'},
    )
