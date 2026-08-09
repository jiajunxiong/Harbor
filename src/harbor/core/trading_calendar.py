"""Market-aware trading calendar for Hong Kong and United States (MVP 2 / SP 2.11).

Implements the :class:`harbor.core.backtest_interfaces.TradingCalendar` contract
with market-specific non-trading days. Weekends and the configured market
holidays are non-trading days, so the calendar is no longer a Monday-to-Friday
approximation.

Holiday sets are injectable for deterministic tests. :data:`DEFAULT_HOLIDAYS`
covers the United States authoritatively for 2019-2024 (the range of the
shipped example strategy configs, SP 2.72) plus the illustrative 2026 set; the
Hong Kong set is an illustrative default covering 2026 only. A production
research run over a different range or market must supply an authoritative
exchange calendar covering the full backtest range (see the documented
limitations in SP 2.73 / 2.74) — otherwise a rebalance anchor can land on a
market holiday with no price data and the run fails (SP 2.36). Rebalance days
are the first trading day on or after each quarter start (SP 2.33 refines this
rule with configuration).
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

#: Authoritative United States (NYSE) holidays, 2019-2024 — the range of the
#: shipped example strategy configs (SP 2.72). Weekday closures only; weekend
#: holidays are already non-trading. Early closes (e.g. the day before
#: Thanksgiving) are still trading days and are intentionally excluded.
_US_HOLIDAYS_2019_2024: frozenset[date] = frozenset(
    {
        # 2019
        date(2019, 1, 1),  # New Year's Day
        date(2019, 1, 21),  # Martin Luther King Jr. Day
        date(2019, 2, 18),  # Presidents' Day
        date(2019, 4, 19),  # Good Friday
        date(2019, 5, 27),  # Memorial Day
        date(2019, 7, 4),  # Independence Day
        date(2019, 9, 2),  # Labor Day
        date(2019, 11, 28),  # Thanksgiving
        date(2019, 12, 25),  # Christmas
        # 2020
        date(2020, 1, 1),
        date(2020, 1, 20),
        date(2020, 2, 17),
        date(2020, 4, 10),  # Good Friday
        date(2020, 5, 25),
        date(2020, 7, 3),  # Independence Day (observed, Jul 4 Sat)
        date(2020, 9, 7),
        date(2020, 11, 26),
        date(2020, 12, 25),
        # 2021
        date(2021, 1, 1),
        date(2021, 1, 18),
        date(2021, 2, 15),
        date(2021, 4, 2),  # Good Friday
        date(2021, 5, 31),
        date(2021, 7, 5),  # Independence Day (observed, Jul 4 Sun)
        date(2021, 9, 6),
        date(2021, 11, 25),
        date(2021, 12, 24),  # Christmas (observed, Dec 25 Sat)
        # 2022
        date(2022, 1, 17),
        date(2022, 2, 21),
        date(2022, 4, 15),  # Good Friday
        date(2022, 5, 30),
        date(2022, 6, 20),  # Juneteenth
        date(2022, 7, 4),
        date(2022, 9, 5),
        date(2022, 11, 24),
        date(2022, 12, 26),  # Christmas (observed, Dec 25 Sun)
        # 2023
        date(2023, 1, 2),  # New Year's Day (observed, Jan 1 Sun)
        date(2023, 1, 16),
        date(2023, 2, 20),
        date(2023, 4, 7),  # Good Friday
        date(2023, 5, 29),
        date(2023, 6, 19),
        date(2023, 7, 4),
        date(2023, 9, 4),
        date(2023, 11, 23),
        date(2023, 12, 25),
        # 2024
        date(2024, 1, 1),
        date(2024, 1, 15),
        date(2024, 2, 19),
        date(2024, 3, 29),  # Good Friday
        date(2024, 5, 27),
        date(2024, 6, 19),
        date(2024, 7, 4),
        date(2024, 9, 2),
        date(2024, 11, 28),
        date(2024, 12, 25),
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
    Market.US: _US_HOLIDAYS_2019_2024 | _US_HOLIDAYS_2026,
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
