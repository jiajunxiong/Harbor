"""Cash dividend processing (MVP 2 / SP 2.43).

Credits cash dividends into the currency ledger using the three dividend dates:
the entitlement date (登记日, falling back to the ex date 除净日) decides which
position quantity is entitled, and the payment date (支付日) is when the cash is
credited. Entitlement is a pure function of the position quantity held on the
entitlement date; the orchestration layer (SP 2.47) supplies that quantity and
only calls this module once the payment date is reached, so there is no
look-ahead.

Special dividends follow the strategy configuration (特别股息遵循策略配置): the
:class:`~harbor.core.backtest_config.DividendConfig.include_special` flag
decides whether ``is_special`` dividends are paid into the ledger at all.

The cash is credited in the dividend's own currency (no FX conversion here);
the ledger's acquisition rate is unchanged, matching the fill convention
(SP 2.42).

Pure core logic: depends only on the domain types, the configuration and the
ledger; never touches storage or CLI code.
"""

from dataclasses import dataclass
from datetime import date

from harbor.core.backtest_config import DividendConfig
from harbor.core.backtest_domain import Currency, Market
from harbor.core.backtest_interfaces import Dividend
from harbor.core.ledger import Ledger, credit


def entitlement_date(dividend: Dividend) -> date:
    """Return the date that determines dividend entitlement (SP 2.43).

    Uses the record date (登记日) when present, otherwise the ex date (除净日).
    """
    return dividend.record_date if dividend.record_date is not None else dividend.ex_date


def include_dividend(dividend: Dividend, config: DividendConfig | None = None) -> bool:
    """Whether a dividend is paid into the ledger (SP 2.43).

    Regular dividends are always included; special dividends follow the
    strategy configuration (特别股息遵循策略配置).

    Args:
        dividend: The declared dividend.
        config: The dividend configuration (SP 2.4); defaults to including
            special dividends.
    """
    if config is None:
        config = DividendConfig()
    if dividend.is_special and not config.include_special:
        return False
    return True


@dataclass(frozen=True)
class CashDividend:
    """A cash dividend payment credited to the ledger (SP 2.43)."""

    market: Market
    symbol: str
    currency: Currency
    entitlement_date: date
    payment_date: date
    quantity: float
    per_share: float
    gross_amount: float
    is_special: bool

    def readable(self) -> str:
        """Render the payment as a human-readable summary."""
        tag = " (special)" if self.is_special else ""
        return (
            f"dividend {self.symbol} ({self.currency.value}): "
            f"{self.quantity:.2f} shares x {self.per_share:.4f} = "
            f"{self.gross_amount:.2f} paid on {self.payment_date.isoformat()}"
            f"{tag}"
        )


def pay_dividend(
    ledger: Ledger,
    *,
    dividend: Dividend,
    quantity: float,
    config: DividendConfig | None = None,
) -> tuple[Ledger, CashDividend | None]:
    """Credit a cash dividend into the ledger on its payment date (SP 2.43).

    Args:
        ledger: The current ledger.
        dividend: The declared dividend.
        quantity: The position quantity held on the entitlement date.
        config: The dividend configuration (SP 2.4); defaults to including
            special dividends.

    Returns:
        The updated ledger and the :class:`CashDividend` payment, or ``None``
        when nothing is paid (no entitlement or a special dividend excluded by
        the configuration).

    Raises:
        ValueError: If ``quantity`` is negative.
    """
    if quantity < 0:
        raise ValueError("Position quantity must be non-negative.")
    if not include_dividend(dividend, config):
        return ledger, None
    if quantity == 0:
        return ledger, None
    gross = quantity * dividend.amount
    updated = credit(ledger, currency=dividend.currency, amount=gross)
    payment = CashDividend(
        market=dividend.market,
        symbol=dividend.symbol,
        currency=dividend.currency,
        entitlement_date=entitlement_date(dividend),
        payment_date=dividend.payment_date
        if dividend.payment_date is not None
        else dividend.ex_date,
        quantity=quantity,
        per_share=dividend.amount,
        gross_amount=gross,
        is_special=dividend.is_special,
    )
    return updated, payment
