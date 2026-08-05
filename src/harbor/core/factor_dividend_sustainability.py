"""Dividend sustainability factor (MVP 2 / SP 2.18).

Scores how sustainable a dividend is from two components, each on a 0-1 scale
(higher is better):

- ``continuity_score``: how consistently regular dividends are paid, measured
  as the number of regular payments in the trailing window relative to a
  configured ``expected_payments`` cadence, capped at 1.0. Special dividends
  are one-off payments and do not count toward recurring continuity by default.
- ``payout_ratio_score``: how well the dividend is covered by earnings. The
  payout ratio is ``annualized_regular_dividends / net_income`` (SP 2.17's
  annualization). A payout fully covered by earnings (<= 100%) scores 1.0; a
  payout above 100% is increasingly unsustainable and declines to 0 at the
  configured ``max_sustainable_payout``.

The factor consumes inputs already aligned to a decision date (SP 2.15) and
re-filters dividends defensively so no future ex-date can enter. When the
payout cannot be assessed — no point-in-time financial record, or no positive
net income — the score is ``None`` and ``missing_reason`` explains why, rather
than fabricating a number. A company that demonstrably pays no regular
dividends but has earnings scores 0.0 (not missing).

Pure core logic: depends only on the backtest domain types and the dividend
yield annualization, and never touches storage or CLI code.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from harbor.core.backtest_interfaces import Dividend, FundamentalRecord
from harbor.core.factor_dividend_yield import annualize_dividend_sum


@dataclass(frozen=True)
class DividendSustainabilityConfig:
    """Parameters for the dividend sustainability factor (SP 2.18).

    ``lookback_days`` is the trailing window used to count payments; a payment
    counts when its ex-date falls in the window on or before the decision date.
    ``expected_payments`` is the cadence assumed for full continuity. A payout
    at or above ``max_sustainable_payout`` scores 0.
    """

    lookback_days: int = 365
    expected_payments: int = 4
    max_sustainable_payout: float = 2.0
    include_special: bool = False

    def __post_init__(self) -> None:
        if self.lookback_days <= 0:
            raise ValueError("lookback_days must be positive.")
        if self.expected_payments <= 0:
            raise ValueError("expected_payments must be positive.")
        if self.max_sustainable_payout <= 1.0:
            raise ValueError("max_sustainable_payout must exceed 1.0.")


def _in_window(dividend: Dividend, decision_date: date, lookback_days: int) -> bool:
    """Return whether the dividend's ex-date falls in the trailing window."""
    window_start = decision_date - timedelta(days=lookback_days)
    return window_start <= dividend.ex_date <= decision_date


def payout_ratio_score(
    payout_ratio: float,
    *,
    max_sustainable: float = 2.0,
) -> float:
    """Score a payout ratio on a 0-1 scale (higher = more sustainable).

    A payout fully covered by earnings (in (0, 1]) scores 1.0; a payout above
    ``1.0`` is increasingly unsustainable and declines linearly to 0 at
    ``max_sustainable``. A zero or negative payout (no dividend) scores 0.

    Raises:
        ValueError: If ``max_sustainable`` is not above 1.0.
    """
    if max_sustainable <= 1.0:
        raise ValueError("max_sustainable must exceed 1.0.")
    if payout_ratio <= 0:
        return 0.0
    if payout_ratio <= 1.0:
        return 1.0
    if payout_ratio >= max_sustainable:
        return 0.0
    return 1.0 - (payout_ratio - 1.0) / (max_sustainable - 1.0)


@dataclass(frozen=True)
class DividendSustainabilityResult:
    """The dividend sustainability score for one symbol on a decision date.

    ``value`` is the composite 0-1 score (mean of the available components), or
    ``None`` when the payout cannot be assessed. ``continuity_score`` is always
    present; ``payout_ratio`` and ``payout_ratio_score`` are present only when a
    positive ``net_income`` is available. ``missing_reason`` explains which
    inputs were missing or why the payout was not assessed.
    """

    value: float | None
    continuity_score: float
    payout_ratio: float | None
    payout_ratio_score: float | None
    regular_payments: int
    regular_sum: float
    special_sum: float
    missing_reason: str | None
    decision_date: date
    include_special: bool


def dividend_sustainability_factor(
    dividends: tuple[Dividend, ...] | list[Dividend],
    fundamental: FundamentalRecord | None,
    decision_date: date,
    *,
    config: DividendSustainabilityConfig | None = None,
) -> DividendSustainabilityResult:
    """Compute the dividend sustainability score on a decision date (SP 2.18).

    Regular dividends contribute to continuity and to the payout numerator by
    default; special dividends are tracked separately and count only when
    ``include_special`` is true. Only ex-dates on or before ``decision_date``
    within the trailing window are used.

    Args:
        dividends: Dividends aligned to the decision date (SP 2.15).
        fundamental: The latest report knowable on or before the decision date
            (SP 2.15), or ``None`` when unavailable.
        decision_date: The date the factor is evaluated on.
        config: Optional factor parameters.
    """
    if config is None:
        config = DividendSustainabilityConfig()
    regular_sum = 0.0
    special_sum = 0.0
    regular_payments = 0
    for dividend in dividends:
        if not _in_window(dividend, decision_date, config.lookback_days):
            continue
        if dividend.is_special:
            special_sum += dividend.amount
            if config.include_special:
                regular_sum += dividend.amount
                regular_payments += 1
        else:
            regular_sum += dividend.amount
            regular_payments += 1

    continuity_score = min(regular_payments / config.expected_payments, 1.0)

    payout_ratio: float | None = None
    payout_ratio_score_value: float | None = None
    reasons: list[str] = []
    net_income = fundamental.net_income if fundamental is not None else None
    if fundamental is None:
        reasons.append("no point-in-time financial data available")
    elif net_income is None or net_income <= 0:
        reasons.append("no positive net income available")
    else:
        payout_ratio = annualize_dividend_sum(regular_sum, config.lookback_days) / net_income
        payout_ratio_score_value = payout_ratio_score(
            payout_ratio,
            max_sustainable=config.max_sustainable_payout,
        )
    if regular_payments == 0:
        reasons.append("no regular dividends paid in the trailing window")

    value: float | None
    if payout_ratio_score_value is None:
        value = None
    else:
        value = (continuity_score + payout_ratio_score_value) / 2.0

    return DividendSustainabilityResult(
        value=value,
        continuity_score=continuity_score,
        payout_ratio=payout_ratio,
        payout_ratio_score=payout_ratio_score_value,
        regular_payments=regular_payments,
        regular_sum=regular_sum,
        special_sum=special_sum,
        missing_reason="; ".join(reasons) if reasons else None,
        decision_date=decision_date,
        include_special=config.include_special,
    )
