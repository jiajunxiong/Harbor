"""Example strategy configuration tests (MVP 2 / SP 2.72).

Verifies the shipped example strategy configurations (conservative HK, US and
cross-market quarterly, long-only, 15-20 stocks) load through the SP 2.5
loader, carry a clearly-stated research purpose and conservative assumptions,
and produce stable, distinct config hashes. No database is required.
"""

import unittest
from pathlib import Path

from harbor.core.backtest_config import BacktestConfig, RebalanceFrequency
from harbor.core.backtest_config_loader import config_hash, load_backtest_config
from harbor.core.backtest_domain import Currency, Market

_EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples" / "configs"

_HK = "hk_quarterly.yaml"
_US = "us_quarterly.yaml"
_US_JSON = "us_quarterly.json"
_CROSS = "cross_market_quarterly.yaml"


def _load(name: str) -> BacktestConfig:
    """Load one shipped example configuration."""
    return load_backtest_config(_EXAMPLES_DIR / name)


class HkExampleTests(unittest.TestCase):
    """Verify the HK quarterly example (SP 2.72)."""

    def setUp(self) -> None:
        self.config = _load(_HK)

    def test_loads_as_hk_quarterly_long_only(self) -> None:
        self.assertEqual(self.config.markets, (Market.HK,))
        self.assertEqual(self.config.base_currency, Currency.HKD)
        self.assertEqual(self.config.rebalance_frequency, RebalanceFrequency.QUARTERLY)
        self.assertEqual(len(self.config.market_quotas), 1)
        self.assertEqual(self.config.market_quotas[0].market, Market.HK)
        self.assertEqual(self.config.market_quotas[0].target_count, 15)
        self.assertGreater(self.config.initial_capital, 0)

    def test_conservative_risk_limits(self) -> None:
        self.assertLess(self.config.risk.max_position_pct, 0.2)
        self.assertGreater(self.config.risk.min_cash_pct, 0.0)

    def test_hk_board_lot_rule(self) -> None:
        self.assertEqual(self.config.cost.lot_size, 100)

    def test_research_purpose_documented(self) -> None:
        self.assertIn("research", self.config.description.lower())


class UsExampleTests(unittest.TestCase):
    """Verify the US quarterly example and its JSON twin (SP 2.72)."""

    def setUp(self) -> None:
        self.config = _load(_US)

    def test_loads_as_us_quarterly_long_only(self) -> None:
        self.assertEqual(self.config.markets, (Market.US,))
        self.assertEqual(self.config.base_currency, Currency.USD)
        self.assertEqual(self.config.rebalance_frequency, RebalanceFrequency.QUARTERLY)
        self.assertEqual(self.config.market_quotas[0].target_count, 15)

    def test_us_fractional_and_regulatory_fee(self) -> None:
        self.assertEqual(self.config.cost.lot_size, 1)
        self.assertGreater(self.config.cost.regulatory_fee_rate, 0.0)
        self.assertGreater(self.config.cost.slippage_bps, 0.0)

    def test_json_equivalent_to_yaml(self) -> None:
        yaml_config = _load(_US)
        json_config = _load(_US_JSON)
        self.assertEqual(config_hash(yaml_config), config_hash(json_config))

    def test_research_purpose_documented(self) -> None:
        self.assertIn("research", self.config.description.lower())


class CrossMarketExampleTests(unittest.TestCase):
    """Verify the cross-market (HK+US) example (SP 2.72)."""

    def setUp(self) -> None:
        self.config = _load(_CROSS)

    def test_loads_hk_and_us_with_balanced_quotas(self) -> None:
        self.assertEqual(self.config.markets, (Market.HK, Market.US))
        self.assertEqual(self.config.base_currency, Currency.HKD)
        self.assertEqual(self.config.rebalance_frequency, RebalanceFrequency.QUARTERLY)
        self.assertEqual(len(self.config.market_quotas), 2)
        self.assertEqual(sum(q.target_count for q in self.config.market_quotas), 20)
        self.assertAlmostEqual(sum(q.weight for q in self.config.market_quotas), 1.0, places=6)

    def test_quotas_respect_market_cap(self) -> None:
        for quota in self.config.market_quotas:
            self.assertLessEqual(quota.weight, self.config.risk.max_market_pct)

    def test_research_purpose_documented(self) -> None:
        self.assertIn("research", self.config.description.lower())


class ExampleConfigCollectionTests(unittest.TestCase):
    """Verify cross-cutting guarantees across all shipped examples."""

    _ALL = (_HK, _US, _CROSS, _US_JSON)

    def test_every_example_documents_research_purpose(self) -> None:
        for name in self._ALL:
            with self.subTest(name=name):
                config = _load(name)
                self.assertTrue(config.description.strip(), name)
                self.assertIn("research", config.description.lower(), name)

    def test_every_example_is_quarterly_long_only(self) -> None:
        for name in (_HK, _US, _CROSS):
            with self.subTest(name=name):
                config = _load(name)
                self.assertEqual(config.rebalance_frequency, RebalanceFrequency.QUARTERLY, name)
                self.assertTrue(all(q.target_count > 0 for q in config.market_quotas), name)

    def test_config_hash_is_stable_across_loads(self) -> None:
        self.assertEqual(config_hash(_load(_HK)), config_hash(_load(_HK)))

    def test_examples_hash_distinctly(self) -> None:
        hashes = {config_hash(_load(name)) for name in (_HK, _US, _CROSS)}
        self.assertEqual(len(hashes), 3)


if __name__ == "__main__":
    unittest.main()
