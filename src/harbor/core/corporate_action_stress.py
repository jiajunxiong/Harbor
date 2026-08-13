"""Corporate-action data stress (MVP 3 / SP 3.55).

Runs conservative scenarios for missing terms (条款缺失), delayed registration
(延迟登记), pending-review events (待复核事件) and price-adjustment deviations
(价格调整偏差); a key unknown makes the conclusion NOT_QUALIFIED (关键未知项使
结论不合格).

- :class:`CorporateActionStressConfig` is one pre-registered conservative
  scenario (versioned + fingerprinted): a scenario ``kind`` and the
  ``CoverageSeverity`` (SP 3.2) a triggering event gets — the three unknown
  kinds default to ``NOT_QUALIFIED`` (使结论不合格), the price-adjustment
  deviation defaults to ``WARNING``.
- :func:`quantify_corporate_action_stress` applies one scenario to the OOS
  corporate-action events and records every triggering event as a finding
  (with the event's identity, the relevant day and a human-readable reason).
  The scenario's conclusion is ``NOT_QUALIFIED`` when any key unknown is found;
  the report's conclusion is the strictest across the pre-registered range.

Pure core layer: depends only on the SP 3.35 run, the MVP 1 corporate-action
rules (SP 2.44 entitlement / MVP 1 review-queue semantics) and the SP 3.2
severity vocabulary, never on storage, services or CLI.
"""

import hashlib
import json
import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum

from harbor.core.action_mapping import allowed_action_types
from harbor.core.adjustments import ActionTerms
from harbor.core.backtest_domain import Market, to_market_target
from harbor.core.equity import EntitlementEvent, compute_entitlement
from harbor.core.market_registry import CorporateActionType
from harbor.core.rolling_oos import RollingOosRun
from harbor.core.validation_config import CoverageSeverity

_TOL = 1e-6
_SEVERITY_RANK = {
    CoverageSeverity.WARNING: 1,
    CoverageSeverity.NOT_QUALIFIED: 2,
    CoverageSeverity.ERROR: 3,
}


class CorporateActionStressError(ValueError):
    """Raised when corporate-action stress inputs are invalid (SP 3.55)."""


class CorporateActionStressKind(StrEnum):
    """The pre-registered conservative corporate-action scenarios (SP 3.55)."""

    MISSING_TERMS = "missing_terms"
    DELAYED_REGISTRATION = "delayed_registration"
    PENDING_REVIEW = "pending_review"
    PRICE_ADJUSTMENT_DEVIATION = "price_adjustment_deviation"


@dataclass(frozen=True)
class CorporateActionStressInput:
    """One OOS corporate-action event's stress context (SP 3.55).

    Each scenario reads the fields it needs: ``terms`` for missing terms (SP
    2.44 entitlement math), ``registered_at`` vs ``snapshot_date`` for delayed
    registration, ``pending_review`` for the review queue, and
    ``adjustment_factor`` vs ``expected_adjustment`` for the price-adjustment
    deviation.
    """

    symbol: str
    action_id: str
    action_type: CorporateActionType
    snapshot_date: date
    terms: ActionTerms = ActionTerms()
    record_date: date | None = None
    ex_date: date | None = None
    registered_at: date | None = None
    pending_review: bool = False
    adjustment_factor: float | None = None
    expected_adjustment: float | None = None

    def __post_init__(self) -> None:
        if not self.symbol:
            raise CorporateActionStressError("event symbol must be non-empty.")
        if not self.action_id:
            raise CorporateActionStressError("event action id must be non-empty.")


@dataclass(frozen=True)
class CorporateActionStressConfig:
    """One pre-registered conservative corporate-action scenario (SP 3.55).

    ``kind`` selects the scenario and ``severity`` the conclusion grade a
    triggering event produces; the key-unknown kinds default to
    ``NOT_QUALIFIED`` (关键未知项使结论不合格).
    """

    version: str
    source: str
    kind: CorporateActionStressKind
    severity: CoverageSeverity
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.version:
            raise CorporateActionStressError("corporate action stress version must be non-empty.")
        if not self.source:
            raise CorporateActionStressError("corporate action stress source must be non-empty.")
        if not self.fingerprint:
            raise CorporateActionStressError(
                "corporate action stress fingerprint must be non-empty."
            )

    def readable(self) -> str:
        """Render the stress as one line."""
        return (
            f"corporate action stress {self.version} ({self.source}): "
            f"{self.kind.value} -> {self.severity.value} fp {self.fingerprint}"
        )


@dataclass(frozen=True)
class CorporateActionFinding:
    """One corporate-action event that triggered the stress scenario (SP 3.55).

    The finding preserves the event's identity, the relevant ``day`` and a
    human-readable reason; its ``severity`` decides whether the conclusion is
    NOT_QUALIFIED.
    """

    kind: CorporateActionStressKind
    market: Market
    symbol: str
    action_id: str
    action_type: CorporateActionType
    day: date
    severity: CoverageSeverity
    message: str

    def __post_init__(self) -> None:
        if not self.symbol:
            raise CorporateActionStressError("finding symbol must be non-empty.")
        if not self.action_id:
            raise CorporateActionStressError("finding action id must be non-empty.")
        if not self.message:
            raise CorporateActionStressError("finding message must be non-empty.")

    def readable(self) -> str:
        """Render the finding as one line."""
        return (
            f"[{self.severity.value}] {self.market.value}/{self.symbol} "
            f"{self.action_id} {self.action_type.value} on "
            f"{self.day.isoformat()}: {self.message}"
        )


@dataclass(frozen=True)
class CorporateActionStressScenarioResult:
    """One scenario's findings across the OOS corporate actions (SP 3.55)."""

    stress: CorporateActionStressConfig
    market: Market
    dataset_fingerprint: str
    code_version: str
    findings: tuple[CorporateActionFinding, ...]
    conclusion_severity: CoverageSeverity | None
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.dataset_fingerprint:
            raise CorporateActionStressError("dataset fingerprint must be non-empty.")
        if not self.code_version:
            raise CorporateActionStressError("code version must be non-empty.")
        if not self.fingerprint:
            raise CorporateActionStressError(
                "corporate action stress scenario fingerprint must be non-empty."
            )
        expected: CoverageSeverity | None = self.stress.severity if self.findings else None
        if self.conclusion_severity is not expected:
            raise CorporateActionStressError(
                "conclusion severity must be the stress severity when findings "
                "exist and None otherwise."
            )
        for finding in self.findings:
            if finding.kind is not self.stress.kind:
                raise CorporateActionStressError(
                    "every finding must match the stress scenario kind."
                )
            if finding.severity is not self.stress.severity:
                raise CorporateActionStressError("every finding must carry the stress severity.")
            if finding.market is not self.market:
                raise CorporateActionStressError(
                    "every finding must belong to the stressed market."
                )

    def __len__(self) -> int:
        return len(self.findings)

    def __iter__(self) -> Iterator[CorporateActionFinding]:
        return iter(self.findings)

    def __getitem__(self, index: int) -> CorporateActionFinding:
        return self.findings[index]

    @property
    def finding_count(self) -> int:
        """Number of events that triggered the scenario."""
        return len(self.findings)

    @property
    def not_qualified(self) -> bool:
        """Whether the scenario conclusion is NOT_QUALIFIED."""
        return self.conclusion_severity is CoverageSeverity.NOT_QUALIFIED

    def readable(self) -> str:
        """Render the scenario as one line."""
        conclusion = "clean" if self.conclusion_severity is None else self.conclusion_severity.value
        return (
            f"corporate action stress {self.stress.version}: "
            f"{len(self.findings)} finding(s), conclusion {conclusion} "
            f"fp {self.fingerprint}"
        )


@dataclass(frozen=True)
class CorporateActionStressReport:
    """The stressed findings across the pre-registered range (SP 3.55)."""

    scenarios: tuple[CorporateActionStressScenarioResult, ...]
    market: Market
    dataset_fingerprint: str
    code_version: str
    conclusion_severity: CoverageSeverity | None
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.scenarios:
            raise CorporateActionStressError(
                "a corporate action stress report requires at least one scenario."
            )
        seen: set[str] = set()
        strictest: CoverageSeverity | None = None
        for scenario in self.scenarios:
            if scenario.market is not self.market:
                raise CorporateActionStressError("all scenarios must share the report's market.")
            if scenario.dataset_fingerprint != self.dataset_fingerprint:
                raise CorporateActionStressError(
                    "all scenarios must share the report's dataset fingerprint."
                )
            if scenario.code_version != self.code_version:
                raise CorporateActionStressError(
                    "all scenarios must share the report's code version."
                )
            if scenario.stress.version in seen:
                raise CorporateActionStressError("scenario stress versions must be unique.")
            seen.add(scenario.stress.version)
            if scenario.conclusion_severity is not None and (
                strictest is None
                or _SEVERITY_RANK[scenario.conclusion_severity] > _SEVERITY_RANK[strictest]
            ):
                strictest = scenario.conclusion_severity
        if self.conclusion_severity is not strictest:
            raise CorporateActionStressError(
                "report conclusion severity must be the strictest scenario severity."
            )
        if not self.fingerprint:
            raise CorporateActionStressError(
                "corporate action stress report fingerprint must be non-empty."
            )

    def __len__(self) -> int:
        return len(self.scenarios)

    def __iter__(self) -> Iterator[CorporateActionStressScenarioResult]:
        return iter(self.scenarios)

    def __getitem__(self, index: int) -> CorporateActionStressScenarioResult:
        return self.scenarios[index]

    def scenario(self, version: str) -> CorporateActionStressScenarioResult | None:
        """Return the scenario for one stress version (None when absent)."""
        for scenario in self.scenarios:
            if scenario.stress.version == version:
                return scenario
        return None

    @property
    def not_qualified(self) -> bool:
        """Whether any key unknown makes the overall conclusion NOT_QUALIFIED."""
        return self.conclusion_severity is CoverageSeverity.NOT_QUALIFIED

    def readable(self) -> str:
        """Render the report as one line."""
        conclusion = "clean" if self.conclusion_severity is None else self.conclusion_severity.value
        return (
            f"{len(self.scenarios)} corporate action stress scenario(s), "
            f"conclusion {conclusion} fp {self.fingerprint}"
        )


def build_corporate_action_stress_config(
    *,
    version: str,
    source: str = "pre-registered",
    kind: CorporateActionStressKind,
    severity: CoverageSeverity,
) -> CorporateActionStressConfig:
    """Assemble a versioned, fingerprint-stamped stress config (SP 3.55)."""
    config = CorporateActionStressConfig(
        version=version,
        source=source,
        kind=kind,
        severity=severity,
        fingerprint="unfingerprinted",
    )
    return replace(config, fingerprint=corporate_action_stress_config_fingerprint(config))


def default_corporate_action_stresses() -> tuple[CorporateActionStressConfig, ...]:
    """Return the pre-registered conservative corporate-action range.

    The three unknown scenarios — missing terms, delayed registration and
    pending review — default to ``NOT_QUALIFIED`` (关键未知项使结论不合格); the
    price-adjustment deviation is surfaced as a ``WARNING``.
    """
    return (
        build_corporate_action_stress_config(
            version="ca-missing-terms",
            kind=CorporateActionStressKind.MISSING_TERMS,
            severity=CoverageSeverity.NOT_QUALIFIED,
        ),
        build_corporate_action_stress_config(
            version="ca-delayed-registration",
            kind=CorporateActionStressKind.DELAYED_REGISTRATION,
            severity=CoverageSeverity.NOT_QUALIFIED,
        ),
        build_corporate_action_stress_config(
            version="ca-pending-review",
            kind=CorporateActionStressKind.PENDING_REVIEW,
            severity=CoverageSeverity.NOT_QUALIFIED,
        ),
        build_corporate_action_stress_config(
            version="ca-price-adjustment-deviation",
            kind=CorporateActionStressKind.PRICE_ADJUSTMENT_DEVIATION,
            severity=CoverageSeverity.WARNING,
        ),
    )


def _triggered_message(
    event: CorporateActionStressInput,
    kind: CorporateActionStressKind,
    market: Market,
) -> str | None:
    """Return the conservative finding message for one event, or ``None``.

    ``MISSING_TERMS`` reuses the SP 2.44 entitlement math: a supported event
    whose required terms are absent cannot be processed. ``DELAYED_REGISTRATION``
    flags terms registered after the snapshot date; ``PENDING_REVIEW`` flags
    review-queue events; ``PRICE_ADJUSTMENT_DEVIATION`` flags a factor that
    deviates from the expected one.
    """
    if kind is CorporateActionStressKind.MISSING_TERMS:
        if event.action_type not in allowed_action_types(to_market_target(market)):
            return None
        try:
            compute_entitlement(
                to_market_target(market),
                event.symbol,
                1.0,
                EntitlementEvent(
                    action_id=event.action_id,
                    action_type=event.action_type,
                    terms=event.terms,
                    record_date=event.record_date,
                    ex_date=event.ex_date,
                ),
            )
        except ValueError:
            return (
                f"missing required terms for {event.action_type.value}; "
                "the entitlement cannot be computed."
            )
        return None
    if kind is CorporateActionStressKind.DELAYED_REGISTRATION:
        if event.registered_at is not None and event.registered_at > event.snapshot_date:
            return (
                f"registered on {event.registered_at.isoformat()} after the "
                f"snapshot {event.snapshot_date.isoformat()}; terms were unknown "
                "at the decision date."
            )
        return None
    if kind is CorporateActionStressKind.PENDING_REVIEW:
        if event.pending_review:
            return "event is pending manual review; its terms are not confirmed."
        return None
    if (
        event.adjustment_factor is not None
        and event.expected_adjustment is not None
        and not math.isclose(event.adjustment_factor, event.expected_adjustment, abs_tol=_TOL)
    ):
        return (
            f"price adjustment factor {event.adjustment_factor} deviates from "
            f"the expected {event.expected_adjustment}."
        )
    return None


def quantify_corporate_action_stress(
    oos_run: RollingOosRun,
    *,
    stress_config: CorporateActionStressConfig,
    market: Market,
    events: Sequence[CorporateActionStressInput],
) -> CorporateActionStressScenarioResult:
    """Apply one conservative scenario to the OOS corporate actions (SP 3.55).

    Every triggering event is recorded as a finding (preserved, never dropped);
    the scenario conclusion is the stress's severity when any finding exists and
    ``None`` (clean) otherwise — a key unknown therefore makes the conclusion
    NOT_QUALIFIED (关键未知项使结论不合格).

    Args:
        oos_run: The SP 3.35 rolling OOS run (records the frozen context).
        stress_config: The pre-registered conservative scenario.
        market: The market whose corporate actions are stressed.
        events: The OOS corporate-action events to check.
    """
    findings: list[CorporateActionFinding] = []
    for event in events:
        message = _triggered_message(event, stress_config.kind, market)
        if message is None:
            continue
        day = event.ex_date or event.record_date or event.snapshot_date
        findings.append(
            CorporateActionFinding(
                kind=stress_config.kind,
                market=market,
                symbol=event.symbol,
                action_id=event.action_id,
                action_type=event.action_type,
                day=day,
                severity=stress_config.severity,
                message=message,
            )
        )
    scenario = CorporateActionStressScenarioResult(
        stress=stress_config,
        market=market,
        dataset_fingerprint=oos_run.dataset_fingerprint,
        code_version=oos_run.code_version,
        findings=tuple(findings),
        conclusion_severity=stress_config.severity if findings else None,
        fingerprint="unfingerprinted",
    )
    return replace(scenario, fingerprint=corporate_action_stress_fingerprint(scenario))


def compute_corporate_action_stress_report(
    oos_run: RollingOosRun,
    *,
    market: Market,
    events: Sequence[CorporateActionStressInput],
    stresses: tuple[CorporateActionStressConfig, ...] | None = None,
) -> CorporateActionStressReport:
    """Quantify the findings across the pre-registered range (SP 3.55)."""
    applied = default_corporate_action_stresses() if stresses is None else stresses
    if not applied:
        raise CorporateActionStressError("at least one corporate action stress is required.")
    scenarios = tuple(
        quantify_corporate_action_stress(
            oos_run,
            stress_config=stress,
            market=market,
            events=events,
        )
        for stress in applied
    )
    strictest: CoverageSeverity | None = None
    for scenario in scenarios:
        if scenario.conclusion_severity is not None and (
            strictest is None
            or _SEVERITY_RANK[scenario.conclusion_severity] > _SEVERITY_RANK[strictest]
        ):
            strictest = scenario.conclusion_severity
    report = CorporateActionStressReport(
        scenarios=scenarios,
        market=market,
        dataset_fingerprint=oos_run.dataset_fingerprint,
        code_version=oos_run.code_version,
        conclusion_severity=strictest,
        fingerprint="unfingerprinted",
    )
    return replace(report, fingerprint=corporate_action_stress_report_fingerprint(report))


def _config_payload(config: CorporateActionStressConfig) -> dict[str, object]:
    """The stress config's JSON payload (its own fingerprint excluded)."""
    return {
        "version": config.version,
        "source": config.source,
        "kind": config.kind.value,
        "severity": config.severity.value,
    }


def _finding_payload(finding: CorporateActionFinding) -> dict[str, object]:
    """Serialize one preserved finding."""
    return {
        "kind": finding.kind.value,
        "market": finding.market.value,
        "symbol": finding.symbol,
        "action_id": finding.action_id,
        "action_type": finding.action_type.value,
        "day": finding.day.isoformat(),
        "severity": finding.severity.value,
        "message": finding.message,
    }


def corporate_action_stress_config_json(config: CorporateActionStressConfig) -> str:
    """Return a stable, key-sorted JSON serialization of a stress config."""
    return json.dumps(
        _config_payload(config), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def corporate_action_stress_config_fingerprint(config: CorporateActionStressConfig) -> str:
    """Return the stable SHA-256 fingerprint of a stress config (SP 3.55)."""
    return hashlib.sha256(corporate_action_stress_config_json(config).encode("utf-8")).hexdigest()


def corporate_action_stress_json(
    scenario: CorporateActionStressScenarioResult,
) -> str:
    """Return a stable, key-sorted JSON serialization of a scenario.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style).
    """
    payload: dict[str, object] = {
        "stress": _config_payload(scenario.stress),
        "market": scenario.market.value,
        "dataset_fingerprint": scenario.dataset_fingerprint,
        "code_version": scenario.code_version,
        "finding_count": scenario.finding_count,
        "not_qualified": scenario.not_qualified,
        "conclusion_severity": (
            None if scenario.conclusion_severity is None else scenario.conclusion_severity.value
        ),
        "findings": [_finding_payload(finding) for finding in scenario.findings],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def corporate_action_stress_fingerprint(
    scenario: CorporateActionStressScenarioResult,
) -> str:
    """Return the stable SHA-256 fingerprint of a scenario (SP 3.55)."""
    return hashlib.sha256(corporate_action_stress_json(scenario).encode("utf-8")).hexdigest()


def corporate_action_stress_report_json(
    report: CorporateActionStressReport,
) -> str:
    """Return a stable, key-sorted JSON serialization of a report."""
    payload: dict[str, object] = {
        "market": report.market.value,
        "dataset_fingerprint": report.dataset_fingerprint,
        "code_version": report.code_version,
        "not_qualified": report.not_qualified,
        "conclusion_severity": (
            None if report.conclusion_severity is None else report.conclusion_severity.value
        ),
        "scenarios": [
            json.loads(corporate_action_stress_json(scenario)) for scenario in report.scenarios
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def corporate_action_stress_report_fingerprint(
    report: CorporateActionStressReport,
) -> str:
    """Return the stable SHA-256 fingerprint of a report (SP 3.55)."""
    return hashlib.sha256(corporate_action_stress_report_json(report).encode("utf-8")).hexdigest()


__all__: tuple[str, ...] = (
    "CorporateActionStressError",
    "CorporateActionStressKind",
    "CorporateActionStressInput",
    "CorporateActionStressConfig",
    "CorporateActionFinding",
    "CorporateActionStressScenarioResult",
    "CorporateActionStressReport",
    "build_corporate_action_stress_config",
    "default_corporate_action_stresses",
    "quantify_corporate_action_stress",
    "compute_corporate_action_stress_report",
    "corporate_action_stress_config_json",
    "corporate_action_stress_config_fingerprint",
    "corporate_action_stress_json",
    "corporate_action_stress_fingerprint",
    "corporate_action_stress_report_json",
    "corporate_action_stress_report_fingerprint",
)
