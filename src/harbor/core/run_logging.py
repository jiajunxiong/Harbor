"""Structured, run-correlated logging for backtests (MVP 2 / SP 2.71).

Every structured log record emitted by a backtest run carries the correlation
fields ``backtest_run_id``, ``strategy_version``, ``market`` and ``stage`` so
each message can be traced back to a specific run and pipeline stage (the
stages come from the SP 2.47 engine). Configuration is never logged verbatim:

* :func:`redact_config` recursively masks keys that name sensitive values
  (passwords, secrets, tokens, API keys, credentials);
* :func:`log_run_event` refuses to emit a field whose name is sensitive or
  would collide with the logging record's own attributes.

Pure core logic (stdlib ``logging`` only): never touches storage or CLI code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Mapping

from harbor.core.backtest_domain import Market
from harbor.core.backtest_engine import BacktestStage


class RunLogError(ValueError):
    """Raised for an invalid run-log correlation request (SP 2.71)."""


_SENSITIVE_SUBSTRINGS = (
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
)

_REDACTED = "<redacted>"

_BASE_LOG_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


def is_sensitive_key(key: str) -> bool:
    """Whether ``key`` names a sensitive configuration value (SP 2.71).

    A key is sensitive when its lowercased name contains a marker such as
    ``password``, ``secret``, ``token``, ``api_key`` or ``credential``.
    """
    lowered = key.lower()
    return any(marker in lowered for marker in _SENSITIVE_SUBSTRINGS)


@dataclass(frozen=True)
class RunLogContext:
    """Correlation fields attached to every structured run log record.

    ``run_id`` and ``strategy_version`` are always required; ``market`` and
    ``stage`` are optional so a single context can describe a whole run and be
    narrowed per market / per stage with :meth:`with_market` /
    :meth:`with_stage` (SP 2.71).
    """

    run_id: str
    strategy_version: str
    market: Market | None = None
    stage: BacktestStage | None = None

    def __post_init__(self) -> None:
        if not self.run_id:
            raise RunLogError("run_id must be non-empty.")
        if not self.strategy_version:
            raise RunLogError("strategy_version must be non-empty.")

    def as_fields(self) -> dict[str, str | None]:
        """Flatten the context to the structured ``extra`` fields (SP 2.71)."""
        return {
            "backtest_run_id": self.run_id,
            "strategy_version": self.strategy_version,
            "market": self.market.value if self.market is not None else None,
            "stage": self.stage.value if self.stage is not None else None,
        }

    def with_market(self, market: Market | None) -> RunLogContext:
        """Return a copy narrowed to ``market`` (SP 2.71)."""
        return replace(self, market=market)

    def with_stage(self, stage: BacktestStage | None) -> RunLogContext:
        """Return a copy narrowed to ``stage`` (SP 2.71)."""
        return replace(self, stage=stage)


def redact_config(config: Mapping[str, object]) -> dict[str, object]:
    """Return a deep copy of ``config`` with sensitive values masked (SP 2.71).

    Keys whose name contains a sensitive marker are kept but their value is
    replaced with ``<redacted>``; nested mappings, lists and tuples are
    processed recursively. The input is never mutated, so no sensitive value
    can reach a log record through this path.
    """
    redacted: dict[str, object] = {}
    for key, value in config.items():
        if is_sensitive_key(key):
            redacted[key] = _REDACTED
            continue
        if isinstance(value, Mapping):
            redacted[key] = redact_config(value)
        elif isinstance(value, list):
            redacted[key] = [_redact_item(item) for item in value]
        elif isinstance(value, tuple):
            redacted[key] = tuple(_redact_item(item) for item in value)
        else:
            redacted[key] = value
    return redacted


def log_run_event(
    logger: logging.Logger,
    *,
    context: RunLogContext,
    event: str,
    level: int = logging.INFO,
    **fields: object,
) -> None:
    """Emit a structured log record carrying the run correlation fields.

    The context's ``backtest_run_id`` / ``strategy_version`` / ``market`` /
    ``stage`` fields are merged with ``fields`` and passed as ``extra`` to the
    logger, so the JSON formatter (SP 2.47 entry points) renders them as
    structured fields.

    Raises:
        RunLogError: If ``event`` is empty, ``level`` is not an integer, or a
            field name is empty, collides with a logging record attribute, or
            names a sensitive value.
    """
    if not event:
        raise RunLogError("A log event must be a non-empty string.")
    if not isinstance(level, int):
        raise RunLogError("Log level must be an integer.")
    extra: dict[str, object] = dict(context.as_fields())
    for key, value in fields.items():
        if not key:
            raise RunLogError("Log field names must be non-empty strings.")
        if key in _BASE_LOG_RECORD_FIELDS:
            raise RunLogError(f"Log field name {key!r} collides with the logging record.")
        if is_sensitive_key(key):
            raise RunLogError(f"Refusing to log sensitive field {key!r}.")
        extra[key] = value
    logger.log(level, event, extra=extra)


def _redact_item(value: object) -> object:
    """Redact a nested container element, recursing into collections."""
    if isinstance(value, Mapping):
        return redact_config(value)
    if isinstance(value, list):
        return [_redact_item(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_item(item) for item in value)
    return value
