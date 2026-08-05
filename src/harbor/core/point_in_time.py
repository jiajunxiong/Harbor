"""Point-in-time data availability rules (MVP 2 / SP 2.9).

Decides whether a research input was actually knowable on a given decision
date. A field is usable only when its availability date — the disclosure date,
announcement date, or an explicit known date — is present and on or before the
decision date. When the availability date is missing, the record is refused
rather than dated by guess, because silently assuming availability is the most
common source of look-ahead bias.

Rules per record type:

- :class:`~harbor.core.backtest_interfaces.FundamentalRecord`: the explicit
  ``available_on`` (the financial disclosure date). ``None`` means the report
  cannot be safely dated and must be refused.
- :class:`~harbor.core.backtest_interfaces.DailyQuote`: the quote's own
  ``day`` (a quote is knowable on its date).
- :class:`~harbor.core.backtest_interfaces.Dividend`: the ``ex_date`` (once the
  ex-date has passed, the dividend is a historical fact; no announcement date
  is stored, so the ex-date is the earliest safe availability).
- :class:`~harbor.core.backtest_interfaces.AdjustmentFactor`: its ``date``.
- :class:`~harbor.core.equity.EntitlementEvent`: the ``ex_date`` (an event is
  applied from its ex-date).

This module is pure core logic: it depends only on the immutable domain types
and never touches storage or CLI code.
"""

from collections.abc import Sequence
from datetime import date
from typing import TypeVar

from harbor.core.backtest_interfaces import (
    AdjustmentFactor,
    DailyQuote,
    Dividend,
    FundamentalRecord,
)
from harbor.core.equity import EntitlementEvent


class PointInTimeError(ValueError):
    """Raised when a record is not knowable on the requested date."""


def available_on(record: object) -> date | None:
    """Return the date a record became knowable, or ``None`` if unknown.

    Raises:
        TypeError: If the record type has no point-in-time rule.
    """
    if isinstance(record, FundamentalRecord):
        return record.available_on
    if isinstance(record, DailyQuote):
        return record.day
    if isinstance(record, Dividend):
        return record.ex_date
    if isinstance(record, AdjustmentFactor):
        return record.date
    if isinstance(record, EntitlementEvent):
        return record.ex_date
    raise TypeError(f"No point-in-time rule for record type {type(record).__name__!r}.")


def available_as_of(record: object, as_of: date) -> bool:
    """Return whether a record was knowable on or before ``as_of``."""
    availability = available_on(record)
    return availability is not None and availability <= as_of


RecordT = TypeVar("RecordT")


def filter_available(records: Sequence[RecordT], as_of: date) -> tuple[RecordT, ...]:
    """Return only records that were knowable on or before ``as_of``.

    Records with an unknown availability date are dropped rather than silently
    used (SP 2.9).
    """
    return tuple(record for record in records if available_as_of(record, as_of))


def require_available(record: object, as_of: date) -> None:
    """Raise unless the record was knowable on or before ``as_of``.

    Raises:
        PointInTimeError: If the availability date is unknown or future-dated.
    """
    if available_as_of(record, as_of):
        return
    availability = available_on(record)
    if availability is None:
        raise PointInTimeError(
            f"{type(record).__name__} has no known availability date; refusing "
            "to use it rather than guessing (SP 2.9)."
        )
    raise PointInTimeError(
        f"{type(record).__name__} was not knowable on {as_of.isoformat()} "
        f"(availability {availability.isoformat()})."
    )
