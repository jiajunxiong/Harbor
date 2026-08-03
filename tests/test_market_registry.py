"""Market registry tests."""

import unittest

from harbor.config import MarketTarget
from harbor.core.market_registry import (
    MARKET_CONFIGS,
    CorporateActionType,
    get_market_config,
    validate_provider,
)


class MarketRegistryTests(unittest.TestCase):
    """Verify the market configuration registry contract."""

    def test_registry_covers_both_markets(self) -> None:
        self.assertEqual(set(MARKET_CONFIGS), {MarketTarget.HK, MarketTarget.US})

    def test_hk_config_defines_data_source_and_rules(self) -> None:
        config = get_market_config(MarketTarget.HK)

        self.assertEqual(config.market, MarketTarget.HK)
        self.assertEqual(config.default_provider, "yfinance")
        self.assertIn("yfinance", config.allowed_providers)
        self.assertIn("akshare", config.allowed_providers)
        self.assertEqual(config.currency, "HKD")
        self.assertEqual(config.stock_pool_source, "hkex_universe")
        self.assertEqual(
            config.corporate_action_types,
            frozenset(
                {
                    CorporateActionType.RIGHTS_ISSUE,
                    CorporateActionType.CONSOLIDATION,
                    CorporateActionType.TENDER_OFFER,
                    CorporateActionType.DIVIDEND,
                }
            ),
        )

    def test_us_config_defines_data_source_and_rules(self) -> None:
        config = get_market_config(MarketTarget.US)

        self.assertEqual(config.market, MarketTarget.US)
        self.assertEqual(config.default_provider, "yfinance")
        self.assertIn("yfinance", config.allowed_providers)
        self.assertNotIn("akshare", config.allowed_providers)
        self.assertEqual(config.currency, "USD")
        self.assertEqual(config.stock_pool_source, "sp500_constituents")
        self.assertEqual(
            config.corporate_action_types,
            frozenset(
                {
                    CorporateActionType.SPLIT,
                    CorporateActionType.MERGER,
                    CorporateActionType.SPIN_OFF,
                    CorporateActionType.DIVIDEND,
                }
            ),
        )

    def test_hk_and_us_actions_are_not_mixed(self) -> None:
        hk_actions = get_market_config(MarketTarget.HK).corporate_action_types
        us_actions = get_market_config(MarketTarget.US).corporate_action_types

        self.assertNotIn(CorporateActionType.RIGHTS_ISSUE, us_actions)
        self.assertNotIn(CorporateActionType.CONSOLIDATION, us_actions)
        self.assertNotIn(CorporateActionType.SPLIT, hk_actions)
        self.assertNotIn(CorporateActionType.MERGER, hk_actions)

    def test_validate_provider_accepts_allowed_provider(self) -> None:
        self.assertEqual(
            validate_provider(MarketTarget.HK, "akshare"),
            "akshare",
        )

    def test_validate_provider_rejects_unknown_provider(self) -> None:
        with self.assertRaisesRegex(ValueError, "not supported for HK"):
            validate_provider(MarketTarget.HK, "wind")
