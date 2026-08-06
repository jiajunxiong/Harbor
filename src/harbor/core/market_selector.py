"""Per-market stock selector (MVP 2 / SP 2.25 HK, SP 2.26 US).

Selects a target number of symbols from a market's candidate set (SP 2.23) by
their composite score (SP 2.24), preserving the input snapshot and the full
ranking detail for the factor snapshot (SP 2.28) and the explainability report
(SP 2.32).

Only candidates with a score are rankable and selectable; a ``None`` score
(unrankable) stays in the candidate snapshot but appears neither in the
ranking nor in the selection. Ordering is best-first with ties broken
deterministically by symbol ascending (SP 2.24's tie rule), so the selection is
replayable. When fewer than ``target_count`` symbols are scored, all of them
are selected.

The Hong Kong (SP 2.25) and United States (SP 2.26) selectors share this one
implementation; the thin wrappers fix the market so the two story points are
exercised explicitly.

Pure core logic: depends only on the backtest domain types and the scoring
tie rule, and never touches storage or CLI code.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from harbor.core.backtest_domain import Market
from harbor.core.factor_scoring import rank_symbols


@dataclass(frozen=True)
class SelectionRank:
    """Ranking detail for one scored candidate (SP 2.25/2.26)."""

    symbol: str
    score: float
    rank: int
    selected: bool


@dataclass(frozen=True)
class SelectionResult:
    """The selector outcome with its input snapshot and ranking detail."""

    market: Market
    as_of: date
    target_count: int
    candidates: tuple[str, ...]
    selected: tuple[str, ...]
    rankings: tuple[SelectionRank, ...]

    def readable(self) -> str:
        """Render the selection as a human-readable summary."""
        lines = [
            f"Selection for {self.market.value} on {self.as_of.isoformat()} "
            f"(target {self.target_count}):"
        ]
        lines.append(f"selected ({len(self.selected)}): {', '.join(self.selected) or 'none'}")
        for rank in self.rankings:
            status = "selected" if rank.selected else "not selected"
            lines.append(f"{rank.rank}. {rank.symbol} ({rank.score:.4f}) {status}")
        return "\n".join(lines)


def _scored(scores: Mapping[str, float | None]) -> dict[str, float]:
    """Return the non-missing scores as a plain mapping."""
    return {symbol: score for symbol, score in scores.items() if score is not None}


def select_candidates(
    market: Market,
    as_of: date,
    scores: Mapping[str, float | None],
    *,
    target_count: int,
) -> SelectionResult:
    """Select ``target_count`` symbols from the scored candidates (SP 2.25/2.26).

    Args:
        market: The market being selected from.
        as_of: The decision date.
        scores: Composite score per candidate symbol; ``None`` means the symbol
            cannot be ranked.
        target_count: The number of symbols to select.

    Raises:
        ValueError: If ``target_count`` is not positive.
    """
    if target_count <= 0:
        raise ValueError("target_count must be positive.")
    scored = _scored(scores)
    ordered = rank_symbols(scores)
    selected = ordered[:target_count]
    selected_set = set(selected)
    rankings = tuple(
        SelectionRank(
            symbol=symbol,
            score=scored[symbol],
            rank=index + 1,
            selected=symbol in selected_set,
        )
        for index, symbol in enumerate(ordered)
    )
    return SelectionResult(
        market=market,
        as_of=as_of,
        target_count=target_count,
        candidates=tuple(sorted(scores)),
        selected=selected,
        rankings=rankings,
    )


def select_hk_candidates(
    as_of: date,
    scores: Mapping[str, float | None],
    *,
    target_count: int,
) -> SelectionResult:
    """SP 2.25: select from the Hong Kong candidate pool."""
    return select_candidates(Market.HK, as_of, scores, target_count=target_count)


def select_us_candidates(
    as_of: date,
    scores: Mapping[str, float | None],
    *,
    target_count: int,
) -> SelectionResult:
    """SP 2.26: select from the United States candidate pool."""
    return select_candidates(Market.US, as_of, scores, target_count=target_count)
