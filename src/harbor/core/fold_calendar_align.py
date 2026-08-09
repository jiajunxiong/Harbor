"""Fold boundary and calendar alignment (MVP 3 / SP 3.32).

Aligns the calendar-date boundaries of an SP 3.31 :class:`FoldSequence` to
tradable days using the HK/US authoritative trading calendar (MVP 2 / SP 2.11
:class:`MarketTradingCalendar`). Start boundaries are aligned forward (the
first trading day on or after the raw date) and end boundaries backward (the
last trading day on or before), so every boundary is a day the market actually
trades (使用 HK/US 权威交易日历将边界对齐到可交易日). For a cross-market
validation every fold records each market's actual aligned dates
(跨市场须记录每个市场的实际日期).

The raw pre-aligned fold is preserved alongside the per-market aligned dates
so the alignment is fully auditable. A window that collapses (becomes empty or
reversed) because it falls entirely on non-trading days is rejected with
:class:`CalendarAlignmentError` rather than silently adjusted — matching the
reject-don't-assume rule of SP 3.4.

Pure core layer: depends on the SP 3.31 fold sequence, the MVP 2 trading
calendar and the backtest/validation domain types, never on storage, services
or CLI.
"""

import hashlib
import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import date

from harbor.core.backtest_domain import Market
from harbor.core.rolling_window import FoldSequence
from harbor.core.trading_calendar import MarketTradingCalendar
from harbor.core.validation_domain import WalkForwardFold


class CalendarAlignmentError(ValueError):
    """Raised when fold boundaries cannot be aligned to tradable days (SP 3.32)."""


def _require_ordered_ranges(*ranges: tuple[date, date]) -> None:
    """Require each range to be non-empty and successive ranges strictly ordered.

    Mirrors the SP 3.4 rule (train_end < validation_start <= validation_end <
    test_start) but raises :class:`CalendarAlignmentError` so the caller can
    attribute the failure to a fold and market.
    """
    previous_end: date | None = None
    for start, end in ranges:
        if start > end:
            raise CalendarAlignmentError(
                f"aligned range is empty or reversed: {start.isoformat()}..{end.isoformat()}."
            )
        if previous_end is not None and not (previous_end < start):
            raise CalendarAlignmentError(
                f"aligned ranges must be strictly ordered: {previous_end.isoformat()} "
                f"must be before {start.isoformat()}."
            )
        previous_end = end


@dataclass(frozen=True)
class MarketAlignedDates:
    """One market's fold boundaries aligned to tradable days (SP 3.32).

    Start boundaries are the first trading day on or after the raw date; end
    boundaries (and the retraining date) are the last trading day on or before.
    The aligned ranges must stay non-empty and strictly ordered; a window that
    is entirely non-trading is rejected.
    """

    market: Market
    train_start: date
    train_end: date
    validation_start: date
    validation_end: date
    test_start: date
    test_end: date
    retrain_date: date | None = None

    def __post_init__(self) -> None:
        _require_ordered_ranges(
            (self.train_start, self.train_end),
            (self.validation_start, self.validation_end),
            (self.test_start, self.test_end),
        )
        if self.retrain_date is not None and self.retrain_date > self.train_end:
            raise CalendarAlignmentError(
                f"{self.market.value} retraining date {self.retrain_date.isoformat()} "
                "must not be after the aligned training end "
                f"{self.train_end.isoformat()}."
            )

    def readable(self) -> str:
        """Render the aligned boundaries for this market as one line."""
        retrain = self.retrain_date.isoformat() if self.retrain_date is not None else "none"
        return (
            f"{self.market.value} train "
            f"{self.train_start.isoformat()}..{self.train_end.isoformat()} "
            f"validation {self.validation_start.isoformat()}..{self.validation_end.isoformat()} "
            f"test {self.test_start.isoformat()}..{self.test_end.isoformat()} "
            f"retrain {retrain}"
        )


@dataclass(frozen=True)
class CalendarAlignedFold:
    """One fold with its boundaries aligned per market (SP 3.32).

    ``fold`` keeps the SP 3.31 raw (pre-alignment) boundaries for audit;
    ``markets`` records each market's actual aligned dates.
    """

    fold: WalkForwardFold
    markets: tuple[MarketAlignedDates, ...]

    def __post_init__(self) -> None:
        if not self.markets:
            raise CalendarAlignmentError("an aligned fold requires at least one market.")
        names = [aligned.market for aligned in self.markets]
        if sorted(names, key=lambda market: market.value) != names:
            raise CalendarAlignmentError("aligned markets must be key-sorted by market.")
        if len(set(names)) != len(names):
            raise CalendarAlignmentError("aligned markets must be unique.")

    def dates_for(self, market: Market) -> MarketAlignedDates | None:
        """Return the aligned dates for ``market``, or ``None`` when absent."""
        for aligned in self.markets:
            if aligned.market == market:
                return aligned
        return None

    def readable(self) -> str:
        """Render the raw fold plus each market's aligned boundaries."""
        return f"fold {self.fold.fold_index}: " + "; ".join(
            aligned.readable() for aligned in self.markets
        )


@dataclass(frozen=True)
class CalendarAlignedSequence:
    """The calendar-aligned fold sequence for a validation run (SP 3.32).

    ``calendar_version`` names the authoritative calendar set used (the same
    for every fold); ``fingerprint`` is the derived SHA-256 digest of the
    aligned dates, so replayability (SP 3.46) can be verified.
    """

    folds: tuple[CalendarAlignedFold, ...]
    calendar_version: str
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.folds:
            raise CalendarAlignmentError("an aligned sequence requires at least one fold.")
        if not self.calendar_version:
            raise CalendarAlignmentError("calendar version must be non-empty.")
        reference = tuple(aligned.market for aligned in self.folds[0].markets)
        if not reference:
            raise CalendarAlignmentError("an aligned sequence requires at least one market.")
        for index, fold in enumerate(self.folds):
            if fold.fold.fold_index != index:
                raise CalendarAlignmentError(f"aligned fold {index} must carry fold_index {index}.")
            if tuple(aligned.market for aligned in fold.markets) != reference:
                raise CalendarAlignmentError("all folds must share the same aligned markets.")
        if not self.fingerprint:
            raise CalendarAlignmentError("aligned sequence fingerprint must be non-empty.")

    def __len__(self) -> int:
        return len(self.folds)

    def __iter__(self) -> Iterator[CalendarAlignedFold]:
        return iter(self.folds)

    def __getitem__(self, index: int) -> CalendarAlignedFold:
        return self.folds[index]

    @property
    def markets(self) -> tuple[Market, ...]:
        """The markets every fold is aligned to, in key-sorted order."""
        return tuple(aligned.market for aligned in self.folds[0].markets)

    def readable(self) -> str:
        """Render the aligned sequence as one line."""
        return (
            f"{len(self.folds)} aligned folds across "
            f"{', '.join(market.value for market in self.markets)} "
            f"calendar {self.calendar_version} fp {self.fingerprint}"
        )


def _align_market(
    fold: WalkForwardFold,
    market: Market,
    calendar: MarketTradingCalendar,
) -> MarketAlignedDates:
    """Align one fold's boundaries to ``market`` tradable days."""
    retrain_date = (
        calendar.previous_trading_day(market, fold.retrain_date)
        if fold.retrain_date is not None
        else None
    )
    return MarketAlignedDates(
        market=market,
        train_start=calendar.next_trading_day(market, fold.train_start),
        train_end=calendar.previous_trading_day(market, fold.train_end),
        validation_start=calendar.next_trading_day(market, fold.validation_start),
        validation_end=calendar.previous_trading_day(market, fold.validation_end),
        test_start=calendar.next_trading_day(market, fold.test_start),
        test_end=calendar.previous_trading_day(market, fold.test_end),
        retrain_date=retrain_date,
    )


def align_fold_boundaries(
    sequence: FoldSequence,
    *,
    markets: Sequence[Market],
    calendar: MarketTradingCalendar,
    calendar_version: str = "default",
) -> CalendarAlignedSequence:
    """Align every fold boundary to tradable days per market (SP 3.32).

    Each market in ``markets`` gets its own actual aligned dates (cross-market
    folds record every market). A boundary window that is entirely non-trading
    in a market is rejected with :class:`CalendarAlignmentError`.
    """
    if not markets:
        raise CalendarAlignmentError("at least one market is required.")
    if len(set(markets)) != len(list(markets)):
        raise CalendarAlignmentError("markets must not contain duplicates.")
    ordered_markets = tuple(sorted(markets, key=lambda market: market.value))
    folds = tuple(
        CalendarAlignedFold(
            fold=fold,
            markets=tuple(_align_market(fold, market, calendar) for market in ordered_markets),
        )
        for fold in sequence.folds
    )
    aligned = CalendarAlignedSequence(
        folds=folds,
        calendar_version=calendar_version,
        fingerprint="unfingerprinted",
    )
    return replace(aligned, fingerprint=aligned_fingerprint(aligned))


def aligned_json(sequence: CalendarAlignedSequence) -> str:
    """Return a stable, key-sorted JSON serialization of an aligned sequence.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style).
    """
    payload: dict[str, object] = {
        "calendar_version": sequence.calendar_version,
        "folds": [
            {
                "fold_index": fold.fold.fold_index,
                "markets": [
                    {
                        "market": aligned.market.value,
                        "train_start": aligned.train_start.isoformat(),
                        "train_end": aligned.train_end.isoformat(),
                        "validation_start": aligned.validation_start.isoformat(),
                        "validation_end": aligned.validation_end.isoformat(),
                        "test_start": aligned.test_start.isoformat(),
                        "test_end": aligned.test_end.isoformat(),
                        "retrain_date": (
                            aligned.retrain_date.isoformat()
                            if aligned.retrain_date is not None
                            else None
                        ),
                    }
                    for aligned in fold.markets
                ],
            }
            for fold in sequence.folds
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def aligned_fingerprint(sequence: CalendarAlignedSequence) -> str:
    """Return the stable SHA-256 fingerprint of an aligned sequence (SP 3.32)."""
    return hashlib.sha256(aligned_json(sequence).encode("utf-8")).hexdigest()
