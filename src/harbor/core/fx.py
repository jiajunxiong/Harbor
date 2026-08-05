"""Currency conversion via daily FX rates (MVP 2 / SP 2.12).

Converts HKD/USD amounts into a benchmark (base) currency using an explicit FX
rate. When the source and target currency differ, a positive rate is required:
a missing or non-positive rate is refused rather than assuming a 1:1 exchange,
so a cross-currency position is never silently misvalued (SP 2.12 / MVP 2
acceptance criteria).

This module is pure core logic; it depends only on the backtest domain types.
"""

from harbor.core.backtest_domain import Currency


class FxConversionError(ValueError):
    """Raised when a currency conversion cannot be performed."""


def convert(amount: float, rate: float) -> float:
    """Convert ``amount`` at ``rate`` (units of target per one source unit).

    Raises:
        FxConversionError: If ``rate`` is not positive.
    """
    if rate <= 0:
        raise FxConversionError(f"FX rate must be positive, got {rate}.")
    return amount * rate


def convert_to_base(
    amount: float,
    from_currency: Currency,
    base_currency: Currency,
    rate: float | None,
) -> float:
    """Convert ``amount`` from ``from_currency`` into ``base_currency``.

    When the currencies are equal the amount is returned unchanged (no FX
    needed). Otherwise a positive ``rate`` is required; a missing (``None``) or
    non-positive rate is refused rather than assuming 1:1 (SP 2.12).

    Raises:
        FxConversionError: If the currencies differ and ``rate`` is missing or
            not positive.
    """
    if from_currency is base_currency:
        return amount
    if rate is None:
        raise FxConversionError(
            f"Missing FX rate to convert {from_currency.value} to "
            f"{base_currency.value}; refusing to assume 1:1 (SP 2.12)."
        )
    return convert(amount, rate)
