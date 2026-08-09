"""Cross-market OOS reconciliation (MVP 3 / SP 3.40).

Separately reconciles the FX, calendar, cost and corporate-action handling of
the HK, US and cross-market out-of-sample runs (分别核对 HK、US 和跨市场组合的
FX、日历、成本和企业行动处理) and continues to REFUSE to compute a
reconciliation when a required FX rate is missing or non-positive (缺失 FX 继续
拒绝计算).

Each portfolio market's SP 3.35 out-of-sample run is reconciled component by
component:

- **FX** (外汇): a market whose quote currency differs from the base currency
  requires a positive FX rate for every OOS trading day (SP 2.12). A missing
  or non-positive rate raises :class:`MissingFxError` instead of assuming 1:1
  (SP 2.27), so a cross-market combination without FX is refused.
- **Calendar** (日历): every day the OOS engine traded in a market must be a
  trading day of the injected authoritative calendar (SP 2.11) and lie within
  the fold's OOS interval; each executed fold must trade at least once.
- **Cost** (成本): each market's OOS execution must use its own cost model —
  HK (SP 2.37) or US (SP 2.38) — never the other market's.
- **Corporate actions** (企业行动): the action types processed in a market
  must belong to that market's allowed set (SP 2.44) — HK and US rules are
  never mixed.

The data-dependent OOS handling (the actual trading dates, the cost-model
label and the processed corporate-action types) is injected per market and
fold so the core layer stays pure; the calendar is the MVP 2 SP 2.11
``TradingCalendar`` contract and the per-market quote currencies / allowed
action types come from the market registry.

Pure core layer: depends only on the SP 3.35 run, the MVP 2 calendar / FX
contracts, the market registry and the domain types, never on storage,
services or CLI.
"""

import hashlib
import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum

from harbor.core.backtest_domain import Currency, Market, to_market_target
from harbor.core.backtest_interfaces import TradingCalendar
from harbor.core.market_registry import CorporateActionType, get_market_config
from harbor.core.rolling_oos import RollingOosRun
from harbor.core.validation_domain import WalkForwardFold


class OosReconcileError(ValueError):
    """Raised when a cross-market OOS reconciliation is invalid (SP 3.40)."""


class MissingFxError(OosReconcileError):
    """Raised when a required FX rate is missing or non-positive.

    Signals that a reconciliation refuses to compute without the required FX
    rate (SP 2.12 / SP 2.27); a 1:1 rate is never assumed.
    """


class ReconcileComponent(StrEnum):
    """The four handling dimensions reconciled per market (SP 3.40)."""

    FX = "fx"
    CALENDAR = "calendar"
    COST = "cost"
    CORPORATE_ACTIONS = "corporate_actions"


#: The four components in the canonical check order.
_COMPONENT_ORDER: tuple[ReconcileComponent, ...] = (
    ReconcileComponent.FX,
    ReconcileComponent.CALENDAR,
    ReconcileComponent.COST,
    ReconcileComponent.CORPORATE_ACTIONS,
)

#: The expected cost-model label per market (SP 2.37 HK / SP 2.38 US).
_EXPECTED_COST_MODEL: dict[Market, str] = {
    Market.HK: "hk",
    Market.US: "us",
}


def _quote_currency(market: Market) -> Currency:
    """Return the currency securities in ``market`` are quoted in."""
    return Currency(get_market_config(to_market_target(market)).currency)


@dataclass(frozen=True)
class ComponentCheck:
    """One market's reconciliation of one handling dimension (SP 3.40).

    ``reconciled`` records whether the dimension reconciled; ``detail`` is
    what was verified and ``reason`` why it failed (``None`` when reconciled).
    """

    component: ReconcileComponent
    market: Market
    reconciled: bool
    detail: str
    reason: str | None

    def __post_init__(self) -> None:
        if not self.detail:
            raise OosReconcileError("a component check must carry a detail.")
        if self.reconciled and self.reason is not None:
            raise OosReconcileError("a reconciled check must not carry a failure reason.")
        if not self.reconciled and self.reason is None:
            raise OosReconcileError("a failed check must carry a failure reason.")

    def readable(self) -> str:
        """Render the check as one line."""
        if self.reconciled:
            return f"{self.market.value}/{self.component.value} reconciled: {self.detail}"
        reason = self.reason if self.reason is not None else "unknown"
        return f"{self.market.value}/{self.component.value} FAILED: {reason}"


@dataclass(frozen=True)
class MarketReconcile:
    """One portfolio market's full cross-market OOS reconciliation (SP 3.40).

    ``quote_currency`` is the market's quote currency and ``fx_required``
    whether an FX rate to the base currency is needed; ``fold_count`` /
    ``executed_fold_count`` surface incomplete OOS execution (never silently
    omitted, SP 3.43) and ``oos_trading_days`` the number of OOS trading days
    verified. ``checks`` covers FX, calendar, cost and corporate actions in
    that order.
    """

    market: Market
    base_currency: Currency
    quote_currency: Currency
    fx_required: bool
    fold_count: int
    executed_fold_count: int
    oos_trading_days: int
    checks: tuple[ComponentCheck, ...]

    def __post_init__(self) -> None:
        if self.fold_count < 0:
            raise OosReconcileError("fold count must be non-negative.")
        if not (0 <= self.executed_fold_count <= self.fold_count):
            raise OosReconcileError("executed fold count must be within the fold count.")
        if self.oos_trading_days < 0:
            raise OosReconcileError("oos trading days must be non-negative.")
        if tuple(check.component for check in self.checks) != _COMPONENT_ORDER:
            raise OosReconcileError(
                "market checks must cover FX, calendar, cost and corporate actions in order."
            )
        for check in self.checks:
            if check.market is not self.market:
                raise OosReconcileError("a market's checks must match its market.")

    @property
    def reconciled(self) -> bool:
        """Whether every component check reconciled."""
        return all(check.reconciled for check in self.checks)

    @property
    def failures(self) -> tuple[ComponentCheck, ...]:
        """The failed component checks, in component order."""
        return tuple(check for check in self.checks if not check.reconciled)

    def readable(self) -> str:
        """Render the market reconciliation as one line."""
        status = "reconciled" if self.reconciled else f"{len(self.failures)} failure(s)"
        if self.fx_required:
            fx = f"{self.quote_currency.value}->{self.base_currency.value}"
        else:
            fx = "no-fx"
        return (
            f"{self.market.value} {status}: fx {fx}, calendar "
            f"{self.executed_fold_count}/{self.fold_count} folds {self.oos_trading_days} days"
        )


@dataclass(frozen=True)
class CrossMarketOosReconcile:
    """The cross-market OOS reconciliation report (SP 3.40).

    One :class:`MarketReconcile` per portfolio market, ordered by market
    (HK before US); ``markets`` is the key-sorted portfolio and ``fingerprint``
    the derived SHA-256 digest. The aggregate properties sum the per-market
    fold and OOS-day counts.
    """

    markets: tuple[Market, ...]
    base_currency: Currency
    dataset_fingerprint: str
    code_version: str
    calendar_version: str | None
    market_results: tuple[MarketReconcile, ...]
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.markets:
            raise OosReconcileError("a reconciliation requires at least one market.")
        if tuple(sorted(self.markets)) != self.markets:
            raise OosReconcileError("markets must be key-sorted.")
        if len(set(self.markets)) != len(self.markets):
            raise OosReconcileError("markets must not contain duplicates.")
        if not self.dataset_fingerprint:
            raise OosReconcileError("dataset fingerprint must be non-empty.")
        if not self.code_version:
            raise OosReconcileError("code version must be non-empty.")
        if len(self.market_results) != len(self.markets):
            raise OosReconcileError("one market result is required per market.")
        for market, result in zip(self.markets, self.market_results):
            if result.market is not market:
                raise OosReconcileError("market results must be ordered by the markets.")
        if not self.fingerprint:
            raise OosReconcileError("reconciliation fingerprint must be non-empty.")

    def __len__(self) -> int:
        return len(self.market_results)

    def __iter__(self) -> Iterator[MarketReconcile]:
        return iter(self.market_results)

    def __getitem__(self, index: int) -> MarketReconcile:
        return self.market_results[index]

    @property
    def reconciled(self) -> bool:
        """Whether every market reconciled on every dimension."""
        return all(result.reconciled for result in self.market_results)

    @property
    def failures(self) -> tuple[ComponentCheck, ...]:
        """All failed component checks across markets."""
        return tuple(check for result in self.market_results for check in result.failures)

    @property
    def fold_count(self) -> int:
        """Total folds across the reconciled markets."""
        return sum(result.fold_count for result in self.market_results)

    @property
    def executed_fold_count(self) -> int:
        """Total executed folds across the reconciled markets."""
        return sum(result.executed_fold_count for result in self.market_results)

    @property
    def oos_trading_days(self) -> int:
        """Total OOS trading days verified across the reconciled markets."""
        return sum(result.oos_trading_days for result in self.market_results)

    def readable(self) -> str:
        """Render the cross-market reconciliation as one line."""
        names = "+".join(market.value for market in self.markets)
        status = "reconciled" if self.reconciled else f"{len(self.failures)} failure(s)"
        return (
            f"cross-market OOS reconciliation {names} base {self.base_currency.value} "
            f"{status} fp {self.fingerprint}"
        )


def _fx_check(
    market: Market,
    *,
    base_currency: Currency,
    quote_currency: Currency,
    fx_required: bool,
    executed_folds: Sequence[WalkForwardFold],
    trading_days: Mapping[int, Sequence[date]],
    fx_rate_for: Callable[[Currency, Currency, date], float | None],
) -> ComponentCheck:
    """Reconcile the FX handling of one market (SP 3.40).

    When FX is required (quote currency differs from the base), every OOS
    trading day must have a positive rate; a missing or non-positive rate
    raises :class:`MissingFxError` — the reconciliation refuses to compute
    rather than assuming 1:1 (SP 2.12 / SP 2.27).
    """
    if not fx_required:
        return ComponentCheck(
            component=ReconcileComponent.FX,
            market=market,
            reconciled=True,
            detail=f"no FX needed: {quote_currency.value} == base {base_currency.value}",
            reason=None,
        )
    checked = 0
    for fold in executed_folds:
        for day in trading_days[fold.fold_index]:
            rate = fx_rate_for(quote_currency, base_currency, day)
            if rate is None or rate <= 0:
                raise MissingFxError(
                    f"{market.value}: missing or non-positive FX rate "
                    f"{quote_currency.value}->{base_currency.value} on {day.isoformat()}; "
                    "refusing to compute a reconciliation without FX (SP 2.12)."
                )
            checked += 1
    return ComponentCheck(
        component=ReconcileComponent.FX,
        market=market,
        reconciled=True,
        detail=(
            f"{quote_currency.value}->{base_currency.value} positive FX on {checked} "
            f"OOS trading day(s) across {len(executed_folds)} fold(s)"
        ),
        reason=None,
    )


def _calendar_check(
    market: Market,
    *,
    calendar: TradingCalendar,
    calendar_version: str | None,
    executed_folds: Sequence[WalkForwardFold],
    trading_days: Mapping[int, Sequence[date]],
) -> ComponentCheck:
    """Reconcile the calendar handling of one market (SP 3.40).

    Every day the OOS engine traded must be a trading day of the injected
    calendar (SP 2.11) within the fold's OOS interval, strictly ascending and
    non-empty per executed fold.
    """
    failures: list[str] = []
    total = 0
    for fold in executed_folds:
        index = fold.fold_index
        days = trading_days[index]
        if not days:
            failures.append(f"fold {index} has no OOS trading days")
            continue
        previous: date | None = None
        for day in days:
            if day < fold.test_start or day > fold.test_end:
                failures.append(
                    f"fold {index} traded on {day.isoformat()} outside its OOS interval "
                    f"{fold.test_start.isoformat()}..{fold.test_end.isoformat()}"
                )
                continue
            if not calendar.is_trading_day(market, day):
                failures.append(
                    f"fold {index} traded on non-trading day {day.isoformat()} "
                    f"(calendar {calendar_version or 'default'})"
                )
                continue
            if previous is not None and day <= previous:
                failures.append(f"fold {index} trading days are not strictly ascending")
                continue
            previous = day
            total += 1
    if failures:
        return ComponentCheck(
            component=ReconcileComponent.CALENDAR,
            market=market,
            reconciled=False,
            detail="calendar handling has errors",
            reason="; ".join(failures),
        )
    version = "" if calendar_version is None else f" {calendar_version}"
    return ComponentCheck(
        component=ReconcileComponent.CALENDAR,
        market=market,
        reconciled=True,
        detail=(
            f"{total} OOS trading day(s) across {len(executed_folds)} fold(s) "
            f"on the market calendar{version}"
        ),
        reason=None,
    )


def _cost_check(
    market: Market,
    *,
    executed_folds: Sequence[WalkForwardFold],
    cost_model_for: Callable[[Market, WalkForwardFold], str],
) -> ComponentCheck:
    """Reconcile the cost handling of one market (SP 3.40).

    Each market's OOS execution must use its own cost model — HK (SP 2.37) or
    US (SP 2.38) — never the other market's.
    """
    expected = _EXPECTED_COST_MODEL[market]
    failures: list[str] = []
    for fold in executed_folds:
        actual = cost_model_for(market, fold)
        if actual != expected:
            failures.append(
                f"fold {fold.fold_index} used cost model '{actual}' for "
                f"{market.value} (expected '{expected}', SP 2.37/2.38)"
            )
    if failures:
        return ComponentCheck(
            component=ReconcileComponent.COST,
            market=market,
            reconciled=False,
            detail="cost handling has errors",
            reason="; ".join(failures),
        )
    return ComponentCheck(
        component=ReconcileComponent.COST,
        market=market,
        reconciled=True,
        detail=f"all {len(executed_folds)} executed fold(s) used the {expected} cost model",
        reason=None,
    )


def _corporate_actions_check(
    market: Market,
    *,
    executed_folds: Sequence[WalkForwardFold],
    corporate_actions_for: Callable[[Market, WalkForwardFold], Sequence[CorporateActionType]],
) -> ComponentCheck:
    """Reconcile the corporate-action handling of one market (SP 3.40).

    Every action type processed in a market must belong to that market's
    allowed set (SP 2.44) — HK and US rules are never mixed.
    """
    allowed = get_market_config(to_market_target(market)).corporate_action_types
    failures: list[str] = []
    processed = 0
    for fold in executed_folds:
        events = corporate_actions_for(market, fold)
        for action_type in events:
            processed += 1
            if action_type not in allowed:
                failures.append(
                    f"fold {fold.fold_index} processed action type "
                    f"'{action_type.value}' in {market.value} (not allowed; "
                    f"{market.value} actions are "
                    f"{', '.join(sorted(item.value for item in allowed))})"
                )
    if failures:
        return ComponentCheck(
            component=ReconcileComponent.CORPORATE_ACTIONS,
            market=market,
            reconciled=False,
            detail="corporate-action handling has errors",
            reason="; ".join(failures),
        )
    return ComponentCheck(
        component=ReconcileComponent.CORPORATE_ACTIONS,
        market=market,
        reconciled=True,
        detail=(
            f"{processed} corporate action(s) processed across "
            f"{len(executed_folds)} fold(s), all {market.value}-allowed"
        ),
        reason=None,
    )


def reconcile_cross_market_oos(
    oos_runs: Mapping[Market, RollingOosRun],
    *,
    base_currency: Currency,
    calendar: TradingCalendar,
    calendar_version: str | None = None,
    fx_rate_for: Callable[[Currency, Currency, date], float | None],
    trading_days_for: Callable[[Market, WalkForwardFold], Sequence[date]],
    cost_model_for: Callable[[Market, WalkForwardFold], str],
    corporate_actions_for: Callable[[Market, WalkForwardFold], Sequence[CorporateActionType]],
) -> CrossMarketOosReconcile:
    """Reconcile the FX/calendar/cost/corporate-action handling of OOS runs.

    Args:
        oos_runs: One SP 3.35 rolling OOS run per portfolio market. All runs
            must share one dataset fingerprint, one code version and the same
            fold count (a consistent validation experiment).
        base_currency: The portfolio's base (benchmark) currency.
        calendar: The authoritative market trading calendar (SP 2.11).
        calendar_version: Optional calendar version label recorded on the
            report and in the calendar-check details.
        fx_rate_for: Returns the last known FX rate (from → to) on a date, or
            ``None`` when unavailable (SP 2.12). A missing or non-positive
            rate for a market that requires FX raises :class:`MissingFxError`
            — the reconciliation refuses to compute (缺失 FX 拒绝计算).
        trading_days_for: The actual OOS trading dates of a market's engine
            for a fold.
        cost_model_for: The cost-model label a market's engine actually used
            for a fold (expected "hk" for HK, "us" for US).
        corporate_actions_for: The corporate-action types a market's engine
            processed for a fold.

    Returns:
        The cross-market OOS reconciliation report (SP 3.40).

    Raises:
        OosReconcileError: If the inputs are inconsistent (no runs, mixed
            dataset fingerprints / code versions / fold counts).
        MissingFxError: If a market requires FX and a required rate is
            missing or non-positive on any OOS trading day.
    """
    if not oos_runs:
        raise OosReconcileError("at least one market OOS run is required.")
    markets = tuple(sorted(oos_runs))
    runs = [oos_runs[market] for market in markets]
    dataset_fingerprint = runs[0].dataset_fingerprint
    code_version = runs[0].code_version
    if len({len(run.results) for run in runs}) != 1:
        raise OosReconcileError("all market OOS runs must cover the same number of folds.")
    for run in runs:
        if run.dataset_fingerprint != dataset_fingerprint:
            raise OosReconcileError("all market OOS runs must share one dataset fingerprint.")
        if run.code_version != code_version:
            raise OosReconcileError("all market OOS runs must share one code version.")

    market_results: list[MarketReconcile] = []
    for market, run in zip(markets, runs):
        quote = _quote_currency(market)
        fx_required = quote is not base_currency
        executed_folds = [result.fold for result in run.results if result.executed]
        trading_days: dict[int, Sequence[date]] = {}
        oos_days = 0
        for fold in executed_folds:
            days = trading_days_for(market, fold)
            trading_days[fold.fold_index] = days
            oos_days += len(days)
        checks = (
            _fx_check(
                market,
                base_currency=base_currency,
                quote_currency=quote,
                fx_required=fx_required,
                executed_folds=executed_folds,
                trading_days=trading_days,
                fx_rate_for=fx_rate_for,
            ),
            _calendar_check(
                market,
                calendar=calendar,
                calendar_version=calendar_version,
                executed_folds=executed_folds,
                trading_days=trading_days,
            ),
            _cost_check(
                market,
                executed_folds=executed_folds,
                cost_model_for=cost_model_for,
            ),
            _corporate_actions_check(
                market,
                executed_folds=executed_folds,
                corporate_actions_for=corporate_actions_for,
            ),
        )
        market_results.append(
            MarketReconcile(
                market=market,
                base_currency=base_currency,
                quote_currency=quote,
                fx_required=fx_required,
                fold_count=len(run.results),
                executed_fold_count=run.executed_count,
                oos_trading_days=oos_days,
                checks=checks,
            )
        )

    report = CrossMarketOosReconcile(
        markets=markets,
        base_currency=base_currency,
        dataset_fingerprint=dataset_fingerprint,
        code_version=code_version,
        calendar_version=calendar_version,
        market_results=tuple(market_results),
        fingerprint="unfingerprinted",
    )
    return replace(report, fingerprint=oos_reconcile_fingerprint(report))


def oos_reconcile_json(report: CrossMarketOosReconcile) -> str:
    """Return a stable, key-sorted JSON serialization of a reconciliation.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style).
    """
    payload: dict[str, object] = {
        "base_currency": report.base_currency.value,
        "calendar_version": report.calendar_version,
        "code_version": report.code_version,
        "dataset_fingerprint": report.dataset_fingerprint,
        "markets": [market.value for market in report.markets],
        "market_results": [
            {
                "base_currency": result.base_currency.value,
                "checks": [
                    {
                        "component": check.component.value,
                        "detail": check.detail,
                        "market": check.market.value,
                        "reason": check.reason,
                        "reconciled": check.reconciled,
                    }
                    for check in result.checks
                ],
                "executed_fold_count": result.executed_fold_count,
                "fold_count": result.fold_count,
                "fx_required": result.fx_required,
                "market": result.market.value,
                "oos_trading_days": result.oos_trading_days,
                "quote_currency": result.quote_currency.value,
            }
            for result in report.market_results
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def oos_reconcile_fingerprint(report: CrossMarketOosReconcile) -> str:
    """Return the stable SHA-256 fingerprint of a reconciliation (SP 3.40)."""
    return hashlib.sha256(oos_reconcile_json(report).encode("utf-8")).hexdigest()
