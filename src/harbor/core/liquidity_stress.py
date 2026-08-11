"""Liquidity and execution stress (MVP 3 / SP 3.52).

Tightens the participation rate, suspension, missing-price and deferred-fill
assumptions within a pre-registered range and PRESERVES the unfilled orders
(未成交订单) and valuation warnings (估值告警) rather than dropping them
(收紧成交参与率、停牌、缺价和延期成交假设，保留未成交订单和估值告警).

- :class:`LiquidityStressConfig` is one pre-registered stressed execution
  assumption set (versioned + fingerprinted): a tightened traded-value
  participation rate (SP 2.40), the deferred-fill ``UnfilledPolicy`` and the
  SP 2.41 ``SuspensionConfig`` (missing-price valuation rule + warning flag);
  ``default_liquidity_stresses()`` ships the pre-registered range.
- :func:`quantify_liquidity_stress` re-runs the SP 2.40 / 2.41 execution rules
  on the OOS orders and held positions under the stressed assumptions: a
  suspended / missing-price day refuses the order and the refusal is PRESERVED,
  a tradeable order is capped by the tightened participation rate and any
  unfilled portion is preserved (deferred or cancelled per policy), and a
  position with no quote is valued at the last available close with its
  warning PRESERVED.

Pure core layer: depends only on the SP 3.35 run, the MVP 2 execution rules and
the domain types, never on storage, services or CLI.
"""

import hashlib
import json
import math
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import date

from harbor.core.backtest_config import SuspensionConfig, UnfilledPolicy
from harbor.core.backtest_domain import Market, Order
from harbor.core.backtest_interfaces import DailyQuote
from harbor.core.rolling_oos import RollingOosRun
from harbor.core.suspension import (
    RefusedOrder,
    SuspensionWarning,
    position_valuation_price,
    refuse_order,
)
from harbor.core.validation_domain import WalkForwardFold
from harbor.core.volume_limit import VolumeLimitOutcome, apply_volume_limit

_TOL = 1e-6
_DEFAULT_PARTICIPATION_RATE = 0.1


class LiquidityStressError(ValueError):
    """Raised when liquidity-stress inputs are invalid (SP 3.52)."""


@dataclass(frozen=True)
class ExecutionDay:
    """One order's execution context on its trade day (SP 3.52).

    ``quote`` is ``None`` (or zero-volume) when the symbol is suspended or has
    no price that day; ``volume`` feeds the SP 2.40 participation cap and
    ``reference_price`` the SP 2.39 fill reference price.
    """

    order: Order
    quote: DailyQuote | None
    volume: int
    reference_price: float

    def __post_init__(self) -> None:
        if self.volume < 0:
            raise LiquidityStressError("execution day volume must be non-negative.")
        if self.reference_price <= 0:
            raise LiquidityStressError("execution day reference price must be positive.")


@dataclass(frozen=True)
class ValuationDay:
    """One held position's valuation context on a day (SP 3.52)."""

    market: Market
    symbol: str
    day: date
    quote: DailyQuote | None
    last_quote: DailyQuote | None

    def __post_init__(self) -> None:
        if not self.symbol:
            raise LiquidityStressError("valuation day symbol must be non-empty.")


@dataclass(frozen=True)
class LiquidityStressConfig:
    """One pre-registered stressed execution assumption set (SP 3.52).

    ``participation_rate`` is tightened (never loosened beyond the documented
    SP 2.4 default of 10%), ``on_unfilled`` decides whether an unfilled order
    is deferred to the next day or cancelled, and ``suspension`` is the SP 2.41
    missing-price valuation rule with its warning flag (warnings preserved).
    """

    version: str
    source: str
    participation_rate: float
    on_unfilled: UnfilledPolicy
    suspension: SuspensionConfig
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.version:
            raise LiquidityStressError("liquidity stress version must be non-empty.")
        if not self.source:
            raise LiquidityStressError("liquidity stress source must be non-empty.")
        if not 0.0 < self.participation_rate <= _DEFAULT_PARTICIPATION_RATE:
            raise LiquidityStressError(
                "participation_rate must be tightened within (0, 10%]; a stress "
                "must not loosen the documented default participation rate."
            )
        if not self.fingerprint:
            raise LiquidityStressError("liquidity stress fingerprint must be non-empty.")

    def readable(self) -> str:
        """Render the stress as one line."""
        return (
            f"liquidity stress {self.version} ({self.source}): participation "
            f"{self.participation_rate:.2%} {self.on_unfilled.value} "
            f"suspension {self.suspension.valuation.value} warn "
            f"{self.suspension.warn} fp {self.fingerprint}"
        )


@dataclass(frozen=True)
class FoldLiquidityStress:
    """One fold's stressed execution outcome (SP 3.52).

    ``refused_orders``, ``unfilled_orders`` and ``valuation_warnings`` are the
    PRESERVED artifacts — never dropped (保留未成交订单和估值告警). Refused
    orders and volume-limit outcomes both count toward ``unfilled_quantity``;
    the volume-limit portion is split into ``deferred_quantity`` and
    ``cancelled_quantity`` per the policy.
    """

    fold_index: int
    executed: bool
    requested_quantity: float
    filled_quantity: float
    unfilled_quantity: float
    deferred_quantity: float
    cancelled_quantity: float
    refused_quantity: float
    refused_orders: tuple[RefusedOrder, ...]
    unfilled_orders: tuple[VolumeLimitOutcome, ...]
    valuation_warnings: tuple[SuspensionWarning, ...]
    failure_reason: str | None

    def __post_init__(self) -> None:
        if self.fold_index < 0:
            raise LiquidityStressError("fold index must be non-negative.")
        if self.requested_quantity < 0 or self.filled_quantity < 0:
            raise LiquidityStressError("quantities must be non-negative.")
        if self.executed:
            if self.failure_reason is not None:
                raise LiquidityStressError("an executed fold must not carry a failure reason.")
        else:
            if self.failure_reason is None:
                raise LiquidityStressError("a non-executed fold must carry a failure reason.")
            if self.requested_quantity != 0.0 or self.filled_quantity != 0.0:
                raise LiquidityStressError("a non-executed fold must not carry quantities.")
        if not math.isclose(
            self.unfilled_quantity,
            self.requested_quantity - self.filled_quantity,
            abs_tol=_TOL,
        ):
            raise LiquidityStressError("unfilled quantity must equal requested minus filled.")
        if not math.isclose(
            self.unfilled_quantity,
            self.deferred_quantity + self.cancelled_quantity + self.refused_quantity,
            abs_tol=_TOL,
        ):
            raise LiquidityStressError(
                "unfilled quantity must equal deferred plus cancelled plus refused."
            )

    @property
    def fill_rate(self) -> float | None:
        """The fraction of the requested quantity that filled."""
        if self.requested_quantity <= 0:
            return None
        return self.filled_quantity / self.requested_quantity

    def readable(self) -> str:
        """Render the fold outcome as one line."""
        if not self.executed:
            return f"fold {self.fold_index} NOT executed: {self.failure_reason}"
        return (
            f"fold {self.fold_index}: filled {self.filled_quantity:.2f}/"
            f"{self.requested_quantity:.2f} ({self.fill_rate:.1%}); "
            f"{len(self.refused_orders)} refused, {len(self.unfilled_orders)} "
            f"unfilled, {len(self.valuation_warnings)} warnings"
        )


@dataclass(frozen=True)
class LiquidityStressScenarioResult:
    """One stress level's execution outcome across all OOS folds (SP 3.52)."""

    stress: LiquidityStressConfig
    folds: tuple[FoldLiquidityStress, ...]
    dataset_fingerprint: str
    code_version: str
    requested_quantity: float
    filled_quantity: float
    unfilled_quantity: float
    deferred_quantity: float
    cancelled_quantity: float
    refused_quantity: float
    refused_count: int
    unfilled_order_count: int
    warning_count: int
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.folds:
            raise LiquidityStressError(
                "a liquidity stress scenario requires at least one fold record."
            )
        for index, fold in enumerate(self.folds):
            if fold.fold_index != index:
                raise LiquidityStressError(
                    f"liquidity stress fold {index} must carry fold_index {index}."
                )
        if not any(fold.executed for fold in self.folds):
            raise LiquidityStressError(
                "at least one fold must be executed to quantify liquidity stress."
            )
        if not self.dataset_fingerprint:
            raise LiquidityStressError("dataset fingerprint must be non-empty.")
        if not self.code_version:
            raise LiquidityStressError("code version must be non-empty.")
        if not self.fingerprint:
            raise LiquidityStressError("liquidity stress scenario fingerprint must be non-empty.")
        if not math.isclose(
            self.unfilled_quantity,
            self.requested_quantity - self.filled_quantity,
            abs_tol=_TOL,
        ):
            raise LiquidityStressError("unfilled quantity must equal requested minus filled.")
        if not math.isclose(
            self.unfilled_quantity,
            self.deferred_quantity + self.cancelled_quantity + self.refused_quantity,
            abs_tol=_TOL,
        ):
            raise LiquidityStressError(
                "unfilled quantity must equal deferred plus cancelled plus refused."
            )
        sums = (
            ("requested", self.requested_quantity, "requested_quantity"),
            ("filled", self.filled_quantity, "filled_quantity"),
            ("unfilled", self.unfilled_quantity, "unfilled_quantity"),
            ("deferred", self.deferred_quantity, "deferred_quantity"),
            ("cancelled", self.cancelled_quantity, "cancelled_quantity"),
            ("refused", self.refused_quantity, "refused_quantity"),
        )
        for label, value, field in sums:
            total = sum(getattr(fold, field) for fold in self.folds)
            if not math.isclose(value, total, abs_tol=_TOL):
                raise LiquidityStressError(
                    f"{label} quantity must equal the sum of the fold quantities."
                )
        if self.refused_count != sum(len(fold.refused_orders) for fold in self.folds):
            raise LiquidityStressError("refused count must equal the sum of the fold refusals.")
        if self.unfilled_order_count != sum(len(fold.unfilled_orders) for fold in self.folds):
            raise LiquidityStressError(
                "unfilled order count must equal the sum of the fold unfilled orders."
            )
        if self.warning_count != sum(len(fold.valuation_warnings) for fold in self.folds):
            raise LiquidityStressError("warning count must equal the sum of the fold warnings.")

    def __len__(self) -> int:
        return len(self.folds)

    def __iter__(self) -> Iterator[FoldLiquidityStress]:
        return iter(self.folds)

    def __getitem__(self, index: int) -> FoldLiquidityStress:
        return self.folds[index]

    @property
    def executed_count(self) -> int:
        """Number of folds whose execution was stressed."""
        return sum(1 for fold in self.folds if fold.executed)

    @property
    def fill_rate(self) -> float | None:
        """The fraction of the requested quantity that filled."""
        if self.requested_quantity <= 0:
            return None
        return self.filled_quantity / self.requested_quantity

    @property
    def refused_orders(self) -> tuple[RefusedOrder, ...]:
        """Every refused order across all folds (preserved, never dropped)."""
        return tuple(refusal for fold in self.folds for refusal in fold.refused_orders)

    @property
    def unfilled_orders(self) -> tuple[VolumeLimitOutcome, ...]:
        """Every preserved unfilled order across all folds."""
        return tuple(outcome for fold in self.folds for outcome in fold.unfilled_orders)

    @property
    def valuation_warnings(self) -> tuple[SuspensionWarning, ...]:
        """Every preserved valuation warning across all folds."""
        return tuple(warning for fold in self.folds for warning in fold.valuation_warnings)

    def readable(self) -> str:
        """Render the scenario as one line."""
        return (
            f"liquidity stress {self.stress.version}: filled "
            f"{self.filled_quantity:.2f}/{self.requested_quantity:.2f} "
            f"({self.fill_rate:.1%}); {self.refused_count} refused, "
            f"{self.unfilled_order_count} unfilled, {self.warning_count} warnings "
            f"fp {self.fingerprint}"
        )


@dataclass(frozen=True)
class LiquidityStressReport:
    """The stressed execution outcomes across the pre-registered range (SP 3.52)."""

    scenarios: tuple[LiquidityStressScenarioResult, ...]
    dataset_fingerprint: str
    code_version: str
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.scenarios:
            raise LiquidityStressError("a liquidity stress report requires at least one scenario.")
        seen: set[str] = set()
        for scenario in self.scenarios:
            if scenario.dataset_fingerprint != self.dataset_fingerprint:
                raise LiquidityStressError(
                    "all scenarios must share the report's dataset fingerprint."
                )
            if scenario.code_version != self.code_version:
                raise LiquidityStressError("all scenarios must share the report's code version.")
            if scenario.stress.version in seen:
                raise LiquidityStressError("scenario stress versions must be unique.")
            seen.add(scenario.stress.version)
        if not self.fingerprint:
            raise LiquidityStressError("liquidity stress report fingerprint must be non-empty.")

    def __len__(self) -> int:
        return len(self.scenarios)

    def __iter__(self) -> Iterator[LiquidityStressScenarioResult]:
        return iter(self.scenarios)

    def __getitem__(self, index: int) -> LiquidityStressScenarioResult:
        return self.scenarios[index]

    def scenario(self, version: str) -> LiquidityStressScenarioResult | None:
        """Return the scenario for one stress version (None when absent)."""
        for scenario in self.scenarios:
            if scenario.stress.version == version:
                return scenario
        return None

    def readable(self) -> str:
        """Render the report as one line."""
        return f"{len(self.scenarios)} liquidity stress scenario(s) fp {self.fingerprint}"


def build_liquidity_stress_config(
    *,
    version: str,
    source: str = "pre-registered",
    participation_rate: float,
    on_unfilled: UnfilledPolicy,
    suspension: SuspensionConfig | None = None,
) -> LiquidityStressConfig:
    """Assemble a versioned, fingerprint-stamped liquidity stress config (SP 3.52)."""
    config = LiquidityStressConfig(
        version=version,
        source=source,
        participation_rate=participation_rate,
        on_unfilled=on_unfilled,
        suspension=suspension or SuspensionConfig(),
        fingerprint="unfingerprinted",
    )
    return replace(config, fingerprint=liquidity_stress_config_fingerprint(config))


def default_liquidity_stresses() -> tuple[LiquidityStressConfig, ...]:
    """Return the pre-registered liquidity stress range.

    Each level tightens the traded-value participation rate (5% / 2% / 1%
    against the 10% default), keeps the SP 2.41 LAST_PRICE valuation with
    warnings enabled, and applies the deferred-fill assumption (DEFER) with the
    severest level cancelling the unfilled portion — the fixed, auditable range
    the acceptance requires (在预注册范围内).
    """
    levels = (
        ("liquidity-stress-tight", 0.05, UnfilledPolicy.DEFER),
        ("liquidity-stress-thin", 0.02, UnfilledPolicy.DEFER),
        ("liquidity-stress-severe", 0.01, UnfilledPolicy.CANCEL),
    )
    return tuple(
        build_liquidity_stress_config(
            version=version,
            participation_rate=participation_rate,
            on_unfilled=policy,
        )
        for version, participation_rate, policy in levels
    )


def quantify_liquidity_stress(
    oos_run: RollingOosRun,
    *,
    stress_config: LiquidityStressConfig,
    orders_for: Callable[[WalkForwardFold], Sequence[ExecutionDay]],
    valuations_for: Callable[[WalkForwardFold], Sequence[ValuationDay]],
) -> LiquidityStressScenarioResult:
    """Quantify one stress level's execution outcome on the OOS folds.

    Every executed fold's orders are re-run under the stressed participation
    rate / deferred-fill policy (SP 2.40) and its held positions under the
    stressed suspension / missing-price rule (SP 2.41). Refused orders,
    unfilled orders and valuation warnings are PRESERVED in the result.

    Args:
        oos_run: The SP 3.35 rolling OOS run (executed folds define the orders).
        stress_config: The pre-registered stressed execution assumptions.
        orders_for: The fold's order execution contexts.
        valuations_for: The fold's held-position valuation contexts.
    """
    fold_records: list[FoldLiquidityStress] = []
    for index, result in enumerate(oos_run.results):
        if not result.executed:
            fold_records.append(
                FoldLiquidityStress(
                    fold_index=index,
                    executed=False,
                    requested_quantity=0.0,
                    filled_quantity=0.0,
                    unfilled_quantity=0.0,
                    deferred_quantity=0.0,
                    cancelled_quantity=0.0,
                    refused_quantity=0.0,
                    refused_orders=(),
                    unfilled_orders=(),
                    valuation_warnings=(),
                    failure_reason=result.failure_reason,
                )
            )
            continue
        fold = result.validation.fold
        requested = 0.0
        filled = 0.0
        deferred = 0.0
        cancelled = 0.0
        refused_quantity = 0.0
        refused_orders: list[RefusedOrder] = []
        unfilled_orders: list[VolumeLimitOutcome] = []
        warnings: list[SuspensionWarning] = []
        for execution in orders_for(fold):
            requested += execution.order.quantity
            refusal = refuse_order(
                order=execution.order,
                day=execution.order.trade_date,
                quote=execution.quote,
            )
            if refusal is not None:
                refused_orders.append(refusal)
                refused_quantity += execution.order.quantity
                continue
            outcome = apply_volume_limit(
                order=execution.order,
                reference_price=execution.reference_price,
                volume=execution.volume,
                participation_rate=stress_config.participation_rate,
                policy=stress_config.on_unfilled,
            )
            filled += outcome.filled_quantity
            deferred += outcome.deferred_quantity
            cancelled += outcome.cancelled_quantity
            if not outcome.is_full:
                unfilled_orders.append(outcome)
        for valuation in valuations_for(fold):
            priced = position_valuation_price(
                market=valuation.market,
                symbol=valuation.symbol,
                day=valuation.day,
                quote=valuation.quote,
                last_quote=valuation.last_quote,
                config=stress_config.suspension,
            )
            if priced.warning is not None:
                warnings.append(priced.warning)
        unfilled = requested - filled
        fold_records.append(
            FoldLiquidityStress(
                fold_index=index,
                executed=True,
                requested_quantity=requested,
                filled_quantity=filled,
                unfilled_quantity=unfilled,
                deferred_quantity=deferred,
                cancelled_quantity=cancelled,
                refused_quantity=refused_quantity,
                refused_orders=tuple(refused_orders),
                unfilled_orders=tuple(unfilled_orders),
                valuation_warnings=tuple(warnings),
                failure_reason=None,
            )
        )

    if not any(record.executed for record in fold_records):
        raise LiquidityStressError(
            "at least one fold must be executed to quantify liquidity stress."
        )
    scenario = LiquidityStressScenarioResult(
        stress=stress_config,
        folds=tuple(fold_records),
        dataset_fingerprint=oos_run.dataset_fingerprint,
        code_version=oos_run.code_version,
        requested_quantity=sum(record.requested_quantity for record in fold_records),
        filled_quantity=sum(record.filled_quantity for record in fold_records),
        unfilled_quantity=sum(record.unfilled_quantity for record in fold_records),
        deferred_quantity=sum(record.deferred_quantity for record in fold_records),
        cancelled_quantity=sum(record.cancelled_quantity for record in fold_records),
        refused_quantity=sum(record.refused_quantity for record in fold_records),
        refused_count=sum(len(record.refused_orders) for record in fold_records),
        unfilled_order_count=sum(len(record.unfilled_orders) for record in fold_records),
        warning_count=sum(len(record.valuation_warnings) for record in fold_records),
        fingerprint="unfingerprinted",
    )
    return replace(scenario, fingerprint=liquidity_stress_fingerprint(scenario))


def compute_liquidity_stress_report(
    oos_run: RollingOosRun,
    *,
    stresses: tuple[LiquidityStressConfig, ...] | None = None,
    orders_for: Callable[[WalkForwardFold], Sequence[ExecutionDay]],
    valuations_for: Callable[[WalkForwardFold], Sequence[ValuationDay]],
) -> LiquidityStressReport:
    """Quantify the outcomes across the pre-registered stress range (SP 3.52)."""
    applied = default_liquidity_stresses() if stresses is None else stresses
    if not applied:
        raise LiquidityStressError("at least one liquidity stress is required.")
    scenarios = tuple(
        quantify_liquidity_stress(
            oos_run,
            stress_config=stress,
            orders_for=orders_for,
            valuations_for=valuations_for,
        )
        for stress in applied
    )
    report = LiquidityStressReport(
        scenarios=scenarios,
        dataset_fingerprint=oos_run.dataset_fingerprint,
        code_version=oos_run.code_version,
        fingerprint="unfingerprinted",
    )
    return replace(report, fingerprint=liquidity_stress_report_fingerprint(report))


def _config_payload(config: LiquidityStressConfig) -> dict[str, object]:
    """The stress config's JSON payload (its own fingerprint excluded)."""
    return {
        "version": config.version,
        "source": config.source,
        "participation_rate": config.participation_rate,
        "on_unfilled": config.on_unfilled.value,
        "suspension_valuation": config.suspension.valuation.value,
        "suspension_warn": config.suspension.warn,
    }


def _refusal_payload(refusal: RefusedOrder) -> dict[str, object]:
    """Serialize one preserved refused order."""
    return {
        "side": refusal.order.side.value,
        "symbol": refusal.order.symbol,
        "day": refusal.day.isoformat(),
        "reason": refusal.reason,
    }


def _outcome_payload(outcome: VolumeLimitOutcome) -> dict[str, object]:
    """Serialize one preserved unfilled order."""
    return {
        "side": outcome.order.side.value,
        "symbol": outcome.order.symbol,
        "requested_quantity": outcome.requested_quantity,
        "filled_quantity": outcome.filled_quantity,
        "unfilled_quantity": outcome.unfilled_quantity,
        "policy": outcome.policy.value,
        "reason": outcome.reason,
    }


def _warning_payload(warning: SuspensionWarning) -> dict[str, object]:
    """Serialize one preserved valuation warning."""
    return {
        "market": warning.market.value,
        "symbol": warning.symbol,
        "day": warning.day.isoformat(),
        "message": warning.message,
    }


def liquidity_stress_config_json(config: LiquidityStressConfig) -> str:
    """Return a stable, key-sorted JSON serialization of a stress config."""
    return json.dumps(
        _config_payload(config), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def liquidity_stress_config_fingerprint(config: LiquidityStressConfig) -> str:
    """Return the stable SHA-256 fingerprint of a stress config (SP 3.52)."""
    return hashlib.sha256(liquidity_stress_config_json(config).encode("utf-8")).hexdigest()


def liquidity_stress_json(scenario: LiquidityStressScenarioResult) -> str:
    """Return a stable, key-sorted JSON serialization of a scenario.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style).
    """
    payload: dict[str, object] = {
        "stress": _config_payload(scenario.stress),
        "dataset_fingerprint": scenario.dataset_fingerprint,
        "code_version": scenario.code_version,
        "requested_quantity": scenario.requested_quantity,
        "filled_quantity": scenario.filled_quantity,
        "unfilled_quantity": scenario.unfilled_quantity,
        "deferred_quantity": scenario.deferred_quantity,
        "cancelled_quantity": scenario.cancelled_quantity,
        "refused_quantity": scenario.refused_quantity,
        "refused_count": scenario.refused_count,
        "unfilled_order_count": scenario.unfilled_order_count,
        "warning_count": scenario.warning_count,
        "folds": [
            {
                "fold_index": fold.fold_index,
                "executed": fold.executed,
                "requested_quantity": fold.requested_quantity,
                "filled_quantity": fold.filled_quantity,
                "unfilled_quantity": fold.unfilled_quantity,
                "deferred_quantity": fold.deferred_quantity,
                "cancelled_quantity": fold.cancelled_quantity,
                "refused_quantity": fold.refused_quantity,
                "refused_orders": [_refusal_payload(r) for r in fold.refused_orders],
                "unfilled_orders": [_outcome_payload(o) for o in fold.unfilled_orders],
                "valuation_warnings": [_warning_payload(w) for w in fold.valuation_warnings],
                "failure_reason": fold.failure_reason,
            }
            for fold in scenario.folds
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def liquidity_stress_fingerprint(scenario: LiquidityStressScenarioResult) -> str:
    """Return the stable SHA-256 fingerprint of a scenario (SP 3.52)."""
    return hashlib.sha256(liquidity_stress_json(scenario).encode("utf-8")).hexdigest()


def liquidity_stress_report_json(report: LiquidityStressReport) -> str:
    """Return a stable, key-sorted JSON serialization of a report."""
    payload: dict[str, object] = {
        "dataset_fingerprint": report.dataset_fingerprint,
        "code_version": report.code_version,
        "scenarios": [json.loads(liquidity_stress_json(scenario)) for scenario in report.scenarios],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def liquidity_stress_report_fingerprint(report: LiquidityStressReport) -> str:
    """Return the stable SHA-256 fingerprint of a report (SP 3.52)."""
    return hashlib.sha256(liquidity_stress_report_json(report).encode("utf-8")).hexdigest()


__all__: tuple[str, ...] = (
    "ExecutionDay",
    "LiquidityStressError",
    "LiquidityStressConfig",
    "LiquidityStressScenarioResult",
    "LiquidityStressReport",
    "FoldLiquidityStress",
    "ValuationDay",
    "build_liquidity_stress_config",
    "default_liquidity_stresses",
    "quantify_liquidity_stress",
    "compute_liquidity_stress_report",
    "liquidity_stress_config_json",
    "liquidity_stress_config_fingerprint",
    "liquidity_stress_json",
    "liquidity_stress_fingerprint",
    "liquidity_stress_report_json",
    "liquidity_stress_report_fingerprint",
)
