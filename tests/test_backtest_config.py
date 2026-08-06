"""Backtest configuration model tests (MVP 2 / SP 2.4)."""

import unittest
from datetime import date
from typing import Any

from pydantic import ValidationError

from harbor.core.backtest_config import (
    BacktestConfig,
    CostConfig,
    FillConfig,
    FillRule,
    MarketQuota,
    RebalanceFrequency,
    RiskConfig,
)
from harbor.core.backtest_domain import Currency, Market


def _valid_config(**overrides: object) -> BacktestConfig:
    """Return a valid backtest config, overriding any given field."""
    defaults: dict[str, Any] = {
        "markets": (Market.HK,),
        "market_quotas": (MarketQuota(market=Market.HK, target_count=15, weight=1.0),),
        "start_date": date(2020, 1, 1),
        "end_date": date(2025, 12, 31),
        "base_currency": Currency.HKD,
        "rebalance_frequency": RebalanceFrequency.QUARTERLY,
        "initial_capital": 1_000_000.0,
    }
    defaults.update(overrides)
    return BacktestConfig(**defaults)


class DefaultsTests(unittest.TestCase):
    """Verify defaults are applied for optional fields."""

    def test_defaults(self) -> None:
        config = _valid_config()
        self.assertEqual(config.strategy, "shareholder-return")
        self.assertEqual(config.strategy_version, "1.0.0")
        self.assertEqual(config.rebalance_frequency, RebalanceFrequency.QUARTERLY)
        self.assertEqual(config.initial_capital, 1_000_000.0)
        self.assertEqual(config.cost.commission_rate, 0.0005)
        self.assertEqual(config.cost.lot_size, 100)
        self.assertEqual(config.risk.max_position_pct, 0.2)
        self.assertEqual(config.risk.min_cash_pct, 0.0)
        self.assertEqual(config.fill.fill_rule, FillRule.CLOSE)


class DateRangeTests(unittest.TestCase):
    """Verify date-range validation."""

    def test_end_may_equal_start(self) -> None:
        config = _valid_config(
            start_date=date(2020, 1, 1),
            end_date=date(2020, 1, 1),
        )
        self.assertEqual(config.start_date, config.end_date)

    def test_end_before_start_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "end_date must be on or after start_date"):
            _valid_config(
                start_date=date(2025, 1, 1),
                end_date=date(2024, 12, 31),
            )


class MarketAndQuotaTests(unittest.TestCase):
    """Verify market and quota consistency rules."""

    def test_quotas_must_cover_markets(self) -> None:
        with self.assertRaisesRegex(ValidationError, "cover exactly the configured markets"):
            _valid_config(markets=(Market.HK, Market.US))

    def test_duplicate_quota_market_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "at most one quota"):
            _valid_config(
                market_quotas=(
                    MarketQuota(market=Market.HK, target_count=15, weight=0.5),
                    MarketQuota(market=Market.HK, target_count=5, weight=0.5),
                )
            )

    def test_duplicate_markets_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "must not contain duplicates"):
            _valid_config(
                markets=(Market.HK, Market.HK),
                market_quotas=(MarketQuota(market=Market.HK, target_count=15, weight=1.0),),
            )

    def test_weights_must_sum_to_one(self) -> None:
        with self.assertRaisesRegex(ValidationError, "weights must sum to 1.0"):
            _valid_config(
                markets=(Market.HK, Market.US),
                market_quotas=(
                    MarketQuota(market=Market.HK, target_count=15, weight=0.5),
                    MarketQuota(market=Market.US, target_count=10, weight=0.4),
                ),
            )

    def test_two_market_configuration_is_valid(self) -> None:
        config = _valid_config(
            markets=(Market.HK, Market.US),
            market_quotas=(
                MarketQuota(market=Market.HK, target_count=15, weight=0.6),
                MarketQuota(market=Market.US, target_count=10, weight=0.4),
            ),
            base_currency=Currency.HKD,
        )
        self.assertEqual(len(config.market_quotas), 2)

    def test_empty_markets_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "At least one market"):
            _valid_config(markets=())


class FillConfigTests(unittest.TestCase):
    """Verify the fill-rule configuration (SP 2.39)."""

    def test_default_rule_is_close(self) -> None:
        config = _valid_config()
        self.assertEqual(config.fill.fill_rule, FillRule.CLOSE)

    def test_custom_fill_rule(self) -> None:
        config = _valid_config(fill=FillConfig(fill_rule=FillRule.NEXT_OPEN))
        self.assertEqual(config.fill.fill_rule, FillRule.NEXT_OPEN)

    def test_invalid_fill_rule_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            FillConfig(fill_rule="at-open")

    def test_fill_rule_changes_canonical_json(self) -> None:
        close = _valid_config(fill=FillConfig(fill_rule=FillRule.CLOSE))
        open_cfg = _valid_config(fill=FillConfig(fill_rule=FillRule.OPEN))
        self.assertNotEqual(close.canonical_json(), open_cfg.canonical_json())


class FieldValidationTests(unittest.TestCase):
    """Verify numeric field bounds on the config and its nested models."""

    def test_initial_capital_must_be_positive(self) -> None:
        with self.assertRaises(ValidationError):
            _valid_config(initial_capital=0.0)

    def test_target_count_must_be_positive(self) -> None:
        with self.assertRaises(ValidationError):
            _valid_config(
                market_quotas=(MarketQuota(market=Market.HK, target_count=0, weight=1.0),)
            )

    def test_cost_rates_must_be_non_negative(self) -> None:
        with self.assertRaises(ValidationError):
            CostConfig(commission_rate=-0.001)

    def test_lot_size_must_be_positive(self) -> None:
        with self.assertRaises(ValidationError):
            CostConfig(lot_size=0)

    def test_risk_constraints_are_bounded(self) -> None:
        with self.assertRaises(ValidationError):
            RiskConfig(max_position_pct=1.5)
        with self.assertRaises(ValidationError):
            RiskConfig(min_cash_pct=1.0)


class ImmutabilityTests(unittest.TestCase):
    """The configuration is frozen after validation."""

    def test_config_is_immutable(self) -> None:
        config = _valid_config()
        with self.assertRaises(ValidationError):
            config.strategy = "other"

    def test_nested_models_are_immutable(self) -> None:
        config = _valid_config()
        with self.assertRaises(ValidationError):
            config.cost.commission_rate = 0.01


class SerializationTests(unittest.TestCase):
    """Verify deterministic serialization used for run identity (SP 2.5)."""

    def test_canonical_json_is_stable(self) -> None:
        first = _valid_config()
        second = _valid_config()
        self.assertEqual(first.canonical_json(), second.canonical_json())

    def test_json_round_trip_preserves_config(self) -> None:
        config = _valid_config()
        raw = config.model_dump_json()
        restored = BacktestConfig.model_validate_json(raw)
        self.assertEqual(restored, config)

    def test_canonical_json_is_key_sorted(self) -> None:
        config = _valid_config()
        blob = config.canonical_json()
        # Keys appear in sorted order: base_currency before markets, etc.
        self.assertLess(blob.index('"base_currency"'), blob.index('"cost"'))
        self.assertLess(blob.index('"description"'), blob.index('"end_date"'))


if __name__ == "__main__":
    unittest.main()
