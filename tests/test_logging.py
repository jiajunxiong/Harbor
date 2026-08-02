"""Structured logging tests."""

import io
import json
import unittest
from datetime import date
from decimal import Decimal

from harbor.config import LogLevel
from harbor.logging import configure_logging, get_logger


class StructuredLoggingTests(unittest.TestCase):
    """Verify JSON event output and configured level filtering."""

    def test_log_record_contains_structured_fields(self) -> None:
        output = io.StringIO()
        configure_logging(LogLevel.INFO, output)

        get_logger("ingestion").info(
            "ingestion_started",
            extra={
                "market": "HK",
                "price": Decimal("12.50"),
                "trading_date": date(2026, 8, 2),
            },
        )

        record = json.loads(output.getvalue())
        self.assertEqual(record["event"], "ingestion_started")
        self.assertEqual(record["level"], "INFO")
        self.assertEqual(record["logger"], "harbor.ingestion")
        self.assertEqual(record["market"], "HK")
        self.assertEqual(record["price"], "12.50")
        self.assertEqual(record["trading_date"], "2026-08-02")
        self.assertTrue(record["timestamp"].endswith("+00:00"))

    def test_log_level_filters_lower_severity_records(self) -> None:
        output = io.StringIO()
        logger = configure_logging(LogLevel.WARNING, output)

        logger.info("ignored_event")
        logger.warning("quality_warning", extra={"symbol": "AAPL"})

        records = output.getvalue().splitlines()
        self.assertEqual(len(records), 1)
        self.assertEqual(json.loads(records[0])["event"], "quality_warning")

    def test_unknown_log_level_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported log level"):
            configure_logging("TRACE")