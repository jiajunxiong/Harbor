"""Research assumption snapshot (MVP 4 / SP 4.69).

Records the OOS research assumptions the paper loop is measured against —
slippage, all-in cost, volume participation rate, fill rule, spread and
execution latency (研究假设快照, SP 4.69). These are the "as researched"
benchmarks for SP 4.71's difference metrics: the paper execution is compared
against exactly what the OOS study assumed, never against a silently changed
baseline.

The snapshot fingerprint is stable and excludes the source research run id,
so two snapshots with the same assumptions are replay-identical (SP 4.59 /
4.81).

Pure core logic: depends on the backtest domain and config; never touches
storage or CLI code.
"""

import hashlib
import json
from dataclasses import dataclass

from harbor.core.backtest_config import FillRule
from harbor.core.backtest_domain import Market


class ResearchAssumptionError(ValueError):
    """Raised when a research assumption snapshot is invalid (SP 4.69)."""


@dataclass(frozen=True)
class ResearchAssumption:
    """One market's OOS research assumptions (SP 4.69).

    All cost-like assumptions are expressed in basis points (1 bps = 0.01%);
    the participation rate is the traded-value cap (SP 2.40) and the latency is
    the assumed signal-to-fill seconds (SP 4.23). ``reference_price`` is the
    optional OOS-assumed fill price used as the fill-price baseline (SP 4.71).
    """

    market: Market
    slippage_bps: float
    cost_bps: float
    spread_bps: float
    participation_rate: float
    fill_rule: FillRule
    execution_latency_seconds: float
    reference_price: float | None = None

    def __post_init__(self) -> None:
        if self.slippage_bps < 0:
            raise ResearchAssumptionError("slippage_bps must be non-negative.")
        if self.cost_bps < 0:
            raise ResearchAssumptionError("cost_bps must be non-negative.")
        if self.spread_bps < 0:
            raise ResearchAssumptionError("spread_bps must be non-negative.")
        if not 0 < self.participation_rate <= 1:
            raise ResearchAssumptionError("participation_rate must be in (0, 1].")
        if self.execution_latency_seconds < 0:
            raise ResearchAssumptionError("execution_latency_seconds must be non-negative.")
        if self.reference_price is not None and self.reference_price <= 0:
            raise ResearchAssumptionError("reference_price must be positive when given.")

    def readable(self) -> str:
        """Render the assumption as a compact summary."""
        reference = (
            f"reference {self.reference_price:g}"
            if self.reference_price is not None
            else "reference (paper)"
        )
        return (
            f"{self.market.value}: slippage {self.slippage_bps:g} bps, cost "
            f"{self.cost_bps:g} bps, spread {self.spread_bps:g} bps, "
            f"participation {self.participation_rate:.2%}, rule "
            f"{self.fill_rule.value}, latency {self.execution_latency_seconds:g}s, {reference}"
        )


@dataclass(frozen=True)
class ResearchAssumptionSnapshot:
    """A versioned snapshot of the OOS research assumptions (SP 4.69)."""

    version: str
    source_run_id: str
    dataset_fingerprint: str
    assumptions: tuple[ResearchAssumption, ...]

    def __post_init__(self) -> None:
        if not self.version:
            raise ResearchAssumptionError("version must be non-empty.")
        if not self.source_run_id:
            raise ResearchAssumptionError("source_run_id must be non-empty.")
        if not self.dataset_fingerprint:
            raise ResearchAssumptionError("dataset_fingerprint must be non-empty.")
        markets = [assumption.market for assumption in self.assumptions]
        if len(set(markets)) != len(markets):
            raise ResearchAssumptionError("assumptions must be unique per market.")

    def assumption_for(self, market: Market) -> ResearchAssumption | None:
        """Return the assumption for ``market`` or ``None`` when absent (SP 4.74)."""
        for assumption in self.assumptions:
            if assumption.market is market:
                return assumption
        return None

    def fingerprint(self) -> str:
        """Return the stable snapshot fingerprint (run id excluded)."""
        return research_assumption_fingerprint(self)

    def readable(self) -> str:
        """Render the snapshot as a compact summary."""
        lines = [
            f"research assumption snapshot v{self.version} "
            f"(dataset {self.dataset_fingerprint[:8]}, source {self.source_run_id})"
        ]
        for assumption in self.assumptions:
            lines.append(f"  {assumption.readable()}")
        return "\n".join(lines)


def _assumption_entry(assumption: ResearchAssumption) -> dict[str, object]:
    """Canonical serialization of one assumption (SP 4.69)."""
    return {
        "market": assumption.market.value,
        "slippage_bps": assumption.slippage_bps,
        "cost_bps": assumption.cost_bps,
        "spread_bps": assumption.spread_bps,
        "participation_rate": assumption.participation_rate,
        "fill_rule": assumption.fill_rule.value,
        "execution_latency_seconds": assumption.execution_latency_seconds,
        "reference_price": assumption.reference_price,
    }


def research_assumption_fingerprint(snapshot: ResearchAssumptionSnapshot) -> str:
    """Return the stable SHA-256 fingerprint of a snapshot (SP 4.69).

    The source research run id is deliberately excluded: two snapshots with
    equal assumptions and dataset identity are replay-identical.
    """
    payload: dict[str, object] = {
        "version": snapshot.version,
        "dataset_fingerprint": snapshot.dataset_fingerprint,
        "assumptions": [_assumption_entry(assumption) for assumption in snapshot.assumptions],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_research_assumption_snapshot(
    *,
    version: str,
    source_run_id: str,
    dataset_fingerprint: str,
    assumptions: tuple[ResearchAssumption, ...],
) -> ResearchAssumptionSnapshot:
    """Build a research assumption snapshot (SP 4.69)."""
    return ResearchAssumptionSnapshot(
        version=version,
        source_run_id=source_run_id,
        dataset_fingerprint=dataset_fingerprint,
        assumptions=assumptions,
    )
