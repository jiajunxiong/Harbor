"""Data source configuration switching tests (SP 1.102).

Verifies that ``DATA_PROVIDER_HK`` and ``DATA_PROVIDER_US`` are configured and
resolved independently, that switching a market's provider replaces the resolved
provider class, and (against a live database) that the fetch pipeline re-runs
cleanly and idempotently after a switch.
"""

import os
import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import Engine, create_engine, text

from harbor.config import MarketTarget, Settings
from harbor.core.ingestion import SecuritiesIngestor
from harbor.infrastructure.data_providers.akshare import HKAKShareProvider
from harbor.infrastructure.data_providers.factory import create_provider
from harbor.infrastructure.data_providers.mock import MockProvider
from harbor.infrastructure.data_providers.yfinance import (
    HKYFinanceProvider,
    USYFinanceProvider,
)
from harbor.storage.repositories import Repository

_TEST_DATABASE_URL = os.getenv("HARBOR_TEST_DATABASE_URL")

_DB_BASE = {
    "DATABASE_URL": "postgresql+psycopg://harbor:harbor@localhost:5432/harbor",
}


class ConfigSwitchingTests(unittest.TestCase):
    """Verify per-market data source configuration switching."""

    def test_hk_and_us_providers_load_independently(self) -> None:
        environment = {
            **_DB_BASE,
            "DATA_PROVIDER_HK": "akshare",
            "DATA_PROVIDER_US": "yfinance",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = Settings()
        self.assertEqual(settings.data_provider_hk, "akshare")
        self.assertEqual(settings.data_provider_us, "yfinance")

    def test_switching_hk_config_replaces_provider(self) -> None:
        cases = {
            "mock": MockProvider,
            "akshare": HKAKShareProvider,
            "yfinance": HKYFinanceProvider,
        }
        for provider_name, expected_class in cases.items():
            with self.subTest(provider_name=provider_name):
                environment = {**_DB_BASE, "DATA_PROVIDER_HK": provider_name}
                with patch.dict(os.environ, environment, clear=True):
                    settings = Settings()
                provider = create_provider(MarketTarget.HK, settings.data_provider_hk)
                self.assertIsInstance(provider, expected_class)

    def test_switching_us_config_replaces_provider(self) -> None:
        cases = {
            "mock": MockProvider,
            "yfinance": USYFinanceProvider,
        }
        for provider_name, expected_class in cases.items():
            with self.subTest(provider_name=provider_name):
                environment = {**_DB_BASE, "DATA_PROVIDER_US": provider_name}
                with patch.dict(os.environ, environment, clear=True):
                    settings = Settings()
                provider = create_provider(MarketTarget.US, settings.data_provider_us)
                self.assertIsInstance(provider, expected_class)

    def test_hk_and_us_resolve_with_different_configs(self) -> None:
        environment = {
            **_DB_BASE,
            "DATA_PROVIDER_HK": "akshare",
            "DATA_PROVIDER_US": "yfinance",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = Settings()
        self.assertIsInstance(
            create_provider(MarketTarget.HK, settings.data_provider_hk), HKAKShareProvider
        )
        self.assertIsInstance(
            create_provider(MarketTarget.US, settings.data_provider_us), USYFinanceProvider
        )


@unittest.skipUnless(_TEST_DATABASE_URL, "HARBOR_TEST_DATABASE_URL is not set")
class ProviderSwitchReRunTests(unittest.TestCase):
    """Verify the fetch pipeline re-runs after a data source config switch."""

    _RESET_TABLES = (
        "securities",
        "daily_quotes",
        "dividends",
        "financials",
        "fundamentals",
        "corporate_actions",
        "action_terms",
        "positions",
        "equity_events",
        "adjusted_factors",
        "ingestion_runs",
        "raw_payloads",
        "quality_issues",
    )

    def _reset_database(self, engine: Engine) -> None:
        with engine.begin() as connection:
            connection.execute(
                text(f"TRUNCATE {', '.join(self._RESET_TABLES)} RESTART IDENTITY CASCADE")
            )

    def _fetch_securities(self, market: MarketTarget, provider_name: str) -> int:
        provider = create_provider(market, provider_name)
        engine = create_engine(_TEST_DATABASE_URL)
        with engine.begin() as connection:
            repository = Repository(connection)
            run_id = uuid.uuid4().hex
            repository.create_ingestion_run(
                market.value, run_id, provider_name, datetime.now(timezone.utc)
            )
            return SecuritiesIngestor(repository, run_id=run_id).ingest(provider, market)

    def _security_count(self, market: MarketTarget) -> int:
        engine = create_engine(_TEST_DATABASE_URL)
        with engine.connect() as connection:
            row = connection.execute(
                text("SELECT COUNT(*) FROM securities WHERE market = :market"),
                {"market": market.value},
            ).one()
        return int(row[0])

    def test_securities_fetch_reruns_after_config_switch(self) -> None:
        engine = create_engine(_TEST_DATABASE_URL)
        self._reset_database(engine)

        first = self._fetch_securities(MarketTarget.HK, "mock")
        self.assertEqual(first, 16)
        self.assertEqual(self._security_count(MarketTarget.HK), 16)

        switched = create_provider(MarketTarget.HK, "akshare")
        self.assertIsInstance(switched, HKAKShareProvider)

        second = self._fetch_securities(MarketTarget.HK, "mock")
        self.assertEqual(second, 0)
        self.assertEqual(self._security_count(MarketTarget.HK), 16)

    def test_us_securities_fetch_reruns_after_config_switch(self) -> None:
        engine = create_engine(_TEST_DATABASE_URL)
        self._reset_database(engine)

        first = self._fetch_securities(MarketTarget.US, "mock")
        self.assertEqual(first, 16)
        self.assertEqual(self._security_count(MarketTarget.US), 16)

        switched = create_provider(MarketTarget.US, "yfinance")
        self.assertIsInstance(switched, USYFinanceProvider)

        second = self._fetch_securities(MarketTarget.US, "mock")
        self.assertEqual(second, 0)
        self.assertEqual(self._security_count(MarketTarget.US), 16)
