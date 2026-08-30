"""Paper example configuration tests (MVP 4 / SP 4.88).

Verifies that the conservative HK / US / cross-market paper example configs
load, validate and carry the expected research-nature and stop conditions.
"""

import unittest
from pathlib import Path

from harbor.core.backtest_domain import Currency, Market
from harbor.core.paper_config import PaperConfig, PaperRebalanceFrequency, PaperRunMode
from harbor.core.paper_config_loader import config_hash, load_paper_config

_EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "configs" / "paper"


class PaperExampleConfigTests(unittest.TestCase):
    """The example paper configs (SP 4.88)."""

    def test_hk_example_loads(self) -> None:
        config = load_paper_config(_EXAMPLES / "hk_paper.yaml")
        self.assertIsInstance(config, PaperConfig)
        self.assertEqual(config.markets, (Market.HK,))
        self.assertEqual(config.base_currency, Currency.HKD)
        self.assertEqual(config.currencies, (Currency.HKD,))
        self.assertEqual(config.rebalance_frequency, PaperRebalanceFrequency.QUARTERLY)
        self.assertEqual(config.run_mode, PaperRunMode.MANUAL)
        self.assertEqual(config.risk.max_position_pct, 0.005)
        self.assertEqual(config.stop.max_drawdown_pct, 0.10)

    def test_us_example_loads(self) -> None:
        config = load_paper_config(_EXAMPLES / "us_paper.yaml")
        self.assertEqual(config.markets, (Market.US,))
        self.assertEqual(config.base_currency, Currency.USD)
        self.assertEqual(config.currencies, (Currency.USD,))

    def test_cross_market_example_loads(self) -> None:
        config = load_paper_config(_EXAMPLES / "cross_market_paper.yaml")
        self.assertEqual(config.markets, (Market.HK, Market.US))
        self.assertEqual(config.base_currency, Currency.HKD)
        self.assertEqual(config.currencies, (Currency.HKD, Currency.USD))

    def test_examples_have_stable_config_hash(self) -> None:
        for name in ("hk_paper.yaml", "us_paper.yaml", "cross_market_paper.yaml"):
            first = config_hash(load_paper_config(_EXAMPLES / name))
            second = config_hash(load_paper_config(_EXAMPLES / name))
            self.assertEqual(first, second, f"config hash unstable for {name}")

    def test_examples_are_research_only(self) -> None:
        for name in ("hk_paper.yaml", "us_paper.yaml", "cross_market_paper.yaml"):
            description = load_paper_config(_EXAMPLES / name).description
            self.assertIn("research only", description)
