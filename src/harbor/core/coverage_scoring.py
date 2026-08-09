"""Data-quality coverage scoring (MVP 3 / SP 3.9).

Quantifies, per market, the coverage and gaps of the data a frozen dataset
relies on: prices, the historical stock pool, fundamentals availability,
corporate-action terms, the trading calendar, FX and the benchmark. A
measurement records how many expected units are actually covered; the scorer
turns measurements into per-item coverage percentages, an overall market
coverage and an explicit list of gaps. Manifest-derived window coverage
(SP 3.6) is provided so a component missing from the frozen manifest surfaces
as a full gap instead of being silently assumed complete.

Core layer: depends only on the validation-domain/assembler types and the
backtest domain, never on storage, services or CLI code.
"""

from collections.abc import Mapping
from dataclasses import dataclass

from harbor.core.backtest_domain import Market
from harbor.core.dataset_manifest import find_component
from harbor.core.validation_domain import DatasetManifest, ManifestComponent

SCORED_ITEMS: tuple[ManifestComponent, ...] = (
    ManifestComponent.PRICES,
    ManifestComponent.STOCK_POOL,
    ManifestComponent.FUNDAMENTALS,
    ManifestComponent.CORPORATE_ACTIONS,
    ManifestComponent.CALENDAR,
    ManifestComponent.FX,
    ManifestComponent.BENCHMARK,
)


@dataclass(frozen=True)
class CoverageMeasurement:
    """How many of the expected units of one coverage item are covered.

    ``gap`` is an optional explicit reason; a coverage below 100% without an
    explicit reason still surfaces as a gap because some expected units are
    missing.
    """

    covered: int
    denominator: int
    gap: str = ""

    def __post_init__(self) -> None:
        if self.denominator < 1:
            raise ValueError("Coverage denominator must be at least 1.")
        if self.covered < 0:
            raise ValueError("Coverage covered must be non-negative.")
        if self.covered > self.denominator:
            raise ValueError(
                f"Coverage covered {self.covered} exceeds denominator {self.denominator}."
            )

    @property
    def coverage_pct(self) -> float:
        """Coverage as a percentage of the expected units."""
        return self.covered / self.denominator * 100.0


@dataclass(frozen=True)
class CoverageScore:
    """The quantified coverage of one item for one market (SP 3.9)."""

    market: Market
    item: ManifestComponent
    measurement: CoverageMeasurement

    @property
    def coverage_pct(self) -> float:
        """Coverage percentage of this item."""
        return self.measurement.coverage_pct

    @property
    def is_gap(self) -> bool:
        """Whether this item is a coverage gap (<100% or an explicit reason)."""
        return self.coverage_pct < 100.0 or bool(self.measurement.gap)

    def readable(self) -> str:
        """Render the score as one line."""
        line = (
            f"{self.market.value}/{self.item.value} coverage "
            f"{self.coverage_pct:.1f}% "
            f"({self.measurement.covered}/{self.measurement.denominator})"
        )
        if self.measurement.gap:
            return line + f" gap: {self.measurement.gap}"
        return line


@dataclass(frozen=True)
class MarketCoverage:
    """Per-market coverage scores with an overall percentage (SP 3.9)."""

    market: Market
    scores: tuple[CoverageScore, ...]

    def __post_init__(self) -> None:
        if not self.scores:
            raise ValueError("Market coverage requires at least one score.")

    @property
    def overall_pct(self) -> float:
        """Simple mean of the scored items' coverage percentages."""
        return sum(score.coverage_pct for score in self.scores) / len(self.scores)

    def score(self, item: ManifestComponent) -> CoverageScore | None:
        """Return the score for ``item``, or None when it was not scored."""
        for score in self.scores:
            if score.item is item:
                return score
        return None

    def gaps(self) -> tuple[CoverageScore, ...]:
        """Return the scores that represent a coverage gap (SP 3.9)."""
        return tuple(score for score in self.scores if score.is_gap)

    def readable(self) -> str:
        """Render the market coverage as a single line."""
        return f"{self.market.value} overall {self.overall_pct:.1f}% gaps {len(self.gaps())}"


def score_market_coverage(
    market: Market,
    measurements: Mapping[ManifestComponent, CoverageMeasurement],
    scored: tuple[ManifestComponent, ...] = SCORED_ITEMS,
) -> MarketCoverage:
    """Score one market's coverage from measurements (SP 3.9).

    Items without a measurement are scored as 0% with an explicit
    "no coverage measurement" gap rather than silently assumed complete.
    """
    scores: list[CoverageScore] = []
    for item in scored:
        measurement = measurements.get(item)
        if measurement is None:
            measurement = CoverageMeasurement(0, 1, gap=f"no coverage measurement for {item.value}")
        scores.append(CoverageScore(market=market, item=item, measurement=measurement))
    return MarketCoverage(market=market, scores=tuple(scores))


def coverage_from_manifest(manifest: DatasetManifest, market: Market) -> MarketCoverage:
    """Score the frozen-window coverage implied by the manifest (SP 3.6/3.9).

    A component recorded with a bounded query range covers the fraction of
    the manifest window that range spans; a component absent from the frozen
    manifest is a full gap and an unbounded component is reported as a gap.
    """
    total = (manifest.end_date - manifest.start_date).days + 1
    measurements: dict[ManifestComponent, CoverageMeasurement] = {}
    for item in SCORED_ITEMS:
        entry = find_component(manifest, item)
        if entry is None:
            measurements[item] = CoverageMeasurement(
                0, 1, gap=f"{item.value} is not frozen in the manifest"
            )
        elif entry.start is None or entry.end is None:
            measurements[item] = CoverageMeasurement(
                0, 1, gap=f"{item.value} query range is unbounded"
            )
        else:
            covered = (entry.end - entry.start).days + 1
            measurements[item] = CoverageMeasurement(covered, total)
    return score_market_coverage(market, measurements)
