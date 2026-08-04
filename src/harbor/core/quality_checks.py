"""Daily quote quality checks for Harbor (per market).

These checks detect duplicate records and missing trading days for the
``(market, symbol, date)`` composite key. A trading day is a weekday
(Monday-Friday), matching the exchange calendar used across the pipeline.
Findings reuse the :class:`~harbor.core.validation.QualityFinding` vocabulary
and are applied identically to Hong Kong (1.86) and United States (1.87) data.
"""

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from harbor.config import MarketTarget
from harbor.core.validation import QualityFinding


def _as_date(value: object) -> date | None:
    """Return a value as a date, or ``None`` when it is not a date."""
    return value if isinstance(value, date) else None


def find_duplicate_daily_quotes(
    market: MarketTarget,
    rows: Sequence[Mapping[str, Any]],
) -> list[QualityFinding]:
    """Detect duplicate ``(symbol, date)`` records in a daily quote batch.

    Args:
        market: The market the rows belong to.
        rows: Daily quote rows with ``symbol`` and ``date`` keys.

    Returns:
        One ``daily_quote_duplicate`` finding (severity ``error``) per
        duplicated ``(symbol, date)`` key, reporting the record count.
    """
    key_count: dict[tuple[str, date], int] = {}
    for row in rows:
        symbol = str(row.get("symbol", ""))
        day = _as_date(row.get("date"))
        if day is None:
            continue
        key = (symbol, day)
        key_count[key] = key_count.get(key, 0) + 1

    findings: list[QualityFinding] = []
    for (symbol, day), count in sorted(key_count.items()):
        if count > 1:
            findings.append(
                QualityFinding(
                    "daily_quote_duplicate",
                    "error",
                    symbol,
                    f"{count} records for {day.isoformat()}.",
                )
            )
    return findings


def _weekdays_between(start: date, end: date) -> list[date]:
    """Return the weekdays strictly between two dates (exclusive)."""
    days: list[date] = []
    day = start + timedelta(days=1)
    while day < end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def find_daily_quote_gaps(
    market: MarketTarget,
    rows: Sequence[Mapping[str, Any]],
) -> list[QualityFinding]:
    """Detect missing trading days between the first and last quote per symbol.

    Args:
        market: The market the rows belong to.
        rows: Daily quote rows with ``symbol`` and ``date`` keys.

    Returns:
        One ``daily_quote_gap`` finding (severity ``warning``) per symbol that
        is missing weekday quotes, reporting the count and a sample of dates.
    """
    by_symbol: dict[str, set[date]] = {}
    for row in rows:
        symbol = str(row.get("symbol", ""))
        day = _as_date(row.get("date"))
        if day is None:
            continue
        by_symbol.setdefault(symbol, set()).add(day)

    findings: list[QualityFinding] = []
    for symbol, days in sorted(by_symbol.items()):
        ordered = sorted(days)
        if len(ordered) < 2:
            continue
        present = set(ordered)
        missing: list[date] = []
        for earlier, later in zip(ordered, ordered[1:]):
            for day in _weekdays_between(earlier, later):
                if day not in present:
                    missing.append(day)
        if missing:
            sample = ", ".join(day.isoformat() for day in missing[:10])
            if len(missing) > 10:
                sample += ", ..."
            findings.append(
                QualityFinding(
                    "daily_quote_gap",
                    "warning",
                    symbol,
                    f"Missing {len(missing)} trading days between {ordered[0]} "
                    f"and {ordered[-1]}: {sample}",
                )
            )
    return findings


def check_daily_quotes(
    market: MarketTarget,
    rows: Sequence[Mapping[str, Any]],
) -> list[QualityFinding]:
    """Run the duplicate and gap checks over a daily quote batch."""
    return find_duplicate_daily_quotes(market, rows) + find_daily_quote_gaps(market, rows)
