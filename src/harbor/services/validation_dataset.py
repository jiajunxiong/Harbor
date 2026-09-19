"""Profile what the stored data covers for a validation run (MVP 5 / SP 5.26, SP 5.30).

Stage 3 needed the validation artifacts the dashboard displays, and six of the
eight SP 3.12 tables had **no writer at all**: only the run row and the frozen
split were ever persisted. The dataset manifest, the coverage measurements and
the warnings therefore did not exist, so there was nothing to display.

This module closes the part of that gap which needs **no** backtest re-runs: it
measures what the database actually holds, freezes that measurement as an SP 3.6
manifest with an SP 3.7 fingerprint, and turns every non-passing SP 3.10 coverage
item into an SP 3.12 warning row.

What it deliberately does not do is invent the rest. Trials, folds, stress
results and the conclusion require the validation pipeline itself to run — a
parameter search across folds plus OOS and stress executions — so they stay
absent and the dashboard reports them as absent (SP 5.27 / SP 5.29 / SP 5.31 /
SP 5.32 remain partial, by the decision recorded for this stage).

Two honesty rules run through the measurements:

* an item counts as covered only when evidence for it is found in the database —
  never because the configuration asked for it;
* a market with no pool and no quotes is a *gap*, not a pass, so a zero
  denominator is never rounded up to full coverage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import Connection, func, select

from harbor.core.backtest_domain import Currency, Market
from harbor.core.backtest_interfaces import TradingCalendar
from harbor.core.coverage_gate import (
    CoverageGateResult,
    evaluate_coverage,
)
from harbor.core.coverage_scoring import (
    CoverageMeasurement,
    MarketCoverage,
    score_market_coverage,
)
from harbor.core.dataset_fingerprint import dataset_fingerprint
from harbor.core.dataset_manifest import build_dataset_manifest, component_manifest
from harbor.core.trading_calendar import MarketTradingCalendar
from harbor.core.validation_config import CoverageSeverity, ValidationConfig
from harbor.core.validation_domain import (
    DataComponentManifest,
    DatasetManifest,
    ManifestComponent,
)
from harbor.storage.models import (
    ActionTerm,
    CorporateAction,
    DailyQuote,
    Dividend,
    Financial,
    FxRate,
    Security,
)
from harbor.storage.validation_repositories import ValidationRepository

#: Where the FX rates used by a validation run come from.
FX_SOURCE = "storage:fx_rates"

#: The calendar is a code artifact, so its version is the code that ships it.
CALENDAR_SOURCE = "harbor.core.trading_calendar"

#: How many offending symbols a gap message names before summarising.
_MAX_NAMED_SYMBOLS = 3

#: Recorded alongside the calendar score: the shipped holiday calendar is
#: illustrative (SP 2.11 / SP 2.74), and a reader must not take 100% coverage
#: here as "the calendar is authoritative".
CALENDAR_CAVEAT = "交易日历为示例性节假日清单（SP 2.74），非交易所官方日历"

#: Recorded as the benchmark gap: no benchmark series is stored anywhere.
BENCHMARK_GAP = "未存储基准序列（无基准数据表），基准超额表现不可计算（SP 5.30）"

#: Why quality records are not part of the frozen window.
QUALITY_NOTE = "质量记录（quality_issues）没有时间戳，无法界定清单区间，未纳入冻结清单"

#: Stand-in for the first manifest pass. ``DatasetManifest`` rejects an empty
#: fingerprint, and the field is excluded from the SP 3.7 digest, so a sentinel
#: lets the fingerprint be derived from the manifest's contents.
PLACEHOLDER_FINGERPRINT = "pending"


@dataclass(frozen=True)
class DatasetProfile:
    """What the stored data covers for one validation run, as measured.

    ``gates`` holds one SP 3.10 gate result **per market**. They are not merged
    into a single verdict: the acceptance asks for per-market coverage and gate
    outcomes, and averaging them would hide the market that actually failed.
    """

    window_start: date
    window_end: date
    data_cutoff: date
    markets: tuple[Market, ...]
    base_currency: Currency
    coverage: tuple[MarketCoverage, ...]
    gates: tuple[CoverageGateResult, ...]
    components: tuple[DataComponentManifest, ...]
    fingerprint: str
    calendar_version: str

    @property
    def component_names(self) -> tuple[str, ...]:
        """The component kinds actually recorded in the manifest."""
        return tuple(component.component.value for component in self.components)

    def coverage_for(self, market: Market) -> MarketCoverage | None:
        """One market's coverage scores, or ``None`` when it was not measured."""
        return next((item for item in self.coverage if item.market is market), None)

    def gate_for(self, market: Market) -> CoverageGateResult | None:
        """One market's threshold verdict, or ``None`` when it was not judged."""
        return next((gate for gate in self.gates if gate.market is market), None)

    @property
    def blocked(self) -> bool:
        """Whether any market's coverage is bad enough to block the run."""
        return any(gate.blocked for gate in self.gates)

    @property
    def passes(self) -> bool:
        """Whether every market clears its coverage thresholds."""
        return all(gate.passes for gate in self.gates)

    @property
    def notes(self) -> tuple[str, ...]:
        """What a reader needs to interpret these numbers."""
        return (
            "覆盖率为实测值：分母是配置要求的数据范围，分子是数据库里确实存在的数据。",
            "缺少覆盖数据只会记为缺口或警告，不会被当作通过。",
            CALENDAR_CAVEAT,
            QUALITY_NOTE,
        )


def profile_window(config: ValidationConfig) -> tuple[date, date]:
    """The window a validation run's data actually spans.

    The split's own boundaries are the honest answer: the run asks for nothing
    outside ``train_start .. test_end``, and the config validator already
    guarantees ``test_end <= data_cutoff`` when a cutoff is set.
    """
    return config.split.train_start, config.split.test_end


def _scalar_pair(
    connection: Connection,
    *,
    column: Any,
    criteria: tuple[Any, ...],
) -> tuple[date, date] | None:
    """Return the earliest and latest non-null value of ``column``, if any."""
    row = connection.execute(select(func.min(column), func.max(column)).where(*criteria)).first()
    if row is None:
        return None
    start, end = row
    if start is None or end is None:
        return None
    return start, end


def _pool_extent(
    connection: Connection,
    *,
    market_values: tuple[str, ...],
    window_start: date,
    window_end: date,
) -> tuple[date, date] | None:
    """The stretch of the window the historical stock pool covers (SP 3.6).

    Membership is a set for the whole window (see :func:`_pool_symbols`), so the
    extent cannot be read off the raw listing dates: a security listed before the
    window start is present for all of it, and delisting is not modelled here.

    Clamping both ends of the raw range independently is *wrong* and was fixed
    against real data: a dataset whose securities were all listed in 2000 inside a
    2024 window produced ``2024-01-01 > 2000-01-03``, a reversed range that the
    manifest model rightly rejects. When no security is listed within the window
    the pool is empty, and the component is left out entirely so that the SP 3.9
    scorer reports a full gap instead of a fabricated range.
    """
    row = connection.execute(
        select(func.min(Security.list_date), func.count()).where(
            Security.market.in_(market_values),
            Security.list_date.is_not(None),
            Security.list_date <= window_end,
        )
    ).first()
    if row is None or row[0] is None or int(row[1]) == 0:
        return None
    earliest = row[0]
    return max(earliest, window_start), window_end


@dataclass(frozen=True)
class _MarketFacts:
    """The raw counts one market's coverage is scored from."""

    market: Market
    pool: tuple[str, ...]
    trading_days: tuple[date, ...]
    quote_days: int
    symbols_without_quotes: tuple[str, ...]
    symbols_without_fundamentals: tuple[str, ...]
    actions: int
    actions_with_terms: int
    fx_pair: tuple[str, str] | None
    fx_days: int


def _pool_symbols(connection: Connection, market: Market, window_end: date) -> tuple[str, ...]:
    """The securities that could take part in this market's run.

    Membership is date-driven (SP 2.10): a symbol listed on or before the end of
    the window counts, including ones delisted since — excluding them would be
    the survivorship bias the backtest layer is careful about.
    """
    rows = connection.execute(
        select(Security.symbol)
        .where(
            Security.market == market.value,
            Security.list_date.is_not(None),
            Security.list_date <= window_end,
        )
        .order_by(Security.symbol.asc())
    ).all()
    return tuple(str(row[0]) for row in rows)


def _measure_market(
    connection: Connection,
    *,
    market: Market,
    base_currency: Currency,
    quote_currency: Currency,
    window_start: date,
    window_end: date,
    calendar: TradingCalendar,
) -> _MarketFacts:
    """Collect one market's raw coverage counts from the database."""
    pool = _pool_symbols(connection, market, window_end)
    trading_days = tuple(calendar.trading_days(market, window_start, window_end))

    quote_criteria: tuple[Any, ...] = (
        DailyQuote.market == market.value,
        DailyQuote.date >= window_start,
        DailyQuote.date <= window_end,
    )
    if pool:
        quote_criteria = (*quote_criteria, DailyQuote.symbol.in_(pool))
    quote_days = int(
        connection.execute(
            select(func.count(func.distinct(DailyQuote.date))).where(*quote_criteria)
        ).scalar_one()
    )
    quoted_symbols = {
        str(row[0])
        for row in connection.execute(
            select(func.distinct(DailyQuote.symbol)).where(*quote_criteria)
        ).all()
    }

    fundamental_criteria: tuple[Any, ...] = (
        Financial.market == market.value,
        Financial.report_date <= window_end,
    )
    if pool:
        fundamental_criteria = (*fundamental_criteria, Financial.symbol.in_(pool))
    funded_symbols = {
        str(row[0])
        for row in connection.execute(
            select(func.distinct(Financial.symbol)).where(*fundamental_criteria)
        ).all()
    }

    action_criteria: tuple[Any, ...] = (
        CorporateAction.market == market.value,
        CorporateAction.ex_date.is_not(None),
        CorporateAction.ex_date >= window_start,
        CorporateAction.ex_date <= window_end,
    )
    if pool:
        action_criteria = (*action_criteria, CorporateAction.symbol.in_(pool))
    actions = int(
        connection.execute(
            select(func.count()).select_from(CorporateAction).where(*action_criteria)
        ).scalar_one()
    )
    # An action is only usable when its terms were recorded: the action row says
    # something happened, the terms say what. Counting actions alone would report
    # full coverage for corporate actions we cannot actually apply.
    actions_with_terms = int(
        connection.execute(
            select(func.count(func.distinct(CorporateAction.action_id))).where(
                *action_criteria,
                CorporateAction.action_id.in_(
                    select(ActionTerm.action_id).where(ActionTerm.market == market.value)
                ),
            )
        ).scalar_one()
    )

    fx_pair: tuple[str, str] | None = None
    fx_days = 0
    if quote_currency is not base_currency:
        fx_pair = (quote_currency.value, base_currency.value)
        fx_days = int(
            connection.execute(
                select(func.count(func.distinct(FxRate.date))).where(
                    FxRate.from_currency == fx_pair[0],
                    FxRate.to_currency == fx_pair[1],
                    FxRate.date >= window_start,
                    FxRate.date <= window_end,
                )
            ).scalar_one()
        )

    return _MarketFacts(
        market=market,
        pool=pool,
        trading_days=trading_days,
        quote_days=quote_days,
        symbols_without_quotes=tuple(symbol for symbol in pool if symbol not in quoted_symbols),
        symbols_without_fundamentals=tuple(
            symbol for symbol in pool if symbol not in funded_symbols
        ),
        actions=actions,
        actions_with_terms=actions_with_terms,
        fx_pair=fx_pair,
        fx_days=fx_days,
    )


def _gap(prefix: str, names: tuple[str, ...]) -> str:
    """Render a gap message naming a few offenders rather than all of them.

    Returns an empty string when nothing is missing: a bare prefix would read as
    an explicit gap, which is how a fully covered item ended up carrying a
    "symbols with no quotes" warning of its own.
    """
    if not names:
        return ""
    shown = ", ".join(names[:_MAX_NAMED_SYMBOLS])
    suffix = f" 等 {len(names)} 个" if len(names) > _MAX_NAMED_SYMBOLS else ""
    return f"{prefix}：{shown}{suffix}"


def _measurements(facts: _MarketFacts) -> dict[ManifestComponent, CoverageMeasurement]:
    """Turn one market's raw counts into coverage measurements (SP 3.9).

    Every denominator is floored at 1 because a zero denominator would be a
    division by nothing: a market with no trading days or no pool is a gap, not a
    perfect score.
    """
    trading_days = max(1, len(facts.trading_days))
    pool_size = max(1, len(facts.pool))
    pool_gap = "该市场没有可参与的历史股票池记录" if not facts.pool else ""

    if facts.quote_days < trading_days:
        price_gap = f"{trading_days - facts.quote_days} 个交易日缺少行情数据"
    else:
        price_gap = ""

    if facts.actions == 0:
        action_gap = "该市场窗口内没有企业行动记录"
    elif facts.actions_with_terms < facts.actions:
        action_gap = f"{facts.actions - facts.actions_with_terms} 条企业行动缺少条款记录"
    else:
        action_gap = ""

    if facts.fx_pair is None:
        fx = CoverageMeasurement(covered=1, denominator=1)
    elif facts.fx_days < trading_days:
        pair = f"{facts.fx_pair[0]}->{facts.fx_pair[1]}"
        fx = CoverageMeasurement(
            covered=min(facts.fx_days, trading_days),
            denominator=trading_days,
            gap=f"{trading_days - facts.fx_days} 个交易日缺少 {pair} 汇率",
        )
    else:
        fx = CoverageMeasurement(covered=trading_days, denominator=trading_days)

    return {
        ManifestComponent.PRICES: CoverageMeasurement(
            covered=min(facts.quote_days, trading_days),
            denominator=trading_days,
            gap=price_gap,
        ),
        ManifestComponent.STOCK_POOL: CoverageMeasurement(
            covered=len(facts.pool) - len(facts.symbols_without_quotes),
            denominator=pool_size,
            gap=pool_gap or _gap("股票池中无行情数据的证券", facts.symbols_without_quotes),
        ),
        ManifestComponent.FUNDAMENTALS: CoverageMeasurement(
            covered=len(facts.pool) - len(facts.symbols_without_fundamentals),
            denominator=pool_size,
            gap=pool_gap or _gap("股票池中无财报数据的证券", facts.symbols_without_fundamentals),
        ),
        ManifestComponent.CORPORATE_ACTIONS: CoverageMeasurement(
            covered=facts.actions_with_terms,
            denominator=max(1, facts.actions),
            gap=action_gap,
        ),
        # The calendar ships with the code, so it is always present — but the
        # shipped holiday list is illustrative (SP 2.74), and recording that as a
        # gap keeps the caveat visible on every run instead of only in a doc.
        ManifestComponent.CALENDAR: CoverageMeasurement(
            covered=1,
            denominator=1,
            gap=CALENDAR_CAVEAT,
        ),
        ManifestComponent.FX: fx,
        ManifestComponent.BENCHMARK: CoverageMeasurement(
            covered=0,
            denominator=1,
            gap=BENCHMARK_GAP,
        ),
    }


def _needs_fx(market: Market, base_currency: Currency) -> bool:
    """Whether a market's quotes need a conversion to reach the base currency."""
    return _quote_currency(market) is not base_currency


def _component_records(
    connection: Connection,
    *,
    markets: tuple[Market, ...],
    base_currency: Currency,
    window_start: date,
    window_end: date,
    code_version: str,
    calendar_version: str,
) -> tuple[DataComponentManifest, ...]:
    """Record the extent each data kind actually covers (SP 3.6).

    A kind with no rows is left out of the manifest rather than recorded with a
    fabricated range: the SP 3.9 scorer reports an absent component as a full
    gap, which is exactly what it is.
    """
    market_values = tuple(market.value for market in markets)
    records: list[DataComponentManifest] = []

    prices = _scalar_pair(
        connection,
        column=DailyQuote.date,
        criteria=(
            DailyQuote.market.in_(market_values),
            DailyQuote.date >= window_start,
            DailyQuote.date <= window_end,
        ),
    )
    if prices is not None:
        records.append(
            component_manifest(
                ManifestComponent.PRICES,
                "storage:daily_quotes",
                code_version,
                start=prices[0],
                end=prices[1],
            )
        )

    dividends = _scalar_pair(
        connection,
        column=Dividend.ex_date,
        criteria=(
            Dividend.market.in_(market_values),
            Dividend.ex_date >= window_start,
            Dividend.ex_date <= window_end,
        ),
    )
    if dividends is not None:
        records.append(
            component_manifest(
                ManifestComponent.DIVIDENDS,
                "storage:dividends",
                code_version,
                start=dividends[0],
                end=dividends[1],
            )
        )

    fundamentals = _scalar_pair(
        connection,
        column=Financial.report_date,
        criteria=(
            Financial.market.in_(market_values),
            Financial.report_date >= window_start,
            Financial.report_date <= window_end,
        ),
    )
    if fundamentals is not None:
        records.append(
            component_manifest(
                ManifestComponent.FUNDAMENTALS,
                "storage:financials",
                code_version,
                start=fundamentals[0],
                end=fundamentals[1],
            )
        )

    actions = _scalar_pair(
        connection,
        column=CorporateAction.ex_date,
        criteria=(
            CorporateAction.market.in_(market_values),
            CorporateAction.ex_date.is_not(None),
            CorporateAction.ex_date >= window_start,
            CorporateAction.ex_date <= window_end,
        ),
    )
    if actions is not None:
        records.append(
            component_manifest(
                ManifestComponent.CORPORATE_ACTIONS,
                "storage:corporate_actions",
                code_version,
                start=actions[0],
                end=actions[1],
            )
        )

    pool_extent = _pool_extent(
        connection,
        market_values=market_values,
        window_start=window_start,
        window_end=window_end,
    )
    if pool_extent is not None:
        records.append(
            component_manifest(
                ManifestComponent.STOCK_POOL,
                "storage:securities",
                code_version,
                start=pool_extent[0],
                end=pool_extent[1],
            )
        )

    fx_criteria: tuple[Any, ...] = (
        FxRate.date >= window_start,
        FxRate.date <= window_end,
    )
    if any(_needs_fx(market, base_currency) for market in markets):
        fx_criteria = (
            *fx_criteria,
            FxRate.to_currency == base_currency.value,
        )
    fx = _scalar_pair(connection, column=FxRate.date, criteria=fx_criteria)
    if fx is not None:
        records.append(
            component_manifest(
                ManifestComponent.FX,
                FX_SOURCE,
                code_version,
                start=fx[0],
                end=fx[1],
            )
        )

    # The calendar is a code artifact rather than stored data, so its "extent" is
    # the window the run queries, and its version is the code that ships it.
    records.append(
        component_manifest(
            ManifestComponent.CALENDAR,
            CALENDAR_SOURCE,
            calendar_version,
            start=window_start,
            end=window_end,
        )
    )
    return tuple(records)


def build_profile(
    connection: Connection,
    *,
    config: ValidationConfig,
    config_hash: str,
    calendar: TradingCalendar | None = None,
    calendar_version: str | None = None,
) -> DatasetProfile:
    """Measure a validation config's dataset and score its coverage (SP 5.26/5.30).

    The measurement is taken from the database, never from the configuration, so
    a run whose data is incomplete shows a gap instead of a clean bill of health.
    """
    reader = calendar or MarketTradingCalendar()
    version = calendar_version or config.code_version
    window_start, window_end = profile_window(config)

    coverages: list[MarketCoverage] = []
    gates: list[CoverageGateResult] = []
    for market in config.markets:
        facts = _measure_market(
            connection,
            market=market,
            base_currency=config.base_currency,
            quote_currency=_quote_currency(market),
            window_start=window_start,
            window_end=window_end,
            calendar=reader,
        )
        coverage = score_market_coverage(market, _measurements(facts))
        coverages.append(coverage)
        gates.append(evaluate_coverage(coverage, config.coverage))

    components = _component_records(
        connection,
        markets=config.markets,
        base_currency=config.base_currency,
        window_start=window_start,
        window_end=window_end,
        code_version=config.code_version,
        calendar_version=version,
    )

    def _manifest(fingerprint: str) -> DatasetManifest:
        return build_dataset_manifest(
            markets=config.markets,
            base_currency=config.base_currency,
            start_date=window_start,
            end_date=window_end,
            data_cutoff=config.data_cutoff or window_end,
            config_hash=config_hash,
            code_version=config.code_version,
            calendar_version=version,
            fx_source=FX_SOURCE,
            fingerprint=fingerprint,
            random_seed=config.tuning.random_seed,
            components=components,
        )

    # Two passes: the fingerprint is derived from the manifest contents and is not
    # itself an input (SP 3.7), so it is computed from a placeholder and recorded.
    fingerprint = dataset_fingerprint(_manifest(PLACEHOLDER_FINGERPRINT))
    manifest = _manifest(fingerprint)

    return DatasetProfile(
        window_start=window_start,
        window_end=window_end,
        data_cutoff=manifest.data_cutoff,
        markets=config.markets,
        base_currency=config.base_currency,
        coverage=tuple(coverages),
        gates=tuple(gates),
        components=components,
        fingerprint=fingerprint,
        calendar_version=version,
    )


def _quote_currency(market: Market) -> Currency:
    """The currency a market's quotes are denominated in."""
    return Currency.HKD if market is Market.HK else Currency.USD


def coverage_warnings(profile: DatasetProfile) -> tuple[dict[str, Any], ...]:
    """Turn the coverage gate's verdicts into warning rows (SP 3.10 / SP 3.12).

    Only non-passing items become warnings: a gate that passes everything says
    nothing worth recording, and a warning per passing check would bury the ones
    that matter.
    """
    recorded_at = datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    for gate in profile.gates:
        coverage = profile.coverage_for(gate.market)
        for result in gate.results:
            if result.severity is None:
                continue
            # Prefer the measured gap text: the gate's own reason is generic
            # ("trading calendar coverage is incomplete") while the measurement
            # says why ("the shipped holiday list is illustrative").
            score = coverage.score(result.item) if coverage is not None else None
            measured_gap = score.measurement.gap if score is not None else ""
            rows.append(
                {
                    "warning_code": f"coverage.{result.item.value}",
                    "severity": "error" if result.severity is CoverageSeverity.ERROR else "warning",
                    "message": measured_gap
                    or result.reason
                    or f"{result.item.value} coverage {result.coverage_pct:.1f}%",
                    "context": {
                        "market": result.market.value,
                        "item": result.item.value,
                        "coverage_pct": result.coverage_pct,
                        "severity": result.severity.value,
                    },
                    "created_at": recorded_at,
                }
            )
    return tuple(rows)


def manifest_values(
    *, profile: DatasetProfile, config_hash: str, random_seed: int | None
) -> dict[str, Any]:
    """The ``validation_manifests`` row for a measured profile."""
    return {
        "markets": [market.value for market in profile.markets],
        "base_currency": profile.base_currency.value,
        "start_date": profile.window_start,
        "end_date": profile.window_end,
        "data_cutoff": profile.data_cutoff,
        "config_hash": config_hash,
        "code_version": profile.calendar_version,
        "calendar_version": profile.calendar_version,
        "fx_source": FX_SOURCE,
        "fingerprint": profile.fingerprint,
        "random_seed": random_seed,
        "components": [
            {
                "component": component.component.value,
                "source": component.source,
                "version": component.version,
                "start": component.start.isoformat() if component.start else None,
                "end": component.end.isoformat() if component.end else None,
            }
            for component in profile.components
        ],
    }


def persist_profile(
    connection: Connection,
    *,
    run_id: str,
    profile: DatasetProfile,
    config_hash: str,
    random_seed: int | None = None,
) -> tuple[int, int]:
    """Freeze a measured profile for a run: manifest + warnings (SP 5.26/5.30).

    Both writes are idempotent (the manifest upserts on the run, warnings append),
    so re-freezing the same dataset cannot duplicate or overwrite anything.

    Returns:
        ``(manifest_rows, warning_rows)``.
    """
    repository = ValidationRepository(connection)
    manifest_rows = repository.upsert_manifest(
        run_id,
        manifest_values(profile=profile, config_hash=config_hash, random_seed=random_seed),
    )
    warnings = coverage_warnings(profile)
    warning_rows = repository.insert_warnings(run_id, warnings) if warnings else 0
    return manifest_rows, warning_rows
