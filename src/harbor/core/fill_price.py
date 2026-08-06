"""Fill price and slippage for order execution (MVP 2 / SP 2.39).

Determines the reference execution price for an order from the configured fill
rule — same-day open, same-day close or next trading day's open — and exposes
the slippage adjustment that moves the execution price in the trade direction
(buy pays up, sell receives less). The fill rule lives in the configuration
(SP 2.4 :class:`~harbor.core.backtest_config.FillConfig`), so execution timing
is fixed at configuration time and is part of the run hash (SP 2.5).

:func:`simulate_fill` is the core execution primitive: it resolves the
reference price per the rule, computes the all-in trading cost with the
market's cost model (SP 2.37 HK fees / SP 2.38 US fees + slippage) and produces
an auditable :class:`~harbor.core.backtest_domain.Fill`. The recorded fill
price is the rule-determined reference price; for the US market the slippage
impact is captured inside the fill fee (SP 2.38). :func:`apply_slippage`
mirrors the US cost model's internal slippage as a standalone helper for
callers that need the adjusted price directly.

Pure core logic: depends only on the domain types, the configuration and the
cost models; never touches storage or CLI code.
"""

from harbor.core.backtest_config import CostConfig, FillRule
from harbor.core.backtest_domain import Fill, Market, Order, OrderSide
from harbor.core.backtest_interfaces import DailyQuote
from harbor.core.cost_hk import hk_order_cost
from harbor.core.cost_us import us_order_cost


def resolve_fill_price(
    *,
    rule: FillRule,
    quote: DailyQuote,
    next_quote: DailyQuote | None = None,
) -> float:
    """Return the reference execution price for a fill rule (SP 2.39).

    Args:
        rule: The configured fill rule (open / close / next open).
        quote: The quote for the decision day.
        next_quote: The quote for the next trading day; required by
            ``FillRule.NEXT_OPEN``.

    Raises:
        ValueError: If ``rule`` is ``FillRule.NEXT_OPEN`` and ``next_quote`` is
            not provided.
    """
    if rule is FillRule.OPEN:
        return quote.open
    if rule is FillRule.CLOSE:
        return quote.close
    if next_quote is None:
        raise ValueError("NEXT_OPEN fill rule requires the next trading day's quote.")
    return next_quote.open


def apply_slippage(
    *,
    price: float,
    side: OrderSide,
    slippage_bps: float,
) -> float:
    """Adjust a price for slippage in the direction of the trade (SP 2.39).

    Buys pay up and sells receive less: the returned price is
    ``price * (1 +- slippage_bps / 10000)``. Mirrors the US cost model's
    internal slippage (SP 2.38); the Hong Kong cost model does not apply
    slippage, so a caller that needs it must call this helper.

    Raises:
        ValueError: If ``price`` is not positive or ``slippage_bps`` is
            negative.
    """
    if price <= 0:
        raise ValueError("Price must be positive.")
    if slippage_bps < 0:
        raise ValueError("slippage_bps must be non-negative.")
    slippage = slippage_bps / 10_000.0
    if side is OrderSide.BUY:
        return price * (1.0 + slippage)
    return price * (1.0 - slippage)


def _trade_cost(order: Order, reference: float, config: CostConfig) -> float:
    """All-in trading cost for an order executed at the reference price."""
    if order.market is Market.HK:
        return hk_order_cost(
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            price=reference,
            config=config,
        ).total_fee
    return us_order_cost(
        symbol=order.symbol,
        side=order.side,
        quantity=order.quantity,
        price=reference,
        config=config,
    ).total_cost


def simulate_fill(
    *,
    order: Order,
    rule: FillRule,
    quote: DailyQuote,
    next_quote: DailyQuote | None = None,
    config: CostConfig | None = None,
) -> Fill:
    """Simulate the execution of a single order (SP 2.39).

    Resolves the reference price per ``rule``, computes the all-in trading cost
    with the market's cost model (SP 2.37 / 2.38) and builds an auditable
    :class:`Fill`. The fill price is the rule-determined reference price; the
    US cost model captures the slippage impact inside the fee (SP 2.38).

    Args:
        order: The order to fill.
        rule: The configured fill rule (SP 2.4).
        quote: The quote for the decision day.
        next_quote: The quote for the next trading day (required for
            ``FillRule.NEXT_OPEN``).
        config: The cost parameters from the configuration (SP 2.4).

    Raises:
        ValueError: If ``rule`` is ``FillRule.NEXT_OPEN`` and ``next_quote`` is
            not provided.
    """
    if config is None:
        config = CostConfig()
    reference = resolve_fill_price(rule=rule, quote=quote, next_quote=next_quote)
    if rule is FillRule.NEXT_OPEN:
        if next_quote is None:
            raise ValueError("NEXT_OPEN fill rule requires the next trading day's quote.")
        trade_date = next_quote.day
    else:
        trade_date = quote.day
    fee = _trade_cost(order, reference, config)
    return Fill(
        order_ref=order.ref,
        symbol=order.symbol,
        market=order.market,
        side=order.side,
        quantity=order.quantity,
        price=reference,
        currency=order.currency,
        trade_date=trade_date,
        fee=fee,
    )
