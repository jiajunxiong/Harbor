"""Concentration constraints (MVP 2 / SP 2.35).

Applies the risk constraints from the configuration (SP 2.4) to the target
weights computed by SP 2.34:

- single-stock cap (单股上限): no symbol may exceed ``max_position_pct``;
- single-market cap (单市场上限): no market's total may exceed
  ``max_market_pct``;
- minimum cash (最小现金比例): the cash weight may not fall below
  ``min_cash_pct``.

The applier honors these as hard constraints and reports a
:class:`ConstraintConflict` for every constraint the target weights violated
(约束冲突): the strategy asked for a weight the risk rules cannot allow, so the
adjustment and the reason are surfaced together. Adjustments are deterministic
and replayable:

- a target cash below ``min_cash_pct`` scales the equity weights down so cash
  reaches the floor (relative weights preserved);
- a market total above ``max_market_pct`` scales that market's weights down to
  the cap (relative weights within the market preserved);
- any symbol still above ``max_position_pct`` is capped, with the freed weight
  moved to cash.

Because every adjustment only reduces symbol weights and the freed weight goes
to cash, the result always satisfies all three constraints; the conflicts
record the target deviations that the constraints forced.

Pure core logic: depends only on the backtest domain types, the risk config
and the target weight model, and never touches storage or CLI code.
"""

from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum

from harbor.core.backtest_config import RiskConfig
from harbor.core.backtest_domain import Currency, Market
from harbor.core.target_weight import TargetWeightResult


class ConstraintKind(StrEnum):
    """The concentration constraint that was violated (SP 2.35)."""

    MAX_POSITION = "max_position"
    MAX_MARKET = "max_market"
    MIN_CASH = "min_cash"


@dataclass(frozen=True)
class ConstraintConflict:
    """A target weight that a hard constraint did not allow (SP 2.35)."""

    constraint: ConstraintKind
    scope: str
    message: str


@dataclass(frozen=True)
class ConstrainedWeight:
    """One symbol's adjusted weight under the concentration constraints."""

    market: Market
    symbol: str
    weight: float


@dataclass(frozen=True)
class ConstrainedPortfolio:
    """The constraint-adjusted portfolio with its recorded conflicts."""

    as_of: date
    base_currency: Currency
    weights: tuple[ConstrainedWeight, ...]
    cash_weight: float
    conflicts: tuple[ConstraintConflict, ...]

    @property
    def total_equity_weight(self) -> float:
        """Return the adjusted equity fraction (``1 - cash_weight``)."""
        return 1.0 - self.cash_weight

    def weight_of(self, market: Market, symbol: str) -> float | None:
        """Return a symbol's adjusted weight, or ``None`` if not held."""
        for weight in self.weights:
            if weight.market is market and weight.symbol == symbol:
                return weight.weight
        return None

    def market_total(self, market: Market) -> float:
        """Return the adjusted total weight of a market."""
        return sum(weight.weight for weight in self.weights if weight.market is market)

    def readable(self) -> str:
        """Render the adjusted portfolio and its conflicts."""
        lines = [
            f"Constrained portfolio for {self.as_of.isoformat()} (base {self.base_currency.value}):"
        ]
        for weight in self.weights:
            lines.append(f"  {weight.market.value}/{weight.symbol}: {weight.weight:.4f}")
        lines.append(f"cash: {self.cash_weight:.4f}; equity: {self.total_equity_weight:.4f}")
        if self.conflicts:
            lines.append(f"conflicts ({len(self.conflicts)}):")
            for conflict in self.conflicts:
                lines.append(
                    f"  [{conflict.constraint.value}] {conflict.scope}: {conflict.message}"
                )
        else:
            lines.append("no constraint conflicts")
        return "\n".join(lines)


def _markets_of(weights: tuple[ConstrainedWeight, ...]) -> tuple[Market, ...]:
    """Return the distinct markets in deterministic order."""
    return tuple(sorted({weight.market for weight in weights}, key=lambda m: m.value))


def apply_concentration_constraints(
    target: TargetWeightResult,
    risk: RiskConfig,
) -> ConstrainedPortfolio:
    """Apply the concentration constraints to target weights (SP 2.35).

    Args:
        target: The target weights from SP 2.34.
        risk: The concentration limits from the configuration (SP 2.4).

    Returns:
        A :class:`ConstrainedPortfolio` whose adjusted weights satisfy the
        single-stock cap, the single-market cap and the minimum cash ratio,
        together with the conflicts the target weights caused.
    """
    weights = [
        ConstrainedWeight(weight.market, weight.symbol, weight.weight) for weight in target.weights
    ]
    cash = target.cash_weight
    conflicts: list[ConstraintConflict] = []

    # Minimum cash: scale equity down so cash reaches the configured floor.
    if cash < risk.min_cash_pct:
        equity = 1.0 - cash
        scale = (1.0 - risk.min_cash_pct) / equity if equity > 0 else 0.0
        weights = [replace(weight, weight=weight.weight * scale) for weight in weights]
        cash = risk.min_cash_pct
        conflicts.append(
            ConstraintConflict(
                ConstraintKind.MIN_CASH,
                "portfolio",
                f"target cash {target.cash_weight:.4f} below min_cash_pct "
                f"{risk.min_cash_pct:.4f}; cash raised to {cash:.4f}",
            )
        )

    # Single-market cap: scale a market down when its total exceeds the cap.
    for market in _markets_of(tuple(weights)):
        indices = [index for index, weight in enumerate(weights) if weight.market is market]
        total = sum(weights[index].weight for index in indices)
        if total > risk.max_market_pct:
            scale = risk.max_market_pct / total
            for index in indices:
                weights[index] = replace(weights[index], weight=weights[index].weight * scale)
            freed = total - risk.max_market_pct
            cash += freed
            conflicts.append(
                ConstraintConflict(
                    ConstraintKind.MAX_MARKET,
                    market.value,
                    f"market total {total:.4f} exceeds max_market_pct "
                    f"{risk.max_market_pct:.4f}; scaled to {risk.max_market_pct:.4f}",
                )
            )

    # Single-stock cap: cap any symbol above max_position_pct, freed -> cash.
    for index, weight in enumerate(weights):
        if weight.weight > risk.max_position_pct:
            freed = weight.weight - risk.max_position_pct
            weights[index] = replace(weight, weight=risk.max_position_pct)
            cash += freed
            conflicts.append(
                ConstraintConflict(
                    ConstraintKind.MAX_POSITION,
                    f"{weight.market.value}/{weight.symbol}",
                    f"weight {weight.weight:.4f} exceeds max_position_pct "
                    f"{risk.max_position_pct:.4f}; capped and {freed:.4f} moved to cash",
                )
            )

    return ConstrainedPortfolio(
        as_of=target.as_of,
        base_currency=target.base_currency,
        weights=tuple(weights),
        cash_weight=cash,
        conflicts=tuple(conflicts),
    )
