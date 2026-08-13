"""Stock-pool integrity stress (MVP 3 / SP 3.56).

Quantifies the impact of scenarios where the historical constituents are
unknown (历史成分未知), delisting coverage is insufficient (退市覆盖不足) and
the tradeable universe shrinks (可交易标的下降); when the impact cannot be
quantified the conclusion is explicitly blocked (不可量化时明确阻断结论).

- :class:`StockPoolStressConfig` is one pre-registered conservative scenario
  (versioned + fingerprinted) of kind UNKNOWN_HISTORY,
  INSUFFICIENT_DELISTING_COVERAGE or SHRINKING_UNIVERSE.
- :func:`quantify_stock_pool_stress` measures the pool's coverage against the
  expected universe (SP 3.9 style) under the scenario: the delisting scenario
  counts an expected symbol as covered when it has ANY membership (so a
  delisted name missing from the pool is a gap), the shrinking and unknown
  scenarios count a symbol as covered only when its membership is active on the
  as-of date. UNKNOWN_HISTORY with an unknown historical source is NOT
  quantifiable — the conclusion is explicitly blocked (NOT_QUALIFIED) rather
  than fabricated.

Pure core layer: depends only on the SP 3.35 run, the SP 2.10 stock-pool
domain and the SP 3.9 coverage vocabulary, never on storage, services or CLI.
"""

import hashlib
import json
import math
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum

from harbor.core.backtest_domain import Market
from harbor.core.rolling_oos import RollingOosRun
from harbor.core.stock_pool import StockPoolMembership, is_active_on
from harbor.core.validation_config import CoverageSeverity

_TOL = 1e-6
_SEVERITY_RANK = {
    CoverageSeverity.WARNING: 1,
    CoverageSeverity.NOT_QUALIFIED: 2,
}


class StockPoolStressError(ValueError):
    """Raised when stock-pool stress inputs are invalid (SP 3.56)."""


class StockPoolStressKind(StrEnum):
    """The pre-registered conservative stock-pool scenarios (SP 3.56)."""

    UNKNOWN_HISTORY = "unknown_history"
    INSUFFICIENT_DELISTING_COVERAGE = "insufficient_delisting_coverage"
    SHRINKING_UNIVERSE = "shrinking_universe"


@dataclass(frozen=True)
class StockPoolStressInput:
    """The stressed stock-pool context (SP 3.56).

    ``memberships`` are the pool's known membership windows (SP 2.10),
    ``expected_universe`` the true expected historical universe (the coverage
    denominator) and ``historical_known`` whether the source guarantees the
    historical constituents including delisted names.
    """

    market: Market
    memberships: tuple[StockPoolMembership, ...]
    expected_universe: tuple[str, ...]
    as_of: date
    historical_known: bool

    def __post_init__(self) -> None:
        if not self.expected_universe:
            raise StockPoolStressError(
                "an expected universe is required to quantify stock-pool coverage."
            )
        if len(set(self.expected_universe)) != len(self.expected_universe):
            raise StockPoolStressError("the expected universe must not contain duplicate symbols.")


@dataclass(frozen=True)
class StockPoolStressConfig:
    """One pre-registered conservative stock-pool scenario (SP 3.56)."""

    version: str
    source: str
    kind: StockPoolStressKind
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.version:
            raise StockPoolStressError("stock pool stress version must be non-empty.")
        if not self.source:
            raise StockPoolStressError("stock pool stress source must be non-empty.")
        if not self.fingerprint:
            raise StockPoolStressError("stock pool stress fingerprint must be non-empty.")

    def readable(self) -> str:
        """Render the stress as one line."""
        return (
            f"stock pool stress {self.version} ({self.source}): "
            f"{self.kind.value} fp {self.fingerprint}"
        )


@dataclass(frozen=True)
class StockPoolStressScenarioResult:
    """One scenario's quantified impact on the pool coverage (SP 3.56).

    ``coverage_pct`` is the covered share of the expected universe (SP 3.9
    style) and ``impact_pct`` the shortfall. When the impact cannot be
    quantified (UNKNOWN_HISTORY with an unknown source) the scenario is
    ``blocked`` with a recorded reason and a NOT_QUALIFIED conclusion — the
    conclusion is explicitly blocked rather than fabricated (不可量化时明确阻
    断结论).
    """

    stress: StockPoolStressConfig
    market: Market
    dataset_fingerprint: str
    code_version: str
    expected_count: int
    covered_count: int
    active_count: int
    coverage_pct: float | None
    impact_pct: float | None
    missing_symbols: tuple[str, ...]
    quantifiable: bool
    blocked: bool
    blocked_reason: str | None
    conclusion_severity: CoverageSeverity | None
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.dataset_fingerprint:
            raise StockPoolStressError("dataset fingerprint must be non-empty.")
        if not self.code_version:
            raise StockPoolStressError("code version must be non-empty.")
        if not self.fingerprint:
            raise StockPoolStressError("stock pool stress scenario fingerprint must be non-empty.")
        if self.expected_count <= 0:
            raise StockPoolStressError("expected universe count must be positive.")
        if not 0 <= self.covered_count <= self.expected_count:
            raise StockPoolStressError("covered count must be within the expected universe.")
        if self.active_count < 0:
            raise StockPoolStressError("active count must be non-negative.")
        if self.blocked != (not self.quantifiable):
            raise StockPoolStressError("a scenario is blocked exactly when it is not quantifiable.")
        if self.blocked:
            if not self.blocked_reason:
                raise StockPoolStressError("a blocked scenario must carry a blocked reason.")
            if self.conclusion_severity is not CoverageSeverity.NOT_QUALIFIED:
                raise StockPoolStressError("a blocked scenario must conclude NOT_QUALIFIED.")
            if self.coverage_pct is not None or self.impact_pct is not None:
                raise StockPoolStressError("a blocked scenario must not carry a quantified impact.")
        else:
            if self.blocked_reason is not None:
                raise StockPoolStressError(
                    "a quantifiable scenario must not carry a blocked reason."
                )
            assert self.coverage_pct is not None
            if not math.isclose(
                self.coverage_pct,
                self.covered_count / self.expected_count * 100.0,
                abs_tol=_TOL,
            ):
                raise StockPoolStressError("coverage percentage is inconsistent.")
            assert self.impact_pct is not None
            if not math.isclose(self.impact_pct, 100.0 - self.coverage_pct, abs_tol=_TOL):
                raise StockPoolStressError("impact percentage must be 100 minus coverage.")
            if self.coverage_pct >= 100.0:
                if self.conclusion_severity is not None:
                    raise StockPoolStressError("full coverage must conclude clean (no severity).")
            elif self.conclusion_severity is not CoverageSeverity.WARNING:
                raise StockPoolStressError("a partial coverage gap must conclude WARNING.")

    @property
    def missing_count(self) -> int:
        """Number of expected symbols the pool does not cover."""
        return len(self.missing_symbols)

    def readable(self) -> str:
        """Render the scenario as one line."""
        if self.blocked:
            conclusion = f"BLOCKED: {self.blocked_reason}"
        else:
            conclusion = f"coverage {self.coverage_pct:.1f}% impact {self.impact_pct:.1f}%"
        return (
            f"stock pool stress {self.stress.version}: "
            f"{self.covered_count}/{self.expected_count} covered "
            f"({self.active_count} active), {self.missing_count} missing; "
            f"{conclusion} fp {self.fingerprint}"
        )


@dataclass(frozen=True)
class StockPoolStressReport:
    """The stressed pool outcomes across the pre-registered range (SP 3.56)."""

    scenarios: tuple[StockPoolStressScenarioResult, ...]
    market: Market
    dataset_fingerprint: str
    code_version: str
    conclusion_severity: CoverageSeverity | None
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.scenarios:
            raise StockPoolStressError("a stock pool stress report requires at least one scenario.")
        seen: set[str] = set()
        strictest: CoverageSeverity | None = None
        for scenario in self.scenarios:
            if scenario.market is not self.market:
                raise StockPoolStressError("all scenarios must share the report's market.")
            if scenario.dataset_fingerprint != self.dataset_fingerprint:
                raise StockPoolStressError(
                    "all scenarios must share the report's dataset fingerprint."
                )
            if scenario.code_version != self.code_version:
                raise StockPoolStressError("all scenarios must share the report's code version.")
            if scenario.stress.version in seen:
                raise StockPoolStressError("scenario stress versions must be unique.")
            seen.add(scenario.stress.version)
            if scenario.conclusion_severity is not None and (
                strictest is None
                or _SEVERITY_RANK[scenario.conclusion_severity] > _SEVERITY_RANK[strictest]
            ):
                strictest = scenario.conclusion_severity
        if self.conclusion_severity is not strictest:
            raise StockPoolStressError(
                "report conclusion severity must be the strictest scenario severity."
            )
        if not self.fingerprint:
            raise StockPoolStressError("stock pool stress report fingerprint must be non-empty.")

    def __len__(self) -> int:
        return len(self.scenarios)

    def __iter__(self) -> Iterator[StockPoolStressScenarioResult]:
        return iter(self.scenarios)

    def __getitem__(self, index: int) -> StockPoolStressScenarioResult:
        return self.scenarios[index]

    def scenario(self, version: str) -> StockPoolStressScenarioResult | None:
        """Return the scenario for one stress version (None when absent)."""
        for scenario in self.scenarios:
            if scenario.stress.version == version:
                return scenario
        return None

    @property
    def blocked(self) -> bool:
        """Whether any scenario is blocked (not quantifiable)."""
        return any(scenario.blocked for scenario in self.scenarios)

    def readable(self) -> str:
        """Render the report as one line."""
        conclusion = "clean" if self.conclusion_severity is None else self.conclusion_severity.value
        return (
            f"{len(self.scenarios)} stock pool stress scenario(s), conclusion "
            f"{conclusion} fp {self.fingerprint}"
        )


def build_stock_pool_stress_config(
    *,
    version: str,
    source: str = "pre-registered",
    kind: StockPoolStressKind,
) -> StockPoolStressConfig:
    """Assemble a versioned, fingerprint-stamped stock-pool stress config (SP 3.56)."""
    config = StockPoolStressConfig(
        version=version,
        source=source,
        kind=kind,
        fingerprint="unfingerprinted",
    )
    return replace(config, fingerprint=stock_pool_stress_config_fingerprint(config))


def default_stock_pool_stresses() -> tuple[StockPoolStressConfig, ...]:
    """Return the pre-registered conservative stock-pool range (SP 3.56)."""
    return (
        build_stock_pool_stress_config(
            version="pool-unknown-history",
            kind=StockPoolStressKind.UNKNOWN_HISTORY,
        ),
        build_stock_pool_stress_config(
            version="pool-insufficient-delisting",
            kind=StockPoolStressKind.INSUFFICIENT_DELISTING_COVERAGE,
        ),
        build_stock_pool_stress_config(
            version="pool-shrinking-universe",
            kind=StockPoolStressKind.SHRINKING_UNIVERSE,
        ),
    )


def _covered_symbols(
    pool: StockPoolStressInput,
    kind: StockPoolStressKind,
) -> set[str]:
    """Return the expected symbols the pool covers under the scenario.

    The delisting scenario counts a symbol as covered when it has ANY
    membership (so a delisted name absent from the pool is a gap); the
    shrinking and unknown scenarios count a symbol only when its membership is
    active on the as-of date (the tradeable universe).
    """
    if kind is StockPoolStressKind.INSUFFICIENT_DELISTING_COVERAGE:
        pool_symbols = {membership.symbol for membership in pool.memberships}
        return {symbol for symbol in pool.expected_universe if symbol in pool_symbols}
    active = {
        membership.symbol for membership in pool.memberships if is_active_on(membership, pool.as_of)
    }
    return {symbol for symbol in pool.expected_universe if symbol in active}


def quantify_stock_pool_stress(
    oos_run: RollingOosRun,
    *,
    stress_config: StockPoolStressConfig,
    pool: StockPoolStressInput,
) -> StockPoolStressScenarioResult:
    """Apply one conservative scenario to the OOS stock pool (SP 3.56).

    Measures the pool's coverage of the expected universe under the scenario
    (SP 3.9 style). UNKNOWN_HISTORY with an unknown historical source cannot be
    quantified and the scenario is explicitly blocked (NOT_QUALIFIED) rather
    than fabricating a coverage (不可量化时明确阻断结论).

    Args:
        oos_run: The SP 3.35 rolling OOS run (records the frozen context).
        stress_config: The pre-registered conservative scenario.
        pool: The stressed stock-pool context.
    """
    covered_symbols = _covered_symbols(pool, stress_config.kind)
    covered = len(covered_symbols)
    expected = len(pool.expected_universe)
    missing = tuple(
        sorted(symbol for symbol in pool.expected_universe if symbol not in covered_symbols)
    )
    active = sum(1 for membership in pool.memberships if is_active_on(membership, pool.as_of))
    quantifiable = True
    blocked_reason: str | None = None
    if stress_config.kind is StockPoolStressKind.UNKNOWN_HISTORY:
        quantifiable = pool.historical_known
        if not quantifiable:
            blocked_reason = (
                "historical constituents are unknown; stock-pool coverage "
                "cannot be quantified (SP 3.56)"
            )
    if quantifiable:
        coverage_pct = covered / expected * 100.0
        impact_pct = 100.0 - coverage_pct
        severity = CoverageSeverity.WARNING if coverage_pct < 100.0 else None
    else:
        coverage_pct = None
        impact_pct = None
        severity = CoverageSeverity.NOT_QUALIFIED
    scenario = StockPoolStressScenarioResult(
        stress=stress_config,
        market=pool.market,
        dataset_fingerprint=oos_run.dataset_fingerprint,
        code_version=oos_run.code_version,
        expected_count=expected,
        covered_count=covered,
        active_count=active,
        coverage_pct=coverage_pct,
        impact_pct=impact_pct,
        missing_symbols=missing,
        quantifiable=quantifiable,
        blocked=not quantifiable,
        blocked_reason=blocked_reason,
        conclusion_severity=severity,
        fingerprint="unfingerprinted",
    )
    return replace(scenario, fingerprint=stock_pool_stress_fingerprint(scenario))


def compute_stock_pool_stress_report(
    oos_run: RollingOosRun,
    *,
    pool: StockPoolStressInput,
    stresses: tuple[StockPoolStressConfig, ...] | None = None,
) -> StockPoolStressReport:
    """Quantify the outcomes across the pre-registered range (SP 3.56)."""
    applied = default_stock_pool_stresses() if stresses is None else stresses
    if not applied:
        raise StockPoolStressError("at least one stock pool stress is required.")
    scenarios = tuple(
        quantify_stock_pool_stress(
            oos_run,
            stress_config=stress,
            pool=pool,
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
    report = StockPoolStressReport(
        scenarios=scenarios,
        market=pool.market,
        dataset_fingerprint=oos_run.dataset_fingerprint,
        code_version=oos_run.code_version,
        conclusion_severity=strictest,
        fingerprint="unfingerprinted",
    )
    return replace(report, fingerprint=stock_pool_stress_report_fingerprint(report))


def _config_payload(config: StockPoolStressConfig) -> dict[str, object]:
    """The stress config's JSON payload (its own fingerprint excluded)."""
    return {
        "version": config.version,
        "source": config.source,
        "kind": config.kind.value,
    }


def stock_pool_stress_config_json(config: StockPoolStressConfig) -> str:
    """Return a stable, key-sorted JSON serialization of a stress config."""
    return json.dumps(
        _config_payload(config), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def stock_pool_stress_config_fingerprint(config: StockPoolStressConfig) -> str:
    """Return the stable SHA-256 fingerprint of a stress config (SP 3.56)."""
    return hashlib.sha256(stock_pool_stress_config_json(config).encode("utf-8")).hexdigest()


def stock_pool_stress_json(scenario: StockPoolStressScenarioResult) -> str:
    """Return a stable, key-sorted JSON serialization of a scenario.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style).
    """
    payload: dict[str, object] = {
        "stress": _config_payload(scenario.stress),
        "market": scenario.market.value,
        "dataset_fingerprint": scenario.dataset_fingerprint,
        "code_version": scenario.code_version,
        "expected_count": scenario.expected_count,
        "covered_count": scenario.covered_count,
        "active_count": scenario.active_count,
        "coverage_pct": scenario.coverage_pct,
        "impact_pct": scenario.impact_pct,
        "missing_symbols": list(scenario.missing_symbols),
        "quantifiable": scenario.quantifiable,
        "blocked": scenario.blocked,
        "blocked_reason": scenario.blocked_reason,
        "conclusion_severity": (
            None if scenario.conclusion_severity is None else scenario.conclusion_severity.value
        ),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stock_pool_stress_fingerprint(scenario: StockPoolStressScenarioResult) -> str:
    """Return the stable SHA-256 fingerprint of a scenario (SP 3.56)."""
    return hashlib.sha256(stock_pool_stress_json(scenario).encode("utf-8")).hexdigest()


def stock_pool_stress_report_json(report: StockPoolStressReport) -> str:
    """Return a stable, key-sorted JSON serialization of a report."""
    payload: dict[str, object] = {
        "market": report.market.value,
        "dataset_fingerprint": report.dataset_fingerprint,
        "code_version": report.code_version,
        "blocked": report.blocked,
        "conclusion_severity": (
            None if report.conclusion_severity is None else report.conclusion_severity.value
        ),
        "scenarios": [
            json.loads(stock_pool_stress_json(scenario)) for scenario in report.scenarios
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stock_pool_stress_report_fingerprint(report: StockPoolStressReport) -> str:
    """Return the stable SHA-256 fingerprint of a report (SP 3.56)."""
    return hashlib.sha256(stock_pool_stress_report_json(report).encode("utf-8")).hexdigest()


__all__: tuple[str, ...] = (
    "StockPoolStressError",
    "StockPoolStressKind",
    "StockPoolStressInput",
    "StockPoolStressConfig",
    "StockPoolStressScenarioResult",
    "StockPoolStressReport",
    "build_stock_pool_stress_config",
    "default_stock_pool_stresses",
    "quantify_stock_pool_stress",
    "compute_stock_pool_stress_report",
    "stock_pool_stress_config_json",
    "stock_pool_stress_config_fingerprint",
    "stock_pool_stress_json",
    "stock_pool_stress_fingerprint",
    "stock_pool_stress_report_json",
    "stock_pool_stress_report_fingerprint",
)
