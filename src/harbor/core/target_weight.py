"""Target weight model (MVP 2 / SP 2.34).

Computes the target portfolio weight of every selected symbol from the merged
cross-market selection (SP 2.27). Two weighting methods are supported:

- equal weight (等权): every selected symbol receives the same share of the
  equity target, regardless of market;
- market quota (配置化市场配额): each market receives its configured quota
  weight (SP 2.4) and divides it equally among that market's selected symbols.

A target cash weight reserves a fraction of the portfolio as cash, so the
equity target is ``1 - cash_weight``. Weights are rounded to a configured
number of decimal places with a deterministic largest-remainder rule (ties
broken by market then symbol), so the rounding is replayable and the rounded
weights sum exactly to the rounded equity target.

Pure core logic: depends only on the backtest domain types, the market quota
config and the cross-market merge result, and never touches storage or CLI
code.
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from harbor.core.backtest_domain import Currency, Market
from harbor.core.cross_market_merge import MergedSelection


class WeightingMethod(StrEnum):
    """How the equity target is split among the selected symbols (SP 2.34)."""

    EQUAL = "equal"
    MARKET_QUOTA = "market_quota"


@dataclass(frozen=True)
class TargetWeightConfig:
    """The target-weight rule (method, cash weight and rounding precision)."""

    method: WeightingMethod = WeightingMethod.EQUAL
    cash_weight: float = 0.0
    decimal_places: int = 4

    def __post_init__(self) -> None:
        if not 0.0 <= self.cash_weight < 1.0:
            raise ValueError("cash_weight must be in [0.0, 1.0).")
        if self.decimal_places < 0:
            raise ValueError("decimal_places must be non-negative.")


@dataclass(frozen=True)
class TargetWeight:
    """One selected symbol's target weight (fraction of the portfolio)."""

    market: Market
    symbol: str
    weight: float


@dataclass(frozen=True)
class TargetWeightResult:
    """The target weights for one rebalance, with the rounding rule recorded."""

    as_of: date
    base_currency: Currency
    method: WeightingMethod
    cash_weight: float
    decimal_places: int
    weights: tuple[TargetWeight, ...]

    @property
    def total_equity_weight(self) -> float:
        """Return the target equity fraction (``1 - cash_weight``)."""
        return 1.0 - self.cash_weight

    def weight_of(self, market: Market, symbol: str) -> float | None:
        """Return the target weight of a symbol, or ``None`` if not selected."""
        for weight in self.weights:
            if weight.market is market and weight.symbol == symbol:
                return weight.weight
        return None

    def readable(self) -> str:
        """Render the target weights as a human-readable summary."""
        lines = [
            f"Target weights for {self.as_of.isoformat()} "
            f"(base {self.base_currency.value}, {self.method.value}):"
        ]
        for weight in self.weights:
            lines.append(f"  {weight.market.value}/{weight.symbol}: {weight.weight:.4f}")
        lines.append(f"cash: {self.cash_weight:.4f}; equity: {self.total_equity_weight:.4f}")
        return "\n".join(lines)


def _apportion(
    keys: list[tuple[Market, str]],
    raw_weights: list[float],
    target: float,
    decimal_places: int,
) -> list[float]:
    """Round raw weights to ``decimal_places`` summing to ``target``.

    Uses the largest-remainder method: every weight is floored to the requested
    precision and the residual units are granted one at a time to the symbols
    with the largest fractional parts, breaking ties by market then symbol, so
    the rounding is deterministic and replayable (SP 2.34).
    """
    scale = 10**decimal_places
    target_units = int(round(target * scale))
    floors = [int(weight * scale) for weight in raw_weights]
    remainder = max(0, target_units - sum(floors))
    fractional = [weight * scale - floor for weight, floor in zip(raw_weights, floors)]
    order = sorted(
        range(len(keys)),
        key=lambda index: (-fractional[index], keys[index][0].value, keys[index][1]),
    )
    result = list(floors)
    for index in order[:remainder]:
        result[index] += 1
    return [units / scale for units in result]


def compute_target_weights(
    merged: MergedSelection,
    config: TargetWeightConfig | None = None,
) -> TargetWeightResult:
    """Compute the target weight of every selected symbol (SP 2.34).

    Args:
        merged: The merged cross-market selection (SP 2.27), whose quotas
            define each market's weight and whose selections define the
            selected symbols.
        config: The weighting method, cash weight and rounding precision.

    Returns:
        A :class:`TargetWeightResult` whose weights are ordered by quota market
        then within-market selection rank, rounded so they sum exactly to
        ``1 - cash_weight``.
    """
    if config is None:
        config = TargetWeightConfig()

    keys: list[tuple[Market, str]] = []
    for quota in merged.quotas:
        selection = next(item for item in merged.selections if item.market is quota.market)
        for symbol in selection.selected:
            keys.append((quota.market, symbol))

    if not keys:
        return TargetWeightResult(
            as_of=merged.as_of,
            base_currency=merged.base_currency,
            method=config.method,
            cash_weight=config.cash_weight,
            decimal_places=config.decimal_places,
            weights=(),
        )

    equity = 1.0 - config.cash_weight
    if config.method is WeightingMethod.EQUAL:
        raw_weights = [equity / len(keys)] * len(keys)
    else:
        raw_weights = []
        for quota in merged.quotas:
            selection = next(item for item in merged.selections if item.market is quota.market)
            count = len(selection.selected)
            share = quota.weight * equity / count if count else 0.0
            raw_weights.extend([share] * count)

    target = round(equity, config.decimal_places)
    rounded = _apportion(keys, raw_weights, target, config.decimal_places)
    weights = tuple(
        TargetWeight(market=market, symbol=symbol, weight=weight)
        for (market, symbol), weight in zip(keys, rounded)
    )
    return TargetWeightResult(
        as_of=merged.as_of,
        base_currency=merged.base_currency,
        method=config.method,
        cash_weight=config.cash_weight,
        decimal_places=config.decimal_places,
        weights=weights,
    )
