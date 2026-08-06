"""Cross-market candidate merge (MVP 2 / SP 2.27).

Merges the standardized Hong Kong (SP 2.25) and United States (SP 2.26)
per-market selections into a single portfolio-level selection denominated in
the configured base currency, using the per-market quotas (SP 2.4).

Each market's securities are quoted in the market's currency (HK -> HKD,
US -> USD). When a market's quote currency differs from the base currency, an
FX rate from the quote currency to the base currency is required (SP 2.12): a
missing or non-positive rate is refused with :class:`CrossMarketFxError`
rather than assuming 1:1, so a cross-market combination is explicitly forbidden
when FX is not configured (SP 2.27 acceptance criteria).

The merge preserves each market's selection snapshot and produces a flattened
view of every ranked symbol with its quote currency, composite score and
within-market rank, so downstream stages (target weights SP 2.34, factor
snapshot SP 2.28, explainability report SP 2.32) can reconstruct the decision
deterministically.

Pure core logic: depends only on the domain types, the market registry and the
per-market selector; it never touches storage or CLI code.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType

from harbor.core.backtest_config import MarketQuota
from harbor.core.backtest_domain import Currency, Market, to_market_target
from harbor.core.market_registry import get_market_config
from harbor.core.market_selector import SelectionResult


class CrossMarketFxError(ValueError):
    """Raised when a merge needs an unavailable FX rate.

    Signals that a cross-market combination is forbidden without the required
    FX rate (SP 2.27), and that a 1:1 rate is never assumed (SP 2.12).
    """


def _quote_currency(market: Market) -> Currency:
    """Return the currency securities in ``market`` are quoted in."""
    return Currency(get_market_config(to_market_target(market)).currency)


@dataclass(frozen=True)
class MergedSymbol:
    """One ranked symbol in the merged selection.

    ``rank`` is the symbol's rank within its own market (1 = best, from
    SP 2.25/2.26); ``quote_currency`` is the market's quote currency, so the
    symbol's value can be converted into the base currency with
    ``MergedSelection.fx_rates`` (SP 2.12).
    """

    market: Market
    symbol: str
    quote_currency: Currency
    score: float
    rank: int
    selected: bool


@dataclass(frozen=True)
class MergedSelection:
    """The combined multi-market selection with its quota and FX context."""

    as_of: date
    base_currency: Currency
    quotas: tuple[MarketQuota, ...]
    selections: tuple[SelectionResult, ...]
    symbols: tuple[MergedSymbol, ...]
    selected: tuple[str, ...]
    fx_rates: Mapping[Market, float]
    fx_required: bool

    def readable(self) -> str:
        """Render the merged selection as a human-readable summary."""
        lines = [f"Merged selection for {self.as_of.isoformat()}, base {self.base_currency.value}:"]
        for quota in self.quotas:
            selection = next(item for item in self.selections if item.market is quota.market)
            quote = _quote_currency(quota.market)
            if quote is self.base_currency:
                fx_text = "none needed"
            else:
                fx_text = (
                    f"{quote.value}->{self.base_currency.value} {self.fx_rates[quota.market]:.4f}"
                )
            lines.append(
                f"  {quota.market.value} (weight {quota.weight:.4f}, target "
                f"{quota.target_count}): selected {len(selection.selected)}; "
                f"quote {quote.value}; FX to base: {fx_text}"
            )
        lines.append(f"selected ({len(self.selected)}): {', '.join(self.selected) or 'none'}")
        return "\n".join(lines)


def merge_selections(
    *,
    as_of: date,
    base_currency: Currency,
    quotas: Sequence[MarketQuota],
    selections: Mapping[Market, SelectionResult],
    fx_rate: Callable[[Currency, Currency, date], float | None],
) -> MergedSelection:
    """Merge per-market selections into one base-currency portfolio (SP 2.27).

    Args:
        as_of: The decision date.
        base_currency: The benchmark currency all positions are expressed in.
        quotas: Per-market participation (target count and portfolio weight);
            markets must be unique and the sequence must not be empty.
        selections: The per-market selection snapshot for each quota market.
        fx_rate: Returns the last known FX rate (from → to) on or before a
            date, or ``None`` when unavailable (SP 2.12).

    Raises:
        ValueError: If ``quotas`` is empty or repeats a market, if a quota
            market has no matching selection, or if a selection's market,
            as-of date or target count does not match its quota.
        CrossMarketFxError: If a market's quote currency differs from the base
            currency and its FX rate to the base currency is missing or
            non-positive; a cross-market combination is then forbidden.
    """
    if not quotas:
        raise ValueError("At least one market quota must be provided.")
    quota_markets = [quota.market for quota in quotas]
    if len(set(quota_markets)) != len(quota_markets):
        raise ValueError("Market quotas must not contain duplicate markets.")

    ordered: list[SelectionResult] = []
    for quota in quotas:
        selection = selections.get(quota.market)
        if selection is None:
            raise ValueError(f"Missing selection for market {quota.market.value}.")
        if selection.market is not quota.market:
            raise ValueError(
                f"Selection market {selection.market.value} does not match quota "
                f"market {quota.market.value}."
            )
        if selection.as_of != as_of:
            raise ValueError(
                f"Selection as-of {selection.as_of.isoformat()} does not match {as_of.isoformat()}."
            )
        if selection.target_count != quota.target_count:
            raise ValueError(
                f"Selection target {selection.target_count} does not match quota "
                f"target {quota.target_count} for {quota.market.value}."
            )
        ordered.append(selection)

    fx_rates: dict[Market, float] = {}
    fx_required = False
    for quota in quotas:
        quote = _quote_currency(quota.market)
        if quote is base_currency:
            fx_rates[quota.market] = 1.0
            continue
        fx_required = True
        rate = fx_rate(quote, base_currency, as_of)
        if rate is None or rate <= 0:
            raise CrossMarketFxError(
                f"Missing valid FX {quote.value}->{base_currency.value} as of "
                f"{as_of.isoformat()} for market {quota.market.value}; "
                "cross-market combination is forbidden without FX (SP 2.27)."
            )
        fx_rates[quota.market] = rate

    symbols: list[MergedSymbol] = []
    selected: list[str] = []
    for selection in ordered:
        quote = _quote_currency(selection.market)
        for rank in selection.rankings:
            symbols.append(
                MergedSymbol(
                    market=selection.market,
                    symbol=rank.symbol,
                    quote_currency=quote,
                    score=rank.score,
                    rank=rank.rank,
                    selected=rank.selected,
                )
            )
        selected.extend(selection.selected)

    return MergedSelection(
        as_of=as_of,
        base_currency=base_currency,
        quotas=tuple(quotas),
        selections=tuple(ordered),
        symbols=tuple(symbols),
        selected=tuple(selected),
        fx_rates=MappingProxyType(fx_rates),
        fx_required=fx_required,
    )
