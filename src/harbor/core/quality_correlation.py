"""Data quality correlation (MVP 2 / SP 2.65).

Associates the MVP 1 data-quality findings (SP 1.28 ``quality_issues``) and
the SP 2.13 readiness precheck findings with the data actually used by a
backtest run, so a research report can state which known data problems affect
the markets and symbols the run traded.

Pure core logic: only stdlib and the SP 2.13 domain types; never touches
storage or CLI code.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from harbor.core.backtest_domain import Market
from harbor.core.data_readiness import PrecheckReport, PrecheckSeverity


class CorrelationError(ValueError):
    """Raised when quality correlation cannot be computed (SP 2.65)."""


class QualitySource(StrEnum):
    """The origin of a correlated finding."""

    QUALITY_ISSUE = "quality_issue"
    PRECHECK = "precheck"


@dataclass(frozen=True)
class QualityIssue:
    """An MVP 1 data-quality finding (SP 1.28) about one market or symbol.

    Mirrors a ``quality_issues`` row: a market-scoped finding (``symbol`` is
    ``None``) or a symbol-scoped finding within a market.
    """

    market: Market
    check_name: str
    severity: PrecheckSeverity
    details: str
    symbol: str | None = None
    resolved: bool = False

    def __post_init__(self) -> None:
        if not self.check_name:
            raise ValueError("Quality issue check_name must be non-empty.")
        if self.symbol == "":
            raise ValueError("Quality issue symbol must be non-empty.")


@dataclass(frozen=True)
class CorrelatedFinding:
    """A quality finding or precheck finding scoped to the data actually used."""

    source: QualitySource
    severity: PrecheckSeverity
    scope: str
    check_name: str
    details: str

    def readable(self) -> str:
        """Render the finding on one line."""
        return (
            f"[{self.source.value}/{self.severity.value}] {self.scope or 'run'} "
            f"· {self.check_name}: {self.details}"
        )


@dataclass(frozen=True)
class QualityCorrelationReport:
    """The data-quality findings correlated to a run's used markets and symbols."""

    markets: tuple[Market, ...]
    findings: tuple[CorrelatedFinding, ...]
    precheck: PrecheckReport | None
    resolved_count: int

    @property
    def unresolved_count(self) -> int:
        """Number of unresolved MVP 1 quality issues correlated to the run."""
        return sum(1 for finding in self.findings if finding.source is QualitySource.QUALITY_ISSUE)

    @property
    def precheck_warning_count(self) -> int:
        """Number of non-blocking precheck findings correlated to the run."""
        return sum(
            1
            for finding in self.findings
            if finding.source is QualitySource.PRECHECK
            and finding.severity is PrecheckSeverity.WARNING
        )

    @property
    def precheck_error_count(self) -> int:
        """Number of blocking precheck findings correlated to the run."""
        return sum(
            1
            for finding in self.findings
            if finding.source is QualitySource.PRECHECK
            and finding.severity is PrecheckSeverity.ERROR
        )

    @property
    def has_findings(self) -> bool:
        """Whether any correlated finding is present."""
        return bool(self.findings)

    def readable(self) -> str:
        """Render the correlation as a human-readable research summary."""
        markets = ", ".join(market.value for market in self.markets)
        lines = [
            "Data quality correlation:",
            f"  markets used: {markets}",
            f"  unresolved MVP 1 quality issues: {self.unresolved_count}",
            f"  resolved MVP 1 quality issues: {self.resolved_count}",
            f"  precheck warnings: {self.precheck_warning_count}",
            f"  precheck errors: {self.precheck_error_count}",
        ]
        if not self.findings:
            lines.append("  no data-quality findings for the data used.")
            return "\n".join(lines)
        lines.append("  findings:")
        for finding in self.findings:
            lines.append(f"    - {finding.readable()}")
        return "\n".join(lines)


def _in_used_data(
    issue: QualityIssue,
    markets: Sequence[Market],
    symbols: Mapping[Market, Sequence[str]] | None,
) -> bool:
    """Whether an issue concerns a used market and, when symbols are known, a used symbol.

    When ``symbols`` is ``None`` the used symbols are unknown, so a symbol-level
    issue in a used market is kept (research honestly reflects potential impact)
    rather than silently dropped.
    """
    if issue.market not in markets:
        return False
    if symbols is not None and issue.symbol is not None:
        return issue.symbol in symbols.get(issue.market, ())
    return True


def correlate_quality(
    *,
    precheck: PrecheckReport,
    issues: Sequence[QualityIssue],
    markets: Sequence[Market],
    symbols: Mapping[Market, Sequence[str]] | None = None,
) -> QualityCorrelationReport:
    """Correlate MVP 1 quality issues and precheck findings to the used data.

    Args:
        precheck: The SP 2.13 readiness precheck for the run.
        issues: The MVP 1 quality issues recorded for the underlying data.
        markets: The markets actually used by the run.
        symbols: The symbols actually used per market; when ``None`` the used
            symbols are unknown and symbol-level issues for a used market are
            kept rather than silently dropped.

    Returns:
        A :class:`QualityCorrelationReport` with the correlated findings.

    Raises:
        CorrelationError: If no market is used, or a symbols key is not one of
            the used markets.
    """
    if not markets:
        raise CorrelationError("At least one market must be used to correlate quality.")
    used = tuple(dict.fromkeys(markets))
    used_symbols = symbols
    if used_symbols is not None:
        for key in used_symbols:
            if key not in used:
                raise CorrelationError(
                    f"Symbols provided for {key.value}, which is not a used market."
                )

    findings: list[CorrelatedFinding] = []
    resolved = 0
    for issue in issues:
        if not _in_used_data(issue, used, used_symbols):
            continue
        if issue.resolved:
            resolved += 1
            continue
        scope = (
            issue.market.value if issue.symbol is None else f"{issue.market.value}/{issue.symbol}"
        )
        findings.append(
            CorrelatedFinding(
                source=QualitySource.QUALITY_ISSUE,
                severity=issue.severity,
                scope=scope,
                check_name=issue.check_name,
                details=issue.details,
            )
        )

    for finding in precheck.findings:
        findings.append(
            CorrelatedFinding(
                source=QualitySource.PRECHECK,
                severity=finding.severity,
                scope=finding.scope,
                check_name="precheck",
                details=finding.message,
            )
        )

    return QualityCorrelationReport(
        markets=used,
        findings=tuple(findings),
        precheck=precheck,
        resolved_count=resolved,
    )
