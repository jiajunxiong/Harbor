"""Paper run logging (MVP 4 / SP 4.60).

Structured log records for the paper loop carry the correlation fields
``paper_run_id``, the strategy version, the market and the stage, and never
log sensitive configuration values (运行日志关联, SP 4.60). The context and the
redaction reuse the MVP 2 run-logging conventions (SP 2.71).

Pure core logic: depends on the backtest domain and the SP 2.71 redaction
helpers; never touches storage or CLI code.
"""

import logging
from dataclasses import dataclass, replace
from enum import StrEnum

from harbor.core.backtest_domain import Market
from harbor.core.run_logging import (
    is_sensitive_key,
    redact_config,
)

_BASE_LOG_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__) | {
    "message",
    "asctime",
}


class PaperStage(StrEnum):
    """The execution stage of a paper log record (SP 4.60)."""

    SIGNAL = "signal"
    ORDER = "order"
    FILL = "fill"
    VALUATION = "valuation"
    RECONCILE = "reconcile"
    AUDIT = "audit"


class PaperRunLogError(ValueError):
    """Raised when a paper log record is invalid (SP 4.60)."""


@dataclass(frozen=True)
class PaperRunLogContext:
    """Correlation fields attached to every structured paper log record (SP 4.60)."""

    paper_run_id: str
    strategy_version: str
    market: Market | None = None
    stage: PaperStage | None = None

    def __post_init__(self) -> None:
        if not self.paper_run_id:
            raise PaperRunLogError("paper_run_id must be non-empty.")
        if not self.strategy_version:
            raise PaperRunLogError("strategy_version must be non-empty.")

    def as_fields(self) -> dict[str, str | None]:
        """Flatten the context to the structured ``extra`` fields (SP 4.60)."""
        return {
            "paper_run_id": self.paper_run_id,
            "strategy_version": self.strategy_version,
            "market": self.market.value if self.market is not None else None,
            "stage": self.stage.value if self.stage is not None else None,
        }

    def with_market(self, market: Market | None) -> "PaperRunLogContext":
        """Return a copy narrowed to ``market`` (SP 4.60)."""
        return replace(self, market=market)

    def with_stage(self, stage: PaperStage | None) -> "PaperRunLogContext":
        """Return a copy narrowed to ``stage`` (SP 4.60)."""
        return replace(self, stage=stage)


def redact_paper_config(config: dict[str, object]) -> dict[str, object]:
    """Return a deep copy of ``config`` with sensitive values masked (SP 4.60).

    Reuses the SP 2.71 redaction so sensitive configuration (secrets, tokens,
    credentials) never reaches a log record.
    """
    return redact_config(config)


def log_paper_event(
    logger: logging.Logger,
    *,
    context: PaperRunLogContext,
    event: str,
    level: int = logging.INFO,
    **fields: object,
) -> None:
    """Emit a structured paper log record (SP 4.60).

    The context's ``paper_run_id`` / ``strategy_version`` / ``market`` /
    ``stage`` fields are merged with ``fields`` and passed as ``extra``.

    Raises:
        PaperRunLogError: If ``event`` is empty, ``level`` is not an integer,
            or a field name is empty, collides with a logging record
            attribute, or names a sensitive value.
    """
    if not event:
        raise PaperRunLogError("event must be non-empty.")
    if not isinstance(level, int):
        raise PaperRunLogError("level must be an integer.")
    merged: dict[str, object] = dict(context.as_fields())
    for name, value in fields.items():
        if not name:
            raise PaperRunLogError("field names must be non-empty.")
        if name in _BASE_LOG_RECORD_FIELDS:
            raise PaperRunLogError(f"field name {name!r} collides with a log record attribute.")
        if is_sensitive_key(name):
            raise PaperRunLogError(f"field name {name!r} is sensitive and must not be logged.")
        merged[name] = value
    logger.log(level, event, extra=merged)
