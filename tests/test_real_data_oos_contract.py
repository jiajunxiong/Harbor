"""Real-data small-sample contract tests (MVP 3 / SP 3.47, TEST-ONLY).

For Hong Kong and the United States, selects a small set of available
instruments and checks the three acceptance dimensions (对 HK 与 US 各选少量可用
标的检查清单冻结、覆盖评分和滚动回测结构；网络失败可跳过):

- 清单冻结 (manifest freezing): the fetched real sample drives an SP 3.6
  :class:`~harbor.core.validation_domain.DatasetManifest` whose SP 3.7
  fingerprint is self-consistent and replayable;
- 覆盖评分 (coverage scoring): SP 3.9 ``coverage_from_manifest`` scores the
  frozen window per component — the bounded price component covers the whole
  window and absent components are flagged as gaps, never assumed complete;
- 滚动回测结构 (rolling backtest structure): the SP 3.31 fold geometry over a
  fixed split and the SP 3.33 → 3.34 → 3.35 → 3.37 pipeline run against the
  real-data reader (the fit consumes the sample's securities), producing one
  executed fold per fold with a concatenated OOS path and replayable
  fingerprints.

Live network calls are wrapped so any network / data-source failure skips the
test (网络失败可跳过), matching the established yfinance contract-test pattern
(SP 1.103 / 1.104 / 2.83).
"""

import importlib
import unittest
from collections.abc import Sequence
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone

from harbor.config import MarketTarget
from harbor.core.backtest_config import BenchmarkKind
from harbor.core.backtest_domain import Currency, Market, NetValue
from harbor.core.backtest_interfaces import (
    BacktestDataReader,
    DailyQuote,
    Dividend,
    FundamentalRecord,
)
from harbor.core.benchmark import BenchmarkLevel, BenchmarkSeries
from harbor.core.coverage_scoring import (
    CoverageMeasurement,
    CoverageScore,
    MarketCoverage,
    coverage_from_manifest,
)
from harbor.core.dataset_fingerprint import dataset_fingerprint
from harbor.core.dataset_manifest import build_dataset_manifest, component_manifest
from harbor.core.drawdown_events import DrawdownConfig, DrawdownSeries
from harbor.core.factor_standardization import StandardizationMethod
from harbor.core.holdout_registry import register_test_set
from harbor.core.oos_concat import OosEquityPath, concatenate_fold_oos
from harbor.core.parameter_space import (
    ParameterDomain,
    ParameterKind,
    ParameterSpace,
    build_parameter_space,
    declare_parameter,
)
from harbor.core.performance_metrics import PerformanceMetrics
from harbor.core.replay_manifest import DataQueryBoundaries, ReplayManifest
from harbor.core.rolling_oos import (
    OosRunOutcome,
    RollingOosRun,
    rolling_oos_fingerprint,
    run_rolling_oos,
)
from harbor.core.rolling_train import run_rolling_training
from harbor.core.rolling_validate import (
    ValidationComponents,
    run_rolling_validation,
)
from harbor.core.rolling_window import FoldSequence, build_walk_forward_folds
from harbor.core.test_access_guard import AccessGuard
from harbor.core.training_fit import build_training_fit
from harbor.core.trial_budget import TrialBudget
from harbor.core.validation_apply import (
    AppliedStandardization,
    ValidationApplication,
    apply_fingerprint,
)
from harbor.core.validation_config import (
    MetricDirection,
    RetrainFrequency,
    RollingWindowConfig,
    RollingWindowMode,
    TuningConfig,
)
from harbor.core.validation_domain import (
    DatasetManifest,
    EvaluationSplit,
    ManifestComponent,
    ValidationStatus,
)
from harbor.infrastructure.data_providers.yfinance import (
    HKYFinanceProvider,
    USYFinanceProvider,
)

HK = Market.HK
US = Market.US
HKD = Currency.HKD
USD = Currency.USD

_QUOTE_START = date(2026, 1, 5)
_QUOTE_END = date(2026, 1, 9)
_DIVIDEND_START = date(2024, 1, 1)
_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
_CREATED = datetime(2025, 1, 1, tzinfo=timezone.utc)
_SYMBOLS: dict[Market, str] = {
    HK: "0700.HK",
    US: "AAPL",
}


def _yfinance_available() -> bool:
    """Return whether the yfinance package can be imported."""
    try:
        importlib.import_module("yfinance")
    except ImportError:
        return False
    return True


def _provider(market: Market) -> object:
    """Return the yfinance provider for a market."""
    if market is HK:
        return HKYFinanceProvider()
    return USYFinanceProvider()


def _market_target(market: Market) -> MarketTarget:
    return MarketTarget(market.value)


def _fetch_quotes(test: unittest.TestCase, market: Market) -> list[DailyQuote]:
    """Fetch a small real quote sample; skip when network / data is unavailable."""
    provider = _provider(market)
    target = _market_target(market)
    symbol = _SYMBOLS[market]
    try:
        rows = list(provider.fetch_daily_quotes(target, symbol, _QUOTE_START, _QUOTE_END))
    except Exception as error:  # pragma: no cover - network dependent
        test.skipTest(f"Live yfinance call failed: {error}")
    if not rows:
        test.skipTest(f"No quote rows returned for {market.value}/{symbol}.")
    return [
        DailyQuote(
            market=Market(str(row["market"])),
            symbol=str(row["symbol"]),
            day=row["date"],  # type: ignore[arg-type]
            open=float(row["open"]),  # type: ignore[arg-type]
            high=float(row["high"]),  # type: ignore[arg-type]
            low=float(row["low"]),  # type: ignore[arg-type]
            close=float(row["close"]),  # type: ignore[arg-type]
            volume=int(row["volume"]),  # type: ignore[arg-type]
            adjusted_close=float(row["adjusted_close"]),  # type: ignore[arg-type]
        )
        for row in rows
    ]


class _SampleReader(BacktestDataReader):
    """In-memory reader over a fetched real-data sample (SP 3.47)."""

    def __init__(self, quotes: list[DailyQuote]) -> None:
        self._quotes = quotes

    def list_securities(self, market: Market, as_of: date) -> Sequence[str]:
        return tuple(sorted({quote.symbol for quote in self._quotes if quote.market is market}))

    def daily_quotes(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[DailyQuote]:
        return tuple(
            quote
            for quote in self._quotes
            if quote.market is market and quote.symbol == symbol and start <= quote.day <= end
        )

    def dividends(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[Dividend]:
        return ()

    def fundamentals(
        self,
        market: Market,
        symbol: str,
        as_of: date,
    ) -> Sequence[FundamentalRecord]:
        return ()

    def corporate_actions(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[object]:
        return ()

    def adjustment_factors(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[object]:
        return ()


def _sample_manifest(market: Market, quotes: Sequence[DailyQuote]) -> DatasetManifest:
    """Build a frozen manifest over the fetched sample's actual date range."""
    start = min(quote.day for quote in quotes)
    end = max(quote.day for quote in quotes)
    base = HKD if market is HK else USD
    manifest = build_dataset_manifest(
        markets=(market,),
        base_currency=base,
        start_date=start,
        end_date=end,
        data_cutoff=end,
        config_hash="cfg-hash",
        code_version="1.0.0",
        calendar_version="cal-1",
        fx_source="yfinance",
        fingerprint="placeholder",
        components=(
            component_manifest(ManifestComponent.PRICES, "yfinance", "1.0", start=start, end=end),
            component_manifest(ManifestComponent.STOCK_POOL, "sample", "1.0", start=start, end=end),
        ),
    )
    return replace(manifest, fingerprint=dataset_fingerprint(manifest))


def _split(**overrides: object) -> EvaluationSplit:
    """Return a tight pre-registered split (final fold is full-length)."""
    fields: dict[str, object] = {
        "train_start": date(2019, 1, 1),
        "train_end": date(2021, 12, 31),
        "validation_start": date(2022, 1, 1),
        "validation_end": date(2022, 12, 31),
        "test_start": date(2023, 1, 1),
        "test_end": date(2026, 12, 30),
    }
    fields.update(overrides)
    return EvaluationSplit(**fields)  # type: ignore[arg-type]


def _rolling(**overrides: object) -> RollingWindowConfig:
    """Return an expanding, every-fold rolling config with overridable fields."""
    fields: dict[str, object] = {
        "mode": RollingWindowMode.EXPANDING,
        "train_length_days": None,
        "step_days": 365,
        "retrain_frequency": RetrainFrequency.EVERY_FOLD,
    }
    fields.update(overrides)
    return RollingWindowConfig(**fields)  # type: ignore[arg-type]


def _sequence(**overrides: object) -> FoldSequence:
    """Build the SP 3.31 fold sequence (defaults to 4 folds)."""
    fields: dict[str, object] = {
        "split": _split(),
        "rolling": _rolling(),
        "dataset_fingerprint": "f" * 64,
    }
    fields.update(overrides)
    return build_walk_forward_folds(**fields)  # type: ignore[arg-type]


def _space() -> ParameterSpace:
    """Return a three-parameter space (two weights + one window)."""
    return build_parameter_space(
        declare_parameter(
            name="cash_weight",
            kind=ParameterKind.FACTOR_WEIGHT,
            domain=ParameterDomain.CONTINUOUS,
            minimum=0.0,
            maximum=1.0,
            step=0.05,
            default=0.05,
            markets=(Market.HK, Market.US),
        ),
        declare_parameter(
            name="factor_weight",
            kind=ParameterKind.FACTOR_WEIGHT,
            domain=ParameterDomain.CONTINUOUS,
            minimum=0.0,
            maximum=1.0,
            step=0.05,
            default=0.95,
            markets=(Market.HK, Market.US),
        ),
        declare_parameter(
            name="lookback",
            kind=ParameterKind.WINDOW,
            domain=ParameterDomain.INTEGER,
            minimum=60,
            maximum=504,
            step=24,
            default=252,
            markets=(Market.HK, Market.US),
        ),
    )


def _budget() -> TrialBudget:
    return TrialBudget(max_trials=3, random_seed=42)


def _tuning() -> TuningConfig:
    return TuningConfig(
        primary_metric="sharpe",
        metric_direction=MetricDirection.HIGHER_BETTER,
        max_trials=3,
        random_seed=42,
        min_validation_days=63,
    )


def _candidates() -> list[dict[str, object]]:
    return [
        {"cash_weight": 0.05, "factor_weight": 0.95, "lookback": 252},
        {"cash_weight": 0.10, "factor_weight": 0.90, "lookback": 252},
        {"cash_weight": 0.05, "factor_weight": 0.95, "lookback": 324},
    ]


def _evaluate(fold, parameters: dict[str, object]) -> float:
    """Deterministic validation metric: larger lookback scores higher."""
    return int(parameters["lookback"]) / 1000.0


def _make_fit_factory(reader: _SampleReader, market: Market, fp: str):
    """A training fit that consumes the real sample's securities (SP 3.47)."""

    def fit_factory(train_start: date, train_end: date):
        securities = tuple(reader.list_securities(market, train_start))
        return build_training_fit(
            fit_start=train_start,
            fit_end=train_end,
            dataset_fingerprint=fp,
            code_version="1.0.0",
            fitted_state=(("security_count", float(len(securities))),),
        )

    return fit_factory


def _applied_standardization(decision_date: date) -> AppliedStandardization:
    """Return a minimal applied standardization record."""
    return AppliedStandardization(
        decision_date=decision_date,
        scores=(("AAA", 0.5), ("BBB", -0.5)),
        method=StandardizationMethod.ZSCORE,
    )


def _application(fold_result, decision_date: date) -> ValidationApplication:
    """Build a validation application from the fold's frozen fit."""
    application = ValidationApplication(
        fit_fingerprint=fold_result.fit.fingerprint,
        decision_date=decision_date,
        dataset_fingerprint=fold_result.fit.dataset_fingerprint,
        code_version=fold_result.fit.code_version,
        fingerprint="unfingerprinted",
        standardization=_applied_standardization(decision_date),
    )
    return replace(application, fingerprint=apply_fingerprint(application))


def _compute_validation(fold_result, application) -> ValidationComponents:
    """Compute the four validation results for a fold (deterministic stub)."""
    fold = fold_result.fold
    strategy = PerformanceMetrics(
        start_date=fold.validation_start,
        end_date=fold.validation_end,
        periods=63,
        cumulative_return=0.05,
        annualized_return=0.20,
        annualized_volatility=0.15,
        max_drawdown=-0.05,
        sharpe_ratio=1.2,
        calmar_ratio=1.0,
        downside_deviation=0.08,
    )
    benchmark = BenchmarkSeries(
        kind=BenchmarkKind.CASH,
        levels=(
            BenchmarkLevel(as_of=fold.validation_start, level=1.0, kind=BenchmarkKind.CASH),
            BenchmarkLevel(as_of=fold.validation_end, level=1.02, kind=BenchmarkKind.CASH),
        ),
    )
    risk = DrawdownSeries(config=DrawdownConfig(), events=())
    data_quality = MarketCoverage(
        market=Market.HK,
        scores=(
            CoverageScore(
                market=Market.HK,
                item=ManifestComponent.PRICES,
                measurement=CoverageMeasurement(covered=63, denominator=63),
            ),
        ),
    )
    return ValidationComponents(
        strategy=strategy,
        benchmark=benchmark,
        risk=risk,
        data_quality=data_quality,
    )


def _registration(**overrides: object):
    """Register the independent holdout over the base split."""
    fields: dict[str, object] = {
        "test_set_id": "holdout-1",
        "split": _split(),
        "config_hash": "cfg-hash",
        "created_at": _CREATED,
    }
    fields.update(overrides)
    return register_test_set(**fields)  # type: ignore[arg-type]


def _guard(**overrides: object) -> AccessGuard:
    """Return an access guard over the registered holdout."""
    fields: dict[str, object] = {"registration": _registration()}
    fields.update(overrides)
    return AccessGuard(**fields)  # type: ignore[arg-type]


def _manifest(fold, run_id: str) -> ReplayManifest:
    """Build a replay manifest covering the fold's OOS segment."""
    return ReplayManifest(
        run_id=run_id,
        config_hash="cfg-hash",
        code_version="1.0.0",
        data_boundaries=DataQueryBoundaries(
            start_date=fold.test_start,
            end_date=fold.test_end,
            data_cutoff=fold.test_end,
        ),
        fx_source="fx-1",
        calendar_version="cal-1",
        random_seed=42,
    )


def _run_engine(fold, selected) -> OosRunOutcome:
    """Deterministic MVP 2 engine stub for one fold's OOS segment."""
    run_id = f"oos-run-{fold.fold_index}"
    return OosRunOutcome(run_id=run_id, replay_manifest=_manifest(fold, run_id))


def _net_values_for(fold) -> tuple[NetValue, ...]:
    """Deterministic per-fold OOS net values (fold-boundary drawdowns)."""
    factor = 1.0 + 0.5 * fold.fold_index
    return tuple(
        NetValue(
            as_of_date=fold.test_start + timedelta(days=index),
            currency=Currency.HKD,
            cash=1_000_000.0 * (1.0 + 0.001 * factor * index),
            securities_value=0.0,
        )
        for index in range((fold.test_end - fold.test_start).days + 1)
    )


def _run_pipeline(
    market: Market,
    reader: _SampleReader,
    fp: str,
) -> tuple[RollingOosRun, OosEquityPath]:
    """Run SP 3.33 → 3.35 → 3.37 over the real-data reader (SP 3.47)."""
    training = run_rolling_training(
        sequence=_sequence(dataset_fingerprint=fp),
        space=_space(),
        budget=_budget(),
        market=market,
        dataset_fingerprint=fp,
        code_version="1.0.0",
        tuning=_tuning(),
        candidate_parameter_sets=_candidates(),
        fit_factory=_make_fit_factory(reader, market, fp),
        evaluate=_evaluate,
        constraints=(),
        validation_samples=200,
    )
    validation = run_rolling_validation(
        training,
        application_factory=_application,
        compute_validation=_compute_validation,
    )
    oos = run_rolling_oos(
        validation,
        guard=_guard(),
        current_stage=ValidationStatus.TEST_LOCKED,
        run_engine=_run_engine,
        requested_at=_AT,
    )
    path = concatenate_fold_oos(oos, net_values_for=_net_values_for)
    return oos, path


@unittest.skipUnless(_yfinance_available(), "yfinance is not installed")
class LiveManifestFreezeTests(unittest.TestCase):
    """清单冻结: the real sample freezes into a self-consistent manifest."""

    def _freeze(self, market: Market) -> DatasetManifest:
        quotes = _fetch_quotes(self, market)
        return _sample_manifest(market, quotes)

    def test_hk_manifest_freezes(self) -> None:
        manifest = self._freeze(HK)
        self.assertIsInstance(manifest, DatasetManifest)
        self.assertEqual(dataset_fingerprint(manifest), manifest.fingerprint)

    def test_us_manifest_freezes(self) -> None:
        manifest = self._freeze(US)
        self.assertEqual(dataset_fingerprint(manifest), manifest.fingerprint)

    def test_manifest_is_frozen_immutable(self) -> None:
        manifest = self._freeze(HK)
        with self.assertRaises(FrozenInstanceError):
            manifest.markets = (US,)  # type: ignore[misc]

    def test_manifest_fingerprint_replayable(self) -> None:
        quotes = _fetch_quotes(self, HK)
        first = _sample_manifest(HK, quotes)
        second = _sample_manifest(HK, quotes)
        self.assertEqual(first.fingerprint, second.fingerprint)

    def test_manifest_records_sample_boundaries(self) -> None:
        quotes = _fetch_quotes(self, HK)
        manifest = _sample_manifest(HK, quotes)
        self.assertEqual(manifest.start_date, min(quote.day for quote in quotes))
        self.assertEqual(manifest.end_date, max(quote.day for quote in quotes))
        self.assertEqual(manifest.markets, (HK,))


@unittest.skipUnless(_yfinance_available(), "yfinance is not installed")
class LiveCoverageScoringTests(unittest.TestCase):
    """覆盖评分: the frozen window is scored, gaps are flagged never assumed."""

    def _coverage(self, market: Market) -> MarketCoverage:
        quotes = _fetch_quotes(self, market)
        return coverage_from_manifest(_sample_manifest(market, quotes), market)

    def test_hk_coverage_scored(self) -> None:
        coverage = self._coverage(HK)
        self.assertIsInstance(coverage, MarketCoverage)
        self.assertGreater(coverage.overall_pct, 0)

    def test_us_coverage_scored(self) -> None:
        coverage = self._coverage(US)
        self.assertGreater(coverage.overall_pct, 0)

    def test_prices_component_fully_covered(self) -> None:
        coverage = self._coverage(HK)
        prices = coverage.score(ManifestComponent.PRICES)
        self.assertIsNotNone(prices)
        assert prices is not None
        self.assertEqual(prices.coverage_pct, 100.0)
        self.assertFalse(prices.is_gap)

    def test_missing_component_flagged_as_gap(self) -> None:
        quotes = _fetch_quotes(self, HK)
        manifest = _sample_manifest(HK, quotes)
        coverage = coverage_from_manifest(manifest, HK)
        fx = coverage.score(ManifestComponent.FX)
        self.assertIsNotNone(fx)
        assert fx is not None
        self.assertEqual(fx.coverage_pct, 0.0)
        self.assertTrue(fx.is_gap)


@unittest.skipUnless(_yfinance_available(), "yfinance is not installed")
class LiveRollingStructureTests(unittest.TestCase):
    """滚动回测结构: folds and the pipeline run against the real-data reader."""

    def _pipeline(self, market: Market) -> tuple[RollingOosRun, OosEquityPath]:
        quotes = _fetch_quotes(self, market)
        reader = _SampleReader(quotes)
        manifest = _sample_manifest(market, quotes)
        return _run_pipeline(market, reader, manifest.fingerprint)

    def test_folds_built_over_fixed_split(self) -> None:
        sequence = _sequence()
        self.assertEqual(len(sequence), 4)
        for previous, current in zip(sequence.folds, sequence.folds[1:]):
            self.assertEqual(
                current.test_start,
                previous.test_end + timedelta(days=1),
            )

    def test_fold_zero_matches_split(self) -> None:
        sequence = _sequence()
        split = _split()
        fold = sequence[0]
        self.assertEqual(fold.train_start, split.train_start)
        self.assertEqual(fold.validation_end, split.validation_end)
        self.assertEqual(fold.test_start, split.test_start)

    def test_retrain_dates_present(self) -> None:
        for fold in _sequence():
            self.assertIsNotNone(fold.retrain_date)
            self.assertLessEqual(fold.retrain_date, fold.train_end)

    def test_pipeline_runs_to_completion_hk(self) -> None:
        oos, path = self._pipeline(HK)
        self.assertEqual(len(oos), 4)
        self.assertTrue(oos.all_executed)
        self.assertEqual(path.fold_count, 4)

    def test_pipeline_runs_to_completion_us(self) -> None:
        oos, path = self._pipeline(US)
        self.assertEqual(len(oos), 4)
        self.assertTrue(oos.all_executed)
        self.assertEqual(path.fold_count, 4)

    def test_oos_path_spans_horizon(self) -> None:
        oos, path = self._pipeline(HK)
        sequence = _sequence()
        self.assertEqual(path.start_date, sequence.oos_start)
        self.assertEqual(path.end_date, sequence.oos_end)

    def test_pipeline_replayable(self) -> None:
        quotes = _fetch_quotes(self, HK)
        reader = _SampleReader(quotes)
        manifest = _sample_manifest(HK, quotes)
        first = _run_pipeline(HK, reader, manifest.fingerprint)
        second = _run_pipeline(HK, reader, manifest.fingerprint)
        self.assertEqual(
            rolling_oos_fingerprint(first[0]),
            rolling_oos_fingerprint(second[0]),
        )
        self.assertEqual(first[1], second[1])
