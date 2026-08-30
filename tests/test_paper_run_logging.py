"""Paper run logging tests (MVP 4 / SP 4.60).

Verifies that structured paper log records carry paper_run_id / market / stage
and never log sensitive configuration values.
"""

import unittest

from harbor.core.backtest_domain import Market
from harbor.core.paper_run_logging import (
    PaperRunLogContext,
    PaperRunLogError,
    PaperStage,
    log_paper_event,
    redact_paper_config,
)


class _RecordingLogger:
    """A minimal duck-typed logger capturing emitted records."""

    def __init__(self) -> None:
        self.records: list[tuple[int, str, dict[str, object]]] = []

    def log(self, level: int, event: str, extra: dict[str, object] | None = None) -> None:
        self.records.append((level, event, extra or {}))


class PaperRunLogContextTests(unittest.TestCase):
    """The correlation context (SP 4.60)."""

    def test_required_fields(self) -> None:
        context = PaperRunLogContext(paper_run_id="paper-1", strategy_version="1.0.0")
        self.assertEqual(
            context.as_fields(),
            {
                "paper_run_id": "paper-1",
                "strategy_version": "1.0.0",
                "market": None,
                "stage": None,
            },
        )

    def test_with_market_and_stage(self) -> None:
        context = (
            PaperRunLogContext(paper_run_id="paper-1", strategy_version="1.0.0")
            .with_market(Market.HK)
            .with_stage(PaperStage.FILL)
        )
        fields = context.as_fields()
        self.assertEqual(fields["market"], "HK")
        self.assertEqual(fields["stage"], "fill")

    def test_missing_identity_rejected(self) -> None:
        with self.assertRaises(PaperRunLogError):
            PaperRunLogContext(paper_run_id="", strategy_version="1.0.0")
        with self.assertRaises(PaperRunLogError):
            PaperRunLogContext(paper_run_id="paper-1", strategy_version="")


class RedactConfigTests(unittest.TestCase):
    """Sensitive configuration is masked (SP 4.60)."""

    def test_redacts_sensitive_keys(self) -> None:
        config = {
            "strategy": "mvp3-qualified",
            "credentials": {"api_key": "secret-123", "strategy": "x"},
            "nested": {"password": "pw", "name": "ok"},
        }
        redacted = redact_paper_config(config)
        # the "credentials" key itself names sensitive content -> masked wholesale
        self.assertEqual(redacted["credentials"], "<redacted>")
        self.assertEqual(redacted["nested"]["password"], "<redacted>")
        self.assertEqual(redacted["nested"]["name"], "ok")
        self.assertEqual(redacted["strategy"], "mvp3-qualified")
        self.assertEqual(config["credentials"]["api_key"], "secret-123")  # input unchanged


class LogPaperEventTests(unittest.TestCase):
    """The structured log emission (SP 4.60)."""

    def test_emits_with_correlation_fields(self) -> None:
        logger = _RecordingLogger()
        context = PaperRunLogContext(paper_run_id="paper-1", strategy_version="1.0.0")
        log_paper_event(logger, context=context, event="order_created", stage_ctx="order-1")
        self.assertEqual(len(logger.records), 1)
        level, event, extra = logger.records[0]
        self.assertEqual(event, "order_created")
        self.assertEqual(extra["paper_run_id"], "paper-1")
        self.assertEqual(extra["stage_ctx"], "order-1")

    def test_empty_event_rejected(self) -> None:
        logger = _RecordingLogger()
        context = PaperRunLogContext(paper_run_id="p", strategy_version="1")
        with self.assertRaises(PaperRunLogError):
            log_paper_event(logger, context=context, event="")

    def test_non_int_level_rejected(self) -> None:
        logger = _RecordingLogger()
        context = PaperRunLogContext(paper_run_id="p", strategy_version="1")
        with self.assertRaises(PaperRunLogError):
            log_paper_event(logger, context=context, event="x", level="INFO")  # type: ignore[arg-type]

    def test_sensitive_field_rejected(self) -> None:
        logger = _RecordingLogger()
        context = PaperRunLogContext(paper_run_id="p", strategy_version="1")
        with self.assertRaises(PaperRunLogError):
            log_paper_event(logger, context=context, event="x", api_key="secret")

    def test_colliding_field_rejected(self) -> None:
        logger = _RecordingLogger()
        context = PaperRunLogContext(paper_run_id="p", strategy_version="1")
        with self.assertRaises(PaperRunLogError):
            log_paper_event(logger, context=context, event="x", message="collides")
