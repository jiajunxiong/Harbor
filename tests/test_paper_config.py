"""Paper-config model tests (MVP 4 / SP 4.2).

Verifies the frozen Pydantic paper configuration: the risk parameters with
their 0.5% / 5% / 20% caps and strictly-increasing drawdown tiers (SP 4.31),
the stop conditions, the multi-currency ledger, the running mode and the
stable canonical serialization used for hashing (SP 4.9).
"""

import json
import unittest

import pydantic

from harbor.core.backtest_domain import Currency, Market
from harbor.core.paper_config import (
    PaperConfig,
    PaperRebalanceFrequency,
    PaperRiskConfig,
    PaperStopConfig,
)
from harbor.core.paper_domain import PaperRunMode


def _config(**overrides: object) -> PaperConfig:
    """Return a valid paper config with overridable fields."""
    fields: dict[str, object] = {
        "strategy": "mvp3-qualified",
        "strategy_version": "1.0.0",
        "description": "research only",
        "markets": (Market.HK, Market.US),
        "base_currency": Currency.HKD,
        "currencies": (Currency.HKD, Currency.USD),
        "initial_capital": 1_000_000.0,
        "rebalance_frequency": PaperRebalanceFrequency.QUARTERLY,
        "run_mode": PaperRunMode.MANUAL,
        "calendar_version": "hkex-2026",
        "fx_source": "mock",
        "random_seed": 42,
    }
    fields.update(overrides)
    return PaperConfig(**fields)  # type: ignore[arg-type]


class PaperRebalanceFrequencyTests(unittest.TestCase):
    """The rebalance-frequency vocabulary is fixed."""

    def test_values(self) -> None:
        self.assertEqual(
            [freq.value for freq in PaperRebalanceFrequency],
            ["QUARTERLY", "MONTHLY", "ANNUAL"],
        )


class PaperRiskConfigTests(unittest.TestCase):
    """The pre-registered risk parameters (SP 4.31)."""

    def test_defaults(self) -> None:
        risk = PaperRiskConfig()
        self.assertEqual(risk.max_position_pct, 0.005)
        self.assertEqual(risk.max_single_stock_pct, 0.05)
        self.assertEqual(risk.max_industry_pct, 0.20)
        self.assertEqual(risk.drawdown_warn_pct, 0.05)
        self.assertEqual(risk.drawdown_defend_pct, 0.08)
        self.assertEqual(risk.drawdown_circuit_pct, 0.10)

    def test_drawdown_tiers_strictly_increasing(self) -> None:
        with self.assertRaises(pydantic.ValidationError):
            PaperRiskConfig(drawdown_warn_pct=0.10, drawdown_defend_pct=0.08)
        with self.assertRaises(pydantic.ValidationError):
            PaperRiskConfig(drawdown_defend_pct=0.10, drawdown_circuit_pct=0.10)

    def test_bounds(self) -> None:
        with self.assertRaises(pydantic.ValidationError):
            PaperRiskConfig(max_position_pct=1.5)
        with self.assertRaises(pydantic.ValidationError):
            PaperRiskConfig(max_single_stock_pct=-0.1)


class PaperStopConfigTests(unittest.TestCase):
    """The stop conditions (SP 4.2)."""

    def test_defaults(self) -> None:
        stop = PaperStopConfig()
        self.assertIsNone(stop.max_days)
        self.assertIsNone(stop.max_drawdown_pct)

    def test_positive_limits(self) -> None:
        with self.assertRaises(pydantic.ValidationError):
            PaperStopConfig(max_days=0)
        with self.assertRaises(pydantic.ValidationError):
            PaperStopConfig(max_drawdown_pct=0.0)


class PaperConfigTests(unittest.TestCase):
    """The top-level paper configuration (SP 4.2)."""

    def test_valid_config(self) -> None:
        config = _config()
        self.assertEqual(config.strategy, "mvp3-qualified")
        self.assertEqual(config.markets, (Market.HK, Market.US))
        self.assertEqual(config.run_mode, PaperRunMode.MANUAL)
        self.assertEqual(config.initial_capital, 1_000_000.0)
        self.assertEqual(config.random_seed, 42)

    def test_identity_required(self) -> None:
        with self.assertRaises(pydantic.ValidationError):
            _config(strategy="  ")
        with self.assertRaises(pydantic.ValidationError):
            _config(strategy_version="")

    def test_market_scope(self) -> None:
        with self.assertRaises(pydantic.ValidationError):
            _config(markets=())
        with self.assertRaises(pydantic.ValidationError):
            _config(markets=(Market.HK, Market.HK))

    def test_ledger_currencies(self) -> None:
        with self.assertRaises(pydantic.ValidationError):
            _config(currencies=())
        with self.assertRaises(pydantic.ValidationError):
            _config(currencies=(Currency.HKD, Currency.HKD))
        with self.assertRaises(pydantic.ValidationError):
            _config(base_currency=Currency.USD, currencies=(Currency.HKD,))

    def test_capital_and_seed(self) -> None:
        with self.assertRaises(pydantic.ValidationError):
            _config(initial_capital=0.0)
        with self.assertRaises(pydantic.ValidationError):
            _config(random_seed=-1)

    def test_frozen(self) -> None:
        config = _config()
        with self.assertRaises(pydantic.ValidationError):
            config.strategy = "other"  # type: ignore[misc]


class CanonicalJsonTests(unittest.TestCase):
    """The stable canonical serialization used for hashing (SP 4.2 / 4.9)."""

    def test_stable_across_equal(self) -> None:
        self.assertEqual(_config().canonical_json(), _config().canonical_json())

    def test_key_sorted(self) -> None:
        parsed = json.loads(_config().canonical_json())
        self.assertEqual(parsed["strategy"], "mvp3-qualified")
        self.assertEqual(parsed["markets"], ["HK", "US"])
        self.assertEqual(parsed["risk"]["max_position_pct"], 0.005)
        self.assertEqual(parsed["run_mode"], "MANUAL")

    def test_changes_with_risk_and_identity(self) -> None:
        base = _config().canonical_json()
        self.assertNotEqual(base, _config(strategy_version="2.0.0").canonical_json())
        self.assertNotEqual(
            base,
            _config(risk=PaperRiskConfig(max_position_pct=0.01)).canonical_json(),
        )

    def test_changes_with_run_mode_and_ledger(self) -> None:
        base = _config().canonical_json()
        self.assertNotEqual(base, _config(run_mode=PaperRunMode.AUTO).canonical_json())
        self.assertNotEqual(base, _config(currencies=(Currency.HKD,)).canonical_json())

    def test_round_trip_preserves_hash(self) -> None:
        config = _config()
        rebuilt = PaperConfig.model_validate_json(config.model_dump_json())
        self.assertEqual(config.canonical_json(), rebuilt.canonical_json())
