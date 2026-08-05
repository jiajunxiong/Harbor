"""Earnings quality factor (MVP 2 / SP 2.21).

Builds a quality score from the four fundamental fields ROE, net income,
revenue and total equity, each mapped to a 0-1 component (higher is better):

- ROE: ``0`` at or below zero, rising linearly to ``1`` at a configured
  ``roe_upper`` (default 30%), flat at 1 above it.
- net income / revenue / total equity: ``1`` when positive, ``0`` when present
  and non-positive.

The composite score is the mean of the components that are present, so a field
with a missing value is skipped rather than treated as zero. When no component
is available the score is ``None`` with a readable reason.

The report is used strictly by its disclosure availability date (SP 2.9 / SP
2.15): a missing report, an undated report, or a report disclosed after the
decision date is refused rather than silently used, so no look-ahead can enter.

Pure core logic: depends only on the backtest domain types and never touches
storage or CLI code.
"""

from dataclasses import dataclass
from datetime import date

from harbor.core.backtest_interfaces import FundamentalRecord


@dataclass(frozen=True)
class EarningsQualityConfig:
    """Parameters for the earnings quality factor (SP 2.21)."""

    roe_upper: float = 0.3

    def __post_init__(self) -> None:
        if self.roe_upper <= 0:
            raise ValueError("roe_upper must be positive.")


def roe_score(roe: float | None, *, roe_upper: float = 0.3) -> float | None:
    """Score a ROE on a 0-1 scale; ``None`` when the value is missing.

    ``roe <= 0`` scores 0, values at or above ``roe_upper`` score 1, and values
    in between scale linearly.

    Raises:
        ValueError: If ``roe_upper`` is not positive.
    """
    if roe_upper <= 0:
        raise ValueError("roe_upper must be positive.")
    if roe is None:
        return None
    if roe <= 0:
        return 0.0
    if roe >= roe_upper:
        return 1.0
    return roe / roe_upper


def positive_value_score(value: float | None) -> float | None:
    """Score a value as ``1`` when positive, ``0`` when present and non-positive.

    Returns ``None`` when the value is missing.
    """
    if value is None:
        return None
    return 1.0 if value > 0 else 0.0


@dataclass(frozen=True)
class EarningsQualityResult:
    """The earnings quality score for one symbol on a decision date.

    ``value`` is the composite 0-1 score, or ``None`` when no component is
    available or the report is not usable. ``missing_fields`` lists the
    fundamental fields that were absent; ``missing_reason`` explains why the
    score is ``None``. ``report_date`` and ``available_on`` preserve the
    report's disclosure traceability (SP 2.15).
    """

    value: float | None
    roe_score: float | None
    net_income_score: float | None
    revenue_score: float | None
    equity_score: float | None
    missing_fields: tuple[str, ...]
    missing_reason: str | None
    report_date: date | None
    available_on: date | None
    decision_date: date


def _refused_result(
    reason: str,
    decision_date: date,
    fundamental: FundamentalRecord | None,
) -> EarningsQualityResult:
    """Build a score-less result for an unusable report."""
    return EarningsQualityResult(
        value=None,
        roe_score=None,
        net_income_score=None,
        revenue_score=None,
        equity_score=None,
        missing_fields=(),
        missing_reason=reason,
        report_date=fundamental.report_date if fundamental is not None else None,
        available_on=fundamental.available_on if fundamental is not None else None,
        decision_date=decision_date,
    )


def earnings_quality_factor(
    fundamental: FundamentalRecord | None,
    decision_date: date,
    *,
    config: EarningsQualityConfig | None = None,
) -> EarningsQualityResult:
    """Compute the earnings quality score on a decision date (SP 2.21).

    The report must be point-in-time available (SP 2.9 / SP 2.15): an undated
    report or one disclosed after ``decision_date`` is refused rather than
    silently used. Component scores are averaged over the fields present; when
    no field is present the score is ``None``.

    Args:
        fundamental: The latest report knowable on or before the decision date
            (SP 2.15), or ``None`` when unavailable.
        decision_date: The date the factor is evaluated on.
        config: Optional factor parameters.
    """
    if config is None:
        config = EarningsQualityConfig()
    if fundamental is None:
        return _refused_result("no point-in-time financial data available", decision_date, None)
    if fundamental.available_on is None:
        return _refused_result("report has no known disclosure date", decision_date, fundamental)
    if fundamental.available_on > decision_date:
        return _refused_result(
            "report not yet available on the decision date", decision_date, fundamental
        )

    roe_s = roe_score(fundamental.roe, roe_upper=config.roe_upper)
    net_income_s = positive_value_score(fundamental.net_income)
    revenue_s = positive_value_score(fundamental.revenue)
    equity_s = positive_value_score(fundamental.total_equity)
    components = [
        score for score in (roe_s, net_income_s, revenue_s, equity_s) if score is not None
    ]
    missing_fields = tuple(
        name
        for name, value in (
            ("roe", fundamental.roe),
            ("net_income", fundamental.net_income),
            ("revenue", fundamental.revenue),
            ("total_equity", fundamental.total_equity),
        )
        if value is None
    )
    if not components:
        value: float | None = None
        missing_reason = "no ROE, net income, revenue or equity available"
    else:
        value = sum(components) / len(components)
        missing_reason = None
    return EarningsQualityResult(
        value=value,
        roe_score=roe_s,
        net_income_score=net_income_s,
        revenue_score=revenue_s,
        equity_score=equity_s,
        missing_fields=missing_fields,
        missing_reason=missing_reason,
        report_date=fundamental.report_date,
        available_on=fundamental.available_on,
        decision_date=decision_date,
    )
