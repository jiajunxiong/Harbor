"""FX stress (MVP 3 / SP 3.53).

Applies pre-registered FX delay, shock and missing scenarios to a cross-market
OOS run and quantifies the base-currency impact; a missing FX rate is always
refused rather than interpolated as 1:1 (对跨市场组合使用预注册 FX 延迟、冲击和
缺失情景；缺失 FX 始终拒绝而非插补 1:1).

- :class:`FxStressConfig` is one pre-registered stressed FX scenario
  (versioned + fingerprinted): ``DELAY`` values the foreign flows at the stale
  rate from ``delay_days`` earlier, ``SHOCK`` shifts the rate by ``shock_bps``,
  and ``MISSING`` makes the foreign rate unavailable so every foreign
  conversion is refused — never assumed 1:1 (SP 2.12). ``default_fx_stresses()``
  ships the pre-registered range (delay 1d / 5d, shock +/-5%, missing).
- :func:`quantify_fx_stress` re-values the OOS foreign-currency fills under the
  stressed scenario and reports the shift in their base-currency value
  (``fx_impact``) relative to the OOS final net value. Every fill whose
  (possibly delayed / shocked) rate is unavailable is recorded as a refused
  fill (缺失 FX 始终拒绝) and excluded from the totals — a fill is never valued
  at 1:1.

Pure core layer: depends only on the SP 3.35 run, the MVP 2 FX/domain types and
the SP 2.12 refusal convention, never on storage, services or CLI.
"""

import hashlib
import json
import math
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import date, timedelta
from enum import StrEnum

from harbor.core.backtest_domain import Currency, Fill, Market, NetValue, OrderSide
from harbor.core.rolling_oos import RollingOosRun
from harbor.core.validation_domain import WalkForwardFold

_TOL = 1e-6


class FxStressError(ValueError):
    """Raised when FX-stress inputs are invalid (SP 3.53)."""


class FxStressScenario(StrEnum):
    """The pre-registered FX stress scenario kinds (SP 3.53)."""

    DELAY = "delay"
    SHOCK = "shock"
    MISSING = "missing"


@dataclass(frozen=True)
class FxStressConfig:
    """One pre-registered stressed FX scenario (SP 3.53).

    ``DELAY`` requires ``delay_days`` >= 1 (the rate applied is the stale rate
    from that many days earlier); ``SHOCK`` requires a non-zero ``shock_bps``
    (the rate is shifted by that many basis points); ``MISSING`` carries no
    parameters (every foreign conversion is refused).
    """

    version: str
    source: str
    scenario: FxStressScenario
    delay_days: int
    shock_bps: float
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.version:
            raise FxStressError("FX stress version must be non-empty.")
        if not self.source:
            raise FxStressError("FX stress source must be non-empty.")
        if self.scenario is FxStressScenario.DELAY:
            if self.delay_days < 1:
                raise FxStressError("a delay scenario requires delay_days >= 1.")
            if self.shock_bps != 0.0:
                raise FxStressError("a delay scenario must not carry a shock.")
        elif self.scenario is FxStressScenario.SHOCK:
            if self.shock_bps == 0.0:
                raise FxStressError("a shock scenario requires a non-zero shock_bps.")
            if self.delay_days != 0:
                raise FxStressError("a shock scenario must not carry a delay.")
        else:
            if self.delay_days != 0 or self.shock_bps != 0.0:
                raise FxStressError("a missing scenario must not carry delay or shock parameters.")
        if not self.fingerprint:
            raise FxStressError("FX stress fingerprint must be non-empty.")

    def readable(self) -> str:
        """Render the stress as one line."""
        detail: str
        if self.scenario is FxStressScenario.DELAY:
            detail = f"delayed {self.delay_days} day(s)"
        elif self.scenario is FxStressScenario.SHOCK:
            detail = f"shock {self.shock_bps:+g}bp"
        else:
            detail = "rate missing -> refuse"
        return (
            f"FX stress {self.version} ({self.source}): {self.scenario.value} "
            f"{detail} fp {self.fingerprint}"
        )


@dataclass(frozen=True)
class FxRefusedFill:
    """A foreign fill whose FX conversion was refused (SP 3.53).

    The record preserves the fill and a human-readable ``reason``; a refused
    fill is never valued at 1:1 and never silently dropped (缺失 FX 始终拒绝).
    """

    market: Market
    symbol: str
    side: OrderSide
    quantity: float
    price: float
    currency: Currency
    day: date
    reason: str

    def __post_init__(self) -> None:
        if not self.symbol:
            raise FxStressError("refused fill symbol must be non-empty.")
        if not self.reason:
            raise FxStressError("refused fill must carry a reason.")

    def readable(self) -> str:
        """Render the refusal as one line."""
        return (
            f"refused FX for {self.side.value} {self.symbol} on "
            f"{self.day.isoformat()}: {self.reason}"
        )


@dataclass(frozen=True)
class FoldFxStress:
    """One fold's stressed FX impact (SP 3.53).

    ``baseline_base_value`` / ``stressed_base_value`` are the base-currency
    values of the fold's foreign fills at the baseline and stressed rates;
    ``fx_impact`` is their difference. Fills whose rate is unavailable are
    preserved in ``refused_fills`` and excluded from both totals.
    """

    fold_index: int
    executed: bool
    baseline_base_value: float
    stressed_base_value: float
    fx_impact: float
    refused_fills: tuple[FxRefusedFill, ...]
    failure_reason: str | None

    def __post_init__(self) -> None:
        if self.fold_index < 0:
            raise FxStressError("fold index must be non-negative.")
        if self.baseline_base_value < 0 or self.stressed_base_value < 0:
            raise FxStressError("base values must be non-negative.")
        if not math.isclose(
            self.fx_impact,
            self.stressed_base_value - self.baseline_base_value,
            abs_tol=_TOL,
        ):
            raise FxStressError("fx impact must equal stressed minus baseline base value.")
        if self.executed:
            if self.failure_reason is not None:
                raise FxStressError("an executed fold must not carry a failure reason.")
        else:
            if self.failure_reason is None:
                raise FxStressError("a non-executed fold must carry a failure reason.")
            if self.baseline_base_value != 0.0 or self.stressed_base_value != 0.0:
                raise FxStressError("a non-executed fold must not carry base values.")

    def readable(self) -> str:
        """Render the fold impact as one line."""
        if not self.executed:
            return f"fold {self.fold_index} NOT executed: {self.failure_reason}"
        return (
            f"fold {self.fold_index}: foreign base value "
            f"{self.baseline_base_value:.2f} -> {self.stressed_base_value:.2f} "
            f"({self.fx_impact:+.2f}); {len(self.refused_fills)} refused"
        )


@dataclass(frozen=True)
class FxStressScenarioResult:
    """One stress level's FX impact across all OOS folds (SP 3.53)."""

    stress: FxStressConfig
    folds: tuple[FoldFxStress, ...]
    dataset_fingerprint: str
    code_version: str
    baseline_base_value: float
    stressed_base_value: float
    fx_impact: float
    baseline_final_net_value: float
    net_value_impact_pct: float
    refused_count: int
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.folds:
            raise FxStressError("an FX stress scenario requires at least one fold record.")
        for index, fold in enumerate(self.folds):
            if fold.fold_index != index:
                raise FxStressError(f"FX stress fold {index} must carry fold_index {index}.")
        if not any(fold.executed for fold in self.folds):
            raise FxStressError("at least one fold must be executed to quantify FX stress.")
        if not self.dataset_fingerprint:
            raise FxStressError("dataset fingerprint must be non-empty.")
        if not self.code_version:
            raise FxStressError("code version must be non-empty.")
        if not self.fingerprint:
            raise FxStressError("FX stress scenario fingerprint must be non-empty.")
        if self.baseline_final_net_value <= 0:
            raise FxStressError("baseline final net value must be positive.")
        if not math.isclose(
            self.fx_impact,
            self.stressed_base_value - self.baseline_base_value,
            abs_tol=_TOL,
        ):
            raise FxStressError("fx impact must equal stressed minus baseline base value.")
        if not math.isclose(
            self.baseline_base_value,
            sum(fold.baseline_base_value for fold in self.folds),
            abs_tol=_TOL,
        ):
            raise FxStressError("baseline base value must equal the sum of the fold values.")
        if not math.isclose(
            self.stressed_base_value,
            sum(fold.stressed_base_value for fold in self.folds),
            abs_tol=_TOL,
        ):
            raise FxStressError("stressed base value must equal the sum of the fold values.")
        if not math.isclose(
            self.net_value_impact_pct,
            self.fx_impact / self.baseline_final_net_value * 100.0,
            abs_tol=_TOL,
        ):
            raise FxStressError("net value impact percentage is inconsistent.")
        if self.refused_count != sum(len(fold.refused_fills) for fold in self.folds):
            raise FxStressError("refused count must equal the sum of the fold refusals.")

    def __len__(self) -> int:
        return len(self.folds)

    def __iter__(self) -> Iterator[FoldFxStress]:
        return iter(self.folds)

    def __getitem__(self, index: int) -> FoldFxStress:
        return self.folds[index]

    @property
    def executed_count(self) -> int:
        """Number of folds whose FX was stressed."""
        return sum(1 for fold in self.folds if fold.executed)

    @property
    def refused_fills(self) -> tuple[FxRefusedFill, ...]:
        """Every refused foreign fill across all folds (preserved, never dropped)."""
        return tuple(fill for fold in self.folds for fill in fold.refused_fills)

    def readable(self) -> str:
        """Render the scenario as one line."""
        return (
            f"FX stress {self.stress.version}: foreign base value "
            f"{self.baseline_base_value:.2f} -> {self.stressed_base_value:.2f} "
            f"({self.fx_impact:+.2f}, {self.net_value_impact_pct:+.4f}% of net "
            f"value); {self.refused_count} refused fp {self.fingerprint}"
        )


@dataclass(frozen=True)
class FxStressReport:
    """The stressed FX impacts across the pre-registered range (SP 3.53)."""

    scenarios: tuple[FxStressScenarioResult, ...]
    dataset_fingerprint: str
    code_version: str
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.scenarios:
            raise FxStressError("an FX stress report requires at least one scenario.")
        seen: set[str] = set()
        for scenario in self.scenarios:
            if scenario.dataset_fingerprint != self.dataset_fingerprint:
                raise FxStressError("all scenarios must share the report's dataset fingerprint.")
            if scenario.code_version != self.code_version:
                raise FxStressError("all scenarios must share the report's code version.")
            if scenario.stress.version in seen:
                raise FxStressError("scenario stress versions must be unique.")
            seen.add(scenario.stress.version)
        if not self.fingerprint:
            raise FxStressError("FX stress report fingerprint must be non-empty.")

    def __len__(self) -> int:
        return len(self.scenarios)

    def __iter__(self) -> Iterator[FxStressScenarioResult]:
        return iter(self.scenarios)

    def __getitem__(self, index: int) -> FxStressScenarioResult:
        return self.scenarios[index]

    def scenario(self, version: str) -> FxStressScenarioResult | None:
        """Return the scenario for one stress version (None when absent)."""
        for scenario in self.scenarios:
            if scenario.stress.version == version:
                return scenario
        return None

    def readable(self) -> str:
        """Render the report as one line."""
        return f"{len(self.scenarios)} FX stress scenario(s) fp {self.fingerprint}"


def build_fx_stress_config(
    *,
    version: str,
    source: str = "pre-registered",
    scenario: FxStressScenario,
    delay_days: int = 0,
    shock_bps: float = 0.0,
) -> FxStressConfig:
    """Assemble a versioned, fingerprint-stamped FX stress config (SP 3.53)."""
    config = FxStressConfig(
        version=version,
        source=source,
        scenario=scenario,
        delay_days=delay_days,
        shock_bps=shock_bps,
        fingerprint="unfingerprinted",
    )
    return replace(config, fingerprint=fx_stress_config_fingerprint(config))


def default_fx_stresses() -> tuple[FxStressConfig, ...]:
    """Return the pre-registered FX stress range.

    Delay scenarios value the foreign flows at the stale rate from 1 / 5 days
    earlier, shock scenarios shift the rate by +/-5%, and the missing scenario
    makes every foreign rate unavailable so all conversions are refused (缺失
    FX 始终拒绝而非插补 1:1) — the fixed, auditable range the acceptance
    requires (在预注册范围内).
    """
    return (
        build_fx_stress_config(
            version="fx-delay-1d",
            scenario=FxStressScenario.DELAY,
            delay_days=1,
        ),
        build_fx_stress_config(
            version="fx-delay-5d",
            scenario=FxStressScenario.DELAY,
            delay_days=5,
        ),
        build_fx_stress_config(
            version="fx-shock-plus-5pct",
            scenario=FxStressScenario.SHOCK,
            shock_bps=500.0,
        ),
        build_fx_stress_config(
            version="fx-shock-minus-5pct",
            scenario=FxStressScenario.SHOCK,
            shock_bps=-500.0,
        ),
        build_fx_stress_config(
            version="fx-missing",
            scenario=FxStressScenario.MISSING,
        ),
    )


def _stressed_rate(
    stress: FxStressConfig,
    fill: Fill,
    base_currency: Currency,
    fx_rate_for: Callable[[Currency, Currency, date], float | None],
) -> float | None:
    """Return the stressed FX rate for one foreign fill (None when unavailable).

    ``DELAY`` reads the stale rate from ``delay_days`` earlier; ``SHOCK`` shifts
    the day's rate by ``shock_bps``; ``MISSING`` always yields ``None`` so the
    conversion is refused (never assumed 1:1).
    """
    if stress.scenario is FxStressScenario.MISSING:
        return None
    if stress.scenario is FxStressScenario.DELAY:
        return fx_rate_for(
            fill.currency, base_currency, fill.trade_date - timedelta(days=stress.delay_days)
        )
    rate = fx_rate_for(fill.currency, base_currency, fill.trade_date)
    if rate is None:
        return None
    return rate * (1.0 + stress.shock_bps / 10_000.0)


def _refusal_reason(
    stress: FxStressConfig,
    fill: Fill,
    base_currency: Currency,
) -> str:
    """The refusal reason for a foreign fill whose FX is unavailable."""
    if stress.scenario is FxStressScenario.MISSING:
        return (
            f"missing FX rate {fill.currency.value}->{base_currency.value} on "
            f"{fill.trade_date.isoformat()} under {stress.version}; refusing to "
            "assume 1:1 (SP 3.53)."
        )
    return (
        f"missing FX rate {fill.currency.value}->{base_currency.value} on "
        f"{fill.trade_date.isoformat()}; refusing to assume 1:1 (SP 2.12)."
    )


def quantify_fx_stress(
    oos_run: RollingOosRun,
    *,
    stress_config: FxStressConfig,
    base_currency: Currency,
    fills_for: Callable[[WalkForwardFold], Sequence[Fill]],
    net_values_for: Callable[[WalkForwardFold], Sequence[NetValue]],
    fx_rate_for: Callable[[Currency, Currency, date], float | None],
) -> FxStressScenarioResult:
    """Quantify one FX stress level's impact on the OOS base currency.

    Every executed fold's foreign-currency fills are re-valued at the stressed
    rate (SP 3.53 delay / shock / missing); a fill whose baseline or stressed
    rate is missing or non-positive is refused (缺失 FX 始终拒绝而非插补 1:1)
    and preserved, never valued at 1:1.

    Args:
        oos_run: The SP 3.35 rolling OOS run (executed folds define the fills).
        stress_config: The pre-registered stressed FX scenario.
        base_currency: The OOS base currency.
        fills_for: The fold's actual OOS fills (their foreign notional is the
            FX exposure).
        net_values_for: The fold's OOS net values (for the final net value).
        fx_rate_for: The day's FX rate; a missing or non-positive rate is
            refused, never assumed 1:1.
    """
    fold_records: list[FoldFxStress] = []
    baseline_final: float | None = None
    for index, result in enumerate(oos_run.results):
        if not result.executed:
            fold_records.append(
                FoldFxStress(
                    fold_index=index,
                    executed=False,
                    baseline_base_value=0.0,
                    stressed_base_value=0.0,
                    fx_impact=0.0,
                    refused_fills=(),
                    failure_reason=result.failure_reason,
                )
            )
            continue
        fold = result.validation.fold
        net_values = tuple(net_values_for(fold))
        if not net_values:
            raise FxStressError(
                f"executed fold {index} has no net values; cannot quantify FX stress."
            )
        baseline_final = net_values[-1].total_value
        baseline_total = 0.0
        stressed_total = 0.0
        refused: list[FxRefusedFill] = []
        for fill in fills_for(fold):
            if fill.currency is base_currency:
                continue
            baseline_rate = fx_rate_for(fill.currency, base_currency, fill.trade_date)
            stressed_rate = _stressed_rate(stress_config, fill, base_currency, fx_rate_for)
            if (
                baseline_rate is None
                or baseline_rate <= 0
                or stressed_rate is None
                or stressed_rate <= 0
            ):
                refused.append(
                    FxRefusedFill(
                        market=fill.market,
                        symbol=fill.symbol,
                        side=fill.side,
                        quantity=fill.quantity,
                        price=fill.price,
                        currency=fill.currency,
                        day=fill.trade_date,
                        reason=_refusal_reason(stress_config, fill, base_currency),
                    )
                )
                continue
            notional = fill.quantity * fill.price
            baseline_total += notional * baseline_rate
            stressed_total += notional * stressed_rate
        fold_records.append(
            FoldFxStress(
                fold_index=index,
                executed=True,
                baseline_base_value=baseline_total,
                stressed_base_value=stressed_total,
                fx_impact=stressed_total - baseline_total,
                refused_fills=tuple(refused),
                failure_reason=None,
            )
        )

    if not any(record.executed for record in fold_records):
        raise FxStressError("at least one fold must be executed to quantify FX stress.")
    assert baseline_final is not None
    baseline_total = sum(record.baseline_base_value for record in fold_records)
    stressed_total = sum(record.stressed_base_value for record in fold_records)
    fx_impact = stressed_total - baseline_total
    scenario = FxStressScenarioResult(
        stress=stress_config,
        folds=tuple(fold_records),
        dataset_fingerprint=oos_run.dataset_fingerprint,
        code_version=oos_run.code_version,
        baseline_base_value=baseline_total,
        stressed_base_value=stressed_total,
        fx_impact=fx_impact,
        baseline_final_net_value=baseline_final,
        net_value_impact_pct=fx_impact / baseline_final * 100.0,
        refused_count=sum(len(record.refused_fills) for record in fold_records),
        fingerprint="unfingerprinted",
    )
    return replace(scenario, fingerprint=fx_stress_fingerprint(scenario))


def compute_fx_stress_report(
    oos_run: RollingOosRun,
    *,
    stresses: tuple[FxStressConfig, ...] | None = None,
    base_currency: Currency,
    fills_for: Callable[[WalkForwardFold], Sequence[Fill]],
    net_values_for: Callable[[WalkForwardFold], Sequence[NetValue]],
    fx_rate_for: Callable[[Currency, Currency, date], float | None],
) -> FxStressReport:
    """Quantify the impacts across the pre-registered FX stress range (SP 3.53)."""
    applied = default_fx_stresses() if stresses is None else stresses
    if not applied:
        raise FxStressError("at least one FX stress is required.")
    scenarios = tuple(
        quantify_fx_stress(
            oos_run,
            stress_config=stress,
            base_currency=base_currency,
            fills_for=fills_for,
            net_values_for=net_values_for,
            fx_rate_for=fx_rate_for,
        )
        for stress in applied
    )
    report = FxStressReport(
        scenarios=scenarios,
        dataset_fingerprint=oos_run.dataset_fingerprint,
        code_version=oos_run.code_version,
        fingerprint="unfingerprinted",
    )
    return replace(report, fingerprint=fx_stress_report_fingerprint(report))


def _config_payload(config: FxStressConfig) -> dict[str, object]:
    """The stress config's JSON payload (its own fingerprint excluded)."""
    return {
        "version": config.version,
        "source": config.source,
        "scenario": config.scenario.value,
        "delay_days": config.delay_days,
        "shock_bps": config.shock_bps,
    }


def _refused_payload(refused: FxRefusedFill) -> dict[str, object]:
    """Serialize one preserved refused fill."""
    return {
        "market": refused.market.value,
        "symbol": refused.symbol,
        "side": refused.side.value,
        "quantity": refused.quantity,
        "price": refused.price,
        "currency": refused.currency.value,
        "day": refused.day.isoformat(),
        "reason": refused.reason,
    }


def fx_stress_config_json(config: FxStressConfig) -> str:
    """Return a stable, key-sorted JSON serialization of a stress config."""
    return json.dumps(
        _config_payload(config), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def fx_stress_config_fingerprint(config: FxStressConfig) -> str:
    """Return the stable SHA-256 fingerprint of a stress config (SP 3.53)."""
    return hashlib.sha256(fx_stress_config_json(config).encode("utf-8")).hexdigest()


def fx_stress_json(scenario: FxStressScenarioResult) -> str:
    """Return a stable, key-sorted JSON serialization of a scenario.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style).
    """
    payload: dict[str, object] = {
        "stress": _config_payload(scenario.stress),
        "dataset_fingerprint": scenario.dataset_fingerprint,
        "code_version": scenario.code_version,
        "baseline_base_value": scenario.baseline_base_value,
        "stressed_base_value": scenario.stressed_base_value,
        "fx_impact": scenario.fx_impact,
        "baseline_final_net_value": scenario.baseline_final_net_value,
        "net_value_impact_pct": scenario.net_value_impact_pct,
        "refused_count": scenario.refused_count,
        "folds": [
            {
                "fold_index": fold.fold_index,
                "executed": fold.executed,
                "baseline_base_value": fold.baseline_base_value,
                "stressed_base_value": fold.stressed_base_value,
                "fx_impact": fold.fx_impact,
                "refused_fills": [_refused_payload(f) for f in fold.refused_fills],
                "failure_reason": fold.failure_reason,
            }
            for fold in scenario.folds
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fx_stress_fingerprint(scenario: FxStressScenarioResult) -> str:
    """Return the stable SHA-256 fingerprint of a scenario (SP 3.53)."""
    return hashlib.sha256(fx_stress_json(scenario).encode("utf-8")).hexdigest()


def fx_stress_report_json(report: FxStressReport) -> str:
    """Return a stable, key-sorted JSON serialization of a report."""
    payload: dict[str, object] = {
        "dataset_fingerprint": report.dataset_fingerprint,
        "code_version": report.code_version,
        "scenarios": [json.loads(fx_stress_json(scenario)) for scenario in report.scenarios],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fx_stress_report_fingerprint(report: FxStressReport) -> str:
    """Return the stable SHA-256 fingerprint of a report (SP 3.53)."""
    return hashlib.sha256(fx_stress_report_json(report).encode("utf-8")).hexdigest()


__all__: tuple[str, ...] = (
    "FxStressError",
    "FxStressScenario",
    "FxStressConfig",
    "FxRefusedFill",
    "FoldFxStress",
    "FxStressScenarioResult",
    "FxStressReport",
    "build_fx_stress_config",
    "default_fx_stresses",
    "quantify_fx_stress",
    "compute_fx_stress_report",
    "fx_stress_config_json",
    "fx_stress_config_fingerprint",
    "fx_stress_json",
    "fx_stress_fingerprint",
    "fx_stress_report_json",
    "fx_stress_report_fingerprint",
)
