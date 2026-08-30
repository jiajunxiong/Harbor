"""Hong Kong and United States paper order rules (MVP 4 / SP 4.17-4.18).

Applies each market's own order rules with parameters coming from the
configuration: Hong Kong board lots (手数), odd-lot rejection and price steps
(板位); United States whole vs fractional shares and minimum sizes. The two
markets are never mixed — ``apply_paper_order_rule`` dispatches on the market
so an HK order is never aligned with US rules or vice versa.

Quantity rounding reuses the MVP 2 cost models: ``round_to_lot`` (SP 2.37)
rounds DOWN to whole HK lots and ``round_to_fraction`` (SP 2.38) keeps US
fractional shares unchanged. A below-minimum outcome carries a human-readable
reason and is never silently rounded away (SP 4.17 / 4.18 acceptance).

Pure core logic: depends on the backtest domain, the HK/US cost models and
Pydantic; never touches storage or CLI code.
"""

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from harbor.core.backtest_domain import Market
from harbor.core.cost_us import round_to_fraction


class PaperOrderRuleError(ValueError):
    """Raised when an order rule cannot be applied (SP 4.17 / 4.18)."""


class OrderRuleStatus(StrEnum):
    """Whether an order quantity is accepted, adjusted or rejected."""

    OK = "OK"
    ADJUSTED = "ADJUSTED"
    REJECTED = "REJECTED"


class HkOrderRuleConfig(BaseModel):
    """Hong Kong order rule parameters (SP 4.17), from the configuration."""

    model_config = ConfigDict(frozen=True)

    lot_size: int = Field(default=100, gt=0, description="一手股数")
    price_step: float = Field(default=0.01, gt=0, description="板位/最小价差")
    min_quantity: int = Field(default=100, gt=0, description="最小交易数量（一手）")
    allow_odd_lot: bool = Field(default=False, description="是否允许碎股单")


class UsOrderRuleConfig(BaseModel):
    """United States order rule parameters (SP 4.18), from the configuration."""

    model_config = ConfigDict(frozen=True)

    allow_fractional: bool = Field(default=True, description="是否允许小数股")
    min_quantity: float = Field(default=1.0, gt=0, description="最小交易数量")
    min_notional: float = Field(default=0.0, ge=0, description="最小成交金额")


@dataclass(frozen=True)
class HkOrderRuleOutcome:
    """The result of applying the HK order rules to a quantity (SP 4.17)."""

    quantity: float
    status: OrderRuleStatus
    reason: str | None = None

    @property
    def ok(self) -> bool:
        """Whether the quantity is accepted or adjusted (not rejected)."""
        return self.status is not OrderRuleStatus.REJECTED

    def readable(self) -> str:
        """Render the outcome as a compact summary."""
        reason = "" if self.reason is None else f" ({self.reason})"
        return f"HK {self.status.value} qty {self.quantity:g}{reason}"


@dataclass(frozen=True)
class UsOrderRuleOutcome:
    """The result of applying the US order rules to a quantity (SP 4.18)."""

    quantity: float
    status: OrderRuleStatus
    reason: str | None = None

    @property
    def ok(self) -> bool:
        """Whether the quantity is accepted or adjusted (not rejected)."""
        return self.status is not OrderRuleStatus.REJECTED

    def readable(self) -> str:
        """Render the outcome as a compact summary."""
        reason = "" if self.reason is None else f" ({self.reason})"
        return f"US {self.status.value} qty {self.quantity:g}{reason}"


def apply_hk_order_rule(
    quantity: float,
    *,
    config: HkOrderRuleConfig | None = None,
) -> HkOrderRuleOutcome:
    """Align a buy/sell quantity to HK board lots (手数, SP 4.17).

    The quantity is rounded DOWN to whole lots (never above the requested
    amount). A quantity below one lot is rejected with a reason (不足一手)
    unless ``allow_odd_lot`` is configured; an unaligned quantity is reported
    as ``ADJUSTED`` with the lot multiple used.

    Raises:
        ValueError: If ``quantity`` is not positive.
    """
    if config is None:
        config = HkOrderRuleConfig()
    if quantity <= 0:
        raise ValueError("quantity must be positive.")
    if config.allow_odd_lot:
        return HkOrderRuleOutcome(
            quantity=quantity,
            status=OrderRuleStatus.ADJUSTED,
            reason="odd lots allowed by configuration",
        )
    lots = int(quantity // config.lot_size)
    if lots == 0:
        return HkOrderRuleOutcome(
            quantity=0.0,
            status=OrderRuleStatus.REJECTED,
            reason=(f"below one lot (不足一手): {quantity:g} < lot size {config.lot_size}."),
        )
    aligned = float(lots * config.lot_size)
    if aligned == quantity:
        return HkOrderRuleOutcome(quantity=quantity, status=OrderRuleStatus.OK)
    return HkOrderRuleOutcome(
        quantity=aligned,
        status=OrderRuleStatus.ADJUSTED,
        reason=f"rounded down to {lots} whole lot(s) of {config.lot_size}",
    )


def align_hk_price(
    price: float,
    *,
    config: HkOrderRuleConfig | None = None,
) -> float:
    """Align a limit price to the HK price step (板位, SP 4.17).

    Raises:
        ValueError: If ``price`` is not positive.
    """
    if config is None:
        config = HkOrderRuleConfig()
    if price <= 0:
        raise ValueError("price must be positive.")
    steps = round(price / config.price_step)
    return round(steps * config.price_step, 10)


def apply_us_order_rule(
    quantity: float,
    *,
    config: UsOrderRuleConfig | None = None,
) -> UsOrderRuleOutcome:
    """Align a buy/sell quantity to US share rules (SP 4.18).

    Fractional shares are kept unchanged when ``allow_fractional`` is set
    (round_to_fraction, SP 2.38); otherwise the quantity is rounded down to
    whole shares. A quantity below ``min_quantity`` is rejected with a reason.

    Raises:
        ValueError: If ``quantity`` is not positive.
    """
    if config is None:
        config = UsOrderRuleConfig()
    if quantity <= 0:
        raise ValueError("quantity must be positive.")
    if not config.allow_fractional:
        adjusted = float(int(quantity))
        if adjusted < config.min_quantity:
            return UsOrderRuleOutcome(
                quantity=0.0,
                status=OrderRuleStatus.REJECTED,
                reason=f"below minimum {config.min_quantity:g} shares.",
            )
        if adjusted == quantity:
            return UsOrderRuleOutcome(quantity=quantity, status=OrderRuleStatus.OK)
        return UsOrderRuleOutcome(
            quantity=adjusted,
            status=OrderRuleStatus.ADJUSTED,
            reason="rounded down to whole shares",
        )
    rounded = round_to_fraction(quantity)
    if rounded < config.min_quantity:
        return UsOrderRuleOutcome(
            quantity=0.0,
            status=OrderRuleStatus.REJECTED,
            reason=f"below minimum {config.min_quantity:g} shares.",
        )
    return UsOrderRuleOutcome(quantity=rounded, status=OrderRuleStatus.OK)


def apply_paper_order_rule(
    market: Market,
    quantity: float,
    *,
    hk: HkOrderRuleConfig | None = None,
    us: UsOrderRuleConfig | None = None,
) -> HkOrderRuleOutcome | UsOrderRuleOutcome:
    """Dispatch an order rule per market (SP 4.17 / 4.18).

    HK and US rules are never mixed: an HK quantity is aligned with HK lots and
    a US quantity with US share rules.
    """
    if market is Market.HK:
        return apply_hk_order_rule(quantity, config=hk)
    return apply_us_order_rule(quantity, config=us)
