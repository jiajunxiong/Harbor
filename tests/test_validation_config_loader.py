"""Validation config loader and hashing tests (MVP 3 / SP 3.3)."""

import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from pydantic import ValidationError

from harbor.core.backtest_domain import Currency, Market
from harbor.core.validation_config import SplitConfig, ValidationConfig
from harbor.core.validation_config_loader import (
    ValidationConfigFormat,
    config_hash,
    load_config_hash,
    load_validation_config,
    load_validation_config_from_mapping,
)

_YAML = """\
strategy: shareholder-return
strategy_version: "1.0.0"
markets:
  - HK
  - US
base_currency: HKD
split:
  train_start: "2019-01-01"
  train_end: "2021-12-31"
  validation_start: "2022-01-03"
  validation_end: "2022-12-30"
  test_start: "2023-01-02"
  test_end: "2024-12-31"
"""

_JSON = json.dumps(
    {
        "strategy": "shareholder-return",
        "strategy_version": "1.0.0",
        "markets": ["HK", "US"],
        "base_currency": "HKD",
        "split": {
            "train_start": "2019-01-01",
            "train_end": "2021-12-31",
            "validation_start": "2022-01-03",
            "validation_end": "2022-12-30",
            "test_start": "2023-01-02",
            "test_end": "2024-12-31",
        },
    }
)


def _expected_config() -> ValidationConfig:
    return ValidationConfig(
        strategy="shareholder-return",
        strategy_version="1.0.0",
        markets=(Market.HK, Market.US),
        base_currency=Currency.HKD,
        split=SplitConfig(
            train_start=date(2019, 1, 1),
            train_end=date(2021, 12, 31),
            validation_start=date(2022, 1, 3),
            validation_end=date(2022, 12, 30),
            test_start=date(2023, 1, 2),
            test_end=date(2024, 12, 31),
        ),
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
        path = self._write("validation.yaml", _YAML)
        self.assertEqual(load_validation_config(path), _expected_config())

    def test_load_json_matches_yaml(self) -> None:
        yaml_config = load_validation_config(self._write("validation.yaml", _YAML))
        json_config = load_validation_config(self._write("validation.json", _JSON))
        self.assertEqual(yaml_config, json_config)

    def test_explicit_format_with_unknown_suffix(self) -> None:
        path = self._write("validation.txt", _YAML)
        config = load_validation_config(path, fmt=ValidationConfigFormat.YAML)
        self.assertEqual(config, _expected_config())

    def test_unknown_suffix_without_format_is_rejected(self) -> None:
        path = self._write("validation.txt", _YAML)
        with self.assertRaisesRegex(ValueError, "Cannot infer validation config format"):
            load_validation_config(path)

    def test_non_mapping_root_is_rejected(self) -> None:
        path = self._write("list.yaml", "- HK\n- US\n")
        with self.assertRaisesRegex(ValueError, "must be a mapping"):
            load_validation_config(path)

    def test_empty_file_is_rejected(self) -> None:
        path = self._write("empty.yaml", "")
        with self.assertRaisesRegex(ValueError, "must be a mapping"):
            load_validation_config(path)

    def test_invalid_yaml_raises_value_error(self) -> None:
        path = self._write("bad.yaml", "markets: [unclosed\n")
        with self.assertRaises(ValueError):
            load_validation_config(path)

    def test_invalid_json_raises_value_error(self) -> None:
        path = self._write("bad.json", "{not json")
        with self.assertRaises(ValueError):
            load_validation_config(path)

    def test_split_boundary_validation_applies(self) -> None:
        # training ending on the same day validation starts must be rejected.
        path = self._write(
            "overlap.yaml",
            _YAML.replace(
                '  train_end: "2021-12-31"',
                '  train_end: "2022-01-03"',
            ),
        )
        with self.assertRaises(ValidationError):
            load_validation_config(path)

    def test_load_from_mapping_matches_file_load(self) -> None:
        config = load_validation_config_from_mapping(_expected_config().model_dump())
        self.assertEqual(config, _expected_config())


class ConfigHashTests(unittest.TestCase):
    """Verify stable hashing of the frozen split config (SP 3.3)."""

    def test_hash_is_stable_across_equal_configs(self) -> None:
        self.assertEqual(config_hash(_expected_config()), config_hash(_expected_config()))

    def test_hash_is_sha256_of_canonical_json(self) -> None:
        expected = hashlib.sha256(_expected_config().canonical_json().encode("utf-8")).hexdigest()
        self.assertEqual(config_hash(_expected_config()), expected)

    def test_hash_changes_when_split_boundaries_change(self) -> None:
        changed = _expected_config().model_copy(
            update={
                "split": _expected_config().split.model_copy(update={"test_end": date(2024, 6, 30)})
            }
        )
        self.assertNotEqual(config_hash(_expected_config()), config_hash(changed))

    def test_hash_changes_when_base_currency_changes(self) -> None:
        changed = _expected_config().model_copy(update={"base_currency": Currency.USD})
        self.assertNotEqual(config_hash(_expected_config()), config_hash(changed))

    def test_hash_changes_when_strategy_version_changes(self) -> None:
        changed = _expected_config().model_copy(update={"strategy_version": "2.0.0"})
        self.assertNotEqual(config_hash(_expected_config()), config_hash(changed))

    def test_round_trip_through_canonical_json_preserves_hash(self) -> None:
        config = _expected_config()
        reloaded = ValidationConfig.model_validate_json(config.model_dump_json())
        self.assertEqual(config_hash(config), config_hash(reloaded))

    def test_load_config_hash_matches_loaded_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "validation.yaml"
            path.write_text(_YAML, encoding="utf-8")
            self.assertEqual(
                load_config_hash(path),
                config_hash(load_validation_config(path)),
            )


if __name__ == "__main__":
    unittest.main()
