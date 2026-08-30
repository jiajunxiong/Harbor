"""Paper-config loader and hashing tests (MVP 4 / SP 4.2).

Verifies YAML/JSON loading with validation propagating from the SP 4.2 model,
and the stable config hash derived from the canonical serialization (SP 4.9).
"""

import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from harbor.core.backtest_domain import Currency, Market
from harbor.core.paper_config import PaperConfig, PaperRebalanceFrequency
from harbor.core.paper_config_loader import (
    PaperConfigFormat,
    config_hash,
    load_config_hash,
    load_paper_config,
    load_paper_config_from_mapping,
)
from harbor.core.paper_domain import PaperRunMode

_YAML = """\
strategy: mvp3-qualified
strategy_version: "1.0.0"
description: research only
markets: [HK, US]
base_currency: HKD
currencies: [HKD, USD]
initial_capital: 1000000.0
rebalance_frequency: QUARTERLY
run_mode: MANUAL
calendar_version: hkex-2026
fx_source: mock
random_seed: 42
"""

_JSON = """\
{
  "strategy": "mvp3-qualified",
  "strategy_version": "1.0.0",
  "description": "research only",
  "markets": ["HK", "US"],
  "base_currency": "HKD",
  "currencies": ["HKD", "USD"],
  "initial_capital": 1000000.0,
  "rebalance_frequency": "QUARTERLY",
  "run_mode": "MANUAL",
  "calendar_version": "hkex-2026",
  "fx_source": "mock",
  "random_seed": 42
}
"""


def _expected_config() -> PaperConfig:
    """The config both fixtures should parse to."""
    return PaperConfig(
        strategy="mvp3-qualified",
        strategy_version="1.0.0",
        description="research only",
        markets=(Market.HK, Market.US),
        base_currency=Currency.HKD,
        currencies=(Currency.HKD, Currency.USD),
        initial_capital=1_000_000.0,
        rebalance_frequency=PaperRebalanceFrequency.QUARTERLY,
        run_mode=PaperRunMode.MANUAL,
        calendar_version="hkex-2026",
        fx_source="mock",
        random_seed=42,
    )


class PaperConfigLoaderTests(unittest.TestCase):
    """YAML/JSON loading with validation (SP 4.2)."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self._dir = Path(self._tmp.name)
        (self._dir / "paper.yaml").write_text(_YAML, encoding="utf-8")
        (self._dir / "paper.json").write_text(_JSON, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_load_yaml(self) -> None:
        loaded = load_paper_config(self._dir / "paper.yaml")
        self.assertEqual(loaded, _expected_config())

    def test_load_json_equals_yaml(self) -> None:
        loaded = load_paper_config(self._dir / "paper.json")
        self.assertEqual(loaded, _expected_config())
        self.assertEqual(
            load_paper_config(self._dir / "paper.json").canonical_json(),
            load_paper_config(self._dir / "paper.yaml").canonical_json(),
        )

    def test_explicit_format_with_unknown_suffix(self) -> None:
        path = self._dir / "paper.conf"
        path.write_text(_YAML, encoding="utf-8")
        loaded = load_paper_config(path, PaperConfigFormat.YAML)
        self.assertEqual(loaded, _expected_config())

    def test_unknown_suffix_rejected(self) -> None:
        path = self._dir / "paper.conf"
        path.write_text(_YAML, encoding="utf-8")
        with self.assertRaises(ValueError):
            load_paper_config(path)

    def test_non_mapping_root_rejected(self) -> None:
        path = self._dir / "list.yaml"
        path.write_text("- 1\n- 2\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_paper_config(path)

    def test_invalid_yaml_rejected(self) -> None:
        path = self._dir / "bad.yaml"
        path.write_text(": : :\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_paper_config(path)

    def test_model_validation_propagates(self) -> None:
        path = self._dir / "no_currencies.yaml"
        path.write_text(
            _YAML.replace("currencies: [HKD, USD]", "currencies: []"),
            encoding="utf-8",
        )
        with self.assertRaises(Exception):
            load_paper_config(path)

    def test_from_mapping_equals_file(self) -> None:
        data = json.loads(_JSON)
        self.assertEqual(load_paper_config_from_mapping(data), _expected_config())


class PaperConfigHashTests(unittest.TestCase):
    """The stable config hash (SP 4.9)."""

    def test_hash_stable_and_sha256(self) -> None:
        first = config_hash(_expected_config())
        second = config_hash(_expected_config())
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_hash_equals_sha256_of_canonical_json(self) -> None:
        expected = hashlib.sha256(_expected_config().canonical_json().encode("utf-8")).hexdigest()
        self.assertEqual(config_hash(_expected_config()), expected)

    def test_hash_changes_with_strategy_version(self) -> None:
        self.assertNotEqual(
            config_hash(_expected_config()),
            config_hash(_expected_config().model_copy(update={"strategy_version": "2.0.0"})),
        )

    def test_hash_changes_with_risk(self) -> None:
        from harbor.core.paper_config import PaperRiskConfig

        modified = _expected_config().model_copy(
            update={"risk": PaperRiskConfig(max_position_pct=0.01)}
        )
        self.assertNotEqual(config_hash(_expected_config()), config_hash(modified))

    def test_load_config_hash_matches(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "paper.yaml"
            path.write_text(_YAML, encoding="utf-8")
            self.assertEqual(load_config_hash(path), config_hash(load_paper_config(path)))
