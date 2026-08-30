"""Difference verification admission registry (MVP 4 / SP 4.77).

Gates entry to the MVP 5 review on the pre-registered difference-verification
requirements: at least 12 months of running, at least 4 complete rebalances
and 30 fills in every enabled market, sufficient coverage and no unresolved
reconciliation differences (差异验证准入登记, SP 4.77). Any waiver of a
requirement must be granted independently and recorded (豁免需独立批准); an
unwaived failure blocks admission.

The assessment is deterministic and pure: given the same run statistics and
waivers it always returns the same result (replayable).

Pure core logic: depends on the paper domain and monitoring modules; never
touches storage or CLI code.
"""

from dataclasses import dataclass
from datetime import date


class PaperAdmissionError(ValueError):
    """Raised when a verification admission assessment is invalid (SP 4.77)."""


@dataclass(frozen=True)
class AdmissionRequirement:
    """The pre-registered admission requirements (SP 4.77)."""

    min_run_months: int = 12
    min_rebalances_per_market: int = 4
    min_fills_per_market: int = 30
    min_coverage_pct: float = 0.90
    max_unresolved_reconciliation_differences: int = 0

    def __post_init__(self) -> None:
        if self.min_run_months <= 0:
            raise PaperAdmissionError("min_run_months must be positive.")
        if self.min_rebalances_per_market <= 0 or self.min_fills_per_market <= 0:
            raise PaperAdmissionError("market minima must be positive.")
        if not 0 <= self.min_coverage_pct <= 1:
            raise PaperAdmissionError("min_coverage_pct must be in [0, 1].")
        if self.max_unresolved_reconciliation_differences < 0:
            raise PaperAdmissionError(
                "max_unresolved_reconciliation_differences must be non-negative."
            )


@dataclass(frozen=True)
class AdmissionWaiver:
    """A recorded, independently granted waiver of one requirement (SP 4.77)."""

    requirement: str
    approver: str
    reason: str
    granted_at: date

    def __post_init__(self) -> None:
        if not self.requirement or not self.approver or not self.reason:
            raise PaperAdmissionError("requirement, approver and reason must be non-empty.")


@dataclass(frozen=True)
class MarketAdmissionStatus:
    """The per-market admission status (SP 4.77)."""

    market: str
    rebalances: int
    fills: int
    coverage_pct: float
    passed: bool
    reasons: tuple[str, ...]

    def readable(self) -> str:
        """Render the status as a compact summary."""
        status = "passed" if self.passed else "blocked"
        lines = [
            f"{self.market}: {self.rebalances} rebalance(s), {self.fills} fill(s), "
            f"coverage {self.coverage_pct:.1%} — {status}"
        ]
        for reason in self.reasons:
            lines.append(f"  reason: {reason}")
        return "\n".join(lines)


@dataclass(frozen=True)
class VerificationAdmission:
    """The overall verification admission assessment (SP 4.77)."""

    paper_run_id: str
    as_of: date
    run_months: int
    per_market: tuple[MarketAdmissionStatus, ...]
    unresolved_reconciliation_differences: int
    waivers: tuple[AdmissionWaiver, ...]
    passed: bool
    reasons: tuple[str, ...]

    def readable(self) -> str:
        """Render the assessment as a compact summary."""
        status = "ADMITTED" if self.passed else "BLOCKED"
        lines = [
            f"verification admission for {self.paper_run_id} on "
            f"{self.as_of.isoformat()}: {status} ({self.run_months} months)"
        ]
        for market in self.per_market:
            lines.append(f"  {market.readable()}")
        if self.unresolved_reconciliation_differences:
            lines.append(
                f"  unresolved reconciliation differences: "
                f"{self.unresolved_reconciliation_differences}"
            )
        for reason in self.reasons:
            lines.append(f"  reason: {reason}")
        for waiver in self.waivers:
            lines.append(
                f"  waiver {waiver.requirement!r} by {waiver.approver} on "
                f"{waiver.granted_at.isoformat()}: {waiver.reason}"
            )
        return "\n".join(lines)


def _months_between(start: date, end: date) -> int:
    """Return the number of full calendar months between two dates."""
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(months, 0)


def _is_waived(waivers: tuple[AdmissionWaiver, ...], requirement: str) -> bool:
    """Whether a requirement has an independent waiver (SP 4.77)."""
    return any(waiver.requirement == requirement for waiver in waivers)


def assess_verification_admission(
    *,
    paper_run_id: str,
    as_of: date,
    started_at: date,
    fills: dict[str, int],
    rebalances: dict[str, int],
    coverage: dict[str, float],
    unresolved_reconciliation_differences: int,
    requirement: AdmissionRequirement | None = None,
    waivers: tuple[AdmissionWaiver, ...] = (),
) -> VerificationAdmission:
    """Assess whether the paper run may enter the MVP 5 review (SP 4.77).

    Every requirement (run months, per-market rebalances / fills / coverage,
    unresolved differences) must pass or carry an independent waiver; an
    unwaived failure blocks admission with the reasons recorded.
    """
    if not paper_run_id:
        raise PaperAdmissionError("paper_run_id must be non-empty.")
    if as_of < started_at:
        raise PaperAdmissionError("as_of must be on or after started_at.")
    if requirement is None:
        requirement = AdmissionRequirement()
    if unresolved_reconciliation_differences < 0:
        raise PaperAdmissionError("unresolved_reconciliation_differences must be non-negative.")
    markets = sorted(set(fills) | set(rebalances) | set(coverage))
    per_market: list[MarketAdmissionStatus] = []
    for market in markets:
        market_reasons: list[str] = []
        market_fills = fills.get(market, 0)
        market_rebalances = rebalances.get(market, 0)
        market_coverage = coverage.get(market, 0.0)
        if market_rebalances < requirement.min_rebalances_per_market:
            market_reasons.append(
                f"only {market_rebalances} rebalance(s); "
                f"{requirement.min_rebalances_per_market} required"
            )
        if market_fills < requirement.min_fills_per_market:
            market_reasons.append(
                f"only {market_fills} fill(s); {requirement.min_fills_per_market} required"
            )
        if market_coverage < requirement.min_coverage_pct:
            market_reasons.append(
                f"coverage {market_coverage:.1%}; {requirement.min_coverage_pct:.0%} required"
            )
        unwaived = [
            reason
            for reason in market_reasons
            if not _is_waived(waivers, f"{market}:{reason.split(';')[0]}")
            and not _is_waived(waivers, "market")
        ]
        per_market.append(
            MarketAdmissionStatus(
                market=market,
                rebalances=market_rebalances,
                fills=market_fills,
                coverage_pct=market_coverage,
                passed=not market_reasons or not unwaived,
                reasons=tuple(market_reasons),
            )
        )

    run_months = _months_between(started_at, as_of)
    reasons: list[str] = []
    if run_months < requirement.min_run_months and not _is_waived(waivers, "run_months"):
        reasons.append(f"only {run_months} month(s) run; {requirement.min_run_months} required")
    for status in per_market:
        if not status.passed and not _is_waived(waivers, "market"):
            reasons.append(f"{status.market} fails a market requirement")
    if (
        unresolved_reconciliation_differences
        > requirement.max_unresolved_reconciliation_differences
        and not _is_waived(waivers, "reconciliation")
    ):
        reasons.append(
            f"{unresolved_reconciliation_differences} unresolved "
            "reconciliation difference(s); "
            f"{requirement.max_unresolved_reconciliation_differences} allowed"
        )
    passed = not reasons
    return VerificationAdmission(
        paper_run_id=paper_run_id,
        as_of=as_of,
        run_months=run_months,
        per_market=tuple(per_market),
        unresolved_reconciliation_differences=unresolved_reconciliation_differences,
        waivers=waivers,
        passed=passed,
        reasons=tuple(reasons),
    )
