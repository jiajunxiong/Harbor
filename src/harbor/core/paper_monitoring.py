"""Paper monitoring and period summaries (MVP 4 / SP 4.75 / 4.76).

Takes a daily snapshot of the paper loop — net value, drawdown tier,
concentration, difference counts and reconciliation state (模拟盘监控, SP 4.75)
— and rolls the snapshots up into daily / weekly / monthly summaries covering
fills, reconciliation, differences, alerts and approvals (周期运行摘要, SP 4.76).

The snapshot and summaries are pure aggregations of already-computed pieces
(SP 4.8 net values, SP 4.34-4.36 drawdown, SP 4.56 valuation, SP 4.57/4.58
reconciliation, SP 4.71 differences, SP 4.73 alerts), so they stay testable
and deterministic.

Pure core logic: depends on the paper valuation, drawdown, reconciliation and
difference-alert modules; never touches storage or CLI code.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from harbor.core.backtest_domain import NetValue
from harbor.core.paper_config import PaperRiskConfig
from harbor.core.paper_difference_alerts import DifferenceAlert
from harbor.core.paper_drawdown import DrawdownTier, evaluate_drawdown
from harbor.core.paper_reconciliation import (
    PaperReconciliationDifference,
    PaperReconciliationResult,
)
from harbor.core.paper_valuation import PaperValuation


class PaperMonitoringError(ValueError):
    """Raised when a monitoring snapshot or summary is invalid (SP 4.75)."""


class SummaryPeriod(StrEnum):
    """The summary period granularity (SP 4.76)."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


@dataclass(frozen=True)
class PaperDailySnapshot:
    """One day's monitoring snapshot (SP 4.75)."""

    paper_run_id: str
    as_of: date
    net_value: float
    drawdown: float
    drawdown_tier: DrawdownTier
    max_single_stock_pct: float
    difference_count: int
    difference_alert_count: int
    reconciled: bool

    def __post_init__(self) -> None:
        if not self.paper_run_id:
            raise PaperMonitoringError("paper_run_id must be non-empty.")
        if self.net_value <= 0:
            raise PaperMonitoringError("net_value must be positive.")
        if not 0 <= self.max_single_stock_pct <= 1:
            raise PaperMonitoringError("max_single_stock_pct must be in [0, 1].")

    def readable(self) -> str:
        """Render the snapshot as a compact summary."""
        return (
            f"paper snapshot {self.as_of.isoformat()} ({self.paper_run_id}): "
            f"net {self.net_value:.2f}, drawdown {self.drawdown:.2%} "
            f"({self.drawdown_tier.value}), max single stock "
            f"{self.max_single_stock_pct:.2%}, {self.difference_count} difference(s), "
            f"{self.difference_alert_count} alert(s), "
            f"{'reconciled' if self.reconciled else 'MISMATCH'}"
        )


def build_paper_daily_snapshot(
    *,
    paper_run_id: str,
    as_of: date,
    valuation: PaperValuation,
    net_values: Sequence[NetValue],
    risk: PaperRiskConfig | None = None,
    differences: Sequence[PaperReconciliationDifference] = (),
    difference_alerts: Sequence[DifferenceAlert] = (),
    reconciliation: PaperReconciliationResult | None = None,
) -> PaperDailySnapshot:
    """Build the daily monitoring snapshot from computed pieces (SP 4.75).

    The net value comes from the valuation, the drawdown tier from the
    net-value series (SP 4.34-4.36), the concentration from the valuation's
    position values and the difference / reconciliation state from the given
    counts.
    """
    net_value = valuation.total_base
    drawdown = evaluate_drawdown(net_values=net_values, risk=risk)
    total = valuation.total_base
    max_single = 0.0
    if total > 0 and valuation.position_values:
        max_single = max(
            (position.market_value_base / total for position in valuation.position_values),
            default=0.0,
        )
    reconciled = reconciliation.reconciled if reconciliation is not None else not differences
    return PaperDailySnapshot(
        paper_run_id=paper_run_id,
        as_of=as_of,
        net_value=net_value,
        drawdown=drawdown.drawdown,
        drawdown_tier=drawdown.tier,
        max_single_stock_pct=max_single,
        difference_count=len(differences),
        difference_alert_count=len(difference_alerts),
        reconciled=reconciled,
    )


@dataclass(frozen=True)
class PaperPeriodSummary:
    """A daily / weekly / monthly run summary (SP 4.76)."""

    paper_run_id: str
    period: SummaryPeriod
    start: date
    end: date
    snapshot_count: int
    start_net_value: float | None
    end_net_value: float | None
    max_drawdown: float
    fills_count: int
    difference_count: int
    alert_count: int
    approval_count: int
    reconciled_days: int

    def __post_init__(self) -> None:
        if not self.paper_run_id:
            raise PaperMonitoringError("paper_run_id must be non-empty.")
        if self.end < self.start:
            raise PaperMonitoringError("end must be on or after start.")
        if self.max_drawdown < 0:
            raise PaperMonitoringError("max_drawdown must be non-negative.")

    def readable(self) -> str:
        """Render the summary as a compact summary."""
        return (
            f"{self.period.value} summary {self.start.isoformat()}.."
            f"{self.end.isoformat()} ({self.paper_run_id}): "
            f"net {self.start_net_value:.2f} -> {self.end_net_value:.2f}, "
            f"max drawdown {self.max_drawdown:.2%}, {self.fills_count} fill(s), "
            f"{self.difference_count} difference(s), {self.alert_count} alert(s), "
            f"{self.approval_count} approval(s), {self.reconciled_days}/"
            f"{self.snapshot_count} day(s) reconciled"
        )


def build_period_summary(
    *,
    paper_run_id: str,
    period: SummaryPeriod,
    start: date,
    end: date,
    snapshots: Sequence[PaperDailySnapshot],
    fills_count: int = 0,
    difference_count: int = 0,
    alert_count: int = 0,
    approval_count: int = 0,
) -> PaperPeriodSummary:
    """Roll daily snapshots up into a period summary (SP 4.76).

    Only snapshots within ``[start, end]`` are included; the net-value move,
    maximum drawdown and reconciled-day count are derived from them.
    """
    if not paper_run_id:
        raise PaperMonitoringError("paper_run_id must be non-empty.")
    if end < start:
        raise PaperMonitoringError("end must be on or after start.")
    included = tuple(snapshot for snapshot in snapshots if start <= snapshot.as_of <= end)
    start_net_value = included[0].net_value if included else None
    end_net_value = included[-1].net_value if included else None
    max_drawdown = max((snapshot.drawdown for snapshot in included), default=0.0)
    reconciled_days = sum(1 for snapshot in included if snapshot.reconciled)
    return PaperPeriodSummary(
        paper_run_id=paper_run_id,
        period=period,
        start=start,
        end=end,
        snapshot_count=len(included),
        start_net_value=start_net_value,
        end_net_value=end_net_value,
        max_drawdown=max_drawdown,
        fills_count=fills_count,
        difference_count=difference_count,
        alert_count=alert_count,
        approval_count=approval_count,
        reconciled_days=reconciled_days,
    )


def summary_rows(summary: PaperPeriodSummary) -> dict[str, object]:
    """Map a summary to a stable row for querying (SP 4.76)."""
    return {
        "paper_run_id": summary.paper_run_id,
        "period": summary.period.value,
        "start": summary.start.isoformat(),
        "end": summary.end.isoformat(),
        "snapshot_count": summary.snapshot_count,
        "start_net_value": summary.start_net_value,
        "end_net_value": summary.end_net_value,
        "max_drawdown": summary.max_drawdown,
        "fills_count": summary.fills_count,
        "difference_count": summary.difference_count,
        "alert_count": summary.alert_count,
        "approval_count": summary.approval_count,
        "reconciled_days": summary.reconciled_days,
    }


def query_period_summaries(
    summaries: Sequence[PaperPeriodSummary],
    *,
    paper_run_id: str | None = None,
    period: SummaryPeriod | None = None,
) -> tuple[PaperPeriodSummary, ...]:
    """Query summaries by run id and period (SP 4.76)."""
    return tuple(
        summary
        for summary in summaries
        if (paper_run_id is None or summary.paper_run_id == paper_run_id)
        and (period is None or summary.period is period)
    )
