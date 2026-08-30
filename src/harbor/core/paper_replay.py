"""Replayable paper execution trace (MVP 4 / SP 4.59).

A :class:`PaperExecutionTrace` captures everything a paper run produced —
orders, fills, valuations and the audit fingerprint — so two runs with equal
traces are replay-identical: identical inputs reproduce identical orders,
fills, net values and audit events (SP 4.59 / 4.67). The fingerprint
deliberately excludes the run id, which identifies an execution rather than
the research inputs (mirroring SP 2.61).

Pure core logic: depends on the paper domain and valuation; never touches
storage or CLI code.
"""

import hashlib
import json
from dataclasses import dataclass

from harbor.core.paper_domain import PaperFill, PaperOrder
from harbor.core.paper_valuation import PaperValuation


class PaperReplayError(ValueError):
    """Raised when a paper execution trace is invalid (SP 4.59)."""


def _order_entry(order: PaperOrder) -> dict[str, object]:
    """Canonical serialization of one order (SP 4.59)."""
    return {
        "order_id": order.order_id,
        "market": order.market.value,
        "symbol": order.symbol,
        "side": order.side.value,
        "quantity": order.quantity,
    }


def _fill_entry(fill: PaperFill) -> dict[str, object]:
    """Canonical serialization of one fill (SP 4.59)."""
    return {
        "fill_id": fill.fill_id,
        "order_id": fill.paper_order_id,
        "symbol": fill.symbol,
        "side": fill.side.value,
        "quantity": fill.quantity,
        "price": fill.price,
        "fee": fill.fee,
        "trade_date": fill.trade_date.isoformat(),
    }


def _valuation_entry(valuation: PaperValuation) -> dict[str, object]:
    """Canonical serialization of one valuation (SP 4.59)."""
    return {
        "as_of": valuation.as_of.isoformat(),
        "cash": valuation.cash_base,
        "securities": valuation.securities_base,
        "fees": valuation.fees_base,
        "total": valuation.total_base,
    }


@dataclass(frozen=True)
class PaperExecutionTrace:
    """Everything a paper run produced, for replay comparison (SP 4.59)."""

    run_id: str
    orders: tuple[PaperOrder, ...]
    fills: tuple[PaperFill, ...]
    valuations: tuple[PaperValuation, ...]
    audit_fingerprint: str

    def __post_init__(self) -> None:
        if not self.run_id:
            raise PaperReplayError("run_id must be non-empty.")

    def fingerprint(self) -> str:
        """Return a stable digest of the execution inputs/outputs (SP 4.59).

        The run id is deliberately excluded: two runs with equal fingerprints
        are replay-identical.
        """
        return paper_execution_trace_fingerprint(self)

    def readable(self) -> str:
        """Render the trace as a compact summary."""
        return (
            f"paper execution trace {self.run_id}: {len(self.orders)} order(s), "
            f"{len(self.fills)} fill(s), {len(self.valuations)} valuation(s), "
            f"fp {self.fingerprint()}"
        )


def _trace_payload(trace: PaperExecutionTrace) -> dict[str, object]:
    """Return the canonical payload of a trace (run id excluded)."""
    return {
        "orders": [_order_entry(order) for order in trace.orders],
        "fills": [_fill_entry(fill) for fill in trace.fills],
        "valuations": [_valuation_entry(valuation) for valuation in trace.valuations],
        "audit_fingerprint": trace.audit_fingerprint,
    }


def paper_execution_trace_fingerprint(trace: PaperExecutionTrace) -> str:
    """Return the stable SHA-256 fingerprint of a trace (SP 4.59)."""
    payload = _trace_payload(trace)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_paper_execution_trace(
    *,
    run_id: str,
    orders: tuple[PaperOrder, ...] = (),
    fills: tuple[PaperFill, ...] = (),
    valuations: tuple[PaperValuation, ...] = (),
    audit_fingerprint: str = "",
) -> PaperExecutionTrace:
    """Build a paper execution trace (SP 4.59)."""
    return PaperExecutionTrace(
        run_id=run_id,
        orders=orders,
        fills=fills,
        valuations=valuations,
        audit_fingerprint=audit_fingerprint,
    )
