"""Three-tier drawdown evaluation for the paper loop (MVP 4 / SP 4.34-4.36).

Evaluates the current drawdown from the net-value peak against the three
pre-registered tiers (5% 预警 / 8% 防御 / 10% 熔断, SP 4.31) and records the
actions each tier triggers:

* 5% (预警, SP 4.34): stop adding risk positions and raise an alert;
* 8% (防御, SP 4.35): reduce the total risk positions to half and pause new
  strategies / parameter changes;
* 10% (熔断, SP 4.36): liquidate non-essential risk positions, freeze new
  orders and enter ``CIRCUIT_BROKEN``.

The tier evaluation is deterministic and pure: given the same net-value series
and risk parameters it always returns the same assessment (replayable).

Pure core logic: depends on the backtest domain (``NetValue``) and the paper
config (SP 4.2); never touches storage or CLI code.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from harbor.core.backtest_domain import NetValue
from harbor.core.paper_config import PaperRiskConfig


class DrawdownTier(StrEnum):
    """The drawdown tier currently in effect (SP 4.34-4.36)."""

    NONE = "NONE"
    WARN = "WARN"
    DEFEND = "DEFEND"
    CIRCUIT = "CIRCUIT"


class PaperDrawdownError(ValueError):
    """Raised when a drawdown cannot be evaluated (SP 4.34)."""


@dataclass(frozen=True)
class DrawdownAssessment:
    """The current drawdown and the actions it triggers (SP 4.34-4.36)."""

    as_of: date
    peak_value: float
    current_value: float
    drawdown: float
    tier: DrawdownTier
    thresholds: tuple[float, float, float]
    actions: tuple[str, ...]
    alert: str | None = None

    @property
    def triggered(self) -> bool:
        """Whether any tier is active (drawdown >= the 5% warning)."""
        return self.tier is not DrawdownTier.NONE

    def readable(self) -> str:
        """Render the assessment as a compact summary."""
        lines = [
            f"drawdown on {self.as_of.isoformat()}: {self.drawdown:.2%} "
            f"from peak {self.peak_value:.2f} (tier {self.tier.value})"
        ]
        for action in self.actions:
            lines.append(f"  action: {action}")
        if self.alert is not None:
            lines.append(f"  alert: {self.alert}")
        return "\n".join(lines)


def _actions_for(tier: DrawdownTier, drawdown: float) -> tuple[str, ...]:
    """Return the pre-registered actions for a drawdown tier (SP 4.34-4.36)."""
    if tier is DrawdownTier.WARN:
        return ("stop adding risk positions",)
    if tier is DrawdownTier.DEFEND:
        return (
            "reduce total risk positions to half",
            "pause new strategies and parameter changes",
        )
    if tier is DrawdownTier.CIRCUIT:
        return (
            "liquidate non-essential risk positions",
            "freeze new orders",
            "enter CIRCUIT_BROKEN",
        )
    return ()


def evaluate_drawdown(
    *,
    net_values: Sequence[NetValue],
    risk: PaperRiskConfig | None = None,
) -> DrawdownAssessment:
    """Evaluate the current drawdown tier from a net-value series (SP 4.34-4.36).

    The drawdown is ``(peak - latest) / peak`` over the series' running peak.
    Tiers use the pre-registered 5% / 8% / 10% thresholds (SP 4.31).

    Raises:
        PaperDrawdownError: If the series is empty, not ascending, or has a
            non-positive value.
    """
    if not net_values:
        raise PaperDrawdownError("At least one net value is required.")
    for index, net_value in enumerate(net_values):
        if index > 0 and net_value.as_of_date <= net_values[index - 1].as_of_date:
            raise PaperDrawdownError("Net values must be strictly ascending by date.")
        if net_value.total_value <= 0:
            raise PaperDrawdownError("Net values must be positive.")
    if risk is None:
        risk = PaperRiskConfig()
    warn, defend, circuit = (
        risk.drawdown_warn_pct,
        risk.drawdown_defend_pct,
        risk.drawdown_circuit_pct,
    )

    peak = max(net_value.total_value for net_value in net_values)
    latest = net_values[-1].total_value
    drawdown = (peak - latest) / peak
    if drawdown >= circuit:
        tier = DrawdownTier.CIRCUIT
    elif drawdown >= defend:
        tier = DrawdownTier.DEFEND
    elif drawdown >= warn:
        tier = DrawdownTier.WARN
    else:
        tier = DrawdownTier.NONE

    alert: str | None = None
    if tier is DrawdownTier.CIRCUIT:
        alert = f"drawdown reached the {circuit:.0%} circuit threshold; freezing new orders."
    elif tier is DrawdownTier.DEFEND:
        alert = f"drawdown reached the {defend:.0%} defense threshold."
    elif tier is DrawdownTier.WARN:
        alert = f"drawdown reached the {warn:.0%} warning threshold."

    return DrawdownAssessment(
        as_of=net_values[-1].as_of_date,
        peak_value=peak,
        current_value=latest,
        drawdown=drawdown,
        tier=tier,
        thresholds=(warn, defend, circuit),
        actions=_actions_for(tier, drawdown),
        alert=alert,
    )
