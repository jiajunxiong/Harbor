"""Paper reconciliation (MVP 4 / SP 4.57-4.58 / 4.61).

Checks that orders, fills and account changes are consistent (SP 4.57) and
that the daily assets close — total == cash + position market value — with
fees, dividends, corporate actions and FX effects closed (SP 4.58). Every
difference is recorded as an immutable :class:`PaperReconciliationDifference`
and surfaced, never silently corrected (对账差异记录, SP 4.61); the storage
layer persists these records into the ``paper_reconciliation_differences``
table.

Pure core logic: depends on the paper domain, the execution engine and the
valuation; never touches storage or CLI code.
"""

from dataclasses import dataclass
from datetime import date

from harbor.core.backtest_domain import OrderSide
from harbor.core.paper_account import PaperAccountState
from harbor.core.paper_domain import PaperFill, PaperOrder
from harbor.core.paper_valuation import PaperValuation

_EPSILON = 1e-9


class PaperReconciliationError(ValueError):
    """Raised when a reconciliation cannot be performed (SP 4.57)."""


@dataclass(frozen=True)
class PaperReconciliationDifference:
    """One recorded reconciliation difference (SP 4.61)."""

    check_name: str
    expected: float
    actual: float
    detail: str

    def __post_init__(self) -> None:
        if not self.check_name or not self.detail:
            raise PaperReconciliationError("check_name and detail must be non-empty.")

    def readable(self) -> str:
        """Render the difference as a compact summary."""
        return (
            f"{self.check_name}: expected {self.expected:.6f}, actual "
            f"{self.actual:.6f} — {self.detail}"
        )


@dataclass(frozen=True)
class PaperReconciliationResult:
    """The reconciliation result for one scope (SP 4.57 / 4.58)."""

    as_of: date
    differences: tuple[PaperReconciliationDifference, ...]

    @property
    def reconciled(self) -> bool:
        """Whether no difference was recorded."""
        return not self.differences

    def readable(self) -> str:
        """Render the result as a compact summary."""
        status = "reconciled" if self.reconciled else "MISMATCH"
        lines = [f"reconciliation {self.as_of.isoformat()}: {status}"]
        for difference in self.differences:
            lines.append(f"  {difference.readable()}")
        return "\n".join(lines)


def _diff(
    differences: list[PaperReconciliationDifference],
    *,
    check_name: str,
    expected: float,
    actual: float,
    detail: str,
    tolerance: float,
) -> None:
    """Append a difference when the expected and actual diverge beyond tolerance."""
    if abs(expected - actual) > tolerance:
        differences.append(
            PaperReconciliationDifference(
                check_name=check_name,
                expected=expected,
                actual=actual,
                detail=detail,
            )
        )


def reconcile_order_fill_account(
    *,
    order: PaperOrder,
    fill: PaperFill,
    account_before: PaperAccountState,
    account_after: PaperAccountState,
    tolerance: float = 1e-6,
) -> PaperReconciliationResult:
    """Check that a fill moved the account consistently (SP 4.57).

    Verifies the cash change, the position quantity change and the fee accrual
    against the fill; every divergence is located to its check.
    """
    differences: list[PaperReconciliationDifference] = []
    notional = fill.quantity * fill.price
    expected_cash_delta = (
        -(notional + fill.fee) if fill.side is OrderSide.BUY else (notional - fill.fee)
    )
    actual_cash_delta = account_after.balance(fill.currency) - account_before.balance(fill.currency)
    _diff(
        differences,
        check_name="cash_delta",
        expected=expected_cash_delta,
        actual=actual_cash_delta,
        detail=f"fill {fill.fill_id} cash movement in {fill.currency.value}",
        tolerance=tolerance,
    )

    before_position = account_before.position(fill.market, fill.symbol)
    before_qty = before_position.quantity if before_position is not None else 0.0
    after_position = account_after.position(fill.market, fill.symbol)
    after_qty = after_position.quantity if after_position is not None else 0.0
    expected_qty_delta = fill.quantity if fill.side is OrderSide.BUY else -fill.quantity
    _diff(
        differences,
        check_name="position_quantity",
        expected=before_qty + expected_qty_delta,
        actual=after_qty,
        detail=f"fill {fill.fill_id} quantity change for {fill.market.value}/{fill.symbol}",
        tolerance=tolerance,
    )

    expected_fees = account_before.fees(fill.currency) + fill.fee
    actual_fees = account_after.fees(fill.currency)
    _diff(
        differences,
        check_name="fees_accrual",
        expected=expected_fees,
        actual=actual_fees,
        detail=f"fill {fill.fill_id} fee accrual in {fill.currency.value}",
        tolerance=tolerance,
    )

    return PaperReconciliationResult(as_of=fill.trade_date, differences=tuple(differences))


def reconcile_daily_assets(
    *,
    valuation: PaperValuation,
    tolerance: float = 1e-6,
) -> PaperReconciliationResult:
    """Check that the daily assets close (SP 4.58).

    Verifies total == cash + position market value; fees, dividends and
    corporate-action cash are already inside the cash balance, so any gap is
    surfaced rather than silently corrected.
    """
    differences: list[PaperReconciliationDifference] = []
    expected_total = valuation.cash_base + valuation.securities_base
    _diff(
        differences,
        check_name="assets_close",
        expected=expected_total,
        actual=valuation.total_base,
        detail=(f"assets must equal cash + securities on {valuation.as_of.isoformat()}"),
        tolerance=tolerance,
    )
    return PaperReconciliationResult(as_of=valuation.as_of, differences=tuple(differences))


def reconciliation_rows(
    result: PaperReconciliationResult,
    *,
    paper_run_id: str,
) -> list[dict[str, object]]:
    """Map reconciliation differences to table rows (SP 4.61).

    Each row carries ``paper_run_id``, the check date, the check name, the
    expected/actual values and the detail, ready for the
    ``paper_reconciliation_differences`` table (SP 4.8).
    """
    return [
        {
            "paper_run_id": paper_run_id,
            "as_of_date": result.as_of,
            "check_name": difference.check_name,
            "expected": difference.expected,
            "actual": difference.actual,
            "detail": difference.detail,
        }
        for difference in result.differences
    ]
