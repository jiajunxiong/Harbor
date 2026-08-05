"""Market-aware trading calendar for Hong Kong and United States (MVP 2 / SP 2.11).

Implements the :class:`harbor.core.backtest_interfaces.TradingCalendar` contract
with market-specific non-trading days. Weekends and the configured market
holidays are non-trading days, so the calendar is no longer a Monday-to-Friday
approximation.

Holiday sets are injectable for deterministic tests. :data:`DEFAULT_HOLIDAYS`
is an illustrative default covering 2026 only; a production research run must
supply an authoritative exchange calendar covering the full backtest range
(see the documented limitations in SP 2.73 / 2.74). Rebalance days are the
first trading day on or after each quarter start (SP 2.33 refines this rule
with configuration).
"""

from collections.abc import Iterator, Mapping, Sequence
from datetime import date, timedelta

from harbor.core.backtest_domain import Market
from harbor.core.backtest_interfaces import TradingCalendar

#: Illustrative Hong Kong holidays (2026). Research default only.
_HK_HOLIDAYS_2026: frozenset[date] = frozenset(
    {
        date(2026, 1, 1),  # New Year's Day
        date(2026, 2, 17),  # Lunar New Year (approximate)
        date(2026, 2, 18),
        date(2026, 2, 19),
        date(2026, 4, 3),  # Good Friday (approximate)
        date(2026, 4, 6),  # Easter Monday (approximate)
        date(2026, 4, 4),  # Ching Ming
        date(2026, 5, 1),  # Labour Day
        date(2026, 5, 25),  # Buddha's Birthday (approximate)
        date(2026, 6, 19),  # Dragon Boat Festival (approximate)
        date(2026, 7, 1),  # HKSAR Establishment Day
        date(2026, 9, 25),  # Mid-Autumn Festival (approximate)
        date(2026, 10, 1),  # National Day
        date(2026, 12, 25),  # Christmas
        date(2026, 12, 26),  # Boxing Day
    }
)

#: Illustrative United States holidays (2026). Research default only.
_US_HOLIDAYS_2026: frozenset[date] = frozenset(
    {
        date(2026, 1, 1),  # New Year's Day
        date(2026, 1, 19),  # Martin Luther King Jr. Day
        date(2026, 2, 16),  # Presidents' Day
        date(2026, 4, 3),  # Good Friday (NYSE)
        date(2026, 5, 25),  # Memorial Day
        date(2026, 6, 19),  # Juneteenth
        date(2026, 7, 3),  # Independence Day (observed)
        date(2026, 9, 7),  # Labor Day
        date(2026, 11, 26),  # Thanksgiving
        date(2026, 11, 27),  # Day after Thanksgiving
        date(2026, 12, 25),  # Christmas
    }
)

DEFAULT_HOLIDAYS: Mapping[Market, frozenset[date]] = {
    Market.HK: _HK_HOLIDAYS_2026,
    Market.US: _US_HOLIDAYS_2026,
}


def _quarter_starts(start: date, end: date) -> Iterator[date]:
    """Yield the quarter-start dates (Jan/Apr/Jul/Oct 1) within [start, end]."""
    year = start.year
    month = ((start.month - 1) // 3) * 3 + 1
    candidate = date(year, month, 1)
    while candidate <= end:
        if candidate >= start:
            yield candidate
        month += 3
        if month > 10:
            month = 1
            year += 1
        candidate = date(year, month, 1)


class MarketTradingCalendar(TradingCalendar):
    """HK/US trading calendar with weekends and market holidays (SP 2.11)."""

    def __init__(
        self,
        holidays: Mapping[Market, frozenset[date]] | None = None,
    ) -> None:
        self._holidays = DEFAULT_HOLIDAYS if holidays is None else holidays

    def _market_holidays(self, market: Market) -> frozenset[date]:
        return self._holidays.get(market, frozenset())

    def is_trading_day(self, market: Market, day: date) -> bool:
        """Return whether ``day`` is a trading day in ``market``."""
        if day.weekday() >= 5:
            return False
        return day not in self._market_holidays(market)

    def next_trading_day(self, market: Market, day: date) -> date:
        """Return the first trading day on or after ``day``."""
        cursor = day
        while not self.is_trading_day(market, cursor):
            cursor += timedelta(days=1)
        return cursor

    def previous_trading_day(self, market: Market, day: date) -> date:
        """Return the last trading day on or before ``day``."""
        cursor = day
        while not self.is_trading_day(market, cursor):
            cursor -= timedelta(days=1)
        return cursor

    def trading_days(self, market: Market, start: date, end: date) -> Sequence[date]:
        """Return trading days in ``market`` within the inclusive range."""
        if end < start:
            raise ValueError("trading_days requires end on or after start.")
        days: list[date] = []
        cursor = self.next_trading_day(market, start)
        while cursor <= end:
            days.append(cursor)
            cursor = self.next_trading_day(market, cursor + timedelta(days=1))
        return days

    def rebalance_days(self, market: Market, start: date, end: date) -> Sequence[date]:
        """Return quarterly rebalance days within the inclusive range.

        Each rebalance day is the first trading day on or after the quarter
        start, so a quarter start that lands on a weekend or holiday is
        deferred to the next trading day. SP 2.33 makes this rule
        configurable.
        """
        if end < start:
            raise ValueError("rebalance_days requires end on or after start.")
        days: list[date] = []
        for quarter_start in _quarter_starts(start, end):
            day = self.next_trading_day(market, quarter_start)
            if day <= end:
                days.append(day)
        return days
