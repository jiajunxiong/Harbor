"""Look-ahead bias protection tests (MVP 2 / SP 2.29).

Runs the full factor → standardization → candidate filter → scoring →
selection → cross-market merge → factor snapshot pipeline (SP 2.15-2.28) on a
decision date, then deliberately contaminates the underlying store with data
that was NOT knowable on that date:

- quotes dated after the decision date (extreme prices),
- dividends with an ex-date after the decision date,
- fundamental reports disclosed after the decision date,
- FX rates dated after the decision date,
- membership windows that expire after (or start after) the decision date.

The acceptance criterion is that historical signals and selection results are
UNCHANGED: any future-dated record must be excluded by the alignment layer
(SP 2.15/2.9) and the reader/as-of boundaries, never leaking into factor
values, standardized scores, composite scores, ranks, selection, the merged
portfolio or the persisted factor snapshot.

A control pair shows the comparison is sensitive: modifying data knowable ON
or BEFORE the decision date DOES change the outcome, so an equal outcome for
contaminated future data is meaningful rather than vacuous.

The fake reader deliberately returns the full store (including future records)
and the FX accessor performs point-in-time lookup, mirroring a real store that
may contain more data than the decision date; the pipeline itself must enforce
the boundary (defense in depth).
"""

import unittest
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from harbor.core.backtest_config import MarketQuota
from harbor.core.backtest_domain import Currency, Market
from harbor.core.backtest_interfaces import (
    BacktestDataReader,
    DailyQuote,
    Dividend,
    FundamentalRecord,
    FxRateRecord,
    TradingCalendar,
)
from harbor.core.candidate_filter import (
    CandidateFilterConfig,
    CandidateInputs,
    filter_candidates,
)
from harbor.core.cross_market_merge import MergedSelection, merge_selections
from harbor.core.factor_alignment import FactorInputSnapshot, build_factor_input_snapshot
from harbor.core.factor_dividend_sustainability import (
    DividendSustainabilityConfig,
    dividend_sustainability_factor,
)
from harbor.core.factor_dividend_yield import dividend_yield_factor
from harbor.core.factor_drawdown_liquidity import (
    DrawdownLiquidityConfig,
    drawdown_liquidity_factor,
)
from harbor.core.factor_earnings_quality import earnings_quality_factor
from harbor.core.factor_scoring import FactorScoreConfig, composite_score
from harbor.core.factor_snapshot import FactorSnapshot, FactorSnapshotInput, build_factor_snapshot
from harbor.core.factor_standardization import (
    FactorDirection,
    StandardizationConfig,
    StandardizationMethod,
    standardize_factor,
)
from harbor.core.factor_volatility import VolatilityConfig, annualized_volatility_factor
from harbor.core.history_window import WindowConfig
from harbor.core.market_selector import SelectionResult, select_candidates
from harbor.core.stock_pool import StockPoolMembership

_AS_OF = date(2026, 3, 31)
_HISTORY_START = date(2025, 9, 1)
_ALIGN_LOOKBACK = 180
_WINDOW = WindowConfig(lookback_days=90, min_observations=30)
_ANNUAL_TRADING_DAYS = {Market.HK: 242, Market.US: 252}

_HK_QUOTA = MarketQuota(market=Market.HK, target_count=2, weight=0.6)
_US_QUOTA = MarketQuota(market=Market.US, target_count=2, weight=0.4)
_TARGET = {Market.HK: _HK_QUOTA.target_count, Market.US: _US_QUOTA.target_count}

_FILTER_CONFIG = CandidateFilterConfig(
    min_history_observations=30,
    min_average_turnover=0.0,
    max_suspension_ratio=0.3,
)
_DS_CONFIG = DividendSustainabilityConfig(lookback_days=_ALIGN_LOOKBACK, expected_payments=4)
_SCORE_CONFIG = FactorScoreConfig.from_mapping(
    {
        "dividend_yield": 0.2,
        "dividend_sustainability": 0.2,
        "volatility": 0.2,
        "earnings_quality": 0.2,
        "liquidity": 0.2,
    }
)
_HIGHER = StandardizationConfig(
    method=StandardizationMethod.QUANTILE,
    direction=FactorDirection.HIGHER_IS_BETTER,
)
_LOWER = StandardizationConfig(
    method=StandardizationMethod.QUANTILE,
    direction=FactorDirection.LOWER_IS_BETTER,
)
_STANDARDIZATION = {
    "dividend_yield": _HIGHER,
    "dividend_sustainability": _HIGHER,
    "volatility": _LOWER,
    "earnings_quality": _HIGHER,
    "liquidity": _HIGHER,
}
_FACTORS = tuple(_STANDARDIZATION)

_QUOTE_CURRENCY = {Market.HK: Currency.HKD, Market.US: Currency.USD}


def _date_range(start: date, end: date) -> list[date]:
    """Return every calendar day in the inclusive range."""
    days: list[date] = []
    day = start
    while day <= end:
        days.append(day)
        day += timedelta(days=1)
    return days


def _weekdays(start: date, end: date) -> list[date]:
    """Return weekdays (Mon-Fri) in the inclusive range."""
    return [day for day in _date_range(start, end) if day.weekday() < 5]


class _WeekdayCalendar(TradingCalendar):
    """A deterministic Mon-Fri trading calendar shared by both markets."""

    def is_trading_day(self, market: Market, day: date) -> bool:
        return day.weekday() < 5

    def next_trading_day(self, market: Market, day: date) -> date:
        while day.weekday() >= 5:
            day += timedelta(days=1)
        return day

    def previous_trading_day(self, market: Market, day: date) -> date:
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        return day

    def trading_days(self, market: Market, start: date, end: date) -> Sequence[date]:
        return tuple(_weekdays(start, end))

    def rebalance_days(self, market: Market, start: date, end: date) -> Sequence[date]:
        return ()


class _NaiveReader(BacktestDataReader):
    """Returns the full store for a symbol, ignoring the as-of/end boundary.

    Future records therefore reach the alignment layer (SP 2.15), which must
    exclude them; this is the defense-in-depth SP 2.29 exercises.
    """

    def __init__(
        self,
        quotes: Sequence[DailyQuote],
        dividends: Sequence[Dividend],
        fundamentals: Sequence[FundamentalRecord],
    ) -> None:
        self._quotes = quotes
        self._dividends = dividends
        self._fundamentals = fundamentals

    def list_securities(self, market: Market, as_of: date) -> Sequence[str]:
        return ()

    def daily_quotes(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[DailyQuote]:
        return tuple(
            quote
            for quote in self._quotes
            if quote.market is market and quote.symbol == symbol and quote.day >= start
        )

    def dividends(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[Dividend]:
        return tuple(
            dividend
            for dividend in self._dividends
            if dividend.market is market and dividend.symbol == symbol and dividend.ex_date >= start
        )

    def fundamentals(
        self,
        market: Market,
        symbol: str,
        as_of: date,
    ) -> Sequence[FundamentalRecord]:
        return tuple(
            record
            for record in self._fundamentals
            if record.market is market and record.symbol == symbol
        )

    def corporate_actions(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[object]:
        return ()

    def adjustment_factors(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[object]:
        return ()


@dataclass
class _Dataset:
    """The underlying (possibly future-contaminated) store."""

    quotes: list[DailyQuote]
    dividends: list[Dividend]
    fundamentals: list[FundamentalRecord]
    fx_rates: list[FxRateRecord]
    memberships: list[StockPoolMembership]

    def clone(
        self,
        *,
        quotes: list[DailyQuote] | None = None,
        dividends: list[Dividend] | None = None,
        fundamentals: list[FundamentalRecord] | None = None,
        fx_rates: list[FxRateRecord] | None = None,
        memberships: list[StockPoolMembership] | None = None,
    ) -> "_Dataset":
        """Return a copy with the given fields replaced (lists are copied)."""
        return _Dataset(
            quotes=list(self.quotes) if quotes is None else quotes,
            dividends=list(self.dividends) if dividends is None else dividends,
            fundamentals=list(self.fundamentals) if fundamentals is None else fundamentals,
            fx_rates=list(self.fx_rates) if fx_rates is None else fx_rates,
            memberships=list(self.memberships) if memberships is None else memberships,
        )

    def reader(self) -> _NaiveReader:
        return _NaiveReader(self.quotes, self.dividends, self.fundamentals)

    def fx_accessor(
        self,
        from_currency: Currency,
        to_currency: Currency,
        as_of: date,
    ) -> FxRateRecord | None:
        """Return the latest rate on or before ``as_of`` (point-in-time)."""
        records = [
            record
            for record in self.fx_rates
            if record.from_currency is from_currency
            and record.to_currency is to_currency
            and record.date <= as_of
        ]
        if not records:
            return None
        return max(records, key=lambda record: record.date)

    def merge_fx_rate(
        self,
        from_currency: Currency,
        to_currency: Currency,
        as_of: date,
    ) -> float | None:
        record = self.fx_accessor(from_currency, to_currency, as_of)
        return record.rate if record is not None else None

    def symbols(self, market: Market) -> tuple[str, ...]:
        return tuple(
            sorted(
                membership.symbol for membership in self.memberships if membership.market is market
            )
        )

    def memberships_for(self, market: Market) -> tuple[StockPoolMembership, ...]:
        return tuple(membership for membership in self.memberships if membership.market is market)


@dataclass(frozen=True)
class _Outcome:
    """The pipeline's decision-relevant outputs for equality comparison."""

    snapshot: FactorSnapshot
    merged: MergedSelection
    composite: tuple[tuple[Market, str, float | None], ...]
    exclusions: tuple[tuple[Market, str, str], ...]


def _quote(
    market: Market,
    symbol: str,
    day: date,
    close: float,
    volume: int = 1_000_000,
) -> DailyQuote:
    return DailyQuote(
        market=market,
        symbol=symbol,
        day=day,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        adjusted_close=close,
    )


def _generate_quotes(
    market: Market,
    symbol: str,
    base: float,
    step_pct: float,
    volume: int,
    seed: int,
) -> list[DailyQuote]:
    """Generate deterministic weekday closes with a per-day wiggle."""
    quotes: list[DailyQuote] = []
    price = base
    index = 0
    for day in _weekdays(_HISTORY_START, _AS_OF):
        wiggle = ((index * 7 + seed * 3) % 11 - 5) / 1000.0
        price = max(1.0, price * (1.0 + step_pct / 100.0 + wiggle))
        quotes.append(_quote(market, symbol, day, round(price, 4), volume))
        index += 1
    return quotes


def _clean_dataset() -> _Dataset:
    """Return a store whose data ends on the decision date."""
    quotes: list[DailyQuote] = []
    dividends: list[Dividend] = []
    fundamentals: list[FundamentalRecord] = []
    memberships: list[StockPoolMembership] = []

    hk_specs = [
        ("0001.HK", 50.0, 0.04, 1_000_000, 1, 1.2, 1.1, 0.12, 1.2e9),
        ("0002.HK", 80.0, 0.06, 800_000, 2, 2.0, 1.8, 0.18, 2.0e9),
        ("0003.HK", 20.0, 0.02, 1_500_000, 3, 0.5, 0.4, 0.09, 0.8e9),
        ("0004.HK", 120.0, 0.09, 500_000, 4, 3.0, 3.2, 0.25, 3.0e9),
    ]
    us_specs = [
        ("AAPL", 180.0, 0.05, 50_000_000, 5, 0.9, 0.9, 0.22, 9.0e10),
        ("MSFT", 400.0, 0.04, 30_000_000, 6, 0.7, 0.7, 0.20, 7.0e10),
        ("GOOGL", 140.0, 0.08, 25_000_000, 7, 0.0, 0.0, 0.15, 6.0e10),
        ("AMZN", 90.0, 0.07, 40_000_000, 8, 0.1, 0.1, 0.10, 3.0e10),
    ]

    for market, specs in ((Market.HK, hk_specs), (Market.US, us_specs)):
        currency = _QUOTE_CURRENCY[market]
        for spec in specs:
            (
                symbol,
                base,
                step,
                volume,
                seed,
                div_a,
                div_b,
                roe,
                net_income,
            ) = spec
            quotes.extend(_generate_quotes(market, symbol, base, step, volume, seed))
            dividends.append(
                Dividend(
                    market,
                    symbol,
                    div_a,
                    currency,
                    date(2026, 1, 15),
                    is_special=False,
                )
            )
            dividends.append(
                Dividend(
                    market,
                    symbol,
                    div_b,
                    currency,
                    date(2026, 3, 1),
                    is_special=False,
                )
            )
            fundamentals.append(
                FundamentalRecord(
                    market,
                    symbol,
                    date(2025, 12, 31),
                    "2025",
                    date(2026, 1, 10),
                    roe=roe,
                    net_income=net_income,
                    total_equity=net_income * 4.0,
                    revenue=net_income * 6.0,
                )
            )
            memberships.append(
                StockPoolMembership(
                    market,
                    symbol,
                    _HISTORY_START,
                    None,
                    "mock",
                )
            )

    fx_rates = [FxRateRecord(Currency.USD, Currency.HKD, 7.8, date(2026, 3, 1))]
    return _Dataset(quotes, dividends, fundamentals, fx_rates, memberships)


def _run_pipeline(dataset: _Dataset) -> _Outcome:
    """Run the SP 2.15-2.28 pipeline on the decision date."""
    calendar = _WeekdayCalendar()
    selections: dict[Market, SelectionResult] = {}
    snapshot_inputs: list[FactorSnapshotInput] = []
    composite_rows: list[tuple[Market, str, float | None]] = []
    exclusion_rows: list[tuple[Market, str, str]] = []

    for market in (Market.HK, Market.US):
        snapshots: dict[str, FactorInputSnapshot] = {}
        raw: dict[str, dict[str, float | None]] = {}
        availability: dict[str, dict[str, date]] = {}
        filter_inputs: dict[str, CandidateInputs] = {}
        for symbol in dataset.symbols(market):
            snap = build_factor_input_snapshot(
                market,
                symbol,
                _AS_OF,
                dataset.reader(),
                fx_accessor=dataset.fx_accessor,
                quote_currency=_QUOTE_CURRENCY[market],
                to_currency=Currency.HKD,
                lookback_days=_ALIGN_LOOKBACK,
            )
            snapshots[symbol] = snap
            latest = snap.latest_price
            latest_close = latest.close if latest is not None else None
            yield_result = dividend_yield_factor(
                snap.dividends,
                latest_close,
                _AS_OF,
                lookback_days=_ALIGN_LOOKBACK,
            )
            sustainability = dividend_sustainability_factor(
                snap.dividends,
                snap.fundamental,
                _AS_OF,
                config=_DS_CONFIG,
            )
            volatility = annualized_volatility_factor(
                snap.price_history,
                _AS_OF,
                config=VolatilityConfig(
                    window=_WINDOW,
                    annual_trading_days=_ANNUAL_TRADING_DAYS[market],
                ),
            )
            drawdown = drawdown_liquidity_factor(
                market,
                snap.price_history,
                _AS_OF,
                calendar,
                config=DrawdownLiquidityConfig(window=_WINDOW),
            )
            quality = earnings_quality_factor(snap.fundamental, _AS_OF)
            raw[symbol] = {
                "dividend_yield": yield_result.value,
                "dividend_sustainability": sustainability.value,
                "volatility": volatility.value,
                "earnings_quality": quality.value,
                "liquidity": drawdown.average_turnover,
            }
            available: dict[str, date] = {}
            if latest is not None:
                available["price"] = latest.day
            if snap.dividends:
                available["dividend"] = max(div.ex_date for div in snap.dividends)
            if snap.fundamental is not None and snap.fundamental.available_on is not None:
                available["fundamental"] = snap.fundamental.available_on
            if snap.fx is not None:
                available["fx"] = snap.fx.date
            availability[symbol] = available
            filter_inputs[symbol] = CandidateInputs(
                observation_count=drawdown.observed_days,
                average_turnover=drawdown.average_turnover,
                suspension_ratio=drawdown.suspension_ratio,
                data_complete=snap.fundamental is not None,
            )

        filtered = filter_candidates(
            market,
            _AS_OF,
            dataset.memberships_for(market),
            filter_inputs,
            config=_FILTER_CONFIG,
        )
        candidates = filtered.candidates
        standardized: dict[str, dict[str, float | None]] = {}
        for factor, std_config in _STANDARDIZATION.items():
            standardized[factor] = standardize_factor(
                {symbol: raw[symbol].get(factor) for symbol in candidates},
                config=std_config,
            )
        composite = composite_score(
            {
                symbol: {factor: standardized[factor].get(symbol) for factor in _FACTORS}
                for symbol in candidates
            },
            _SCORE_CONFIG,
        )
        selection = select_candidates(
            market,
            _AS_OF,
            composite,
            target_count=_TARGET[market],
        )
        selections[market] = selection

        for symbol in sorted(candidates):
            composite_rows.append((market, symbol, composite.get(symbol)))
        for outcome in filtered.excluded:
            exclusion_rows.append((market, outcome.symbol, outcome.reason or ""))

        rank_by_symbol = {item.symbol: item.rank for item in selection.rankings}
        selected_set = set(selection.selected)
        for symbol in candidates:
            snapshot_inputs.append(
                FactorSnapshotInput(
                    market=market,
                    symbol=symbol,
                    raw_values=raw[symbol],
                    availability_dates=availability.get(symbol, {}),
                    standardized_scores={
                        factor: standardized[factor].get(symbol) for factor in _FACTORS
                    },
                    composite_score=composite.get(symbol),
                    rank=rank_by_symbol.get(symbol),
                    selected=symbol in selected_set,
                    exclusion_reason=None,
                )
            )
        for outcome in filtered.excluded:
            snapshot_inputs.append(
                FactorSnapshotInput(
                    market=market,
                    symbol=outcome.symbol,
                    raw_values=raw.get(outcome.symbol, {}),
                    availability_dates=availability.get(outcome.symbol, {}),
                    standardized_scores={},
                    composite_score=None,
                    rank=None,
                    selected=False,
                    exclusion_reason=outcome.reason,
                )
            )

    merged = merge_selections(
        as_of=_AS_OF,
        base_currency=Currency.HKD,
        quotas=[_HK_QUOTA, _US_QUOTA],
        selections=selections,
        fx_rate=dataset.merge_fx_rate,
    )
    snapshot = build_factor_snapshot(_AS_OF, snapshot_inputs)
    return _Outcome(
        snapshot=snapshot,
        merged=merged,
        composite=tuple(sorted(composite_rows, key=lambda item: (item[0].value, item[1]))),
        exclusions=tuple(sorted(exclusion_rows, key=lambda item: (item[0].value, item[1]))),
    )


def _contaminate_future_quotes(dataset: _Dataset) -> _Dataset:
    """Add extreme quotes dated strictly after the decision date."""
    extra: list[DailyQuote] = []
    for symbol in dataset.symbols(Market.HK):
        extra.append(_quote(Market.HK, symbol, _AS_OF + timedelta(days=1), 0.001, 1))
        extra.append(_quote(Market.HK, symbol, _AS_OF + timedelta(days=2), 99_999.0, 1))
    for symbol in dataset.symbols(Market.US):
        extra.append(_quote(Market.US, symbol, _AS_OF + timedelta(days=1), 0.001, 1))
        extra.append(_quote(Market.US, symbol, _AS_OF + timedelta(days=2), 99_999.0, 1))
    return dataset.clone(quotes=dataset.quotes + extra)


def _contaminate_future_dividends(dataset: _Dataset) -> _Dataset:
    """Add large dividends with an ex-date strictly after the decision date."""
    extra: list[Dividend] = []
    for market in (Market.HK, Market.US):
        currency = _QUOTE_CURRENCY[market]
        for symbol in dataset.symbols(market):
            extra.append(Dividend(market, symbol, 1_000.0, currency, _AS_OF + timedelta(days=1)))
            extra.append(
                Dividend(
                    market,
                    symbol,
                    2_000.0,
                    currency,
                    _AS_OF + timedelta(days=2),
                    is_special=True,
                )
            )
    return dataset.clone(dividends=dataset.dividends + extra)


def _contaminate_future_disclosures(dataset: _Dataset) -> _Dataset:
    """Add fundamental reports disclosed strictly after the decision date."""
    extra: list[FundamentalRecord] = []
    for market in (Market.HK, Market.US):
        for symbol in dataset.symbols(market):
            extra.append(
                FundamentalRecord(
                    market,
                    symbol,
                    _AS_OF + timedelta(days=2),
                    "2026Q1",
                    _AS_OF + timedelta(days=1),
                    roe=0.99,
                    net_income=9.9e12,
                    total_equity=9.9e13,
                    revenue=9.9e13,
                )
            )
    return dataset.clone(fundamentals=dataset.fundamentals + extra)


def _contaminate_future_fx(dataset: _Dataset) -> _Dataset:
    """Add FX rates dated strictly after the decision date."""
    return dataset.clone(
        fx_rates=dataset.fx_rates
        + [
            FxRateRecord(Currency.USD, Currency.HKD, 99.0, _AS_OF + timedelta(days=1)),
            FxRateRecord(Currency.USD, Currency.HKD, 0.01, _AS_OF + timedelta(days=2)),
        ]
    )


def _contaminate_delist_after(dataset: _Dataset) -> _Dataset:
    """Delist one symbol per market on the day AFTER the decision date."""
    memberships = [
        (
            StockPoolMembership(
                membership.market,
                membership.symbol,
                membership.effective_date,
                _AS_OF + timedelta(days=1),
                membership.source,
            )
            if membership.symbol in ("0001.HK", "AAPL")
            else membership
        )
        for membership in dataset.memberships
    ]
    return dataset.clone(memberships=memberships)


def _contaminate_not_yet_listed(dataset: _Dataset) -> _Dataset:
    """Add a symbol that only lists AFTER the decision date."""
    memberships = dataset.memberships + [
        StockPoolMembership(Market.HK, "NEW.HK", _AS_OF + timedelta(days=1), None, "mock")
    ]
    return dataset.clone(memberships=memberships)


def _contaminate_past_dividend(dataset: _Dataset) -> _Dataset:
    """Change a dividend amount knowable BEFORE the decision date (control)."""
    dividends = [
        Dividend(
            div.market,
            div.symbol,
            5.0 if (div.market is Market.HK and div.symbol == "0001.HK") else div.amount,
            div.currency,
            div.ex_date,
            div.record_date,
            div.payment_date,
            div.is_special,
        )
        for div in dataset.dividends
    ]
    return dataset.clone(dividends=dividends)


def _contaminate_past_disclosure(dataset: _Dataset) -> _Dataset:
    """Change a disclosed ROE knowable BEFORE the decision date (control)."""
    fundamentals = [
        FundamentalRecord(
            rec.market,
            rec.symbol,
            rec.report_date,
            rec.fiscal_period,
            rec.available_on,
            roe=0.99 if (rec.market is Market.HK and rec.symbol == "0001.HK") else rec.roe,
            net_income=rec.net_income,
            total_equity=rec.total_equity,
            revenue=rec.revenue,
        )
        for rec in dataset.fundamentals
    ]
    return dataset.clone(fundamentals=fundamentals)


class LookAheadProtectionTests(unittest.TestCase):
    """Verify future-dated data never changes decision-date signals."""

    def setUp(self) -> None:
        self.clean = _run_pipeline(_clean_dataset())

    def _assert_unchanged(self, contaminated: _Dataset) -> None:
        outcome = _run_pipeline(contaminated)
        self.assertEqual(outcome, self.clean)

    def test_selection_is_non_trivial(self) -> None:
        """Sanity: the clean pipeline ranks symbols differently and selects."""
        merged = self.clean.merged
        known = {"0001.HK", "0002.HK", "0003.HK", "0004.HK", "AAPL", "MSFT", "GOOGL", "AMZN"}
        self.assertEqual(len(merged.selected), 4)
        self.assertEqual(len(set(merged.selected)), 4)
        self.assertTrue(set(merged.selected).issubset(known))
        distinct = {score for _, _, score in self.clean.composite if score is not None}
        self.assertGreater(len(distinct), 1)

    def test_future_quotes_do_not_change_signals_or_selection(self) -> None:
        self._assert_unchanged(_contaminate_future_quotes(_clean_dataset()))

    def test_future_dividends_do_not_change_signals_or_selection(self) -> None:
        self._assert_unchanged(_contaminate_future_dividends(_clean_dataset()))

    def test_future_disclosures_do_not_change_signals_or_selection(self) -> None:
        self._assert_unchanged(_contaminate_future_disclosures(_clean_dataset()))

    def test_future_fx_does_not_change_merge_or_signals(self) -> None:
        self._assert_unchanged(_contaminate_future_fx(_clean_dataset()))

    def test_delisting_after_decision_date_does_not_change_selection(self) -> None:
        self._assert_unchanged(_contaminate_delist_after(_clean_dataset()))

    def test_not_yet_listed_symbol_excluded_without_changing_selection(self) -> None:
        outcome = _run_pipeline(_contaminate_not_yet_listed(_clean_dataset()))
        # The new symbol is excluded, not selected; the decision outputs hold.
        self.assertIn((Market.HK, "NEW.HK", "not yet listed on the as-of date"), outcome.exclusions)
        self.assertEqual(outcome.merged, self.clean.merged)
        self.assertEqual(outcome.composite, self.clean.composite)
        self.assertNotIn("NEW.HK", outcome.merged.selected)
        self.assertTrue(all(item.symbol != "NEW.HK" for item in outcome.merged.symbols))

    def test_modifying_past_dividend_changes_outcome(self) -> None:
        outcome = _run_pipeline(_contaminate_past_dividend(_clean_dataset()))
        self.assertNotEqual(outcome, self.clean)

    def test_modifying_past_disclosure_changes_outcome(self) -> None:
        outcome = _run_pipeline(_contaminate_past_disclosure(_clean_dataset()))
        self.assertNotEqual(outcome, self.clean)


if __name__ == "__main__":
    unittest.main()
