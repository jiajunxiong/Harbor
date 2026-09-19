"""Pagination, filtering and sorting primitives (MVP 5 / SP 5.6).

Every collection endpoint is paginated: ``limit`` is always bounded by the
configured maximum and ``offset`` must be non-negative, so an unbounded query
is impossible by construction (大结果集强制分页，拒绝无界查询). The response
envelope reports ``total`` and ``next_offset`` so a client can page through
without guessing.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, Sequence, TypeVar

from fastapi import Depends, Query
from pydantic import BaseModel, Field

from harbor.api.config import ApiSettings
from harbor.api.deps import get_settings
from harbor.api.errors import ApiError

T = TypeVar("T")


class SortOrder(StrEnum):
    """Sort direction for collection endpoints (SP 5.6)."""

    ASC = "asc"
    DESC = "desc"


@dataclass(frozen=True)
class PageParams:
    """Validated pagination parameters (SP 5.6)."""

    limit: int
    offset: int
    sort: str | None
    order: SortOrder

    def slice_bounds(self) -> tuple[int, int]:
        """Return the ``[start, stop)`` bounds for an in-memory page slice."""
        return self.offset, self.offset + self.limit


def page_params(
    limit: int | None = Query(default=None, description="Page size; bounded by the server."),
    offset: int = Query(default=0, ge=0, description="Number of rows to skip."),
    sort: str | None = Query(default=None, description="Field to sort by, when supported."),
    order: SortOrder = Query(default=SortOrder.DESC, description="Sort direction."),
    settings: ApiSettings = Depends(get_settings),
) -> PageParams:
    """Validate pagination parameters against the server bounds (SP 5.6).

    Raises:
        ApiError: 422 when ``limit`` is not a positive integer within bounds.
    """
    resolved = settings.default_page_limit if limit is None else limit
    if resolved < 1:
        raise ApiError(
            status_code=422,
            code="invalid_page_limit",
            detail="limit must be a positive integer; unbounded queries are not permitted.",
        )
    if resolved > settings.max_page_limit:
        raise ApiError(
            status_code=422,
            code="invalid_page_limit",
            detail=f"limit must not exceed {settings.max_page_limit}.",
        )
    if not isinstance(offset, int) or offset < 0:
        raise ApiError(
            status_code=422,
            code="invalid_page_offset",
            detail="offset must be a non-negative integer.",
        )
    if sort is not None and not sort.strip():
        raise ApiError(
            status_code=422,
            code="invalid_sort",
            detail="sort must be a non-empty field name when provided.",
        )
    return PageParams(limit=resolved, offset=offset, sort=sort, order=order)


class Page(BaseModel, Generic[T]):
    """A paginated collection envelope (SP 5.6)."""

    items: list[T] = Field(default_factory=list)
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    next_offset: int | None = Field(default=None, ge=0)


def build_page(items: Sequence[T], *, total: int, params: PageParams) -> Page[T]:
    """Wrap rows that the data source already limited and offset (SP 5.6).

    ``total`` is the full count *before* pagination, so the envelope stays
    truthful even though only one page was fetched.
    """
    consumed = min(params.offset + len(items), total)
    next_offset = consumed if consumed < total else None
    return Page[T](
        items=list(items),
        total=total,
        limit=params.limit,
        offset=params.offset,
        next_offset=next_offset,
    )


def paginate_in_memory(items: Sequence[T], *, params: PageParams) -> Page[T]:
    """Slice a fully materialized sequence into a page (SP 5.6)."""
    total = len(items)
    start, stop = params.slice_bounds()
    return build_page(items[start:stop], total=total, params=params)
