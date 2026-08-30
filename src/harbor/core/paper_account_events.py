"""Paper account events: dividends and corporate actions (MVP 4 / SP 4.53-4.55).

Applies cash dividends (SP 4.54) and market-specific corporate actions
(SP 4.55) to the paper account, reusing the MVP 2 processing (SP 2.43
dividends, SP 2.44 corporate actions) so the paper loop and the research loop
share the same rules. Cash is credited in the dividend's / action's own
currency (SP 2.42, no implicit FX); share actions update the position quantity
and cash actions credit the ledger. HK and US rules are never mixed (SP 2.44).

Pure core logic: depends on the paper account (SP 4.4), the MVP 2 dividend /
corporate-action modules and the backtest domain; never touches storage or CLI
code.
"""

from dataclasses import replace
from datetime import date

from harbor.core.backtest_config import DividendConfig
from harbor.core.backtest_domain import Currency, Market, Position
from harbor.core.backtest_interfaces import Dividend
from harbor.core.corporate_actions import (
    PositionAdjustment,
    apply_corporate_action,
)
from harbor.core.dividend_processing import CashDividend, pay_dividend
from harbor.core.equity import EntitlementEvent
from harbor.core.ledger import credit
from harbor.core.paper_account import PaperAccountState
from harbor.core.paper_domain import PaperPosition


class PaperAccountEventError(ValueError):
    """Raised when a paper account event cannot be applied (SP 4.53)."""


def apply_paper_dividend(
    state: PaperAccountState,
    *,
    dividend: Dividend,
    quantity: float,
    config: DividendConfig | None = None,
) -> tuple[PaperAccountState, CashDividend | None]:
    """Credit a cash dividend into the account ledger (SP 4.54).

    Reuses SP 2.43 ``pay_dividend``: the dividend is credited in its own
    currency on its payment date; special dividends follow the configuration.
    Returns the updated account and the payment (None when nothing is paid).
    """
    ledger, payment = pay_dividend(
        state.ledger,
        dividend=dividend,
        quantity=quantity,
        config=config,
    )
    return replace(state, ledger=ledger), payment


def credit_paper_cash(
    state: PaperAccountState,
    *,
    currency: Currency,
    amount: float,
) -> PaperAccountState:
    """Credit cash in ``currency`` without an FX conversion (SP 4.53).

    Used for cash inflows that carry no explicit conversion rate (SP 2.43).
    """
    return replace(state, ledger=credit(state.ledger, currency=currency, amount=amount))


def _position_to_backtest(position: PaperPosition, as_of_date: date) -> Position:
    """Convert a paper position to the MVP 2 backtest position."""
    return Position(
        symbol=position.symbol,
        market=position.market,
        quantity=position.quantity,
        average_cost=position.average_cost,
        currency=position.currency,
        as_of_date=as_of_date,
    )


def apply_paper_corporate_action(
    state: PaperAccountState,
    *,
    market: Market,
    symbol: str,
    event: EntitlementEvent,
    as_of_date: date,
) -> tuple[PaperAccountState, PositionAdjustment]:
    """Apply a market-specific corporate action to a held position (SP 4.55).

    Reuses SP 2.44 ``apply_corporate_action``: share actions (split,
    consolidation, rights issue, merger, spin-off) change the quantity; cash
    actions (dividend, tender offer) credit the ledger in the position's
    currency. HK/US action types are validated by the MVP 2 rule (never mixed).

    Raises:
        PaperAccountEventError: If the position is not held, or the action is
            invalid for the position's market (propagated from SP 2.44).
    """
    position = state.position(market, symbol)
    if position is None:
        raise PaperAccountEventError(
            f"Cannot apply a corporate action to unheld {market.value}/{symbol}."
        )
    adjustment = apply_corporate_action(
        _position_to_backtest(position, as_of_date),
        event,
    )
    updated_state = state
    if adjustment.cash_amount != 0.0:
        updated_state = replace(
            updated_state,
            ledger=credit(
                updated_state.ledger,
                currency=position.currency,
                amount=adjustment.cash_amount,
            ),
        )
    new_position = PaperPosition(
        market=position.market,
        symbol=position.symbol,
        quantity=adjustment.new_quantity,
        average_cost=position.average_cost,
        currency=position.currency,
        acquired_at=position.acquired_at,
    )
    updated_positions = tuple(
        new_position if existing.market is market and existing.symbol == symbol else existing
        for existing in updated_state.positions
    )
    return replace(updated_state, positions=updated_positions), adjustment
