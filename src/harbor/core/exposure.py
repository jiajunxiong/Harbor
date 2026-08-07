"""Exposure and concentration metrics (MVP 2 / SP 2.55).

Computes, for each trading day, the exposure of the portfolio (SP 2.45
:class:`~harbor.core.valuation.DailyValuation`) as fractions of total net
value:

- market exposure (市场暴露): per-market securities value / total value;
- currency exposure (币种暴露): per-currency securities + cash value / total;
- individual exposure (个股暴露): per ``(market, symbol)`` value / total;
- cash exposure (现金暴露): cash / total value;
- optional industry exposure (行业暴露): per-industry value / total, when an
  industry classifier is supplied (otherwise ``None``).

Every exposure is a fraction of the day's total net value, so cash plus the
market (or symbol) exposures sum to one. Cash balances in a foreign currency
are converted to the base currency explicitly; a missing FX rate raises
:class:`ExposureError` rather than assuming 1:1 (SP 2.12). A non-positive total
value also raises, since the exposure is then undefined (never fabricate).

Pure core logic: depends only on the valuation and backtest domain types;
never touches storage or CLI code.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType

from harbor.core.backtest_domain import Currency, Market
from harbor.core.valuation import DailyValuation


class ExposureError(ValueError):
    """Raised when an exposure cannot be computed (SP 2.55)."""


@dataclass(frozen=True)
class ExposurePoint:
    """The exposure breakdown for one trading day (SP 2.55)."""

    as_of: date
    base_currency: Currency
    total_value: float
    cash_exposure: float
    market_exposure: MappingProxyType[Market, float]
    currency_exposure: MappingProxyType[Currency, float]
    symbol_exposure: MappingProxyType[tuple[Market, str], float]
    industry_exposure: MappingProxyType[str, float] | None

    def readable(self) -> str:
        """Render the day's exposure breakdown as a summary."""
        lines = [
            f"exposure on {self.as_of.isoformat()} "
            f"({self.base_currency.value}, total {self.total_value:.2f}):",
            f"  cash: {self.cash_exposure:.4%}",
        ]
        for market, exposure in sorted(
            self.market_exposure.items(), key=lambda item: item[0].value
        ):
            lines.append(f"  market {market.value}: {exposure:.4%}")
        for (market, symbol), exposure in sorted(
            self.symbol_exposure.items(), key=lambda item: (item[0][0].value, item[0][1])
        ):
            lines.append(f"  {market.value}/{symbol}: {exposure:.4%}")
        if self.industry_exposure is not None:
            for industry, exposure in sorted(self.industry_exposure.items()):
                lines.append(f"  industry {industry}: {exposure:.4%}")
        return "\n".join(lines)


@dataclass(frozen=True)
class ExposureSeries:
    """The day-by-day exposure series for a run (SP 2.55)."""

    points: tuple[ExposurePoint, ...]

    def __post_init__(self) -> None:
        if not self.points:
            raise ValueError("An exposure series requires at least one day.")

    def cash_series(self) -> tuple[tuple[date, float], ...]:
        """Return the ``(day, cash exposure)`` series, in date order."""
        return tuple((point.as_of, point.cash_exposure) for point in self.points)

    def market_series(self, market: Market) -> tuple[tuple[date, float], ...]:
        """Return the ``(day, market exposure)`` series for one market."""
        return tuple((point.as_of, point.market_exposure.get(market, 0.0)) for point in self.points)

    def currency_series(self, currency: Currency) -> tuple[tuple[date, float], ...]:
        """Return the ``(day, currency exposure)`` series for one currency."""
        return tuple(
            (point.as_of, point.currency_exposure.get(currency, 0.0)) for point in self.points
        )

    def symbol_series(self, market: Market, symbol: str) -> tuple[tuple[date, float], ...]:
        """Return the ``(day, symbol exposure)`` series for one position."""
        return tuple(
            (point.as_of, point.symbol_exposure.get((market, symbol), 0.0)) for point in self.points
        )

    def industry_series(self, industry: str) -> tuple[tuple[date, float], ...]:
        """Return the ``(day, industry exposure)`` series for one industry."""
        return tuple(
            (
                point.as_of,
                point.industry_exposure.get(industry, 0.0)
                if point.industry_exposure is not None
                else 0.0,
            )
            for point in self.points
        )

    def readable(self) -> str:
        """Render the full exposure series."""
        return "\n".join(point.readable() for point in self.points)


def _base_cash(
    valuation: DailyValuation,
    fx_rate: Callable[[Currency, Currency, date], float | None],
) -> dict[Currency, float]:
    """Return per-currency cash value in the base currency (SP 2.55)."""
    result: dict[Currency, float] = {}
    for balance in valuation.cash:
        if balance.currency is valuation.base_currency:
            result[balance.currency] = result.get(balance.currency, 0.0) + balance.amount
            continue
        rate = fx_rate(balance.currency, valuation.base_currency, valuation.as_of)
        if rate is None or rate <= 0:
            raise ExposureError(
                f"Missing FX rate to value {balance.currency.value} cash on "
                f"{valuation.as_of.isoformat()}; refusing to assume 1:1."
            )
        result[balance.currency] = result.get(balance.currency, 0.0) + balance.amount * rate
    return result


def compute_exposure_series(
    valuations: Sequence[DailyValuation],
    *,
    fx_rate: Callable[[Currency, Currency, date], float | None],
    industry: Callable[[Market, str], str | None] | None = None,
) -> ExposureSeries:
    """Compute the day-by-day exposure series (SP 2.55).

    Args:
        valuations: The daily valuations in date order (SP 2.45).
        fx_rate: Returns base units per one unit of the source currency for a
            day, or ``None`` when unavailable (SP 2.12).
        industry: Optional classifier mapping ``(market, symbol)`` to an
            industry name; when ``None`` the industry exposure is omitted.

    Returns:
        An :class:`ExposureSeries` with one point per valuation day.

    Raises:
        ExposureError: If a day's total value is not positive, or a foreign
            cash balance lacks a positive FX rate.
    """
    points: list[ExposurePoint] = []
    for valuation in valuations:
        total = valuation.net_value.total_value
        if total <= 0:
            raise ExposureError(
                f"Cannot compute exposure on {valuation.as_of.isoformat()}: "
                f"total value {total:.2f} is not positive."
            )
        cash_base = _base_cash(valuation, fx_rate)
        base_cash_total = sum(cash_base.values())

        market_exposure: dict[Market, float] = {}
        currency_exposure: dict[Currency, float] = dict(cash_base)
        symbol_exposure: dict[tuple[Market, str], float] = {}
        industry_exposure: dict[str, float] = {}
        industries_present = False

        for position in valuation.position_values:
            market_exposure[position.market] = (
                market_exposure.get(position.market, 0.0) + position.market_value_base
            )
            symbol_exposure[(position.market, position.symbol)] = (
                symbol_exposure.get((position.market, position.symbol), 0.0)
                + position.market_value_base
            )
            currency_exposure[position.currency] = (
                currency_exposure.get(position.currency, 0.0) + position.market_value_base
            )
            if industry is not None:
                name = industry(position.market, position.symbol)
                if name is not None:
                    industries_present = True
                    industry_exposure[name] = (
                        industry_exposure.get(name, 0.0) + position.market_value_base
                    )

        market_exposure_fraction = {
            market: value / total for market, value in market_exposure.items()
        }
        currency_exposure_fraction = {
            currency: value / total for currency, value in currency_exposure.items()
        }
        symbol_exposure_fraction = {key: value / total for key, value in symbol_exposure.items()}
        cash_exposure = base_cash_total / total
        industry_exposure_fraction = (
            {name: value / total for name, value in industry_exposure.items()}
            if industries_present and industry is not None
            else None
        )

        points.append(
            ExposurePoint(
                as_of=valuation.as_of,
                base_currency=valuation.base_currency,
                total_value=total,
                cash_exposure=cash_exposure,
                market_exposure=MappingProxyType(market_exposure_fraction),
                currency_exposure=MappingProxyType(currency_exposure_fraction),
                symbol_exposure=MappingProxyType(symbol_exposure_fraction),
                industry_exposure=(
                    MappingProxyType(industry_exposure_fraction)
                    if industry_exposure_fraction is not None
                    else None
                ),
            )
        )
    return ExposureSeries(points=tuple(points))
