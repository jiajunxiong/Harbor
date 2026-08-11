"""Cost and slippage stress (MVP 3 / SP 3.51).

Within a pre-registered range, raises the HK and US costs, minimum fees and
slippage and quantifies the impact on the out-of-sample net value and turnover
(在预注册范围内提高港美各自成本、最低收费和滑点，量化对 OOS 净值与换手的影响).

- :class:`CostStressConfig` is one pre-registered stressed cost configuration
  (versioned + fingerprinted): a stressed HK ``CostConfig`` (SP 2.37) and a
  stressed US ``CostConfig`` (SP 2.38), each carrying the raised rates, minimum
  commission and slippage; ``default_cost_stresses()`` ships the pre-registered
  range (2x / 5x / 10x).
- :func:`quantify_cost_stress` re-prices every executed fold's actual OOS fills
  (SP 2.39 ``Fill.fee`` = baseline all-in cost) under the stressed config and
  reports the cost increase, the resulting OOS net-value reduction (fees reduce
  cash one-for-one; the stressed daily net values are the baseline path minus
  the cumulative additional fees) and the turnover impact (the same dollar
  turnover against a lower average net value raises the turnover ratio).

The baseline cost is the fill's recorded fee — the honest number the OOS run
actually paid; the stressed cost is re-computed by the SP 2.37 / 2.38 models
with the pre-registered stressed parameters. Missing FX is never assumed 1:1:
if a fill needs a currency conversion that is unavailable, the turnover metrics
are reported as unavailable rather than fabricated.

Pure core layer: depends only on the SP 3.35 run, the MVP 2 cost models and the
domain types, never on storage, services or CLI.
"""

import bisect
import hashlib
import json
import math
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import date

from harbor.core.backtest_config import CostConfig
from harbor.core.backtest_domain import Currency, Fill, Market, NetValue, OrderSide
from harbor.core.cost_hk import hk_order_cost
from harbor.core.cost_us import us_order_cost
from harbor.core.rolling_oos import RollingOosRun
from harbor.core.validation_domain import WalkForwardFold

_TOL = 1e-6


class CostStressError(ValueError):
    """Raised when cost-stress inputs are invalid (SP 3.51)."""


@dataclass(frozen=True)
class CostStressConfig:
    """One pre-registered stressed cost configuration (SP 3.51).

    ``hk`` / ``us`` carry the stressed SP 2.37 / 2.38 cost parameters: the
    raised rates and minimum fees, and for the US model the slippage basis
    points. A stress always raises the documented defaults (never lowers them),
    so the pre-registered range is auditable (不根据结果事后划分).
    """

    version: str
    source: str
    hk: CostConfig
    us: CostConfig
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.version:
            raise CostStressError("cost stress version must be non-empty.")
        if not self.source:
            raise CostStressError("cost stress source must be non-empty.")
        defaults = CostConfig()
        _require_at_least("HK commission rate", self.hk.commission_rate, defaults.commission_rate)
        _require_at_least("HK minimum commission", self.hk.min_commission, defaults.min_commission)
        _require_at_least("HK stamp duty rate", self.hk.stamp_duty_rate, defaults.stamp_duty_rate)
        _require_at_least(
            "HK transaction levy rate",
            self.hk.transaction_levy_rate,
            defaults.transaction_levy_rate,
        )
        _require_at_least(
            "HK trading fee rate", self.hk.trading_fee_rate, defaults.trading_fee_rate
        )
        _require_at_least("US commission rate", self.us.commission_rate, defaults.commission_rate)
        _require_at_least("US minimum commission", self.us.min_commission, defaults.min_commission)
        _require_at_least(
            "US regulatory fee rate", self.us.regulatory_fee_rate, defaults.regulatory_fee_rate
        )
        _require_at_least("US slippage", self.us.slippage_bps, defaults.slippage_bps)
        if not self.fingerprint:
            raise CostStressError("cost stress fingerprint must be non-empty.")

    def readable(self) -> str:
        """Render the stress as one line."""
        return (
            f"cost stress {self.version} ({self.source}): HK commission "
            f"{self.hk.commission_rate} min {self.hk.min_commission} "
            f"stamp {self.hk.stamp_duty_rate} levy {self.hk.transaction_levy_rate} "
            f"trading {self.hk.trading_fee_rate} | US commission {self.us.commission_rate} "
            f"min {self.us.min_commission} regulatory {self.us.regulatory_fee_rate} "
            f"slippage {self.us.slippage_bps}bp fp {self.fingerprint}"
        )


def _require_at_least(name: str, value: float, floor: float) -> None:
    """Reject a stress parameter that would lower the documented default."""
    if value < floor:
        raise CostStressError(
            f"{name} {value} is below the documented default {floor}; a stress "
            "must raise costs, never lower them."
        )


@dataclass(frozen=True)
class FoldCostStress:
    """One fold's re-priced cost impact (SP 3.51).

    ``baseline_costs`` is the sum of the fold's OOS fill fees actually paid;
    ``stressed_costs`` re-prices the same fills under the stressed config;
    ``cost_increase`` is their difference. ``baseline_final_net_value`` is the
    fold's last OOS net value and ``stressed_final_net_value`` the value
    reduced by the fold's additional fees. A fold that did not execute carries
    no data and its ``failure_reason``.
    """

    fold_index: int
    executed: bool
    baseline_costs: float | None
    stressed_costs: float | None
    cost_increase: float | None
    baseline_final_net_value: float | None
    stressed_final_net_value: float | None
    turnover: float | None
    failure_reason: str | None

    def __post_init__(self) -> None:
        if self.fold_index < 0:
            raise CostStressError("fold index must be non-negative.")
        if self.executed:
            if self.failure_reason is not None:
                raise CostStressError("an executed fold must not carry a failure reason.")
            if any(
                value is None
                for value in (
                    self.baseline_costs,
                    self.stressed_costs,
                    self.cost_increase,
                    self.baseline_final_net_value,
                    self.stressed_final_net_value,
                )
            ):
                raise CostStressError("an executed fold must carry its cost impact and net values.")
            assert self.baseline_final_net_value is not None
            assert self.stressed_final_net_value is not None
            assert self.cost_increase is not None
            if not math.isclose(
                self.stressed_final_net_value,
                self.baseline_final_net_value - self.cost_increase,
                abs_tol=_TOL,
            ):
                raise CostStressError(
                    "the stressed final net value must equal the baseline final "
                    "net value minus the additional fees."
                )
        else:
            if self.failure_reason is None:
                raise CostStressError("a non-executed fold must carry a failure reason.")
            if any(
                value is not None
                for value in (
                    self.baseline_costs,
                    self.stressed_costs,
                    self.cost_increase,
                    self.baseline_final_net_value,
                    self.stressed_final_net_value,
                )
            ):
                raise CostStressError("a non-executed fold must not carry cost impact data.")

    def readable(self) -> str:
        """Render the fold impact as one line."""
        if not self.executed:
            return f"fold {self.fold_index} NOT executed: {self.failure_reason}"
        return (
            f"fold {self.fold_index}: costs {self.baseline_costs} -> "
            f"{self.stressed_costs} (+{self.cost_increase}) final "
            f"{self.baseline_final_net_value} -> {self.stressed_final_net_value}"
        )


@dataclass(frozen=True)
class CostStressScenarioResult:
    """One stress level's impact across all OOS folds (SP 3.51).

    Aggregates the per-fold cost increases into the OOS net-value impact:
    ``stressed_final_net_value`` is the baseline final net value minus the
    total additional fees, and the turnover is recomputed against the
    stress-reduced average net value (the same dollar turnover against a lower
    average net value raises the turnover ratio).
    """

    stress: CostStressConfig
    folds: tuple[FoldCostStress, ...]
    dataset_fingerprint: str
    code_version: str
    baseline_first_net_value: float
    baseline_final_net_value: float
    stressed_final_net_value: float
    baseline_avg_nav: float
    stressed_avg_nav: float
    baseline_costs: float
    stressed_costs: float
    cost_increase: float
    baseline_cumulative_return: float
    stressed_cumulative_return: float
    net_value_impact_pct: float
    baseline_turnover: float | None
    stressed_turnover: float | None
    turnover_delta: float | None
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.folds:
            raise CostStressError("a cost stress scenario requires at least one fold record.")
        for index, fold in enumerate(self.folds):
            if fold.fold_index != index:
                raise CostStressError(f"cost stress fold {index} must carry fold_index {index}.")
        if not any(fold.executed for fold in self.folds):
            raise CostStressError("at least one fold must be executed to quantify cost stress.")
        if not self.dataset_fingerprint:
            raise CostStressError("dataset fingerprint must be non-empty.")
        if not self.code_version:
            raise CostStressError("code version must be non-empty.")
        if not self.fingerprint:
            raise CostStressError("cost stress scenario fingerprint must be non-empty.")
        if self.baseline_first_net_value <= 0 or self.baseline_final_net_value <= 0:
            raise CostStressError("baseline net values must be positive.")
        baseline = sum(fold.baseline_costs or 0.0 for fold in self.folds)
        stressed = sum(fold.stressed_costs or 0.0 for fold in self.folds)
        if not math.isclose(self.baseline_costs, baseline, abs_tol=_TOL):
            raise CostStressError("baseline costs must equal the sum of the fold costs.")
        if not math.isclose(self.stressed_costs, stressed, abs_tol=_TOL):
            raise CostStressError("stressed costs must equal the sum of the fold costs.")
        if not math.isclose(
            self.cost_increase, self.stressed_costs - self.baseline_costs, abs_tol=_TOL
        ):
            raise CostStressError("cost increase must equal stressed minus baseline costs.")
        if not math.isclose(
            self.stressed_final_net_value,
            self.baseline_final_net_value - self.cost_increase,
            abs_tol=_TOL,
        ):
            raise CostStressError(
                "the stressed final net value must equal the baseline final net "
                "value minus the total additional fees."
            )
        if not math.isclose(
            self.baseline_cumulative_return,
            self.baseline_final_net_value / self.baseline_first_net_value - 1.0,
            abs_tol=_TOL,
        ):
            raise CostStressError("baseline cumulative return is inconsistent.")
        if not math.isclose(
            self.stressed_cumulative_return,
            self.stressed_final_net_value / self.baseline_first_net_value - 1.0,
            abs_tol=_TOL,
        ):
            raise CostStressError("stressed cumulative return is inconsistent.")
        if not math.isclose(
            self.net_value_impact_pct,
            self.cost_increase / self.baseline_final_net_value * 100.0,
            abs_tol=_TOL,
        ):
            raise CostStressError("net value impact percentage is inconsistent.")
        if self.baseline_turnover is None or self.stressed_turnover is None:
            if self.turnover_delta is not None:
                raise CostStressError(
                    "turnover delta requires both baseline and stressed turnover."
                )
        else:
            if self.turnover_delta is None or not math.isclose(
                self.turnover_delta,
                self.stressed_turnover - self.baseline_turnover,
                abs_tol=_TOL,
            ):
                raise CostStressError("turnover delta must equal stressed minus baseline turnover.")

    def __len__(self) -> int:
        return len(self.folds)

    def __iter__(self) -> Iterator[FoldCostStress]:
        return iter(self.folds)

    def __getitem__(self, index: int) -> FoldCostStress:
        return self.folds[index]

    @property
    def executed_count(self) -> int:
        """Number of folds whose OOS execution was priced."""
        return sum(1 for fold in self.folds if fold.executed)

    def readable(self) -> str:
        """Render the scenario as one line."""
        return (
            f"cost stress {self.stress.version}: costs {self.baseline_costs} -> "
            f"{self.stressed_costs} (+{self.cost_increase}); final net value "
            f"{self.baseline_final_net_value} -> {self.stressed_final_net_value} "
            f"(-{self.net_value_impact_pct:.2f}%); turnover "
            f"{self.baseline_turnover} -> {self.stressed_turnover} fp {self.fingerprint}"
        )


@dataclass(frozen=True)
class CostStressReport:
    """The quantified cost stress across the pre-registered range (SP 3.51)."""

    scenarios: tuple[CostStressScenarioResult, ...]
    dataset_fingerprint: str
    code_version: str
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.scenarios:
            raise CostStressError("a cost stress report requires at least one scenario.")
        seen: set[str] = set()
        for scenario in self.scenarios:
            if scenario.dataset_fingerprint != self.dataset_fingerprint:
                raise CostStressError("all scenarios must share the report's dataset fingerprint.")
            if scenario.code_version != self.code_version:
                raise CostStressError("all scenarios must share the report's code version.")
            if scenario.stress.version in seen:
                raise CostStressError("scenario stress versions must be unique.")
            seen.add(scenario.stress.version)
        if not self.fingerprint:
            raise CostStressError("cost stress report fingerprint must be non-empty.")

    def __len__(self) -> int:
        return len(self.scenarios)

    def __iter__(self) -> Iterator[CostStressScenarioResult]:
        return iter(self.scenarios)

    def __getitem__(self, index: int) -> CostStressScenarioResult:
        return self.scenarios[index]

    def scenario(self, version: str) -> CostStressScenarioResult | None:
        """Return the scenario for one stress version (None when absent)."""
        for scenario in self.scenarios:
            if scenario.stress.version == version:
                return scenario
        return None

    def readable(self) -> str:
        """Render the report as one line."""
        return f"{len(self.scenarios)} cost stress scenario(s) fp {self.fingerprint}"


def build_cost_stress_config(
    *,
    version: str,
    source: str = "pre-registered",
    hk: CostConfig,
    us: CostConfig,
) -> CostStressConfig:
    """Assemble a versioned, fingerprint-stamped cost stress config (SP 3.51)."""
    config = CostStressConfig(
        version=version,
        source=source,
        hk=hk,
        us=us,
        fingerprint="unfingerprinted",
    )
    return replace(config, fingerprint=cost_stress_config_fingerprint(config))


def _scaled_cost_config(
    *,
    cost_multiplier: float,
    min_commission: float,
    slippage_bps: float,
) -> tuple[CostConfig, CostConfig]:
    """Scale the default cost parameters into stressed HK and US configs."""
    defaults = CostConfig()
    hk = CostConfig(
        commission_rate=defaults.commission_rate * cost_multiplier,
        min_commission=min_commission,
        stamp_duty_rate=defaults.stamp_duty_rate * cost_multiplier,
        transaction_levy_rate=defaults.transaction_levy_rate * cost_multiplier,
        trading_fee_rate=defaults.trading_fee_rate * cost_multiplier,
        regulatory_fee_rate=defaults.regulatory_fee_rate * cost_multiplier,
        slippage_bps=slippage_bps,
        lot_size=defaults.lot_size,
    )
    us = CostConfig(
        commission_rate=defaults.commission_rate * cost_multiplier,
        min_commission=min_commission,
        stamp_duty_rate=defaults.stamp_duty_rate * cost_multiplier,
        transaction_levy_rate=defaults.transaction_levy_rate * cost_multiplier,
        trading_fee_rate=defaults.trading_fee_rate * cost_multiplier,
        regulatory_fee_rate=defaults.regulatory_fee_rate * cost_multiplier,
        slippage_bps=slippage_bps,
        lot_size=1,
    )
    return hk, us


def default_cost_stresses() -> tuple[CostStressConfig, ...]:
    """Return the pre-registered cost stress range (2x / 5x / 10x).

    Each level raises the HK and US commission/stamp/levy/trading/regulatory
    rates by the multiplier, sets a pre-registered minimum commission and a
    pre-registered US slippage in basis points — the fixed, auditable range the
    acceptance requires (在预注册范围内).
    """
    levels = (
        ("cost-stress-2x", 2.0, 10.0, 10.0),
        ("cost-stress-5x", 5.0, 25.0, 25.0),
        ("cost-stress-10x", 10.0, 50.0, 50.0),
    )
    stresses: list[CostStressConfig] = []
    for version, multiplier, min_commission, slippage in levels:
        hk, us = _scaled_cost_config(
            cost_multiplier=multiplier,
            min_commission=min_commission,
            slippage_bps=slippage,
        )
        stresses.append(build_cost_stress_config(version=version, hk=hk, us=us))
    return tuple(stresses)


def _stressed_cost(fill: Fill, stress: CostStressConfig) -> float:
    """Re-price one fill under the stressed cost config (SP 2.37 / 2.38)."""
    if fill.market is Market.HK:
        return hk_order_cost(
            symbol=fill.symbol,
            side=fill.side,
            quantity=fill.quantity,
            price=fill.price,
            config=stress.hk,
        ).total_fee
    return us_order_cost(
        symbol=fill.symbol,
        side=fill.side,
        quantity=fill.quantity,
        price=fill.price,
        config=stress.us,
    ).total_cost


def _base_notional(
    fill: Fill,
    base_currency: Currency,
    fx_rate_for: Callable[[Currency, Currency, date], float | None] | None,
) -> float | None:
    """Return the fill's notional in the base currency (None when FX missing)."""
    if fill.currency is base_currency:
        return fill.quantity * fill.price
    if fx_rate_for is None:
        return None
    rate = fx_rate_for(fill.currency, base_currency, fill.trade_date)
    if rate is None or rate <= 0:
        return None
    return fill.quantity * fill.price * rate


def quantify_cost_stress(
    oos_run: RollingOosRun,
    *,
    stress_config: CostStressConfig,
    fills_for: Callable[[WalkForwardFold], Sequence[Fill]],
    net_values_for: Callable[[WalkForwardFold], Sequence[NetValue]],
    base_currency: Currency,
    fx_rate_for: Callable[[Currency, Currency, date], float | None] | None = None,
) -> CostStressScenarioResult:
    """Quantify one stress level's impact on the OOS net value and turnover.

    Every executed fold's actual OOS fills are re-priced under the stressed
    config; the baseline cost is the fill's recorded fee (what the OOS run
    actually paid). The stressed daily net values are the baseline path minus
    the cumulative additional fees (fees reduce cash one-for-one), and the
    turnover is recomputed against the stress-reduced average net value.

    Args:
        oos_run: The SP 3.35 rolling OOS run (executed folds define the fills).
        stress_config: The pre-registered stressed cost configuration.
        fills_for: The fold's actual OOS fills (their ``fee`` is the baseline).
        net_values_for: The fold's OOS net values.
        base_currency: The OOS path currency for the turnover computation.
        fx_rate_for: Converts a fill currency into the base currency; missing
            or non-positive rates are never assumed 1:1.
    """
    per_day: dict[date, float] = {}
    fold_records: list[FoldCostStress] = []
    first_value: float | None = None
    last_executed_final: float | None = None
    total_buy_base = 0.0
    total_sell_base = 0.0
    base_computable = True
    nav_total = 0.0
    nav_count = 0

    for index, result in enumerate(oos_run.results):
        fold = result.validation.fold
        if not result.executed:
            fold_records.append(
                FoldCostStress(
                    fold_index=index,
                    executed=False,
                    baseline_costs=None,
                    stressed_costs=None,
                    cost_increase=None,
                    baseline_final_net_value=None,
                    stressed_final_net_value=None,
                    turnover=None,
                    failure_reason=result.failure_reason,
                )
            )
            continue
        fills = tuple(fills_for(fold))
        net_values = tuple(net_values_for(fold))
        if not net_values:
            raise CostStressError(
                f"executed fold {index} has no net values; cannot quantify cost stress."
            )
        baseline_costs = sum(fill.fee for fill in fills)
        stressed_costs = sum(_stressed_cost(fill, stress_config) for fill in fills)
        cost_increase = stressed_costs - baseline_costs
        baseline_final = net_values[-1].total_value
        if first_value is None:
            first_value = net_values[0].total_value
        last_executed_final = baseline_final
        fold_buy = 0.0
        fold_sell = 0.0
        fold_base_ok = True
        for fill in fills:
            stressed = _stressed_cost(fill, stress_config)
            per_day[fill.trade_date] = per_day.get(fill.trade_date, 0.0) + (stressed - fill.fee)
            base = _base_notional(fill, base_currency, fx_rate_for)
            if base is None:
                fold_base_ok = False
            elif fill.side is OrderSide.BUY:
                fold_buy += base
            else:
                fold_sell += base

        if not fold_base_ok:
            base_computable = False
        else:
            total_buy_base += fold_buy
            total_sell_base += fold_sell
        fold_avg = sum(net.total_value for net in net_values) / len(net_values)
        nav_total += sum(net.total_value for net in net_values)
        nav_count += len(net_values)
        fold_turnover: float | None = None
        if fold_base_ok and fold_avg > 0:
            fold_turnover = min(fold_buy, fold_sell) / fold_avg
        fold_records.append(
            FoldCostStress(
                fold_index=index,
                executed=True,
                baseline_costs=baseline_costs,
                stressed_costs=stressed_costs,
                cost_increase=cost_increase,
                baseline_final_net_value=baseline_final,
                stressed_final_net_value=baseline_final - cost_increase,
                turnover=fold_turnover,
                failure_reason=None,
            )
        )

    if not any(record.executed for record in fold_records):
        raise CostStressError("at least one fold must be executed to quantify cost stress.")
    assert first_value is not None
    assert last_executed_final is not None
    baseline_costs = sum(record.baseline_costs or 0.0 for record in fold_records)
    stressed_costs = sum(record.stressed_costs or 0.0 for record in fold_records)
    cost_increase = stressed_costs - baseline_costs
    stressed_final = last_executed_final - cost_increase
    baseline_avg_nav = nav_total / nav_count
    stressed_avg_nav = _stressed_average_nav(
        fold_records, oos_run, net_values_for, per_day, baseline_avg_nav
    )
    baseline_cumulative = last_executed_final / first_value - 1.0
    stressed_cumulative = stressed_final / first_value - 1.0
    impact_pct = cost_increase / last_executed_final * 100.0
    baseline_turnover: float | None = None
    stressed_turnover: float | None = None
    turnover_delta: float | None = None
    if base_computable and baseline_avg_nav > 0:
        baseline_turnover = min(total_buy_base, total_sell_base) / baseline_avg_nav
        if stressed_avg_nav is not None and stressed_avg_nav > 0:
            stressed_turnover = min(total_buy_base, total_sell_base) / stressed_avg_nav
            turnover_delta = stressed_turnover - baseline_turnover
    scenario = CostStressScenarioResult(
        stress=stress_config,
        folds=tuple(fold_records),
        dataset_fingerprint=oos_run.dataset_fingerprint,
        code_version=oos_run.code_version,
        baseline_first_net_value=first_value,
        baseline_final_net_value=last_executed_final,
        stressed_final_net_value=stressed_final,
        baseline_avg_nav=baseline_avg_nav,
        stressed_avg_nav=stressed_avg_nav,
        baseline_costs=baseline_costs,
        stressed_costs=stressed_costs,
        cost_increase=cost_increase,
        baseline_cumulative_return=baseline_cumulative,
        stressed_cumulative_return=stressed_cumulative,
        net_value_impact_pct=impact_pct,
        baseline_turnover=baseline_turnover,
        stressed_turnover=stressed_turnover,
        turnover_delta=turnover_delta,
        fingerprint="unfingerprinted",
    )
    return replace(scenario, fingerprint=cost_stress_fingerprint(scenario))


def _stressed_average_nav(
    fold_records: Sequence[FoldCostStress],
    oos_run: RollingOosRun,
    net_values_for: Callable[[WalkForwardFold], Sequence[NetValue]],
    per_day: dict[date, float],
    baseline_avg_nav: float,
) -> float:
    """Return the mean net value after subtracting the cumulative extra fees.

    Raises :class:`CostStressError` if the additional fees would drive any
    stressed net value non-positive (the stress exceeds the OOS value).
    """
    fill_days = sorted(per_day)
    cumulative: list[float] = []
    running = 0.0
    for day in fill_days:
        running += per_day[day]
        cumulative.append(running)
    total = 0.0
    count = 0
    for index, result in enumerate(oos_run.results):
        record = fold_records[index]
        if not record.executed:
            continue
        for net in net_values_for(result.validation.fold):
            idx = bisect.bisect_right(fill_days, net.as_of_date) - 1
            increase = cumulative[idx] if idx >= 0 else 0.0
            value = net.total_value - increase
            if value <= 0:
                raise CostStressError(
                    "stressed net value would be non-positive; the stress exceeds "
                    "the available OOS value."
                )
            total += value
            count += 1
    if count == 0:
        return baseline_avg_nav
    return total / count


def compute_cost_stress_report(
    oos_run: RollingOosRun,
    *,
    stresses: tuple[CostStressConfig, ...] | None = None,
    fills_for: Callable[[WalkForwardFold], Sequence[Fill]],
    net_values_for: Callable[[WalkForwardFold], Sequence[NetValue]],
    base_currency: Currency,
    fx_rate_for: Callable[[Currency, Currency, date], float | None] | None = None,
) -> CostStressReport:
    """Quantify the impact across the pre-registered cost stress range (SP 3.51)."""
    applied = default_cost_stresses() if stresses is None else stresses
    if not applied:
        raise CostStressError("at least one cost stress is required.")
    scenarios = tuple(
        quantify_cost_stress(
            oos_run,
            stress_config=stress,
            fills_for=fills_for,
            net_values_for=net_values_for,
            base_currency=base_currency,
            fx_rate_for=fx_rate_for,
        )
        for stress in applied
    )
    report = CostStressReport(
        scenarios=scenarios,
        dataset_fingerprint=oos_run.dataset_fingerprint,
        code_version=oos_run.code_version,
        fingerprint="unfingerprinted",
    )
    return replace(report, fingerprint=cost_stress_report_fingerprint(report))


def _config_payload(config: CostStressConfig) -> dict[str, object]:
    """The stress config's JSON payload (its own fingerprint excluded)."""
    return {
        "version": config.version,
        "source": config.source,
        "hk": _cost_config_payload(config.hk),
        "us": _cost_config_payload(config.us),
    }


def _cost_config_payload(config: CostConfig) -> dict[str, object]:
    """Serialize one stressed CostConfig as scalar fields."""
    return {
        "commission_rate": config.commission_rate,
        "min_commission": config.min_commission,
        "stamp_duty_rate": config.stamp_duty_rate,
        "transaction_levy_rate": config.transaction_levy_rate,
        "trading_fee_rate": config.trading_fee_rate,
        "regulatory_fee_rate": config.regulatory_fee_rate,
        "slippage_bps": config.slippage_bps,
        "lot_size": config.lot_size,
    }


def cost_stress_config_json(config: CostStressConfig) -> str:
    """Return a stable, key-sorted JSON serialization of a stress config."""
    return json.dumps(
        _config_payload(config), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def cost_stress_config_fingerprint(config: CostStressConfig) -> str:
    """Return the stable SHA-256 fingerprint of a stress config (SP 3.51)."""
    return hashlib.sha256(cost_stress_config_json(config).encode("utf-8")).hexdigest()


def cost_stress_json(scenario: CostStressScenarioResult) -> str:
    """Return a stable, key-sorted JSON serialization of a scenario.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style).
    """
    payload: dict[str, object] = {
        "stress": _config_payload(scenario.stress),
        "dataset_fingerprint": scenario.dataset_fingerprint,
        "code_version": scenario.code_version,
        "baseline_first_net_value": scenario.baseline_first_net_value,
        "baseline_final_net_value": scenario.baseline_final_net_value,
        "stressed_final_net_value": scenario.stressed_final_net_value,
        "baseline_avg_nav": scenario.baseline_avg_nav,
        "stressed_avg_nav": scenario.stressed_avg_nav,
        "baseline_costs": scenario.baseline_costs,
        "stressed_costs": scenario.stressed_costs,
        "cost_increase": scenario.cost_increase,
        "baseline_cumulative_return": scenario.baseline_cumulative_return,
        "stressed_cumulative_return": scenario.stressed_cumulative_return,
        "net_value_impact_pct": scenario.net_value_impact_pct,
        "baseline_turnover": scenario.baseline_turnover,
        "stressed_turnover": scenario.stressed_turnover,
        "turnover_delta": scenario.turnover_delta,
        "folds": [
            {
                "fold_index": fold.fold_index,
                "executed": fold.executed,
                "baseline_costs": fold.baseline_costs,
                "stressed_costs": fold.stressed_costs,
                "cost_increase": fold.cost_increase,
                "baseline_final_net_value": fold.baseline_final_net_value,
                "stressed_final_net_value": fold.stressed_final_net_value,
                "turnover": fold.turnover,
                "failure_reason": fold.failure_reason,
            }
            for fold in scenario.folds
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def cost_stress_fingerprint(scenario: CostStressScenarioResult) -> str:
    """Return the stable SHA-256 fingerprint of a scenario (SP 3.51)."""
    return hashlib.sha256(cost_stress_json(scenario).encode("utf-8")).hexdigest()


def cost_stress_report_json(report: CostStressReport) -> str:
    """Return a stable, key-sorted JSON serialization of a cost stress report."""
    payload: dict[str, object] = {
        "dataset_fingerprint": report.dataset_fingerprint,
        "code_version": report.code_version,
        "scenarios": [json.loads(cost_stress_json(scenario)) for scenario in report.scenarios],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def cost_stress_report_fingerprint(report: CostStressReport) -> str:
    """Return the stable SHA-256 fingerprint of a report (SP 3.51)."""
    return hashlib.sha256(cost_stress_report_json(report).encode("utf-8")).hexdigest()


__all__: tuple[str, ...] = (
    "CostStressError",
    "CostStressConfig",
    "CostStressScenarioResult",
    "CostStressReport",
    "FoldCostStress",
    "build_cost_stress_config",
    "default_cost_stresses",
    "quantify_cost_stress",
    "compute_cost_stress_report",
    "cost_stress_config_json",
    "cost_stress_config_fingerprint",
    "cost_stress_json",
    "cost_stress_fingerprint",
    "cost_stress_report_json",
    "cost_stress_report_fingerprint",
)
