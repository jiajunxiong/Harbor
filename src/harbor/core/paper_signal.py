"""Signal model for the paper loop (MVP 4 / SP 4.14).

Normalizes a strategy's output (target weights per market, the rebalance day
and the source research run/version) into a validated
:class:`~harbor.core.paper_domain.SignalIntention`. The target weights must
sum within ``(0, 1]`` — a fully-cash signal or an overweighted signal is
rejected rather than silently normalized — and every weight is non-negative.
The resulting intention carries its source MVP 2/3 research run and strategy
version so every downstream order is traceable (SP 4.25).

Pure core logic: depends on the paper domain and never touches storage or CLI
code.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone

from harbor.core.backtest_domain import Market
from harbor.core.paper_domain import SignalIntention

_WEIGHT_TOLERANCE = 1e-9


class PaperSignalError(ValueError):
    """Raised when a strategy signal cannot be normalized (SP 4.14)."""


def _now_utc() -> datetime:
    """Return the current UTC time (default signal timestamp)."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class MarketSignal:
    """One market's normalized strategy output (SP 4.14).

    ``target_weights`` maps symbol -> target weight within the market on
    ``rebalance_date``.
    """

    market: Market
    rebalance_date: date
    target_weights: tuple[tuple[str, float], ...]

    def readable(self) -> str:
        """Render the signal spec as a compact summary."""
        weights = ", ".join(f"{symbol}:{weight:g}" for symbol, weight in self.target_weights)
        return f"{self.market.value} on {self.rebalance_date.isoformat()} {{{weights}}}"


def build_signal_intention(
    *,
    intention_id: str,
    paper_run_id: str,
    strategy: str,
    strategy_version: str,
    market: Market,
    rebalance_date: date,
    target_weights: tuple[tuple[str, float], ...],
    source_run_id: str | None = None,
    created_at: datetime | None = None,
) -> SignalIntention:
    """Normalize a strategy output into a validated signal intention (SP 4.14).

    Requires at least one target weight, non-negative weights and a total
    within ``(0, 1]``. ``source_run_id`` links the signal to the MVP 2/3
    research run that produced it (SP 4.25 traceability).

    Raises:
        PaperSignalError: If the weights are empty, negative, or their sum is
            outside ``(0, 1]``.
        ValueError: If the intention identity fields are empty (propagated from
            :class:`SignalIntention`).
    """
    if not target_weights:
        raise PaperSignalError("A signal must declare at least one target weight.")
    total = sum(weight for _symbol, weight in target_weights)
    if any(weight < 0 for _symbol, weight in target_weights):
        raise PaperSignalError("Target weights must be non-negative.")
    if total <= 0 or total > 1.0 + _WEIGHT_TOLERANCE:
        raise PaperSignalError(f"Target weights must sum within (0, 1], got {total}.")
    timestamp = _now_utc() if created_at is None else created_at
    return SignalIntention(
        intention_id=intention_id,
        paper_run_id=paper_run_id,
        strategy=strategy,
        strategy_version=strategy_version,
        market=market,
        rebalance_date=rebalance_date,
        target_weights=target_weights,
        source_run_id=source_run_id,
        created_at=timestamp,
    )
