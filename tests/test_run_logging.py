"""Run-log correlation tests (MVP 2 / SP 2.71).

Verifies the pure run-logging module: the correlation context
(``backtest_run_id`` / ``strategy_version`` / ``market`` / ``stage``), the
recursive sensitive-config redaction (never log passwords/tokens/secrets), and
the structured event helper that refuses sensitive or record-colliding field
names. No database is required.
"""

import logging
import unittest
from dataclasses import FrozenInstanceError

from harbor.core.backtest_domain import Market
from harbor.core.backtest_engine import BacktestStage
from harbor.core.run_logging import (
    RunLogContext,
    RunLogError,
    is_sensitive_key,
    log_run_event,
    redact_config,
)


class _CaptureHandler(logging.Handler):
    """Collects emitted records in memory for assertions."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def _logger() -> tuple[logging.Logger, _CaptureHandler]:
    """Return an isolated logger with a capturing handler."""
    logger = logging.getLogger("harbor.test_run_logging")
    logger.handlers.clear()
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    handler = _CaptureHandler()
    logger.addHandler(handler)
    return logger, handler


class RunLogContextTests(unittest.TestCase):
    """Verify the correlation context and its structured fields."""

    def test_as_fields_populated(self) -> None:
        context = RunLogContext(
            run_id="run-1",
            strategy_version="1.2.3",
            market=Market.US,
            stage=BacktestStage.FILL,
        )
        fields = context.as_fields()
        self.assertEqual(fields["backtest_run_id"], "run-1")
        self.assertEqual(fields["strategy_version"], "1.2.3")
        self.assertEqual(fields["market"], "US")
        self.assertEqual(fields["stage"], "fill")

    def test_as_fields_optional_fields_are_none(self) -> None:
        context = RunLogContext(run_id="run-1", strategy_version="1.2.3")
        fields = context.as_fields()
        self.assertIsNone(fields["market"])
        self.assertIsNone(fields["stage"])

    def test_empty_run_id_rejected(self) -> None:
        with self.assertRaises(RunLogError):
            RunLogContext(run_id="", strategy_version="1.2.3")

    def test_empty_strategy_version_rejected(self) -> None:
        with self.assertRaises(RunLogError):
            RunLogContext(run_id="run-1", strategy_version="")

    def test_frozen(self) -> None:
        context = RunLogContext(run_id="run-1", strategy_version="1.2.3")
        with self.assertRaises(FrozenInstanceError):
            context.run_id = "other"  # type: ignore[misc]

    def test_with_market_and_stage_narrow_the_context(self) -> None:
        base = RunLogContext(run_id="run-1", strategy_version="1.2.3")
        narrowed = base.with_market(Market.HK).with_stage(BacktestStage.VALUATION)
        self.assertEqual(narrowed.market, Market.HK)
        self.assertEqual(narrowed.stage, BacktestStage.VALUATION)
        self.assertEqual(narrowed.run_id, "run-1")
        self.assertEqual(narrowed.strategy_version, "1.2.3")
        self.assertIsNone(base.market)
        self.assertIsNone(base.stage)


class IsSensitiveKeyTests(unittest.TestCase):
    """Verify the sensitive-key classifier (SP 2.71)."""

    def test_sensitive_markers(self) -> None:
        for key in (
            "password",
            "api_key",
            "apiKey",
            "secret",
            "token",
            "credential",
            "DB_PASSWORD",
            "refresh_token",
        ):
            self.assertTrue(is_sensitive_key(key), key)

    def test_non_sensitive_keys(self) -> None:
        for key in (
            "market",
            "run_id",
            "strategy_version",
            "stage",
            "config_hash",
            "symbol",
            "commission_rate",
        ):
            self.assertFalse(is_sensitive_key(key), key)


class RedactConfigTests(unittest.TestCase):
    """Verify config redaction never leaks a sensitive value (SP 2.71)."""

    def test_masks_sensitive_top_level_key(self) -> None:
        redacted = redact_config({"strategy": "x", "api_token": "abc123"})
        self.assertEqual(redacted["api_token"], "<redacted>")
        self.assertEqual(redacted["strategy"], "x")

    def test_masks_nested_and_list_entries(self) -> None:
        config = {
            "cost": {"commission_rate": 0.001, "password": "hunter2"},
            "targets": [{"token": "t"}, {"market": "US"}],
        }
        redacted = redact_config(config)
        self.assertEqual(redacted["cost"]["password"], "<redacted>")
        self.assertEqual(redacted["cost"]["commission_rate"], 0.001)
        self.assertEqual(redacted["targets"][0]["token"], "<redacted>")
        self.assertEqual(redacted["targets"][1]["market"], "US")

    def test_does_not_mutate_input(self) -> None:
        config = {"password": "x", "nested": {"token": "y"}}
        redact_config(config)
        self.assertEqual(config["password"], "x")
        self.assertEqual(config["nested"]["token"], "y")

    def test_leaves_scalars_untouched(self) -> None:
        redacted = redact_config({"market": "US", "initial_capital": 100000.0})
        self.assertEqual(redacted["market"], "US")
        self.assertEqual(redacted["initial_capital"], 100000.0)


class LogRunEventTests(unittest.TestCase):
    """Verify the structured event emitter (SP 2.71)."""

    def test_emits_context_and_extra_fields(self) -> None:
        logger, handler = _logger()
        context = RunLogContext(run_id="run-1", strategy_version="1.2.3", market=Market.US)
        log_run_event(logger, context=context, event="run_started", extra_field="v")
        self.assertEqual(len(handler.records), 1)
        record = handler.records[0]
        self.assertEqual(record.getMessage(), "run_started")
        self.assertEqual(record.backtest_run_id, "run-1")
        self.assertEqual(record.strategy_version, "1.2.3")
        self.assertEqual(record.market, "US")
        self.assertIsNone(record.stage)
        self.assertEqual(record.extra_field, "v")

    def test_emits_stage_narrowed_context(self) -> None:
        logger, handler = _logger()
        context = RunLogContext(run_id="run-1", strategy_version="1.2.3").with_stage(
            BacktestStage.FILL
        )
        log_run_event(logger, context=context, event="stage_started")
        self.assertEqual(handler.records[0].stage, "fill")

    def test_emits_at_requested_level(self) -> None:
        logger, handler = _logger()
        context = RunLogContext(run_id="run-1", strategy_version="1.2.3")
        log_run_event(logger, context=context, event="boom", level=logging.ERROR)
        self.assertEqual(handler.records[0].levelno, logging.ERROR)

    def test_empty_event_rejected(self) -> None:
        logger, _ = _logger()
        context = RunLogContext(run_id="run-1", strategy_version="1.2.3")
        with self.assertRaises(RunLogError):
            log_run_event(logger, context=context, event="")

    def test_non_integer_level_rejected(self) -> None:
        logger, _ = _logger()
        context = RunLogContext(run_id="run-1", strategy_version="1.2.3")
        with self.assertRaises(RunLogError):
            log_run_event(logger, context=context, event="x", level="INFO")  # type: ignore[arg-type]

    def test_sensitive_field_rejected(self) -> None:
        logger, _ = _logger()
        context = RunLogContext(run_id="run-1", strategy_version="1.2.3")
        with self.assertRaisesRegex(RunLogError, "sensitive"):
            log_run_event(logger, context=context, event="x", api_token="abc")

    def test_record_colliding_field_rejected(self) -> None:
        logger, _ = _logger()
        context = RunLogContext(run_id="run-1", strategy_version="1.2.3")
        with self.assertRaisesRegex(RunLogError, "collides"):
            log_run_event(logger, context=context, event="x", message="nope")

    def test_empty_field_name_rejected(self) -> None:
        logger, _ = _logger()
        context = RunLogContext(run_id="run-1", strategy_version="1.2.3")
        with self.assertRaises(RunLogError):
            log_run_event(logger, context=context, event="x", **{"": "v"})


if __name__ == "__main__":
    unittest.main()
