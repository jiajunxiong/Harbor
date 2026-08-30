"""Daily and monthly circuit breakers for the paper loop (MVP 4 / SP 4.37-4.38).

The daily breaker (SP 4.37) trips when a single-day loss exceeds the
pre-registered threshold, freezing new orders and producing an audit event.
The monthly breaker (SP 4.38) trips when the month-to-date loss exceeds its
threshold and additionally requires an independent review before recovery
(SP 4.42). A breaker assessment is deterministic and pure: the same loss and
threshold always produce the same decision.

Pure core logic: depends on the paper domain (``CircuitBreakerKind``) and the
paper config (SP 4.2); never touches storage or CLI code.
"""

from dataclasses import dataclass
from datetime import date

from harbor.core.paper_config import PaperRiskConfig
from harbor.core.paper_domain import CircuitBreakerKind


class PaperBreakerError(ValueError):
    """Raised when a breaker assessment is invalid (SP 4.37 / 4.38)."""


@dataclass(frozen=True)
class BreakerAssessment:
    """Whether a daily/monthly loss trips a circuit breaker (SP 4.37 / 4.38)."""

    as_of: date
    kind: CircuitBreakerKind
    loss_fraction: float
    threshold: float
    triggered: bool
    reason: str
    requires_review: bool = False

    def readable(self) -> str:
        """Render the assessment as a compact summary."""
        state = "TRIGGERED" if self.triggered else "CLEAR"
        review = " (independent review required)" if self.requires_review else ""
        return (
            f"{self.kind.value} breaker {state} on {self.as_of.isoformat()}: "
            f"loss {self.loss_fraction:.2%} vs threshold {self.threshold:.2%}"
            f"{review} — {self.reason}"
        )


def evaluate_daily_breaker(
    *,
    as_of: date,
    daily_loss_pct: float,
    risk: PaperRiskConfig | None = None,
    threshold: float | None = None,
) -> BreakerAssessment:
    """Evaluate the daily circuit breaker (SP 4.37).

    A single-day loss above ``threshold`` (defaults to the pre-registered
    daily-loss limit) trips the breaker, freezing new orders and producing an
    audit event (handled by the orchestration layer).

    Raises:
        PaperBreakerError: If ``daily_loss_pct`` is not non-negative or the
            threshold is not within (0, 1].
    """
    limit = (
        threshold
        if threshold is not None
        else (risk.daily_loss_limit_pct if risk is not None else 0.05)
    )
    if daily_loss_pct < 0:
        raise PaperBreakerError("daily_loss_pct must be non-negative.")
    if not 0.0 < limit <= 1.0:
        raise PaperBreakerError("The daily loss threshold must be within (0, 1].")
    triggered = daily_loss_pct > limit
    if triggered:
        reason = (
            f"daily loss {daily_loss_pct:.2%} exceeded the {limit:.2%} "
            "threshold; freezing new orders."
        )
    else:
        reason = f"daily loss {daily_loss_pct:.2%} within the {limit:.2%} threshold."
    return BreakerAssessment(
        as_of=as_of,
        kind=CircuitBreakerKind.DAILY,
        loss_fraction=daily_loss_pct,
        threshold=limit,
        triggered=triggered,
        reason=reason,
    )


def evaluate_monthly_breaker(
    *,
    as_of: date,
    monthly_loss_pct: float,
    risk: PaperRiskConfig | None = None,
    threshold: float | None = None,
) -> BreakerAssessment:
    """Evaluate the monthly circuit breaker (SP 4.38).

    A month-to-date loss above ``threshold`` (defaults to the pre-registered
    monthly-loss limit) trips the breaker, freezing the run and flagging that
    an independent review is required before recovery (SP 4.42).

    Raises:
        PaperBreakerError: If ``monthly_loss_pct`` is not non-negative or the
            threshold is not within (0, 1].
    """
    limit = (
        threshold
        if threshold is not None
        else (risk.monthly_loss_limit_pct if risk is not None else 0.10)
    )
    if monthly_loss_pct < 0:
        raise PaperBreakerError("monthly_loss_pct must be non-negative.")
    if not 0.0 < limit <= 1.0:
        raise PaperBreakerError("The monthly loss threshold must be within (0, 1].")
    triggered = monthly_loss_pct > limit
    if triggered:
        reason = (
            f"monthly loss {monthly_loss_pct:.2%} exceeded the {limit:.2%} "
            "threshold; freezing and triggering an independent review."
        )
    else:
        reason = f"monthly loss {monthly_loss_pct:.2%} within the {limit:.2%} threshold."
    return BreakerAssessment(
        as_of=as_of,
        kind=CircuitBreakerKind.MONTHLY,
        loss_fraction=monthly_loss_pct,
        threshold=limit,
        triggered=triggered,
        reason=reason,
        requires_review=triggered,
    )
