"""Real-data small-sample validation tests (MVP 3 / SP 3.82, TEST-ONLY).

For known-available HK and US instruments, runs the 清单 (manifest, SP 3.6/3.7),
覆盖评分 (coverage scoring, SP 3.9/3.10) and a 小型滚动验证 (small rolling
validation, SP 3.33 → 3.35 → 3.37); a live network failure skips the live tests
(网络失败可跳过) and the small-sample validation can NEVER produce a
QUALIFICATION conclusion (不可产生资格结论): the real-data coverage gaps
(FX / fundamentals / calendar / benchmark are not frozen) fail the coverage
gate, and the SP 3.64 structured conclusion built over that evidence carries
the gaps as unresolved limitations or missing stability evidence — so its
``overall`` is ``INCONCLUSIVE`` / ``NOT_QUALIFIED``, never ``QUALIFIED``.

The honesty dimension (``SmallSampleHonestyTests``) is deterministic and runs
everywhere; the live fetch tests follow the established yfinance contract-test
pattern (SP 1.103 / 2.83 / 3.47). No database is required.
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
from harbor.core.coverage_gate import evaluate_coverage
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
from harbor.core.oos_conclusion import (
    OosStructuredConclusion,
    build_oos_conclusion,
    no_return_promise_statement,
)
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
from harbor.core.stability_rule import (
    StabilityConclusion,
    StabilitySignals,
    adjudicate_stability,
    default_stability_rule,
)
from harbor.core.test_access_guard import AccessGuard
from harbor.core.training_fit import build_training_fit
from harbor.core.trial_budget import TrialBudget
from harbor.core.validation_apply import (
    AppliedStandardization,
    ValidationApplication,
    apply_fingerprint,
)
from harbor.core.validation_config import (
    CoverageThresholdConfig,
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
    OOSConclusion,
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
_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
_CREATED = datetime(2025, 1, 1, tzinfo=timezone.utc)
_FP = "f" * 64
_SYMBOLS: dict[Market, str] = {
    HK: "0700.HK",
    US: "AAPL",
}


def _yfinance_available() -> bool:
    try:
        importlib.import_module("yfinance")
    except ImportError:
        return False
    return True


def _provider(market: Market) -> object:
    return HKYFinanceProvider() if market is HK else USYFinanceProvider()


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
    """In-memory reader over a small quote sample."""

    def __init__(self, quotes: list[DailyQuote]) -> None:
        self._quotes = quotes

    def list_securities(self, market: Market, as_of: date) -> Sequence[str]:
        return tuple(sorted({quote.symbol for quote in self._quotes if quote.market is market}))

    def daily_quotes(
        self, market: Market, symbol: str, start: date, end: date
    ) -> Sequence[DailyQuote]:
        return tuple(
            quote
            for quote in self._quotes
            if quote.market is market and quote.symbol == symbol and start <= quote.day <= end
        )

    def dividends(self, market: Market, symbol: str, start: date, end: date) -> Sequence[Dividend]:
        return ()

    def fundamentals(self, market: Market, symbol: str, as_of: date) -> Sequence[FundamentalRecord]:
        return ()

    def corporate_actions(
        self, market: Market, symbol: str, start: date, end: date
    ) -> Sequence[object]:
        return ()

    def adjustment_factors(
        self, market: Market, symbol: str, start: date, end: date
    ) -> Sequence[object]:
        return ()


def _sample_manifest(market: Market, quotes: Sequence[DailyQuote]) -> DatasetManifest:
    """Build a frozen manifest over the small sample's actual date range."""
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
    fields: dict[str, object] = {
        "mode": RollingWindowMode.EXPANDING,
        "train_length_days": None,
        "step_days": 365,
        "retrain_frequency": RetrainFrequency.EVERY_FOLD,
    }
    fields.update(overrides)
    return RollingWindowConfig(**fields)  # type: ignore[arg-type]


def _sequence(**overrides: object) -> FoldSequence:
    fields: dict[str, object] = {
        "split": _split(),
        "rolling": _rolling(),
        "dataset_fingerprint": _FP,
    }
    fields.update(overrides)
    return build_walk_forward_folds(**fields)  # type: ignore[arg-type]


def _space() -> ParameterSpace:
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
    return int(parameters["lookback"]) / 1000.0


def _make_fit_factory(reader: _SampleReader, market: Market, fp: str):
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
    return AppliedStandardization(
        decision_date=decision_date,
        scores=(("AAA", 0.5), ("BBB", -0.5)),
        method=StandardizationMethod.ZSCORE,
    )


def _application(fold_result, decision_date: date) -> ValidationApplication:
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
    fields: dict[str, object] = {
        "test_set_id": "holdout-1",
        "split": _split(),
        "config_hash": "cfg-hash",
        "created_at": _CREATED,
    }
    fields.update(overrides)
    return register_test_set(**fields)  # type: ignore[arg-type]


def _guard(**overrides: object) -> AccessGuard:
    fields: dict[str, object] = {"registration": _registration()}
    fields.update(overrides)
    return AccessGuard(**fields)  # type: ignore[arg-type]


def _manifest(fold, run_id: str) -> ReplayManifest:
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
    run_id = f"oos-run-{fold.fold_index}"
    return OosRunOutcome(run_id=run_id, replay_manifest=_manifest(fold, run_id))


def _net_values_for(fold) -> tuple[NetValue, ...]:
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
    """Run SP 3.33 → 3.35 → 3.37 over the small sample."""
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


# ---------------------------------------------------------------------------
# Honesty helpers (SP 3.64 structured conclusion over the sample evidence).
# ---------------------------------------------------------------------------


def _performance(**overrides: object) -> PerformanceMetrics:
    fields: dict[str, object] = {
        "start_date": date(2019, 1, 1),
        "end_date": date(2022, 12, 31),
        "periods": 252,
        "cumulative_return": 0.05,
        "annualized_return": 0.20,
        "annualized_volatility": 0.15,
        "max_drawdown": -0.05,
        "sharpe_ratio": 1.2,
        "calmar_ratio": 1.0,
        "downside_deviation": 0.08,
    }
    fields.update(overrides)
    return PerformanceMetrics(**fields)  # type: ignore[arg-type]


def _signals(market: Market, **overrides) -> StabilitySignals:
    fields = dict(
        market=market,
        dataset_fingerprint=_FP,
        code_version="1.0.0",
        fold_spread=0.05,
        fold_count=4,
        fold_failure_count=0,
        neighborhood_cliff_ratio=0.1,
        neighborhood_infeasible_ratio=0.0,
        environment_insufficient_ratio=0.0,
        max_stress_loss_pct=2.0,
        stress_unquantifiable=False,
        coverage_blocked=False,
    )
    fields.update(overrides)
    return StabilitySignals(**fields)  # type: ignore[arg-type]


def _conclusion(
    market: Market,
    coverage: MarketCoverage,
    stability: StabilityConclusion | None = None,
    limitations: tuple[str, ...] = (),
) -> OosStructuredConclusion:
    """Build the structured OOS conclusion over the sample evidence (SP 3.64)."""
    return build_oos_conclusion(
        version="conclusion-1.0",
        source="pre-registered",
        market=market,
        dataset_fingerprint=_FP,
        code_version="1.0.0",
        performance=_performance(),
        benchmark_return=0.02,
        excess_return=0.03,
        coverage=coverage,
        stability=(
            stability
            if stability is not None
            else adjudicate_stability(_signals(market), config=default_stability_rule())
        ),
        budget=_budget(),
        unresolved_limitations=limitations,
    )


class SmallSampleHonestyTests(unittest.TestCase):
    """不可产生资格结论: a small sample with coverage gaps never qualifies."""

    def _small_quotes(self, market: Market) -> list[DailyQuote]:
        symbol = _SYMBOLS[market]
        return [
            DailyQuote(
                market=market,
                symbol=symbol,
                day=date(2026, 1, 5) + timedelta(days=index),
                open=50.0,
                high=50.0,
                low=50.0,
                close=50.0 + index,
                volume=100000,
                adjusted_close=50.0 + index,
            )
            for index in range(5)
        ]

    def test_small_sample_manifest_freezes_self_consistent(self) -> None:
        manifest = _sample_manifest(HK, self._small_quotes(HK))
        self.assertEqual(dataset_fingerprint(manifest), manifest.fingerprint)

    def test_small_sample_coverage_has_critical_gaps(self) -> None:
        quotes = self._small_quotes(HK)
        coverage = coverage_from_manifest(_sample_manifest(HK, quotes), HK)
        gate = evaluate_coverage(coverage, CoverageThresholdConfig())
        # The small sample freezes only PRICES + STOCK_POOL: the FX / fundamentals
        # / calendar / benchmark gaps fail the coverage gate.
        self.assertFalse(gate.passes)
        self.assertTrue(gate.not_qualified_items)

    def test_gaps_as_limitations_never_qualify(self) -> None:
        quotes = self._small_quotes(HK)
        coverage = coverage_from_manifest(_sample_manifest(HK, quotes), HK)
        gate = evaluate_coverage(coverage, CoverageThresholdConfig())
        limitations = tuple(
            f"{item.item.value} coverage {item.coverage_pct:.0f}% gap"
            for item in gate.not_qualified_items
        )
        conclusion = _conclusion(HK, coverage, limitations=limitations)
        self.assertNotEqual(conclusion.overall, OOSConclusion.QUALIFIED)

    def test_missing_evidence_never_qualifies(self) -> None:
        coverage = MarketCoverage(
            market=HK,
            scores=(
                CoverageScore(
                    market=HK,
                    item=ManifestComponent.PRICES,
                    measurement=CoverageMeasurement(covered=100, denominator=100),
                ),
            ),
        )
        stability = adjudicate_stability(
            _signals(HK, fold_spread=None), config=default_stability_rule()
        )
        conclusion = _conclusion(HK, coverage, stability=stability)
        self.assertEqual(conclusion.overall, OOSConclusion.INCONCLUSIVE)
        self.assertNotEqual(conclusion.overall, OOSConclusion.QUALIFIED)

    def test_conclusion_has_no_return_promise(self) -> None:
        statement = no_return_promise_statement()
        self.assertIn("no projection, guarantee or promise of future returns", statement)


@unittest.skipUnless(_yfinance_available(), "yfinance is not installed")
class LiveManifestFreezeTests(unittest.TestCase):
    """清单: a known HK/US instrument freezes into a self-consistent manifest."""

    def _freeze(self, market: Market) -> DatasetManifest:
        return _sample_manifest(market, _fetch_quotes(self, market))

    def test_hk_manifest_freezes(self) -> None:
        manifest = self._freeze(HK)
        self.assertIsInstance(manifest, DatasetManifest)
        self.assertEqual(dataset_fingerprint(manifest), manifest.fingerprint)

    def test_us_manifest_freezes(self) -> None:
        manifest = self._freeze(US)
        self.assertEqual(dataset_fingerprint(manifest), manifest.fingerprint)

    def test_manifest_is_immutable(self) -> None:
        manifest = self._freeze(HK)
        with self.assertRaises(FrozenInstanceError):
            manifest.markets = (US,)  # type: ignore[misc]

    def test_manifest_fingerprint_replayable(self) -> None:
        quotes = _fetch_quotes(self, HK)
        self.assertEqual(
            _sample_manifest(HK, quotes).fingerprint,
            _sample_manifest(HK, quotes).fingerprint,
        )


@unittest.skipUnless(_yfinance_available(), "yfinance is not installed")
class LiveCoverageScoringTests(unittest.TestCase):
    """覆盖评分: the real sample's frozen window is scored, gaps flagged."""

    def _coverage(self, market: Market) -> MarketCoverage:
        return coverage_from_manifest(_sample_manifest(market, _fetch_quotes(self, market)), market)

    def test_hk_prices_score_full_window(self) -> None:
        coverage = self._coverage(HK)
        price = coverage.score(ManifestComponent.PRICES)
        self.assertIsNotNone(price)
        assert price is not None
        self.assertEqual(price.coverage_pct, 100.0)

    def test_us_prices_score_full_window(self) -> None:
        coverage = self._coverage(US)
        price = coverage.score(ManifestComponent.PRICES)
        self.assertIsNotNone(price)
        assert price is not None
        self.assertEqual(price.coverage_pct, 100.0)

    def test_absent_components_flagged_as_gaps(self) -> None:
        coverage = self._coverage(HK)
        for item in (ManifestComponent.FX, ManifestComponent.FUNDAMENTALS):
            score = coverage.score(item)
            self.assertIsNotNone(score)
            assert score is not None
            self.assertEqual(score.coverage_pct, 0.0)
            self.assertTrue(score.is_gap)

    def test_real_gaps_fail_the_coverage_gate(self) -> None:
        coverage = self._coverage(HK)
        gate = evaluate_coverage(coverage, CoverageThresholdConfig())
        self.assertFalse(gate.passes)


@unittest.skipUnless(_yfinance_available(), "yfinance is not installed")
class LiveRollingValidationTests(unittest.TestCase):
    """小型滚动验证: the real-data sample runs the rolling OOS structure."""

    def _run(self, market: Market) -> tuple[RollingOosRun, OosEquityPath]:
        quotes = _fetch_quotes(self, market)
        reader = _SampleReader(quotes)
        manifest = _sample_manifest(market, quotes)
        return _run_pipeline(market, reader, manifest.fingerprint)

    def test_hk_pipeline_runs_all_folds(self) -> None:
        oos, path = self._run(HK)
        self.assertTrue(oos.all_executed)
        self.assertEqual(oos.executed_count, len(oos.results))
        self.assertEqual(path.fold_count, len(oos.results))

    def test_us_pipeline_runs_all_folds(self) -> None:
        oos, path = self._run(US)
        self.assertTrue(oos.all_executed)
        self.assertEqual(path.fold_count, 4)

    def test_fit_consumes_the_sample_securities(self) -> None:
        quotes = _fetch_quotes(self, HK)
        reader = _SampleReader(quotes)
        fp = _sample_manifest(HK, quotes).fingerprint
        oos, _ = _run_pipeline(HK, reader, fp)
        for result in oos.results:
            fitted = dict(result.validation.training.fit.fitted_state)
            self.assertEqual(fitted["security_count"], 1.0)

    def test_oos_fingerprint_rederivable(self) -> None:
        oos, _ = self._run(HK)
        self.assertEqual(rolling_oos_fingerprint(oos), rolling_oos_fingerprint(oos))


if __name__ == "__main__":
    unittest.main()
