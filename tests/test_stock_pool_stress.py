"""Stock-pool integrity stress tests (MVP 3 / SP 3.56).

Verifies that the impact of unknown historical constituents (历史成分未知),
insufficient delisting coverage (退市覆盖不足) and a shrinking tradeable
universe (可交易标的下降) is quantified, and that when the impact cannot be
quantified the conclusion is explicitly blocked (不可量化时明确阻断结论).
"""

import json
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone

from harbor.core.backtest_config import BenchmarkKind
from harbor.core.backtest_domain import Market
from harbor.core.benchmark import BenchmarkLevel, BenchmarkSeries
from harbor.core.coverage_scoring import (
    CoverageMeasurement,
    CoverageScore,
    MarketCoverage,
)
from harbor.core.drawdown_events import DrawdownConfig, DrawdownSeries
from harbor.core.factor_standardization import StandardizationMethod
from harbor.core.holdout_registry import register_test_set
from harbor.core.parameter_space import (
    ParameterDomain,
    ParameterKind,
    ParameterSpace,
    build_parameter_space,
    declare_parameter,
)
from harbor.core.performance_metrics import PerformanceMetrics
from harbor.core.replay_manifest import DataQueryBoundaries, ReplayManifest
from harbor.core.rolling_oos import OosRunOutcome, run_rolling_oos
from harbor.core.rolling_train import run_rolling_training
from harbor.core.rolling_validate import (
    ValidationComponents,
    run_rolling_validation,
)
from harbor.core.rolling_window import build_walk_forward_folds
from harbor.core.stock_pool import StockPoolMembership
from harbor.core.stock_pool_stress import (
    StockPoolStressError,
    StockPoolStressInput,
    StockPoolStressKind,
    StockPoolStressScenarioResult,
    build_stock_pool_stress_config,
    compute_stock_pool_stress_report,
    default_stock_pool_stresses,
    quantify_stock_pool_stress,
    stock_pool_stress_config_fingerprint,
    stock_pool_stress_fingerprint,
    stock_pool_stress_json,
    stock_pool_stress_report_fingerprint,
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
    CoverageSeverity,
    MetricDirection,
    RetrainFrequency,
    RollingWindowConfig,
    RollingWindowMode,
    TuningConfig,
)
from harbor.core.validation_domain import (
    EvaluationSplit,
    ManifestComponent,
    ValidationStatus,
)

_FINGERPRINT = "f" * 64
_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
_OOS_START = date(2023, 1, 1)
_OOS_END = date(2026, 12, 30)


def _membership(
    symbol: str,
    effective: date,
    expiry: date | None = None,
) -> StockPoolMembership:
    """A US stock-pool membership window."""
    return StockPoolMembership(
        market=Market.US,
        symbol=symbol,
        effective_date=effective,
        expiry_date=expiry,
        source="pool",
    )


def _pool(**overrides: object) -> StockPoolStressInput:
    """A pool with two active, two delisted-covered and one missing name."""
    fields: dict[str, object] = {
        "market": Market.US,
        "memberships": (
            _membership("AAPL", date(2019, 1, 1)),
            _membership("MSFT", date(2019, 1, 1)),
            _membership("GONE", date(2019, 1, 1), date(2025, 12, 31)),
            _membership("LOST", date(2019, 1, 1), date(2024, 6, 30)),
        ),
        "expected_universe": ("AAPL", "MSFT", "GONE", "LOST", "MISSING"),
        "as_of": date(2026, 1, 10),
        "historical_known": True,
    }
    fields.update(overrides)
    return StockPoolStressInput(**fields)  # type: ignore[arg-type]


def _full_pool(**overrides: object) -> StockPoolStressInput:
    """A pool whose every expected name is covered (full coverage)."""
    fields: dict[str, object] = {
        "market": Market.US,
        "memberships": (
            _membership("AAPL", date(2019, 1, 1)),
            _membership("MSFT", date(2019, 1, 1)),
        ),
        "expected_universe": ("AAPL", "MSFT"),
        "as_of": date(2026, 1, 10),
        "historical_known": True,
    }
    fields.update(overrides)
    return StockPoolStressInput(**fields)  # type: ignore[arg-type]


def _split(**overrides: object) -> EvaluationSplit:
    """Return a tight pre-registered split (final fold is full-length)."""
    fields: dict[str, object] = {
        "train_start": date(2019, 1, 1),
        "train_end": date(2021, 12, 31),
        "validation_start": date(2022, 1, 1),
        "validation_end": date(2022, 12, 31),
        "test_start": _OOS_START,
        "test_end": _OOS_END,
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


def _sequence(**overrides: object):
    """Build the SP 3.31 fold sequence (defaults to 4 folds)."""
    fields: dict[str, object] = {
        "split": _split(),
        "rolling": _rolling(),
        "dataset_fingerprint": _FINGERPRINT,
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


def _fit_factory(train_start: date, train_end: date):
    """Fit a snapshot confined to the requested training interval."""
    return build_training_fit(
        fit_start=train_start,
        fit_end=train_end,
        dataset_fingerprint=_FINGERPRINT,
        code_version="1.0.0",
        fitted_state=(("lookback", 252.0),),
    )


def _evaluate(fold, parameters: dict[str, object]) -> float:
    """Deterministic validation metric: larger lookback scores higher."""
    return int(parameters["lookback"]) / 1000.0


def _training_run(**overrides: object):
    """Build the SP 3.33 rolling training run with overridable arguments."""
    fields: dict[str, object] = {
        "sequence": _sequence(),
        "space": _space(),
        "budget": _budget(),
        "market": Market.HK,
        "dataset_fingerprint": _FINGERPRINT,
        "code_version": "1.0.0",
        "tuning": _tuning(),
        "candidate_parameter_sets": _candidates(),
        "fit_factory": _fit_factory,
        "evaluate": _evaluate,
        "constraints": (),
        "validation_samples": 200,
    }
    fields.update(overrides)
    return run_rolling_training(**fields)  # type: ignore[arg-type]


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


def _validation_run(**overrides: object):
    """Build the SP 3.34 rolling validation run with overridable arguments."""
    fields: dict[str, object] = {
        "training_run": _training_run(),
        "application_factory": _application,
        "compute_validation": _compute_validation,
    }
    fields.update(overrides)
    return run_rolling_validation(**fields)  # type: ignore[arg-type]


def _registration(**overrides: object):
    """Register the independent holdout over the base split."""
    fields: dict[str, object] = {
        "test_set_id": "holdout-1",
        "split": _split(),
        "config_hash": "cfg-hash",
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


def _oos_run(**overrides: object):
    """Run the SP 3.35 rolling OOS execution with overridable arguments."""
    fields: dict[str, object] = {
        "validation_run": _validation_run(),
        "guard": _guard(),
        "current_stage": ValidationStatus.TEST_LOCKED,
        "run_engine": _run_engine,
        "requested_at": _AT,
    }
    fields.update(overrides)
    return run_rolling_oos(**fields)  # type: ignore[arg-type]


def _scenario(**overrides: object) -> StockPoolStressScenarioResult:
    """Quantify the default unknown-history stress with overridable arguments."""
    fields: dict[str, object] = {
        "oos_run": _oos_run(),
        "stress_config": default_stock_pool_stresses()[0],
        "pool": _pool(),
    }
    fields.update(overrides)
    return quantify_stock_pool_stress(**fields)  # type: ignore[arg-type]


def _scenario_kwargs() -> dict[str, object]:
    """The validated field values of one quantified scenario, for direct builds."""
    scenario = _scenario()
    return {
        "stress": scenario.stress,
        "market": scenario.market,
        "dataset_fingerprint": scenario.dataset_fingerprint,
        "code_version": scenario.code_version,
        "expected_count": scenario.expected_count,
        "covered_count": scenario.covered_count,
        "active_count": scenario.active_count,
        "coverage_pct": scenario.coverage_pct,
        "impact_pct": scenario.impact_pct,
        "missing_symbols": scenario.missing_symbols,
        "quantifiable": scenario.quantifiable,
        "blocked": scenario.blocked,
        "blocked_reason": scenario.blocked_reason,
        "conclusion_severity": scenario.conclusion_severity,
        "fingerprint": "x" * 64,
    }


def _scenario_direct(**overrides: object) -> StockPoolStressScenarioResult:
    """A directly-constructed scenario (bypasses quantify) with overrides."""
    fields = _scenario_kwargs()
    fields.update(overrides)
    return StockPoolStressScenarioResult(**fields)  # type: ignore[arg-type]


class StockPoolStressErrorTests(unittest.TestCase):
    """The dedicated error type."""

    def test_error_is_value_error(self) -> None:
        self.assertTrue(issubclass(StockPoolStressError, ValueError))

    def test_empty_expected_universe_rejected(self) -> None:
        with self.assertRaises(StockPoolStressError):
            _pool(expected_universe=())


class StockPoolStressKindTests(unittest.TestCase):
    """The pre-registered scenario kinds."""

    def test_three_kinds(self) -> None:
        self.assertEqual(
            tuple(StockPoolStressKind),
            (
                StockPoolStressKind.UNKNOWN_HISTORY,
                StockPoolStressKind.INSUFFICIENT_DELISTING_COVERAGE,
                StockPoolStressKind.SHRINKING_UNIVERSE,
            ),
        )
        self.assertEqual(StockPoolStressKind.UNKNOWN_HISTORY.value, "unknown_history")


class StockPoolStressConfigTests(unittest.TestCase):
    """The pre-registered conservative scenarios."""

    def test_valid_config(self) -> None:
        stress = default_stock_pool_stresses()[0]
        self.assertEqual(stress.version, "pool-unknown-history")
        self.assertIs(stress.kind, StockPoolStressKind.UNKNOWN_HISTORY)

    def test_default_range(self) -> None:
        stresses = default_stock_pool_stresses()
        self.assertEqual(len(stresses), 3)
        self.assertEqual(
            [s.version for s in stresses],
            [
                "pool-unknown-history",
                "pool-insufficient-delisting",
                "pool-shrinking-universe",
            ],
        )

    def test_fingerprint_rederivable_stable(self) -> None:
        stress = default_stock_pool_stresses()[0]
        self.assertEqual(stress.fingerprint, stock_pool_stress_config_fingerprint(stress))
        self.assertEqual(stress.fingerprint, default_stock_pool_stresses()[0].fingerprint)

    def test_empty_version_rejected(self) -> None:
        with self.assertRaises(StockPoolStressError):
            build_stock_pool_stress_config(version="", kind=StockPoolStressKind.UNKNOWN_HISTORY)

    def test_readable(self) -> None:
        self.assertIn("pool-unknown-history", default_stock_pool_stresses()[0].readable())


class StockPoolStressInputTests(unittest.TestCase):
    """The injected stock-pool context."""

    def test_valid(self) -> None:
        pool = _pool()
        self.assertEqual(len(pool.expected_universe), 5)

    def test_duplicate_universe_rejected(self) -> None:
        with self.assertRaises(StockPoolStressError):
            _pool(expected_universe=("AAPL", "AAPL"))


class StockPoolStressScenarioResultTests(unittest.TestCase):
    """The quantified scenario and its consistency invariants."""

    def test_valid_quantifiable(self) -> None:
        scenario = _scenario()
        self.assertTrue(scenario.quantifiable)
        self.assertFalse(scenario.blocked)
        self.assertIs(scenario.conclusion_severity, CoverageSeverity.WARNING)

    def test_valid_blocked(self) -> None:
        scenario = _scenario(
            stress_config=default_stock_pool_stresses()[0],
            pool=_pool(historical_known=False),
        )
        self.assertFalse(scenario.quantifiable)
        self.assertTrue(scenario.blocked)
        self.assertIs(scenario.conclusion_severity, CoverageSeverity.NOT_QUALIFIED)
        self.assertIn("cannot be quantified", scenario.blocked_reason)

    def test_blocked_with_coverage_rejected(self) -> None:
        with self.assertRaises(StockPoolStressError):
            _scenario_direct(
                quantifiable=False,
                blocked=True,
                blocked_reason="cannot be quantified",
                coverage_pct=40.0,
                impact_pct=60.0,
            )

    def test_blocked_without_reason_rejected(self) -> None:
        with self.assertRaises(StockPoolStressError):
            _scenario_direct(
                quantifiable=False,
                blocked=True,
                blocked_reason=None,
                coverage_pct=None,
                impact_pct=None,
            )

    def test_quantifiable_with_blocked_reason_rejected(self) -> None:
        with self.assertRaises(StockPoolStressError):
            _scenario_direct(blocked_reason="boom")

    def test_covered_exceeds_expected_rejected(self) -> None:
        with self.assertRaises(StockPoolStressError):
            _scenario_direct(covered_count=99)

    def test_readable(self) -> None:
        self.assertIn("pool-unknown-history", _scenario().readable())


class QuantifyStockPoolStressTests(unittest.TestCase):
    """The three conservative scenarios quantified on the pool."""

    def test_unknown_history_quantifiable(self) -> None:
        scenario = _scenario()
        self.assertEqual(scenario.covered_count, 2)
        self.assertEqual(scenario.expected_count, 5)
        self.assertEqual(scenario.active_count, 2)
        self.assertAlmostEqual(scenario.coverage_pct, 40.0, places=6)
        self.assertAlmostEqual(scenario.impact_pct, 60.0, places=6)
        self.assertEqual(scenario.missing_symbols, ("GONE", "LOST", "MISSING"))

    def test_unknown_history_blocked(self) -> None:
        scenario = _scenario(
            stress_config=default_stock_pool_stresses()[0],
            pool=_pool(historical_known=False),
        )
        self.assertTrue(scenario.blocked)
        self.assertIsNone(scenario.coverage_pct)
        self.assertIsNone(scenario.impact_pct)
        self.assertIn("historical constituents are unknown", scenario.blocked_reason)

    def test_insufficient_delisting_coverage(self) -> None:
        scenario = _scenario(stress_config=default_stock_pool_stresses()[1])
        # GONE and LOST have memberships (delisted but covered); MISSING has none.
        self.assertEqual(scenario.covered_count, 4)
        self.assertAlmostEqual(scenario.coverage_pct, 80.0, places=6)
        self.assertAlmostEqual(scenario.impact_pct, 20.0, places=6)
        self.assertEqual(scenario.missing_symbols, ("MISSING",))
        self.assertIs(scenario.conclusion_severity, CoverageSeverity.WARNING)

    def test_shrinking_universe(self) -> None:
        scenario = _scenario(stress_config=default_stock_pool_stresses()[2])
        self.assertEqual(scenario.covered_count, 2)
        self.assertAlmostEqual(scenario.coverage_pct, 40.0, places=6)
        self.assertEqual(scenario.missing_symbols, ("GONE", "LOST", "MISSING"))

    def test_full_pool_clean(self) -> None:
        scenario = _scenario(
            stress_config=default_stock_pool_stresses()[2],
            pool=_full_pool(),
        )
        self.assertAlmostEqual(scenario.coverage_pct, 100.0, places=6)
        self.assertEqual(scenario.missing_symbols, ())
        self.assertIsNone(scenario.conclusion_severity)

    def test_missing_symbols_preserved(self) -> None:
        scenario = _scenario(stress_config=default_stock_pool_stresses()[1])
        self.assertEqual(scenario.missing_count, 1)

    def test_report_context(self) -> None:
        scenario = _scenario()
        self.assertEqual(scenario.dataset_fingerprint, _FINGERPRINT)
        self.assertEqual(scenario.code_version, "1.0.0")
        self.assertIs(scenario.market, Market.US)


class ReportTests(unittest.TestCase):
    """The stressed pool outcomes across the pre-registered range."""

    def test_report_has_three_scenarios(self) -> None:
        report = compute_stock_pool_stress_report(
            _oos_run(),
            pool=_pool(),
        )
        self.assertEqual(len(report), 3)

    def test_scenario_lookup(self) -> None:
        report = compute_stock_pool_stress_report(
            _oos_run(),
            pool=_pool(),
        )
        self.assertIsNotNone(report.scenario("pool-insufficient-delisting"))
        self.assertIsNone(report.scenario("nope"))

    def test_unknown_history_blocks_report(self) -> None:
        report = compute_stock_pool_stress_report(
            _oos_run(),
            pool=_pool(historical_known=False),
        )
        self.assertTrue(report.blocked)
        self.assertIs(report.conclusion_severity, CoverageSeverity.NOT_QUALIFIED)

    def test_coverage_gap_warns(self) -> None:
        report = compute_stock_pool_stress_report(
            _oos_run(),
            pool=_pool(),
        )
        self.assertFalse(report.blocked)
        self.assertIs(report.conclusion_severity, CoverageSeverity.WARNING)

    def test_full_pool_clean_report(self) -> None:
        report = compute_stock_pool_stress_report(
            _oos_run(),
            pool=_full_pool(),
        )
        self.assertFalse(report.blocked)
        self.assertIsNone(report.conclusion_severity)

    def test_readable(self) -> None:
        report = compute_stock_pool_stress_report(
            _oos_run(),
            pool=_pool(),
        )
        self.assertIn("3 stock pool stress scenario(s)", report.readable())


class FingerprintTests(unittest.TestCase):
    """Stable, re-derivable, sensitive stock-pool stress fingerprints."""

    def test_config_sha256_rederivable(self) -> None:
        stress = default_stock_pool_stresses()[0]
        self.assertEqual(len(stress.fingerprint), 64)
        int(stress.fingerprint, 16)
        self.assertEqual(stress.fingerprint, stock_pool_stress_config_fingerprint(stress))

    def test_config_changes_with_kind(self) -> None:
        base = default_stock_pool_stresses()[0]
        other = build_stock_pool_stress_config(
            version="v1",
            source="pre-registered",
            kind=StockPoolStressKind.SHRINKING_UNIVERSE,
        )
        self.assertNotEqual(base.fingerprint, other.fingerprint)

    def test_scenario_sha256_rederivable(self) -> None:
        scenario = _scenario()
        self.assertEqual(len(scenario.fingerprint), 64)
        int(scenario.fingerprint, 16)
        self.assertEqual(scenario.fingerprint, stock_pool_stress_fingerprint(scenario))

    def test_scenario_stable(self) -> None:
        self.assertEqual(_scenario().fingerprint, _scenario().fingerprint)

    def test_scenario_changes_with_pool(self) -> None:
        self.assertNotEqual(
            _scenario().fingerprint,
            _scenario(pool=_full_pool()).fingerprint,
        )

    def test_scenario_changes_with_stress(self) -> None:
        self.assertNotEqual(
            _scenario().fingerprint,
            _scenario(stress_config=default_stock_pool_stresses()[1]).fingerprint,
        )

    def test_json_excludes_fingerprint_and_key_sorted(self) -> None:
        scenario = _scenario()
        serialized = stock_pool_stress_json(scenario)
        self.assertNotIn('"fingerprint"', serialized)
        self.assertIn('"version":"pool-unknown-history"', serialized)
        self.assertIn('"covered_count":2', serialized)
        self.assertIn('"blocked":false', serialized)
        payload = json.loads(serialized)
        self.assertEqual(
            serialized,
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        )

    def test_report_fingerprint_rederivable(self) -> None:
        report = compute_stock_pool_stress_report(
            _oos_run(),
            pool=_pool(),
        )
        self.assertEqual(len(report.fingerprint), 64)
        self.assertEqual(report.fingerprint, stock_pool_stress_report_fingerprint(report))
