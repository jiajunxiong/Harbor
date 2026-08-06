"""Single-market candidate filtering (MVP 2 / SP 2.23).

Filters a market's stock pool down to tradable candidates on a decision date,
excluding symbols with a readable reason:

- delisted before the as-of date, not yet listed on it, or with an unknown
  listing window (from the membership windows, SP 2.10);
- insufficient price history (fewer than ``min_history_observations``
  observations in the window, SP 2.16/2.20);
- insufficient liquidity (average daily turnover below
  ``min_average_turnover``, or not assessable — SP 2.20);
- suspended too long (``suspension_ratio`` above ``max_suspension_ratio``, SP
  2.20);
- incomplete data (missing factor/metric inputs, e.g. no point-in-time
  fundamental).

Checks are applied in a fixed order and the first failing check determines the
reason, so results are deterministic and replayable. The excluded symbols and
their reasons are preserved for the factor snapshot (SP 2.28) and the
selection explainability report (SP 2.32).

Pure core logic: depends only on the stock pool contract (SP 2.10) and never
touches storage or CLI code.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date

from harbor.core.backtest_domain import Market
from harbor.core.stock_pool import StockPoolMembership


@dataclass(frozen=True)
class CandidateFilterConfig:
    """Thresholds for the single-market candidate filter (SP 2.23)."""

    min_history_observations: int = 60
    min_average_turnover: float = 0.0
    max_suspension_ratio: float = 0.3

    def __post_init__(self) -> None:
        if self.min_history_observations < 0:
            raise ValueError("min_history_observations must be non-negative.")
        if self.min_average_turnover < 0:
            raise ValueError("min_average_turnover must be non-negative.")
        if not 0.0 <= self.max_suspension_ratio <= 1.0:
            raise ValueError("max_suspension_ratio must be in [0.0, 1.0].")


@dataclass(frozen=True)
class CandidateInputs:
    """Per-symbol tradability inputs for the filter (SP 2.23).

    ``observation_count`` is the number of price observations in the history
    window (SP 2.16). ``average_turnover`` and ``suspension_ratio`` come from
    the drawdown and liquidity factor (SP 2.20); ``None`` means the metric was
    not assessable. ``data_complete`` reports whether the factor inputs (e.g.
    the point-in-time fundamental) are available.
    """

    observation_count: int
    average_turnover: float | None
    suspension_ratio: float | None
    data_complete: bool


@dataclass(frozen=True)
class CandidateOutcome:
    """The filter result for one symbol."""

    symbol: str
    accepted: bool
    reason: str | None


@dataclass(frozen=True)
class CandidateFilterResult:
    """The filtered candidate set and the excluded symbols with reasons."""

    market: Market
    as_of: date
    candidates: tuple[str, ...]
    excluded: tuple[CandidateOutcome, ...]

    def readable(self) -> str:
        """Render the filter outcome as a human-readable summary."""
        lines = [f"Candidate filter for {self.market.value} on {self.as_of.isoformat()}:"]
        lines.append(f"accepted ({len(self.candidates)}): {', '.join(self.candidates) or 'none'}")
        for outcome in self.excluded:
            lines.append(f"- EXCLUDED {outcome.symbol}: {outcome.reason}")
        return "\n".join(lines)


def _membership_reason(
    membership: StockPoolMembership,
    as_of: date,
) -> str | None:
    """Return an exclusion reason for a membership window, or ``None`` if active."""
    effective = membership.effective_date
    expiry = membership.expiry_date
    if effective is not None and effective > as_of:
        return "not yet listed on the as-of date"
    if expiry is not None and expiry < as_of:
        return "delisted before the as-of date"
    if effective is None:
        return "listing window unknown on the as-of date"
    return None


def _tradability_reason(
    candidate: CandidateInputs,
    config: CandidateFilterConfig,
) -> str | None:
    """Return an exclusion reason from tradability inputs, or ``None`` if ok."""
    if candidate.observation_count < config.min_history_observations:
        return (
            f"insufficient history ({candidate.observation_count} observations < "
            f"{config.min_history_observations})"
        )
    if candidate.average_turnover is None:
        return "insufficient liquidity (average turnover not assessable)"
    if candidate.average_turnover < config.min_average_turnover:
        return (
            f"insufficient liquidity (average turnover "
            f"{candidate.average_turnover:.2f} < {config.min_average_turnover})"
        )
    if (
        candidate.suspension_ratio is not None
        and candidate.suspension_ratio > config.max_suspension_ratio
    ):
        return (
            f"suspended too long (ratio {candidate.suspension_ratio:.2f} > "
            f"{config.max_suspension_ratio})"
        )
    if not candidate.data_complete:
        return "incomplete data"
    return None


def filter_candidates(
    market: Market,
    as_of: date,
    memberships: Sequence[StockPoolMembership],
    inputs: Mapping[str, CandidateInputs],
    *,
    config: CandidateFilterConfig | None = None,
) -> CandidateFilterResult:
    """Filter a market's pool to tradable candidates on ``as_of`` (SP 2.23).

    The full membership set (including delisted and not-yet-listed symbols) is
    evaluated for its validity window on ``as_of``; each active symbol is then
    checked for history, liquidity, suspension and data completeness. The first
    failing check determines the exclusion reason.

    Args:
        market: The market being filtered.
        as_of: The decision date.
        memberships: The full set of known memberships for the market.
        inputs: Tradability inputs keyed by symbol (for active memberships).
        config: Optional filter thresholds.
    """
    if config is None:
        config = CandidateFilterConfig()
    outcomes: list[CandidateOutcome] = []
    for membership in memberships:
        reason = _membership_reason(membership, as_of)
        if reason is not None:
            outcomes.append(CandidateOutcome(membership.symbol, False, reason))
            continue
        symbol = membership.symbol
        symbol_inputs = inputs.get(symbol)
        if symbol_inputs is None:
            outcomes.append(CandidateOutcome(symbol, False, "incomplete data"))
            continue
        reason = _tradability_reason(symbol_inputs, config)
        if reason is not None:
            outcomes.append(CandidateOutcome(symbol, False, reason))
            continue
        outcomes.append(CandidateOutcome(symbol, True, None))
    candidates = tuple(sorted(outcome.symbol for outcome in outcomes if outcome.accepted))
    excluded = tuple(outcome for outcome in outcomes if not outcome.accepted)
    return CandidateFilterResult(
        market=market,
        as_of=as_of,
        candidates=candidates,
        excluded=excluded,
    )
