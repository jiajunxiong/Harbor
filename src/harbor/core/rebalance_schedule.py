"""Configurable rebalance-date generation (MVP 2 / SP 2.33).

Generates the dates on which a low-frequency strategy rebalances, given a
market calendar (SP 2.11). Three anchor styles are supported:

- quarter start (季度首): the first trading day on/after each quarter start
  (Jan/Apr/Jul/Oct 1);
- quarter end (季度末): the first (or previous) trading day around each quarter
  end (Mar/Jun/Sep/Dec 31);
- specified dates (指定日期): an explicit, sorted set of dates.

When an anchor lands on a non-trading day, the :class:`DeferralRule` decides
how to defer: ``FORWARD`` uses the next trading day on or after the anchor,
``BACKWARD`` the previous trading day on or before it. Because the calendar is
market-scoped, Hong Kong and United States are deferred independently with
their own holidays and weekends (SP 2.33 acceptance criteria).

The generator is deterministic and replayable: resulting days are deduplicated
and sorted ascending, and any day that defers outside the requested range is
dropped.

Pure core logic: depends only on the backtest domain types, the calendar
contract and the config module, and never touches storage or CLI code.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from harbor.core.backtest_domain import Market
from harbor.core.backtest_interfaces import TradingCalendar


class RebalanceAnchor(StrEnum):
    """What anchors the rebalance schedule (SP 2.33)."""

    QUARTER_START = "quarter_start"
    QUARTER_END = "quarter_end"
    SPECIFIED = "specified"


class DeferralRule(StrEnum):
    """How a non-trading anchor is deferred to a trading day (SP 2.33)."""

    FORWARD = "forward"
    BACKWARD = "backward"


_QUARTER_START_MONTHS = (1, 4, 7, 10)
_QUARTER_END_MONTHS = (3, 6, 9, 12)
_ONE_DAY = timedelta(days=1)


def _last_day_of_month(year: int, month: int) -> date:
    """Return the last calendar day of the given month."""
    next_month = 1 if month == 12 else month + 1
    next_year = year + 1 if month == 12 else year
    return date(next_year, next_month, 1) - _ONE_DAY


@dataclass(frozen=True)
class RebalanceSchedule:
    """The rebalance rule for one strategy (SP 2.33).

    ``anchor`` selects the anchor style; ``specified_dates`` provides the
    explicit dates when ``anchor`` is :attr:`RebalanceAnchor.SPECIFIED`.
    ``deferral`` decides how a non-trading anchor is deferred.
    """

    anchor: RebalanceAnchor = RebalanceAnchor.QUARTER_START
    specified_dates: tuple[date, ...] = ()
    deferral: DeferralRule = DeferralRule.FORWARD

    def __post_init__(self) -> None:
        if self.anchor is RebalanceAnchor.SPECIFIED and not self.specified_dates:
            raise ValueError("specified anchor requires at least one specified date.")
        if self.anchor is not RebalanceAnchor.SPECIFIED and self.specified_dates:
            raise ValueError("specified_dates only apply to the specified anchor.")
        if len(set(self.specified_dates)) != len(self.specified_dates):
            raise ValueError("specified_dates must not contain duplicates.")
        if tuple(sorted(self.specified_dates)) != self.specified_dates:
            raise ValueError("specified_dates must be sorted ascending.")


def _anchor_dates(
    start: date,
    end: date,
    schedule: RebalanceSchedule,
) -> tuple[date, ...]:
    """Return the raw anchor dates within the inclusive range, ascending."""
    anchors: list[date] = []
    if schedule.anchor is RebalanceAnchor.SPECIFIED:
        anchors = [day for day in schedule.specified_dates if start <= day <= end]
    else:
        months = (
            _QUARTER_START_MONTHS
            if schedule.anchor is RebalanceAnchor.QUARTER_START
            else _QUARTER_END_MONTHS
        )
        for year in range(start.year, end.year + 1):
            for month in months:
                if schedule.anchor is RebalanceAnchor.QUARTER_START:
                    candidate = date(year, month, 1)
                else:
                    candidate = _last_day_of_month(year, month)
                if start <= candidate <= end:
                    anchors.append(candidate)
    return tuple(sorted(anchors))


def generate_rebalance_days(
    market: Market,
    start: date,
    end: date,
    calendar: TradingCalendar,
    schedule: RebalanceSchedule,
) -> tuple[date, ...]:
    """Generate the rebalance days for a market within the inclusive range.

    Each anchor is deferred per the schedule's :class:`DeferralRule` using the
    market's calendar; a day that defers outside ``[start, end]`` is dropped.
    The result is deduplicated and sorted ascending, so repeated generation is
    identical and replayable.

    Raises:
        ValueError: If ``end`` is before ``start``.
    """
    if end < start:
        raise ValueError("generate_rebalance_days requires end on or after start.")
    days: list[date] = []
    for anchor in _anchor_dates(start, end, schedule):
        if schedule.deferral is DeferralRule.FORWARD:
            day = calendar.next_trading_day(market, anchor)
        else:
            day = calendar.previous_trading_day(market, anchor)
        if start <= day <= end:
            days.append(day)
    return tuple(sorted(set(days)))
