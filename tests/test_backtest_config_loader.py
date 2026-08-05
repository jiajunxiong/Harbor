"""Backtest config loader and hashing tests (MVP 2 / SP 2.5)."""

import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from harbor.core.backtest_config import (
    BacktestConfig,
    MarketQuota,
    RebalanceFrequency,
)
from harbor.core.backtest_config_loader import (
    ConfigFormat,
    config_hash,
    load_backtest_config,
    load_backtest_config_from_mapping,
    load_config_hash,
)
from harbor.core.backtest_domain import Currency, Market

_YAML = """\
strategy: shareholder-return
strategy_version: "1.0.0"
markets:
  - HK
market_quotas:
  - market: HK
    target_count: 15
    weight: 1.0
start_date: "2020-01-01"
end_date: "2025-12-31"
base_currency: HKD
rebalance_frequency: quarterly
initial_capital: 1000000
"""

_JSON = json.dumps(
    {
        "strategy": "shareholder-return",
        "strategy_version": "1.0.0",
        "markets": ["HK"],
        "market_quotas": [{"market": "HK", "target_count": 15, "weight": 1.0}],
        "start_date": "2020-01-01",
        "end_date": "2025-12-31",
        "base_currency": "HKD",
        "rebalance_frequency": "quarterly",
        "initial_capital": 1_000_000,
    }
)


def _expected_config() -> BacktestConfig:
    return BacktestConfig(
        strategy="shareholder-return",
        strategy_version="1.0.0",
        markets=(Market.HK,),
        market_quotas=(MarketQuota(market=Market.HK, target_count=15, weight=1.0),),
        start_date=date(2020, 1, 1),
        end_date=date(2025, 12, 31),
        base_currency=Currency.HKD,
        rebalance_frequency=RebalanceFrequency.QUARTERLY,
        initial_capital=1_000_000.0,
    )


class ConfigLoaderTests(unittest.TestCase):
    """Verify YAML/JSON loading and validation."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _write(self, name: str, content: str) -> Path:
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_load_yaml_matches_expected_config(self) -> None:
        path = self._write("strategy.yaml", _YAML)
        self.assertEqual(load_backtest_config(path), _expected_config())

    def test_load_json_matches_yaml(self) -> None:
        yaml_config = load_backtest_config(self._write("strategy.yaml", _YAML))
        json_config = load_backtest_config(self._write("strategy.json", _JSON))
        self.assertEqual(yaml_config, json_config)

    def test_explicit_format_with_unknown_suffix(self) -> None:
        path = self._write("strategy.txt", _YAML)
        config = load_backtest_config(path, fmt=ConfigFormat.YAML)
        self.assertEqual(config, _expected_config())

    def test_unknown_suffix_without_format_is_rejected(self) -> None:
        path = self._write("strategy.txt", _YAML)
        with self.assertRaisesRegex(ValueError, "Cannot infer config format"):
            load_backtest_config(path)

    def test_non_mapping_root_is_rejected(self) -> None:
        path = self._write("list.yaml", "- AAPL\n- MSFT\n")
        with self.assertRaisesRegex(ValueError, "must be a mapping"):
            load_backtest_config(path)

    def test_empty_file_is_rejected(self) -> None:
        path = self._write("empty.yaml", "")
        with self.assertRaisesRegex(ValueError, "must be a mapping"):
            load_backtest_config(path)

    def test_invalid_yaml_raises_value_error(self) -> None:
        path = self._write("bad.yaml", "markets: [unclosed\n")
        with self.assertRaises(ValueError):
            load_backtest_config(path)

    def test_invalid_json_raises_value_error(self) -> None:
        path = self._write("bad.json", "{not json")
        with self.assertRaises(ValueError):
            load_backtest_config(path)

    def test_model_validation_errors_propagate(self) -> None:
        path = self._write(
            "bad-dates.yaml",
            _YAML.replace('start_date: "2020-01-01"', 'start_date: "2026-01-01"'),
        )
        with self.assertRaises(ValidationError):
            load_backtest_config(path)

    def test_quota_weight_validation_applies(self) -> None:
        path = self._write(
            "bad-weight.yaml",
            _YAML.replace("weight: 1.0", "weight: 0.5"),
        )
        with self.assertRaisesRegex(ValidationError, "weights must sum to 1.0"):
            load_backtest_config(path)

    def test_load_from_mapping_matches_file_load(self) -> None:
        config = load_backtest_config_from_mapping(_expected_config().model_dump())
        self.assertEqual(config, _expected_config())


class ConfigHashTests(unittest.TestCase):
    """Verify stable hashing of validated config content."""

    def test_hash_is_stable_across_equal_configs(self) -> None:
        self.assertEqual(config_hash(_expected_config()), config_hash(_expected_config()))

    def test_hash_is_sha256_of_canonical_json(self) -> None:
        expected = hashlib.sha256(_expected_config().canonical_json().encode("utf-8")).hexdigest()
        self.assertEqual(config_hash(_expected_config()), expected)

    def test_hash_changes_when_config_changes(self) -> None:
        changed = _expected_config().model_copy(update={"initial_capital": 2_000_000.0})
        self.assertNotEqual(config_hash(_expected_config()), config_hash(changed))

    def test_round_trip_through_canonical_json_preserves_hash(self) -> None:
        config = _expected_config()
        reloaded = BacktestConfig.model_validate_json(config.model_dump_json())
        self.assertEqual(config_hash(config), config_hash(reloaded))

    def test_load_config_hash_matches_loaded_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "strategy.yaml"
            path.write_text(_YAML, encoding="utf-8")
            self.assertEqual(
                load_config_hash(path),
                config_hash(load_backtest_config(path)),
            )


if __name__ == "__main__":
    unittest.main()
