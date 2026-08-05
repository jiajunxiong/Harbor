"""Data readiness precheck for a backtest run (MVP 2 / SP 2.13).

Runs before a backtest starts to verify coverage of prices, fundamentals, FX
rates, the stock pool and corporate actions across the configured markets, and
produces human-readable findings. Errors block the run; warnings surface
documented limitations.

The precheck operates on the reader, calendar, FX and stock-pool contracts, so
it is pure core logic and fully testable with fakes. It never touches storage
or CLI code.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from harbor.core.backtest_config import BacktestConfig
from harbor.core.backtest_domain import Currency, Market, to_market_target
from harbor.core.backtest_interfaces import BacktestDataReader, TradingCalendar
from harbor.core.market_registry import get_market_config
from harbor.core.stock_pool import StockPool


class PrecheckSeverity(StrEnum):
    """Severity of a readiness finding."""

    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class PrecheckFinding:
    """A single readiness finding with a human-readable message."""

    severity: PrecheckSeverity
    scope: str
    message: str


@dataclass(frozen=True)
class PrecheckReport:
    """The outcome of a data readiness precheck."""

    findings: tuple[PrecheckFinding, ...]

    @property
    def errors(self) -> tuple[PrecheckFinding, ...]:
        """Return only the blocking findings."""
        return tuple(
            finding for finding in self.findings if finding.severity is PrecheckSeverity.ERROR
        )

    @property
    def has_errors(self) -> bool:
        """Return whether any blocking finding is present."""
        return bool(self.errors)

    def readable(self) -> str:
        """Render the findings as a human-readable summary."""
        if not self.findings:
            return "Data readiness precheck passed."
        lines = ["Data readiness precheck:"]
        for finding in self.findings:
            lines.append(f"- [{finding.severity.value.upper()}] {finding.scope}: {finding.message}")
        if self.has_errors:
            lines.append("Blocking errors found; backtest cannot start.")
        else:
            lines.append("No blocking errors; proceed with documented limitations.")
        return "\n".join(lines)


def _quote_currency(market: Market) -> Currency:
    """Return the currency securities in ``market`` are quoted in."""
    return Currency(get_market_config(to_market_target(market)).currency)


def run_precheck(
    config: BacktestConfig,
    reader: BacktestDataReader,
    calendar: TradingCalendar,
    *,
    fx_rate: Callable[[Currency, Currency, date], float | None],
    stock_pool: Callable[[Market, date], StockPool],
) -> PrecheckReport:
    """Run the data readiness precheck for a configured backtest run.

    Args:
        config: The validated backtest configuration.
        reader: Point-in-time backtest data access.
        calendar: The trading calendar used to derive trading days.
        fx_rate: Returns the last known FX rate (from→to) on or before a date,
            or ``None`` when unavailable.
        stock_pool: Returns the historical stock pool of a market on a date.

    Returns:
        A :class:`PrecheckReport` with one finding per detected issue.
        Coverage is checked at ``config.start_date`` (a representative as-of
        date) and over the configured date range.
    """
    findings: list[PrecheckFinding] = []
    as_of = config.start_date
    for quota in config.market_quotas:
        market = quota.market
        scope = market.value
        trading_days = calendar.trading_days(market, config.start_date, config.end_date)
        if not trading_days:
            findings.append(
                PrecheckFinding(
                    PrecheckSeverity.ERROR,
                    scope,
                    "no trading days in the configured date range",
                )
            )
        pool = stock_pool(market, as_of)
        if not pool.symbols:
            findings.append(
                PrecheckFinding(
                    PrecheckSeverity.ERROR, scope, "stock pool is empty on the as-of date"
                )
            )
        if pool.survivorship_bias_risk:
            findings.append(
                PrecheckFinding(
                    PrecheckSeverity.WARNING,
                    scope,
                    f"survivorship-bias risk: {pool.risk_reason}",
                )
            )
        quote_currency = _quote_currency(market)
        for symbol in pool.symbols:
            quotes = reader.daily_quotes(market, symbol, config.start_date, config.end_date)
            if not quotes:
                findings.append(
                    PrecheckFinding(
                        PrecheckSeverity.ERROR,
                        f"{scope}/{symbol}",
                        "no daily quotes in the backtest range",
                    )
                )
            fundamentals = reader.fundamentals(market, symbol, as_of)
            if not fundamentals:
                findings.append(
                    PrecheckFinding(
                        PrecheckSeverity.WARNING,
                        f"{scope}/{symbol}",
                        "no point-in-time fundamentals as of the as-of date",
                    )
                )
        if quote_currency is not config.base_currency:
            rate = fx_rate(quote_currency, config.base_currency, as_of)
            if rate is None:
                findings.append(
                    PrecheckFinding(
                        PrecheckSeverity.ERROR,
                        f"{scope}/fx",
                        f"missing FX {quote_currency.value}->{config.base_currency.value} "
                        "as of the as-of date; refusing to assume 1:1",
                    )
                )
        action_count = sum(
            len(reader.corporate_actions(market, symbol, config.start_date, config.end_date))
            for symbol in pool.symbols
        )
        if action_count == 0:
            findings.append(
                PrecheckFinding(
                    PrecheckSeverity.WARNING,
                    f"{scope}/corporate_actions",
                    "no corporate actions found in range; coverage cannot be confirmed",
                )
            )
    return PrecheckReport(tuple(findings))
