"""Selection explainability report (MVP 2 / SP 2.32).

Renders, for any rebalance date, the full decision trail: the candidate pool,
the exclusion reasons (SP 2.23), the factor rankings behind each composite
score (SP 2.22 / SP 2.24) and the final selected symbols (SP 2.25-2.27).

The report is derived from the persisted factor snapshot (SP 2.28), which
already records every symbol's standardized scores, composite score, within-
market rank, selection flag and exclusion reason, so any rebalance can be
re-explained from its snapshot alone. Output is deterministic and replayable:
entries are ordered by market then symbol, ranked rows by market then rank.

Pure core logic: depends only on the factor snapshot domain model and never
touches storage or CLI code.
"""

from dataclasses import dataclass
from datetime import date

from harbor.core.backtest_domain import Market
from harbor.core.factor_snapshot import FactorSnapshot


def _fmt(value: float | None) -> str:
    """Format a nullable float for readable output."""
    return "n/a" if value is None else f"{value:.4f}"


@dataclass(frozen=True)
class ReportEntry:
    """One symbol's decision record within the explainability report."""

    market: Market
    symbol: str
    factor_scores: tuple[tuple[str, float | None], ...]
    composite_score: float | None
    rank: int | None
    selected: bool
    exclusion_reason: str | None


@dataclass(frozen=True)
class SelectionReport:
    """The explainability report for one rebalance date (SP 2.32)."""

    as_of: date
    entries: tuple[ReportEntry, ...]

    def for_market(self, market: Market) -> tuple[ReportEntry, ...]:
        """Return the report entries belonging to ``market``."""
        return tuple(entry for entry in self.entries if entry.market is market)

    def candidates(self) -> tuple[ReportEntry, ...]:
        """Return the accepted candidate pool (no exclusion reason)."""
        return tuple(entry for entry in self.entries if entry.exclusion_reason is None)

    def excluded(self) -> tuple[ReportEntry, ...]:
        """Return the excluded symbols with their reasons."""
        return tuple(entry for entry in self.entries if entry.exclusion_reason is not None)

    def ranked(self) -> tuple[ReportEntry, ...]:
        """Return the ranked symbols, best first within each market."""
        ranked = [entry for entry in self.entries if entry.rank is not None]
        return tuple(sorted(ranked, key=lambda entry: (entry.market.value, entry.rank or 0)))

    def selected(self) -> tuple[ReportEntry, ...]:
        """Return the final selected symbols, best first within each market."""
        selected = [entry for entry in self.entries if entry.selected]
        return tuple(sorted(selected, key=lambda entry: (entry.market.value, entry.rank or 0)))

    def readable(self) -> str:
        """Render the full decision trail as a human-readable summary."""
        lines = [f"Selection report for {self.as_of.isoformat()}:"]
        markets = sorted({entry.market for entry in self.entries}, key=lambda m: m.value)
        for market in markets:
            market_entries = [entry for entry in self.entries if entry.market is market]
            lines.append(f"  {market.value}:")
            candidates = [e for e in market_entries if e.exclusion_reason is None]
            lines.append(
                f"    candidates ({len(candidates)}): "
                f"{', '.join(e.symbol for e in candidates) or 'none'}"
            )
            for entry in market_entries:
                if entry.exclusion_reason is not None:
                    lines.append(f"    excluded {entry.symbol}: {entry.exclusion_reason}")
            ranked = sorted(
                (e for e in market_entries if e.rank is not None),
                key=lambda e: e.rank or 0,
            )
            for entry in ranked:
                scores = ", ".join(f"{name}={_fmt(value)}" for name, value in entry.factor_scores)
                lines.append(
                    f"    {entry.rank}. {entry.symbol} "
                    f"(composite {_fmt(entry.composite_score)}): {scores}"
                )
            selected = [e for e in market_entries if e.selected]
            lines.append(
                f"    selected ({len(selected)}): {', '.join(e.symbol for e in selected) or 'none'}"
            )
        all_selected = [entry for entry in self.entries if entry.selected]
        lines.append(
            f"selected ({len(all_selected)}): {', '.join(e.symbol for e in all_selected) or 'none'}"
        )
        return "\n".join(lines)


def build_selection_report(snapshot: FactorSnapshot) -> SelectionReport:
    """Build the explainability report from a rebalance's factor snapshot.

    The snapshot's entries already carry the standardized factor scores, the
    composite score, the within-market rank, the selection flag and the
    exclusion reason (SP 2.28), so the report is a deterministic projection of
    that persisted record and can be rebuilt for any rebalance date.
    """
    entries = tuple(
        ReportEntry(
            market=entry.market,
            symbol=entry.symbol,
            factor_scores=entry.standardized_scores,
            composite_score=entry.composite_score,
            rank=entry.rank,
            selected=entry.selected,
            exclusion_reason=entry.exclusion_reason,
        )
        for entry in snapshot.entries
    )
    return SelectionReport(as_of=snapshot.as_of, entries=entries)
