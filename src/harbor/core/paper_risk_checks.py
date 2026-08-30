"""Daily and monthly risk checks for the paper loop (MVP 4 / SP 4.32-4.33).

The daily check (SP 4.32) evaluates every order before opening: per-trade risk
(单笔 ≤ 0.5%), available cash, single-stock concentration (单股 ≤ 5%) and
single-industry concentration (单行业 ≤ 20%). The monthly check (SP 4.33)
evaluates market / industry / currency exposure and cumulative metrics; an
over-limit item triggers a limit finding and an alert. Violating orders are
rejected with a recorded reason — never silently adjusted.

Pure core logic: depends on the paper config (SP 4.2) and never touches
storage or CLI code.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from harbor.core.backtest_domain import Currency, Market
from harbor.core.paper_config import PaperRiskConfig


class RiskFindingSeverity(StrEnum):
    """Whether a finding blocks the order or is only an alert."""

    ERROR = "ERROR"
    WARNING = "WARNING"


class RiskFindingKind(StrEnum):
    """The kind of risk rule that produced a finding (SP 4.32 / 4.33)."""

    TRADE_RISK = "TRADE_RISK"
    CASH = "CASH"
    CONCENTRATION_SINGLE_STOCK = "CONCENTRATION_SINGLE_STOCK"
    CONCENTRATION_INDUSTRY = "CONCENTRATION_INDUSTRY"
    EXPOSURE_MARKET = "EXPOSURE_MARKET"
    EXPOSURE_CURRENCY = "EXPOSURE_CURRENCY"
    EXPOSURE_INDUSTRY = "EXPOSURE_INDUSTRY"
    CUMULATIVE_LOSS = "CUMULATIVE_LOSS"


@dataclass(frozen=True)
class RiskFinding:
    """One risk-rule violation or alert (SP 4.32 / 4.33)."""

    kind: RiskFindingKind
    rule: str
    reason: str
    severity: RiskFindingSeverity
    market: Market | None = None
    symbol: str | None = None

    def __post_init__(self) -> None:
        if not self.rule or not self.reason:
            raise ValueError("Risk finding rule and reason must be non-empty.")

    def readable(self) -> str:
        """Render the finding as a compact summary."""
        scope = ""
        if self.market is not None and self.symbol is not None:
            scope = f" {self.market.value}/{self.symbol}"
        return f"[{self.severity.value}] {self.kind.value}{scope}: {self.reason} (rule {self.rule})"


@dataclass(frozen=True)
class DailyRiskInput:
    """One order's inputs for the daily risk check (SP 4.32)."""

    market: Market
    symbol: str
    trade_notional_base: float
    position_value_base: float
    industry: str | None = None

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("DailyRiskInput symbol must be non-empty.")
        if self.trade_notional_base < 0 or self.position_value_base < 0:
            raise ValueError("DailyRiskInput values must be non-negative.")


@dataclass(frozen=True)
class DailyRiskContext:
    """The portfolio context for a daily risk check (SP 4.32)."""

    portfolio_value: float
    available_cash: float
    risk: PaperRiskConfig

    def __post_init__(self) -> None:
        if self.portfolio_value <= 0:
            raise ValueError("portfolio_value must be positive.")
        if self.available_cash < 0:
            raise ValueError("available_cash must be non-negative.")


@dataclass(frozen=True)
class DailyRiskReport:
    """The findings of a daily risk check (SP 4.32)."""

    as_of: date
    findings: tuple[RiskFinding, ...]

    @property
    def approved(self) -> bool:
        """Whether no blocking (ERROR) finding was raised."""
        return all(finding.severity is not RiskFindingSeverity.ERROR for finding in self.findings)

    def findings_for(self, symbol: str) -> tuple[RiskFinding, ...]:
        """Return the findings targeting ``symbol``."""
        return tuple(finding for finding in self.findings if finding.symbol == symbol)

    def readable(self) -> str:
        """Render the report as a compact summary."""
        status = "approved" if self.approved else "BLOCKED"
        lines = [f"daily risk check {self.as_of.isoformat()}: {status}"]
        for finding in self.findings:
            lines.append(f"  {finding.readable()}")
        return "\n".join(lines)


def run_daily_risk_check(
    *,
    as_of: date,
    inputs: Sequence[DailyRiskInput],
    context: DailyRiskContext,
) -> DailyRiskReport:
    """Run the pre-open daily risk check over every order (SP 4.32).

    Checks, per order, in fixed order: per-trade risk (单笔), available cash,
    single-stock concentration (单股) and single-industry concentration
    (单行业). A violating order is reported with an explicit reason.

    Raises:
        ValueError: If the context or an input is invalid.
    """
    findings: list[RiskFinding] = []
    industry_values: dict[str, float] = {}
    for item in inputs:
        risk = context.risk
        if item.trade_notional_base / context.portfolio_value > risk.max_position_pct:
            findings.append(
                RiskFinding(
                    kind=RiskFindingKind.TRADE_RISK,
                    rule="max_position_pct",
                    reason=(
                        f"per-trade risk {item.trade_notional_base / context.portfolio_value:.2%} "
                        f"exceeds {risk.max_position_pct:.2%}."
                    ),
                    severity=RiskFindingSeverity.ERROR,
                    market=item.market,
                    symbol=item.symbol,
                )
            )
        if item.trade_notional_base > context.available_cash:
            findings.append(
                RiskFinding(
                    kind=RiskFindingKind.CASH,
                    rule="available_cash",
                    reason=(
                        f"trade notional {item.trade_notional_base:.2f} exceeds "
                        f"available cash {context.available_cash:.2f}."
                    ),
                    severity=RiskFindingSeverity.ERROR,
                    market=item.market,
                    symbol=item.symbol,
                )
            )
        single_stock = (
            item.position_value_base + item.trade_notional_base
        ) / context.portfolio_value
        if single_stock > risk.max_single_stock_pct:
            findings.append(
                RiskFinding(
                    kind=RiskFindingKind.CONCENTRATION_SINGLE_STOCK,
                    rule="max_single_stock_pct",
                    reason=(
                        f"single-stock exposure {single_stock:.2%} exceeds "
                        f"{risk.max_single_stock_pct:.2%}."
                    ),
                    severity=RiskFindingSeverity.ERROR,
                    market=item.market,
                    symbol=item.symbol,
                )
            )
        if item.industry is not None:
            industry_values[item.industry] = (
                industry_values.get(item.industry, 0.0)
                + item.position_value_base
                + item.trade_notional_base
            )
    for industry, total in industry_values.items():
        exposure = total / context.portfolio_value
        if exposure > context.risk.max_industry_pct:
            findings.append(
                RiskFinding(
                    kind=RiskFindingKind.CONCENTRATION_INDUSTRY,
                    rule="max_industry_pct",
                    reason=(
                        f"industry {industry} exposure {exposure:.2%} exceeds "
                        f"{context.risk.max_industry_pct:.2%}."
                    ),
                    severity=RiskFindingSeverity.ERROR,
                )
            )
    findings.sort(key=lambda finding: (finding.severity.value, finding.kind.value))
    return DailyRiskReport(as_of=as_of, findings=tuple(findings))


@dataclass(frozen=True)
class MonthlyRiskInput:
    """One position's inputs for the monthly risk check (SP 4.33)."""

    market: Market
    symbol: str
    position_value_base: float
    currency: Currency
    industry: str | None = None

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("MonthlyRiskInput symbol must be non-empty.")
        if self.position_value_base < 0:
            raise ValueError("MonthlyRiskInput value must be non-negative.")


@dataclass(frozen=True)
class MonthlyRiskLimits:
    """The monthly exposure limits (SP 4.33).

    ``max_industry_pct`` defaults to the daily risk config's industry cap.
    """

    max_market_pct: float = 1.0
    max_currency_pct: float = 1.0
    max_industry_pct: float | None = None
    cumulative_loss_limit_pct: float = 0.10

    def __post_init__(self) -> None:
        for name, value in (
            ("max_market_pct", self.max_market_pct),
            ("max_currency_pct", self.max_currency_pct),
            ("cumulative_loss_limit_pct", self.cumulative_loss_limit_pct),
        ):
            if not 0.0 < value <= 1.0:
                raise ValueError(f"{name} must be within (0, 1].")


@dataclass(frozen=True)
class MonthlyRiskReport:
    """The findings of a monthly risk check (SP 4.33)."""

    as_of: date
    findings: tuple[RiskFinding, ...]
    exposures: tuple[tuple[str, float], ...]

    @property
    def exceeded_limits(self) -> tuple[str, ...]:
        """The exposed dimensions that breached their limit (SP 4.33)."""
        prefix_by_kind: tuple[tuple[RiskFindingKind, str], ...] = (
            (RiskFindingKind.EXPOSURE_MARKET, "market:"),
            (RiskFindingKind.EXPOSURE_CURRENCY, "currency:"),
            (RiskFindingKind.EXPOSURE_INDUSTRY, "industry:"),
            (RiskFindingKind.CUMULATIVE_LOSS, "cumulative_loss"),
        )
        names: list[str] = []
        for name, _exposure in self.exposures:
            for kind, prefix in prefix_by_kind:
                if name.startswith(prefix) and any(
                    finding.kind is kind and finding.severity is RiskFindingSeverity.ERROR
                    for finding in self.findings
                ):
                    names.append(name)
                    break
        return tuple(names)

    def readable(self) -> str:
        """Render the report as a compact summary."""
        lines = [f"monthly risk check {self.as_of.isoformat()}"]
        for name, exposure in self.exposures:
            lines.append(f"  {name}: {exposure:.2%}")
        for finding in self.findings:
            lines.append(f"  {finding.readable()}")
        return "\n".join(lines)


def run_monthly_risk_check(
    *,
    as_of: date,
    inputs: Sequence[MonthlyRiskInput],
    portfolio_value: float,
    cumulative_loss_pct: float,
    risk: PaperRiskConfig,
    limits: MonthlyRiskLimits | None = None,
) -> MonthlyRiskReport:
    """Run the monthly risk check (SP 4.33).

    Evaluates market / industry / currency exposure from the position values
    and the cumulative loss; an over-limit dimension raises a limit finding
    (限额与告警).

    Raises:
        ValueError: If ``portfolio_value`` is not positive.
    """
    if portfolio_value <= 0:
        raise ValueError("portfolio_value must be positive.")
    if limits is None:
        limits = MonthlyRiskLimits()
    industry_limit = (
        limits.max_industry_pct if limits.max_industry_pct is not None else risk.max_industry_pct
    )

    market_values: dict[Market, float] = {}
    currency_values: dict[Currency, float] = {}
    industry_values: dict[str, float] = {}
    for item in inputs:
        market_values[item.market] = market_values.get(item.market, 0.0) + item.position_value_base
        currency_values[item.currency] = (
            currency_values.get(item.currency, 0.0) + item.position_value_base
        )
        if item.industry is not None:
            industry_values[item.industry] = (
                industry_values.get(item.industry, 0.0) + item.position_value_base
            )

    findings: list[RiskFinding] = []
    exposures: list[tuple[str, float]] = []
    for market, total in market_values.items():
        exposure = total / portfolio_value
        exposures.append((f"market:{market.value}", exposure))
        if exposure > limits.max_market_pct:
            findings.append(
                RiskFinding(
                    kind=RiskFindingKind.EXPOSURE_MARKET,
                    rule="max_market_pct",
                    reason=f"market {market.value} exposure {exposure:.2%} exceeds limit.",
                    severity=RiskFindingSeverity.ERROR,
                )
            )
    for currency, total in currency_values.items():
        exposure = total / portfolio_value
        exposures.append((f"currency:{currency.value}", exposure))
        if exposure > limits.max_currency_pct:
            findings.append(
                RiskFinding(
                    kind=RiskFindingKind.EXPOSURE_CURRENCY,
                    rule="max_currency_pct",
                    reason=f"currency {currency.value} exposure {exposure:.2%} exceeds limit.",
                    severity=RiskFindingSeverity.ERROR,
                )
            )
    for industry, total in industry_values.items():
        exposure = total / portfolio_value
        exposures.append((f"industry:{industry}", exposure))
        if exposure > industry_limit:
            findings.append(
                RiskFinding(
                    kind=RiskFindingKind.EXPOSURE_INDUSTRY,
                    rule="max_industry_pct",
                    reason=f"industry {industry} exposure {exposure:.2%} exceeds limit.",
                    severity=RiskFindingSeverity.ERROR,
                )
            )
    exposures.append(("cumulative_loss", cumulative_loss_pct))
    if cumulative_loss_pct > limits.cumulative_loss_limit_pct:
        findings.append(
            RiskFinding(
                kind=RiskFindingKind.CUMULATIVE_LOSS,
                rule="cumulative_loss_limit_pct",
                reason=(
                    f"cumulative loss {cumulative_loss_pct:.2%} exceeds "
                    f"{limits.cumulative_loss_limit_pct:.2%}."
                ),
                severity=RiskFindingSeverity.ERROR,
            )
        )
    findings.sort(key=lambda finding: (finding.severity.value, finding.kind.value))
    return MonthlyRiskReport(
        as_of=as_of,
        findings=tuple(findings),
        exposures=tuple(exposures),
    )
