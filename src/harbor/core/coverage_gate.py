"""Coverage thresholds and blocking rules (MVP 3 / SP 3.10).

Applies the configured coverage thresholds (SP 3.2) to a per-market coverage
score (SP 3.9) and decides, per item, whether a gap is a hard ``ERROR``
(blocks the validation run), a ``NOT_QUALIFIED`` (blocks the conclusion from
being ``QUALIFIED``) or a soft ``WARNING``. Missing FX, an unknown historical
stock pool and missing corporate-action terms are never silently passed: when
the corresponding requirement flag is enabled they disqualify the conclusion
(SP 3.10 acceptance).

Severity mapping: price/stock-pool percentage breaches are ``ERROR``;
fundamental percentage shortfalls, calendar and benchmark gaps are
``WARNING``; a required-but-missing FX / stock pool / corporate-action terms
is ``NOT_QUALIFIED`` (or ``WARNING`` when the requirement flag is off).

Core layer: depends only on the validation-config and coverage-scoring types,
never on storage, services or CLI code.
"""

from dataclasses import dataclass

from harbor.core.backtest_domain import Market
from harbor.core.coverage_scoring import CoverageScore, MarketCoverage
from harbor.core.validation_config import CoverageSeverity, CoverageThresholdConfig
from harbor.core.validation_domain import ManifestComponent

_SEVERITY_RANK: dict[CoverageSeverity, int] = {
    CoverageSeverity.ERROR: 3,
    CoverageSeverity.NOT_QUALIFIED: 2,
    CoverageSeverity.WARNING: 1,
}


@dataclass(frozen=True)
class CoverageThresholdResult:
    """The blocking decision for one coverage item (SP 3.10).

    ``severity`` is None when the item meets its configured requirement;
    otherwise it is the outcome of the gap (``ERROR`` / ``WARNING`` /
    ``NOT_QUALIFIED``) with a readable ``reason``.
    """

    market: Market
    item: ManifestComponent
    coverage_pct: float
    severity: CoverageSeverity | None = None
    reason: str = ""

    @property
    def passed(self) -> bool:
        """Whether this item meets its configured requirement."""
        return self.severity is None

    def readable(self) -> str:
        """Render the decision as one line."""
        if self.severity is None:
            return f"{self.market.value}/{self.item.value} coverage {self.coverage_pct:.1f}% passed"
        return (
            f"{self.market.value}/{self.item.value} coverage {self.coverage_pct:.1f}% "
            f"{self.severity.value}: {self.reason}"
        )


@dataclass(frozen=True)
class CoverageGateResult:
    """The aggregate gate outcome for one market (SP 3.10)."""

    market: Market
    results: tuple[CoverageThresholdResult, ...]

    def __post_init__(self) -> None:
        if not self.results:
            raise ValueError("Coverage gate requires at least one result.")

    @property
    def errors(self) -> tuple[CoverageThresholdResult, ...]:
        """Items that hard-block the validation run."""
        return tuple(r for r in self.results if r.severity is CoverageSeverity.ERROR)

    @property
    def warnings(self) -> tuple[CoverageThresholdResult, ...]:
        """Items that only warn."""
        return tuple(r for r in self.results if r.severity is CoverageSeverity.WARNING)

    @property
    def not_qualified_items(self) -> tuple[CoverageThresholdResult, ...]:
        """Items that disqualify the conclusion."""
        return tuple(r for r in self.results if r.severity is CoverageSeverity.NOT_QUALIFIED)

    @property
    def blocked(self) -> bool:
        """A hard error blocks the validation run from proceeding."""
        return bool(self.errors)

    @property
    def passes(self) -> bool:
        """No error and no NOT_QUALIFIED means the run may conclude."""
        return all(
            r.severity not in (CoverageSeverity.ERROR, CoverageSeverity.NOT_QUALIFIED)
            for r in self.results
        )

    def readable(self) -> str:
        """Render the gate outcome as one line."""
        return (
            f"{self.market.value} passes={self.passes} blocked={self.blocked} "
            f"errors {len(self.errors)} warnings {len(self.warnings)} "
            f"not_qualified {len(self.not_qualified_items)}"
        )


def evaluate_coverage(
    coverage: MarketCoverage,
    thresholds: CoverageThresholdConfig,
) -> CoverageGateResult:
    """Evaluate a market coverage score against the configured thresholds.

    Args:
        coverage: The per-market coverage score (SP 3.9).
        thresholds: The configured minimums and required-component flags
            (SP 3.2).

    Returns:
        The gate outcome with one decision per scored item.
    """
    results = tuple(_evaluate_item(score, thresholds) for score in coverage.scores)
    return CoverageGateResult(market=coverage.market, results=results)


def _evaluate_item(
    score: CoverageScore,
    thresholds: CoverageThresholdConfig,
) -> CoverageThresholdResult:
    candidates: list[tuple[int, CoverageSeverity, str]] = []

    def add(severity: CoverageSeverity, reason: str) -> None:
        candidates.append((_SEVERITY_RANK[severity], severity, reason))

    item = score.item
    pct = score.coverage_pct
    if item is ManifestComponent.PRICES:
        if pct < thresholds.min_price_coverage_pct:
            add(
                CoverageSeverity.ERROR,
                f"price coverage {pct:.1f}% below threshold "
                f"{thresholds.min_price_coverage_pct:.1f}%",
            )
    elif item is ManifestComponent.STOCK_POOL:
        if thresholds.historical_stock_pool_required and score.is_gap:
            add(CoverageSeverity.NOT_QUALIFIED, "historical stock pool is unknown or incomplete")
        if pct < thresholds.min_stock_pool_coverage_pct:
            add(
                CoverageSeverity.ERROR,
                f"stock pool coverage {pct:.1f}% below threshold "
                f"{thresholds.min_stock_pool_coverage_pct:.1f}%",
            )
    elif item is ManifestComponent.FUNDAMENTALS:
        if pct < thresholds.min_fundamental_coverage_pct:
            add(
                CoverageSeverity.WARNING,
                f"fundamental coverage {pct:.1f}% below threshold "
                f"{thresholds.min_fundamental_coverage_pct:.1f}%",
            )
    elif item is ManifestComponent.FX:
        if score.is_gap:
            if thresholds.fx_required:
                add(
                    CoverageSeverity.NOT_QUALIFIED,
                    "FX data is required but missing or incomplete",
                )
            else:
                add(CoverageSeverity.WARNING, "FX data is incomplete")
    elif item is ManifestComponent.CORPORATE_ACTIONS:
        if score.is_gap:
            if thresholds.action_terms_required:
                add(
                    CoverageSeverity.NOT_QUALIFIED,
                    "corporate-action terms are required but missing or incomplete",
                )
            else:
                add(CoverageSeverity.WARNING, "corporate-action terms are incomplete")
    elif item is ManifestComponent.CALENDAR:
        if score.is_gap:
            add(CoverageSeverity.WARNING, "trading calendar coverage is incomplete")
    elif item is ManifestComponent.BENCHMARK:
        if score.is_gap:
            add(CoverageSeverity.WARNING, "benchmark coverage is incomplete")

    if not candidates:
        return CoverageThresholdResult(
            market=score.market,
            item=item,
            coverage_pct=pct,
        )
    _, severity, reason = max(candidates, key=lambda entry: entry[0])
    return CoverageThresholdResult(
        market=score.market,
        item=item,
        coverage_pct=pct,
        severity=severity,
        reason=reason,
    )
