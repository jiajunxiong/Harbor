"""Configuration validation tests."""

import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from harbor.config import LogLevel, MarketTarget, Settings


class SettingsTests(unittest.TestCase):
    """Verify configuration accepts valid values and rejects unsafe startup state."""

    def test_valid_settings_load(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings(
                _env_file=None,
                database_url="postgresql+psycopg://harbor:secret@localhost:5432/harbor",
            )

        self.assertEqual(settings.market_target, MarketTarget.BOTH)
        self.assertEqual(settings.data_provider_hk, "mock")
        self.assertEqual(settings.data_provider_us, "mock")
        self.assertEqual(settings.log_level, LogLevel.INFO)

    def test_log_level_is_normalized(self) -> None:
        with patch.dict(os.environ, {"LOG_LEVEL": "debug"}, clear=True):
            settings = Settings(
                _env_file=None,
                database_url="postgresql+psycopg://harbor:secret@localhost:5432/harbor",
            )

        self.assertEqual(settings.log_level, LogLevel.DEBUG)

    def test_missing_database_url_is_rejected(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValidationError, "(?s)database_url.*Field required"):
                Settings(_env_file=None)

    def test_invalid_market_target_is_rejected(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValidationError):
                Settings(
                    _env_file=None,
                    database_url="postgresql+psycopg://harbor:secret@localhost:5432/harbor",
                    market_target="EU",
                )

    def test_blank_provider_is_rejected(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValidationError, "Data provider must be a non-empty"):
                Settings(
                    _env_file=None,
                    database_url="postgresql+psycopg://harbor:secret@localhost:5432/harbor",
                    data_provider_hk="   ",
                )

    def test_database_url_without_scheme_is_rejected(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValidationError, "DATABASE_URL must include a URI scheme"):
                Settings(_env_file=None, database_url="localhost:5432/harbor")