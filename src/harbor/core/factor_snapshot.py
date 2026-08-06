"""Factor snapshot for a rebalance (MVP 2 / SP 2.28).

A factor snapshot records, for every symbol considered at a rebalance, the six
artifacts required for persistence and later explainability:

- the symbol (and its market),
- the raw (unstandardized) factor values (SP 2.17-2.21),
- each input's actual availability date (SP 2.9 / SP 2.15),
- the standardized scores (SP 2.22),
- the composite score and within-market rank/selection (SP 2.24 / SP 2.25 /
  SP 2.26),
- the exclusion reason for symbols that were filtered out (SP 2.23).

The snapshot is a frozen, deterministic structure: mappings are normalized to
key-sorted tuples and entries are ordered by market then symbol, so two runs
over identical inputs persist identical rows (replayability). Persistence
itself lives in the storage layer; this module only defines the domain model
and its builder.

Pure core logic: depends only on the backtest domain types and never touches
storage or CLI code.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from harbor.core.backtest_domain import Market


@dataclass(frozen=True)
class FactorSnapshotInput:
    """Per-symbol inputs from which a snapshot entry is built (SP 2.28).

    ``raw_values`` maps a factor name to its unstandardized value
    (SP 2.17-2.21); a factor that is not assessable is ``None``.
    ``availability_dates`` maps each input (e.g. ``"price"``, ``"dividend"``,
    ``"fundamental"``, ``"fx"``) to the date the input was actually knowable
    (SP 2.9 / SP 2.15). ``standardized_scores`` maps a factor name to its
    standardized score (SP 2.22). ``composite_score`` comes from SP 2.24 and
    ``rank``/``selected`` from the per-market selection (SP 2.25 / SP 2.26).
    ``exclusion_reason`` records why a symbol was excluded (SP 2.23), or is
    ``None`` when the symbol was accepted.
    """

    market: Market
    symbol: str
    raw_values: Mapping[str, float | None] = field(default_factory=dict)
    availability_dates: Mapping[str, date] = field(default_factory=dict)
    standardized_scores: Mapping[str, float | None] = field(default_factory=dict)
    composite_score: float | None = None
    rank: int | None = None
    selected: bool = False
    exclusion_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("symbol must not be empty.")
        if self.rank is not None and self.rank <= 0:
            raise ValueError("rank must be positive when set.")
        if self.selected and self.rank is None:
            raise ValueError("a selected symbol must have a rank.")


@dataclass(frozen=True)
class FactorSnapshotEntry:
    """One symbol's normalized record within a rebalance snapshot.

    Mappings are stored as key-sorted tuples so the record is canonical and
    replayable (SP 2.28).
    """

    market: Market
    symbol: str
    raw_values: tuple[tuple[str, float | None], ...]
    availability_dates: tuple[tuple[str, date], ...]
    standardized_scores: tuple[tuple[str, float | None], ...]
    composite_score: float | None
    rank: int | None
    selected: bool
    exclusion_reason: str | None


def _fmt(value: float | None) -> str:
    """Format a nullable float for readable output."""
    return "n/a" if value is None else f"{value:.4f}"


@dataclass(frozen=True)
class FactorSnapshot:
    """The full factor snapshot for one rebalance (SP 2.28).

    ``entries`` are ordered deterministically by market then symbol.
    """

    as_of: date
    entries: tuple[FactorSnapshotEntry, ...]

    def for_market(self, market: Market) -> tuple[FactorSnapshotEntry, ...]:
        """Return the snapshot entries belonging to ``market``."""
        return tuple(entry for entry in self.entries if entry.market is market)

    def readable(self) -> str:
        """Render the snapshot as a human-readable summary."""
        lines = [f"Factor snapshot for {self.as_of.isoformat()}:"]
        for entry in self.entries:
            raw = ", ".join(f"{name}={_fmt(value)}" for name, value in entry.raw_values)
            available = ", ".join(
                f"{name}@{day.isoformat()}" for name, day in entry.availability_dates
            )
            scores = ", ".join(f"{name}={_fmt(value)}" for name, value in entry.standardized_scores)
            if entry.selected:
                status = "selected"
            elif entry.rank is not None:
                status = "ranked"
            else:
                status = "not ranked"
            rank_text = "n/a" if entry.rank is None else str(entry.rank)
            line = (
                f"- {entry.market.value}/{entry.symbol}: raw {raw}; "
                f"available {available}; standardized {scores}; "
                f"composite {_fmt(entry.composite_score)}; rank {rank_text} ({status})"
            )
            if entry.exclusion_reason is not None:
                line += f"; excluded: {entry.exclusion_reason}"
            lines.append(line)
        return "\n".join(lines)


def build_factor_snapshot(
    as_of: date,
    entries: Sequence[FactorSnapshotInput],
) -> FactorSnapshot:
    """Normalize per-symbol inputs into a deterministic snapshot (SP 2.28).

    Each input's mappings are converted to key-sorted tuples and the entries
    are ordered by market then symbol, so the snapshot is canonical and
    replayable.

    Raises:
        ValueError: If two inputs share the same ``(market, symbol)``.
    """
    normalized: list[FactorSnapshotEntry] = []
    seen: set[tuple[Market, str]] = set()
    for entry in entries:
        key = (entry.market, entry.symbol)
        if key in seen:
            raise ValueError(f"Duplicate snapshot symbol {entry.market.value}/{entry.symbol}.")
        seen.add(key)
        normalized.append(
            FactorSnapshotEntry(
                market=entry.market,
                symbol=entry.symbol,
                raw_values=tuple(sorted(entry.raw_values.items())),
                availability_dates=tuple(sorted(entry.availability_dates.items())),
                standardized_scores=tuple(sorted(entry.standardized_scores.items())),
                composite_score=entry.composite_score,
                rank=entry.rank,
                selected=entry.selected,
                exclusion_reason=entry.exclusion_reason,
            )
        )
    ordered = sorted(normalized, key=lambda item: (item.market.value, item.symbol))
    return FactorSnapshot(as_of=as_of, entries=tuple(ordered))
