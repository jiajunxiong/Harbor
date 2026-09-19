"""Data-quality endpoints (read-only, MVP 5 / SP 5.2).

The dashboard must be able to show *why* a market's data is not trustworthy
before it shows any performance number, so unresolved quality issues and the
last ingestion outcome are part of the read contract (SP 5.11).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from harbor.api.deps import get_read_store
from harbor.api.read_store import ReadStore
from harbor.api.schemas import QualitySummary
from harbor.api.security import require_readonly
from harbor.core.backtest_domain import Market

router = APIRouter(
    prefix="/quality",
    tags=["quality"],
    dependencies=[Depends(require_readonly)],
)


@router.get("/{market}", response_model=QualitySummary, summary="Data quality for one market")
def read_quality_summary(
    market: Market,
    store: ReadStore = Depends(get_read_store),
) -> QualitySummary:
    """Return coverage, open issues and the last ingestion for one market."""
    return QualitySummary.model_validate(store.quality_summary(market.value))
