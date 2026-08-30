"""Paper actual execution metrics (MVP 4 / SP 4.70).

Collects the actual execution parameters of each paper fill — realized
slippage against the reference price, the observed spread, the execution
latency and the volume participation (模拟盘实际参数采集, SP 4.70). These are the
"as executed" measurements fed to SP 4.71's difference metrics, so the paper
loop always compares against what was actually observed, never a guess.

Slippage is directional but recorded as a positive cost in basis points (a
buy pays up, a sell receives less) and the participation rate is the filled
quantity relative to the day's volume; invalid values are rejected rather
than silently normalized.

Pure core logic: depends on the paper domain and the SP 2.39 slippage helper;
never touches storage or CLI code.
"""

from dataclasses import dataclass
from datetime import date

from harbor.core.backtest_domain import Market, OrderSide
from harbor.core.paper_domain import PaperFill


class PaperActualMetricsError(ValueError):
    """Raised when paper actual metrics are invalid (SP 4.70)."""


def realized_slippage_bps(fill: PaperFill, reference_price: float) -> float:
    """Return the realized slippage of ``fill`` in basis points (SP 4.70).

    Always a positive cost: buys compare against a higher fill price, sells
    against a lower one (mirroring SP 2.39 ``apply_slippage``).
    """
    if reference_price <= 0:
        raise PaperActualMetricsError("reference_price must be positive.")
    if fill.price <= 0:
        raise PaperActualMetricsError("fill price must be positive.")
    if fill.side is OrderSide.BUY:
        return (fill.price - reference_price) / reference_price * 10_000.0
    return (reference_price - fill.price) / reference_price * 10_000.0


@dataclass(frozen=True)
class ActualExecutionMetrics:
    """The observed execution parameters of one paper fill (SP 4.70)."""

    paper_run_id: str
    market: Market
    symbol: str
    trade_date: date
    fill_id: str
    fill_price: float
    quantity: float
    fee: float
    reference_price: float
    slippage_bps: float
    spread_bps: float
    execution_latency_seconds: float
    participation_rate: float

    def readable(self) -> str:
        """Render the actual metrics as a compact summary."""
        return (
            f"{self.market.value}/{self.symbol} on {self.trade_date.isoformat()} "
            f"({self.fill_id}): fill {self.fill_price:g} vs reference "
            f"{self.reference_price:g} (slippage {self.slippage_bps:.2f} bps), "
            f"spread {self.spread_bps:.2f} bps, latency "
            f"{self.execution_latency_seconds:.1f}s, participation "
            f"{self.participation_rate:.2%}"
        )


def collect_paper_actual_metrics(
    *,
    paper_run_id: str,
    fill: PaperFill,
    reference_price: float,
    spread_bps: float,
    execution_latency_seconds: float,
    day_volume: int,
) -> ActualExecutionMetrics:
    """Collect the actual execution metrics of one fill (SP 4.70).

    Args:
        paper_run_id: The paper run that produced the fill.
        fill: The executed fill.
        reference_price: The reference price the fill is measured against
            (SP 2.39), used to derive the realized slippage.
        spread_bps: The observed bid-ask spread in basis points.
        execution_latency_seconds: The observed signal-to-fill latency (SP 4.23).
        day_volume: The day's traded volume used for the participation rate.

    Raises:
        PaperActualMetricsError: If the inputs are invalid or the fill exceeds
            the day volume.
    """
    if not paper_run_id:
        raise PaperActualMetricsError("paper_run_id must be non-empty.")
    if reference_price <= 0:
        raise PaperActualMetricsError("reference_price must be positive.")
    if spread_bps < 0:
        raise PaperActualMetricsError("spread_bps must be non-negative.")
    if execution_latency_seconds < 0:
        raise PaperActualMetricsError("execution_latency_seconds must be non-negative.")
    if day_volume <= 0:
        raise PaperActualMetricsError("day_volume must be positive.")
    if fill.quantity > day_volume:
        raise PaperActualMetricsError("fill quantity cannot exceed the day volume.")
    slippage = realized_slippage_bps(fill, reference_price)
    participation = fill.quantity / day_volume
    if not 0 <= participation <= 1:
        raise PaperActualMetricsError("participation_rate must be in [0, 1].")
    return ActualExecutionMetrics(
        paper_run_id=paper_run_id,
        market=fill.market,
        symbol=fill.symbol,
        trade_date=fill.trade_date,
        fill_id=fill.fill_id,
        fill_price=fill.price,
        quantity=fill.quantity,
        fee=fill.fee,
        reference_price=reference_price,
        slippage_bps=slippage,
        spread_bps=spread_bps,
        execution_latency_seconds=execution_latency_seconds,
        participation_rate=participation,
    )
