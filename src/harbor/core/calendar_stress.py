"""Calendar and rebalance stress (MVP 3 / SP 3.54).

Checks the impact of rebalances meeting market closures, long holidays, delays
and different legal deferral rules, and records the authoritative calendar
version (检验调仓遇休市、长假、延迟与不同合法顺延规则的影响，并记录权威日历版
本).

- :class:`CalendarStressConfig` is one pre-registered stressed calendar
  scenario (versioned + fingerprinted): the injected market closures
  (``holidays``), the legal ``DeferralRule`` (SP 2.33 forward / backward) and
  the authoritative ``calendar_version`` being stressed (记录权威日历版本).
  ``default_calendar_stresses()`` ships the pre-registered range (a closure, a
  long holiday and a backward-deferral variant).
- :func:`quantify_calendar_stress` re-derives each OOS rebalance anchor's
  scheduled day under the stressed calendar and deferral rule. An anchor that
  is closed only because of the stress's added holidays is flagged
  ``stress_closed``; the deferral (shift) is quantified per anchor and
  aggregated (deferred count, total / max shift days).

Pure core layer: depends only on the SP 3.35 run, the MVP 2 calendar (SP 2.11)
and deferral rule (SP 2.33), never on storage, services or CLI.
"""

import hashlib
import json
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import date

from harbor.core.backtest_domain import Market
from harbor.core.backtest_interfaces import TradingCalendar
from harbor.core.rebalance_schedule import DeferralRule
from harbor.core.rolling_oos import RollingOosRun


class CalendarStressError(ValueError):
    """Raised when calendar-stress inputs are invalid (SP 3.54)."""


@dataclass(frozen=True)
class CalendarStressConfig:
    """One pre-registered stressed calendar scenario (SP 3.54).

    ``holidays`` are the injected market closures (a single closure 休市 or a
    long holiday 长假), ``deferral_rule`` the legal deferral (SP 2.33 forward /
    backward) and ``calendar_version`` the authoritative calendar version being
    stressed (记录权威日历版本).
    """

    version: str
    source: str
    holidays: tuple[date, ...]
    deferral_rule: DeferralRule
    calendar_version: str
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.version:
            raise CalendarStressError("calendar stress version must be non-empty.")
        if not self.source:
            raise CalendarStressError("calendar stress source must be non-empty.")
        if not self.calendar_version:
            raise CalendarStressError("calendar version must be non-empty.")
        previous: date | None = None
        for holiday in self.holidays:
            if previous is not None and holiday <= previous:
                raise CalendarStressError(
                    "calendar stress holidays must be unique and strictly ascending."
                )
            previous = holiday
        if not self.fingerprint:
            raise CalendarStressError("calendar stress fingerprint must be non-empty.")

    def readable(self) -> str:
        """Render the stress as one line."""
        holidays = ",".join(day.isoformat() for day in self.holidays) or "none"
        return (
            f"calendar stress {self.version} ({self.source}): holidays "
            f"[{holidays}] {self.deferral_rule.value} calendar "
            f"{self.calendar_version} fp {self.fingerprint}"
        )


@dataclass(frozen=True)
class RebalanceDayImpact:
    """One OOS rebalance anchor's scheduled day under stress (SP 3.54).

    ``trading_day`` is whether the anchor itself is a trading day under the
    stressed calendar; ``stress_closed`` marks an anchor that was tradeable
    before the stress but closed by its added holidays; ``scheduled`` is the
    rebalance day after the legal deferral and ``shift_days`` the signed
    calendar-day shift (negative for backward deferral).
    """

    anchor: date
    trading_day: bool
    stress_closed: bool
    scheduled: date
    shift_days: int
    reason: str

    def __post_init__(self) -> None:
        if self.shift_days != (self.scheduled - self.anchor).days:
            raise CalendarStressError(
                "shift days must equal the scheduled minus anchor difference."
            )
        if self.trading_day != (self.shift_days == 0):
            raise CalendarStressError("an anchor is a trading day exactly when it is not deferred.")
        if self.stress_closed and self.trading_day:
            raise CalendarStressError("a stress-closed anchor cannot be a trading day.")
        if not self.reason:
            raise CalendarStressError("rebalance impact reason must be non-empty.")

    def readable(self) -> str:
        """Render the impact as one line."""
        return (
            f"{self.anchor.isoformat()} -> {self.scheduled.isoformat()} "
            f"({self.shift_days:+d}d, trading {self.trading_day}, "
            f"stress-closed {self.stress_closed}): {self.reason}"
        )


@dataclass(frozen=True)
class CalendarStressScenarioResult:
    """One stress level's impact across the OOS rebalance anchors (SP 3.54)."""

    stress: CalendarStressConfig
    market: Market
    calendar_version: str
    dataset_fingerprint: str
    code_version: str
    impacts: tuple[RebalanceDayImpact, ...]
    deferred_count: int
    stress_closed_count: int
    total_shift_days: int
    max_shift_days: int
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.impacts:
            raise CalendarStressError(
                "a calendar stress scenario requires at least one rebalance anchor."
            )
        if not self.calendar_version:
            raise CalendarStressError("calendar version must be non-empty.")
        if not self.dataset_fingerprint:
            raise CalendarStressError("dataset fingerprint must be non-empty.")
        if not self.code_version:
            raise CalendarStressError("code version must be non-empty.")
        if not self.fingerprint:
            raise CalendarStressError("calendar stress scenario fingerprint must be non-empty.")
        deferred = sum(1 for impact in self.impacts if impact.shift_days != 0)
        if self.deferred_count != deferred:
            raise CalendarStressError("deferred count must equal the number of shifted anchors.")
        stress_closed = sum(1 for impact in self.impacts if impact.stress_closed)
        if self.stress_closed_count != stress_closed:
            raise CalendarStressError(
                "stress-closed count must equal the number of stress-closed anchors."
            )
        total = sum(abs(impact.shift_days) for impact in self.impacts)
        if self.total_shift_days != total:
            raise CalendarStressError("total shift days must equal the sum of the absolute shifts.")
        maximum = max(abs(impact.shift_days) for impact in self.impacts)
        if self.max_shift_days != maximum:
            raise CalendarStressError("max shift days must equal the largest absolute shift.")

    def __len__(self) -> int:
        return len(self.impacts)

    def __iter__(self) -> Iterator[RebalanceDayImpact]:
        return iter(self.impacts)

    def __getitem__(self, index: int) -> RebalanceDayImpact:
        return self.impacts[index]

    def impact_for(self, anchor: date) -> RebalanceDayImpact | None:
        """Return the impact for one anchor (None when absent)."""
        for impact in self.impacts:
            if impact.anchor == anchor:
                return impact
        return None

    def readable(self) -> str:
        """Render the scenario as one line."""
        return (
            f"calendar stress {self.stress.version}: {len(self.impacts)} anchors, "
            f"{self.deferred_count} deferred ({self.stress_closed_count} by the "
            f"stress), total shift {self.total_shift_days}d, max {self.max_shift_days}d "
            f"calendar {self.calendar_version} fp {self.fingerprint}"
        )


@dataclass(frozen=True)
class CalendarStressReport:
    """The stressed rebalance outcomes across the pre-registered range (SP 3.54)."""

    scenarios: tuple[CalendarStressScenarioResult, ...]
    market: Market
    calendar_version: str
    dataset_fingerprint: str
    code_version: str
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.scenarios:
            raise CalendarStressError("a calendar stress report requires at least one scenario.")
        seen: set[str] = set()
        for scenario in self.scenarios:
            if scenario.market is not self.market:
                raise CalendarStressError("all scenarios must share the report's market.")
            if scenario.calendar_version != self.calendar_version:
                raise CalendarStressError("all scenarios must share the report's calendar version.")
            if scenario.dataset_fingerprint != self.dataset_fingerprint:
                raise CalendarStressError(
                    "all scenarios must share the report's dataset fingerprint."
                )
            if scenario.code_version != self.code_version:
                raise CalendarStressError("all scenarios must share the report's code version.")
            if scenario.stress.version in seen:
                raise CalendarStressError("scenario stress versions must be unique.")
            seen.add(scenario.stress.version)
        if not self.fingerprint:
            raise CalendarStressError("calendar stress report fingerprint must be non-empty.")

    def __len__(self) -> int:
        return len(self.scenarios)

    def __iter__(self) -> Iterator[CalendarStressScenarioResult]:
        return iter(self.scenarios)

    def __getitem__(self, index: int) -> CalendarStressScenarioResult:
        return self.scenarios[index]

    def scenario(self, version: str) -> CalendarStressScenarioResult | None:
        """Return the scenario for one stress version (None when absent)."""
        for scenario in self.scenarios:
            if scenario.stress.version == version:
                return scenario
        return None

    def readable(self) -> str:
        """Render the report as one line."""
        return (
            f"{len(self.scenarios)} calendar stress scenario(s) "
            f"calendar {self.calendar_version} fp {self.fingerprint}"
        )


def build_calendar_stress_config(
    *,
    version: str,
    source: str = "pre-registered",
    holidays: tuple[date, ...],
    deferral_rule: DeferralRule,
    calendar_version: str,
) -> CalendarStressConfig:
    """Assemble a versioned, fingerprint-stamped calendar stress config (SP 3.54)."""
    config = CalendarStressConfig(
        version=version,
        source=source,
        holidays=holidays,
        deferral_rule=deferral_rule,
        calendar_version=calendar_version,
        fingerprint="unfingerprinted",
    )
    return replace(config, fingerprint=calendar_stress_config_fingerprint(config))


def default_calendar_stresses() -> tuple[CalendarStressConfig, ...]:
    """Return the pre-registered calendar stress range.

    The closure scenario injects a single market closure, the long-holiday
    scenario a full trading week, and the backward variant re-derives the same
    closure with the backward legal deferral rule — all against the recorded
    authoritative calendar version ``cal-2026a`` (记录权威日历版本).
    """
    return (
        build_calendar_stress_config(
            version="calendar-stress-closure",
            holidays=(date(2026, 1, 2),),
            deferral_rule=DeferralRule.FORWARD,
            calendar_version="cal-2026a",
        ),
        build_calendar_stress_config(
            version="calendar-stress-long-holiday",
            holidays=(
                date(2026, 4, 6),
                date(2026, 4, 7),
                date(2026, 4, 8),
                date(2026, 4, 9),
                date(2026, 4, 10),
            ),
            deferral_rule=DeferralRule.FORWARD,
            calendar_version="cal-2026a",
        ),
        build_calendar_stress_config(
            version="calendar-stress-backward",
            holidays=(date(2026, 1, 2),),
            deferral_rule=DeferralRule.BACKWARD,
            calendar_version="cal-2026a",
        ),
    )


def quantify_calendar_stress(
    oos_run: RollingOosRun,
    *,
    stress_config: CalendarStressConfig,
    market: Market,
    anchors: Sequence[date],
    calendar_factory: Callable[[frozenset[date]], TradingCalendar],
) -> CalendarStressScenarioResult:
    """Quantify one stress level's impact on the OOS rebalance anchors.

    The stressed calendar is built from the injected holiday set and the legal
    deferral rule (SP 2.33); each anchor's scheduled rebalance day is derived
    and its shift quantified. An anchor closed only by the stress's added
    holidays is flagged ``stress_closed`` (休市/长假 impact).

    Args:
        oos_run: The SP 3.35 rolling OOS run (records the frozen context).
        stress_config: The pre-registered stressed calendar scenario.
        market: The market whose rebalance schedule is stressed.
        anchors: The intended OOS rebalance dates.
        calendar_factory: Builds a calendar from a holiday set (the stressed
            holidays are injected on top of the authoritative calendar).
    """
    if not anchors:
        raise CalendarStressError("at least one rebalance anchor is required.")
    baseline = calendar_factory(frozenset())
    stressed = calendar_factory(frozenset(stress_config.holidays))
    impacts: list[RebalanceDayImpact] = []
    for anchor in anchors:
        trading = stressed.is_trading_day(market, anchor)
        baseline_trading = baseline.is_trading_day(market, anchor)
        stress_closed = baseline_trading and not trading
        if trading:
            scheduled = anchor
        elif stress_config.deferral_rule is DeferralRule.FORWARD:
            scheduled = stressed.next_trading_day(market, anchor)
        else:
            scheduled = stressed.previous_trading_day(market, anchor)
        shift = (scheduled - anchor).days
        if shift == 0:
            reason = "rebalance on a trading day"
        elif shift > 0:
            reason = f"deferred forward {shift} day(s) to a trading day"
        else:
            reason = f"deferred backward {-shift} day(s) to a trading day"
        impacts.append(
            RebalanceDayImpact(
                anchor=anchor,
                trading_day=trading,
                stress_closed=stress_closed,
                scheduled=scheduled,
                shift_days=shift,
                reason=reason,
            )
        )
    scenario = CalendarStressScenarioResult(
        stress=stress_config,
        market=market,
        calendar_version=stress_config.calendar_version,
        dataset_fingerprint=oos_run.dataset_fingerprint,
        code_version=oos_run.code_version,
        impacts=tuple(impacts),
        deferred_count=sum(1 for impact in impacts if impact.shift_days != 0),
        stress_closed_count=sum(1 for impact in impacts if impact.stress_closed),
        total_shift_days=sum(abs(impact.shift_days) for impact in impacts),
        max_shift_days=max(abs(impact.shift_days) for impact in impacts),
        fingerprint="unfingerprinted",
    )
    return replace(scenario, fingerprint=calendar_stress_fingerprint(scenario))


def compute_calendar_stress_report(
    oos_run: RollingOosRun,
    *,
    market: Market,
    anchors: Sequence[date],
    calendar_factory: Callable[[frozenset[date]], TradingCalendar],
    stresses: tuple[CalendarStressConfig, ...] | None = None,
) -> CalendarStressReport:
    """Quantify the outcomes across the pre-registered stress range (SP 3.54)."""
    applied = default_calendar_stresses() if stresses is None else stresses
    if not applied:
        raise CalendarStressError("at least one calendar stress is required.")
    scenarios = tuple(
        quantify_calendar_stress(
            oos_run,
            stress_config=stress,
            market=market,
            anchors=anchors,
            calendar_factory=calendar_factory,
        )
        for stress in applied
    )
    report = CalendarStressReport(
        scenarios=scenarios,
        market=market,
        calendar_version=applied[0].calendar_version,
        dataset_fingerprint=oos_run.dataset_fingerprint,
        code_version=oos_run.code_version,
        fingerprint="unfingerprinted",
    )
    return replace(report, fingerprint=calendar_stress_report_fingerprint(report))


def _config_payload(config: CalendarStressConfig) -> dict[str, object]:
    """The stress config's JSON payload (its own fingerprint excluded)."""
    return {
        "version": config.version,
        "source": config.source,
        "holidays": [day.isoformat() for day in config.holidays],
        "deferral_rule": config.deferral_rule.value,
        "calendar_version": config.calendar_version,
    }


def calendar_stress_config_json(config: CalendarStressConfig) -> str:
    """Return a stable, key-sorted JSON serialization of a stress config."""
    return json.dumps(
        _config_payload(config), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def calendar_stress_config_fingerprint(config: CalendarStressConfig) -> str:
    """Return the stable SHA-256 fingerprint of a stress config (SP 3.54)."""
    return hashlib.sha256(calendar_stress_config_json(config).encode("utf-8")).hexdigest()


def calendar_stress_json(scenario: CalendarStressScenarioResult) -> str:
    """Return a stable, key-sorted JSON serialization of a scenario.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style).
    """
    payload: dict[str, object] = {
        "stress": _config_payload(scenario.stress),
        "market": scenario.market.value,
        "calendar_version": scenario.calendar_version,
        "dataset_fingerprint": scenario.dataset_fingerprint,
        "code_version": scenario.code_version,
        "deferred_count": scenario.deferred_count,
        "stress_closed_count": scenario.stress_closed_count,
        "total_shift_days": scenario.total_shift_days,
        "max_shift_days": scenario.max_shift_days,
        "impacts": [
            {
                "anchor": impact.anchor.isoformat(),
                "trading_day": impact.trading_day,
                "stress_closed": impact.stress_closed,
                "scheduled": impact.scheduled.isoformat(),
                "shift_days": impact.shift_days,
                "reason": impact.reason,
            }
            for impact in scenario.impacts
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def calendar_stress_fingerprint(scenario: CalendarStressScenarioResult) -> str:
    """Return the stable SHA-256 fingerprint of a scenario (SP 3.54)."""
    return hashlib.sha256(calendar_stress_json(scenario).encode("utf-8")).hexdigest()


def calendar_stress_report_json(report: CalendarStressReport) -> str:
    """Return a stable, key-sorted JSON serialization of a report."""
    payload: dict[str, object] = {
        "market": report.market.value,
        "calendar_version": report.calendar_version,
        "dataset_fingerprint": report.dataset_fingerprint,
        "code_version": report.code_version,
        "scenarios": [json.loads(calendar_stress_json(scenario)) for scenario in report.scenarios],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def calendar_stress_report_fingerprint(report: CalendarStressReport) -> str:
    """Return the stable SHA-256 fingerprint of a report (SP 3.54)."""
    return hashlib.sha256(calendar_stress_report_json(report).encode("utf-8")).hexdigest()


__all__: tuple[str, ...] = (
    "CalendarStressError",
    "CalendarStressConfig",
    "RebalanceDayImpact",
    "CalendarStressScenarioResult",
    "CalendarStressReport",
    "build_calendar_stress_config",
    "default_calendar_stresses",
    "quantify_calendar_stress",
    "compute_calendar_stress_report",
    "calendar_stress_config_json",
    "calendar_stress_config_fingerprint",
    "calendar_stress_json",
    "calendar_stress_fingerprint",
    "calendar_stress_report_json",
    "calendar_stress_report_fingerprint",
)
