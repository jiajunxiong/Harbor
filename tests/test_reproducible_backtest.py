"""Reproducible backtest integration tests (MVP 2 / SP 2.82).

Verifies that the end-to-end backtest runner (SP 2.51) is fully reproducible:
with fixed Mock data, a fixed configuration, a fixed clock and a fixed random
seed, running the SAME backtest twice produces COMPLETELY identical artifacts
(完全相同产物). Equality is asserted at three independent levels:

- the raw traces (net-value series and reconciliation);
- the exported SP 2.58 JSON-safe artifacts (deep dictionary equality);
- the SP 2.62 consistency check (``compare_artifacts`` reports consistent and
  matching replay fingerprints) and the SP 2.61 replay manifests.

Control tests then prove the equality is meaningful: changing the initial
capital, code version, data cutoff, random seed or the fixed selection IS
detected by the consistency check / manifest fingerprint, so a reproducible
run is not accidentally hiding real differences.

The suite is self-contained; no database is required.
"""

import unittest
from datetime import date

from harbor.core.backtest_config import (
    BacktestConfig,
    MarketQuota,
    RebalanceFrequency,
    RiskConfig,
)
from harbor.core.backtest_domain import Currency, Market
from harbor.core.backtest_interfaces import DailyQuote
from harbor.core.backtest_runner import BacktestTrace, MockUniverse, run_end_to_end_backtest
from harbor.core.consistency_check import compare_artifacts
from harbor.core.replay_manifest import build_replay_manifest, manifest_from_artifact
from harbor.core.result_export import export_run_to_dict
from harbor.core.target_weight import TargetWeightConfig, WeightingMethod
from harbor.core.trading_calendar import MarketTradingCalendar

HKD = Currency.HKD
USD = Currency.USD
HK = Market.HK
US = Market.US

_DAYS = (
    date(2024, 1, 2),
    date(2024, 1, 3),
    date(2024, 1, 4),
    date(2024, 1, 5),
    date(2024, 1, 8),
)
_REBALANCE_DAY = _DAYS[0]


def _calendar() -> MarketTradingCalendar:
    """Return a fixed weekday calendar with no holidays."""
    return MarketTradingCalendar({HK: frozenset(), US: frozenset()})


def _quote(
    *,
    market: Market,
    symbol: str,
    day: date,
    close: float,
    volume: int = 1_000_000,
) -> DailyQuote:
    return DailyQuote(
        market=market,
        symbol=symbol,
        day=day,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        adjusted_close=close,
    )


def _fixed_quotes(
    *,
    market: Market,
    prices: dict[str, float],
) -> dict[tuple[Market, str], dict[date, DailyQuote]]:
    """Return fixed per-symbol flat-close quote maps over the five days."""
    return {
        (market, symbol): {
            day: _quote(market=market, symbol=symbol, day=day, close=price) for day in _DAYS
        }
        for symbol, price in prices.items()
    }


def _hk_quotes() -> dict[tuple[Market, str], dict[date, DailyQuote]]:
    return _fixed_quotes(market=HK, prices={"0001.HK": 50.0, "0002.HK": 20.0})


def _us_quotes() -> dict[tuple[Market, str], dict[date, DailyQuote]]:
    return _fixed_quotes(market=US, prices={"AAPL": 100.0, "MSFT": 200.0})


def _fx() -> dict[tuple[Currency, Currency], dict[date, float]]:
    return {(USD, HKD): {day: 7.8 for day in _DAYS}}


def _config(
    *,
    markets: tuple[Market, ...],
    quotas: tuple[MarketQuota, ...],
    base: Currency,
    initial_capital: float = 1_000_000.0,
) -> BacktestConfig:
    return BacktestConfig(
        markets=markets,
        market_quotas=quotas,
        start_date=_DAYS[0],
        end_date=_DAYS[-1],
        base_currency=base,
        rebalance_frequency=RebalanceFrequency.QUARTERLY,
        initial_capital=initial_capital,
        risk=RiskConfig(max_position_pct=1.0, max_market_pct=1.0, min_cash_pct=0.0),
    )


def _weighting(cash_weight: float = 0.05) -> TargetWeightConfig:
    return TargetWeightConfig(
        method=WeightingMethod.EQUAL,
        cash_weight=cash_weight,
        decimal_places=4,
    )


def _hk_setup() -> tuple[BacktestConfig, MockUniverse]:
    config = _config(
        markets=(HK,),
        quotas=(MarketQuota(market=HK, target_count=2, weight=1.0),),
        base=HKD,
    )
    universe = MockUniverse(
        calendar=_calendar(),
        quotes=_hk_quotes(),
        selections={(HK, _REBALANCE_DAY): ("0001.HK", "0002.HK")},
    )
    return config, universe


def _us_setup() -> tuple[BacktestConfig, MockUniverse]:
    config = _config(
        markets=(US,),
        quotas=(MarketQuota(market=US, target_count=2, weight=1.0),),
        base=USD,
    )
    universe = MockUniverse(
        calendar=_calendar(),
        quotes=_us_quotes(),
        selections={(US, _REBALANCE_DAY): ("AAPL", "MSFT")},
    )
    return config, universe


def _cross_market_setup() -> tuple[BacktestConfig, MockUniverse]:
    config = _config(
        markets=(HK, US),
        quotas=(
            MarketQuota(market=HK, target_count=1, weight=0.5),
            MarketQuota(market=US, target_count=1, weight=0.5),
        ),
        base=HKD,
    )
    universe = MockUniverse(
        calendar=_calendar(),
        quotes={**_hk_quotes(), **_us_quotes()},
        fx_rates=_fx(),
        selections={
            (HK, _REBALANCE_DAY): ("0001.HK",),
            (US, _REBALANCE_DAY): ("AAPL",),
        },
    )
    return config, universe


def _run(
    config: BacktestConfig,
    universe: MockUniverse,
    *,
    run_id: str = "run-1",
    code_version: str = "1.0.0",
    data_cutoff: date | None = None,
) -> BacktestTrace:
    return run_end_to_end_backtest(
        run_id=run_id,
        config=config,
        universe=universe,
        data_cutoff=data_cutoff,
        code_version=code_version,
        weighting=_weighting(),
    )


def _artifact(trace: BacktestTrace) -> dict[str, object]:
    return export_run_to_dict(trace=trace, schema_version="1.0")


class ReproducibleRunTests(unittest.TestCase):
    """The same fixed inputs always produce identical artifacts."""

    def test_hk_artifacts_identical_across_runs(self) -> None:
        config, universe = _hk_setup()
        first = _run(config, universe)
        second = _run(config, universe)
        self.assertTrue(first.succeeded)
        self.assertEqual(first.reconcile_all(), ())
        self.assertEqual(first.net_values(), second.net_values())
        self.assertEqual(_artifact(first), _artifact(second))

    def test_us_artifacts_identical_across_runs(self) -> None:
        config, universe = _us_setup()
        first = _run(config, universe)
        second = _run(config, universe)
        self.assertTrue(first.succeeded)
        self.assertEqual(first.reconcile_all(), ())
        self.assertEqual(first.net_values(), second.net_values())
        self.assertEqual(_artifact(first), _artifact(second))

    def test_cross_market_artifacts_identical_across_runs(self) -> None:
        config, universe = _cross_market_setup()
        first = _run(config, universe)
        second = _run(config, universe)
        self.assertTrue(first.succeeded)
        self.assertEqual(first.reconcile_all(), ())
        self.assertEqual(first.net_values(), second.net_values())
        self.assertEqual(_artifact(first), _artifact(second))

    def test_consistency_check_reports_consistent(self) -> None:
        config, universe = _cross_market_setup()
        report = compare_artifacts(
            _artifact(_run(config, universe)),
            _artifact(_run(config, universe)),
        )
        self.assertTrue(report.fingerprints_match)
        self.assertEqual(report.issues, ())
        self.assertTrue(report.consistent)

    def test_replay_manifests_match_across_runs(self) -> None:
        config, universe = _cross_market_setup()
        first = _run(config, universe)
        second = _run(config, universe)
        manifest_a = build_replay_manifest(
            run_id=first.run_id,
            config=first.config,
            identity=first.identity,
            fx_source="mock",
            calendar_version="v1",
            random_seed=42,
        )
        manifest_b = build_replay_manifest(
            run_id=second.run_id,
            config=second.config,
            identity=second.identity,
            fx_source="mock",
            calendar_version="v1",
            random_seed=42,
        )
        self.assertEqual(manifest_a.fingerprint(), manifest_b.fingerprint())
        # The manifest reconstructed from the exported artifact matches too.
        from_artifact = manifest_from_artifact(
            _artifact(first),
            fx_source="mock",
            calendar_version="v1",
            random_seed=42,
        )
        self.assertEqual(from_artifact.fingerprint(), manifest_a.fingerprint())


class DeterminismControlTests(unittest.TestCase):
    """Real differences are detected, so reproducibility is meaningful."""

    def test_different_initial_capital_is_detected(self) -> None:
        config_a, universe = _hk_setup()
        config_b = _config(
            markets=(HK,),
            quotas=(MarketQuota(market=HK, target_count=2, weight=1.0),),
            base=HKD,
            initial_capital=2_000_000.0,
        )
        artifact_a = _artifact(_run(config_a, universe))
        artifact_b = _artifact(_run(config_b, universe))
        self.assertNotEqual(artifact_a, artifact_b)
        report = compare_artifacts(artifact_a, artifact_b)
        self.assertFalse(report.fingerprints_match)
        self.assertFalse(report.consistent)

    def test_different_code_version_is_detected(self) -> None:
        config, universe = _cross_market_setup()
        artifact_a = _artifact(_run(config, universe, code_version="1.0.0"))
        artifact_b = _artifact(_run(config, universe, code_version="2.0.0"))
        report = compare_artifacts(artifact_a, artifact_b)
        self.assertFalse(report.fingerprints_match)
        self.assertFalse(report.consistent)

    def test_different_data_cutoff_is_detected(self) -> None:
        config, universe = _cross_market_setup()
        artifact_a = _artifact(_run(config, universe, data_cutoff=None))
        artifact_b = _artifact(_run(config, universe, data_cutoff=date(2024, 1, 4)))
        report = compare_artifacts(artifact_a, artifact_b)
        self.assertFalse(report.fingerprints_match)
        self.assertFalse(report.consistent)

    def test_random_seed_changes_manifest_fingerprint(self) -> None:
        config, universe = _cross_market_setup()
        trace = _run(config, universe)
        seed_a = build_replay_manifest(
            run_id=trace.run_id,
            config=trace.config,
            identity=trace.identity,
            random_seed=1,
        )
        seed_b = build_replay_manifest(
            run_id=trace.run_id,
            config=trace.config,
            identity=trace.identity,
            random_seed=2,
        )
        self.assertNotEqual(seed_a.fingerprint(), seed_b.fingerprint())

    def test_different_fixed_selection_is_detected(self) -> None:
        config, _unused = _hk_setup()
        one_symbol = MockUniverse(
            calendar=_calendar(),
            quotes=_hk_quotes(),
            selections={(HK, _REBALANCE_DAY): ("0001.HK",)},
        )
        other_symbol = MockUniverse(
            calendar=_calendar(),
            quotes=_hk_quotes(),
            selections={(HK, _REBALANCE_DAY): ("0002.HK",)},
        )
        artifact_a = _artifact(_run(config, one_symbol))
        artifact_b = _artifact(_run(config, other_symbol))
        self.assertNotEqual(artifact_a, artifact_b)
        report = compare_artifacts(artifact_a, artifact_b)
        self.assertFalse(report.consistent)


if __name__ == "__main__":
    unittest.main()
