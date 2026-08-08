"""Research audit query tests (MVP 2 / SP 2.66).

Verifies that a run's audit record — configuration, input range, artifacts
and failure reasons — is assembled from the persisted run record (SP 2.6) and
the optional SP 2.58 results artifact, keyed by run id.
"""

import unittest
from datetime import date, datetime, timezone

from harbor.core.audit_query import AuditError, RunRecord, build_run_audit
from harbor.core.backtest_domain import BacktestStatus, Currency, Market

HK = Market.HK
US = Market.US
_COMPLETED = BacktestStatus.COMPLETED
_FAILED = BacktestStatus.FAILED


def _utc(
    year: int = 2026, month: int = 1, day: int = 1, hour: int = 0, minute: int = 0
) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def _config_dict(
    *,
    markets: tuple[str, ...] = ("HK",),
    start: str = "2026-01-02",
    end: str = "2026-06-30",
    base: str = "HKD",
    initial: float = 1_000_000.0,
) -> dict:
    return {
        "strategy": "shareholder-return",
        "strategy_version": "1.0.0",
        "market_quotas": [
            {"market": market, "target_count": 15, "weight": 1.0} for market in markets
        ],
        "start_date": start,
        "end_date": end,
        "base_currency": base,
        "initial_capital": initial,
    }


def _record(
    *,
    run_id: str = "run-1",
    status: BacktestStatus = _COMPLETED,
    error_summary: str | None = None,
    finished_at: datetime | None = _utc(month=7),
    config_snapshot: dict | None = None,
    **overrides: object,
) -> RunRecord:
    values: dict[str, object] = dict(
        run_id=run_id,
        config_hash="hash-1",
        config_snapshot=config_snapshot if config_snapshot is not None else _config_dict(),
        strategy="shareholder-return",
        strategy_version="1.0.0",
        code_version="2.0.0",
        data_cutoff=date(2026, 6, 30),
        status=status,
        started_at=_utc(),
        finished_at=finished_at,
        error_summary=error_summary,
    )
    values.update(overrides)
    return RunRecord(**values)


def _artifact(
    *,
    run_id: str = "run-1",
    day_count: int = 2,
    failures: tuple[str, ...] = (),
    warnings: tuple[dict, ...] = (),
    config: dict | None = None,
    net_values: list[dict] | None = None,
) -> dict:
    return {
        "run": {
            "run_id": run_id,
            "status": "COMPLETED",
            "succeeded": True,
            "inputs": {
                "code_version": "2.0.0",
                "config_hash": "hash-1",
                "data_cutoff": "2026-06-30",
                "data_range_start": "2026-01-02",
                "data_range_end": "2026-06-30",
            },
            "base_currency": "HKD",
            "initial_capital": 1_000_000.0,
            "day_count": day_count,
            "reconciliation_failures": list(failures),
        },
        "config": config if config is not None else _config_dict(),
        "metrics": {"performance": None, "drawdown": None},
        "warnings": list(warnings),
        "net_values": net_values
        if net_values is not None
        else [
            {"date": "2026-01-02", "total_value": 1_000_000.0},
            {"date": "2026-01-03", "total_value": 1_010_000.0},
        ],
        "positions": [
            {
                "date": "2026-01-02",
                "market": "HK",
                "symbol": "0001.HK",
                "quantity": 100.0,
            }
        ],
        "trades": [
            {
                "date": "2026-01-02",
                "market": "HK",
                "symbol": "0001.HK",
                "side": "BUY",
                "quantity": 100.0,
                "price": 10.0,
                "fee": 5.0,
            }
        ],
        "dividends": [],
        "corporate_actions": [],
        "refused": [
            {
                "date": "2026-01-02",
                "market": "HK",
                "symbol": "0002.HK",
                "reason": "suspended",
            }
        ],
    }


class RunRecordValidationTests(unittest.TestCase):
    """Verify the persisted run record validation."""

    def test_empty_run_id_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "run_id"):
            _record(run_id="")

    def test_empty_config_hash_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "config_hash"):
            _record(config_hash="")

    def test_empty_code_version_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "code_version"):
            _record(code_version="")

    def test_naive_started_at_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            _record(started_at=datetime(2026, 1, 1))

    def test_naive_finished_at_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            _record(finished_at=datetime(2026, 7, 1))


class BuildAuditWithoutArtifactTests(unittest.TestCase):
    """Verify the audit assembled from the persisted record alone."""

    def test_parses_config_snapshot(self) -> None:
        audit = build_run_audit(_record())
        self.assertEqual(audit.markets, (HK,))
        self.assertEqual(audit.start_date, date(2026, 1, 2))
        self.assertEqual(audit.end_date, date(2026, 6, 30))
        self.assertEqual(audit.base_currency, Currency.HKD)
        self.assertEqual(audit.initial_capital, 1_000_000.0)
        self.assertFalse(audit.artifact_present)
        self.assertIsNone(audit.day_count)
        self.assertEqual(sum(audit.result_counts.values()), 0)

    def test_cross_market_markets_parsed(self) -> None:
        audit = build_run_audit(_record(config_snapshot=_config_dict(markets=("HK", "US"))))
        self.assertEqual(audit.markets, (HK, US))

    def test_failed_status_and_failure_reason_from_record(self) -> None:
        audit = build_run_audit(_record(status=_FAILED, error_summary="missing FX"))
        self.assertTrue(audit.failed)
        self.assertEqual(audit.failure_reason, "missing FX")

    def test_readable_without_artifact(self) -> None:
        text = build_run_audit(_record()).readable()
        self.assertIn("Run audit run-1", text)
        self.assertIn("status: COMPLETED", text)
        self.assertIn("input range: 2026-01-02 -> 2026-06-30 (cutoff 2026-06-30)", text)
        self.assertIn("artifacts: absent", text)
        self.assertIn("config hash: hash-1", text)


class BuildAuditWithArtifactTests(unittest.TestCase):
    """Verify the audit enriched by the SP 2.58 results artifact."""

    def test_parses_artifact_config_and_counts(self) -> None:
        audit = build_run_audit(_record(), artifact=_artifact())
        self.assertTrue(audit.artifact_present)
        self.assertEqual(audit.day_count, 2)
        self.assertEqual(audit.markets, (HK,))
        self.assertEqual(audit.base_currency, Currency.HKD)
        self.assertEqual(audit.result_counts["net_values"], 2)
        self.assertEqual(audit.result_counts["positions"], 1)
        self.assertEqual(audit.result_counts["trades"], 1)
        self.assertEqual(audit.result_counts["dividends"], 0)
        self.assertEqual(audit.result_counts["corporate_actions"], 0)
        self.assertEqual(audit.result_counts["refused"], 1)

    def test_failure_reason_falls_back_to_reconciliation_failure(self) -> None:
        audit = build_run_audit(
            _record(),
            artifact=_artifact(failures=("2026-01-03: cash gap -100.00",)),
        )
        self.assertFalse(audit.failed)
        self.assertEqual(audit.reconciliation_failures, ("2026-01-03: cash gap -100.00",))
        self.assertEqual(audit.failure_reason, "2026-01-03: cash gap -100.00")

    def test_warnings_from_artifact(self) -> None:
        warning = {"date": "2026-01-02", "message": "low volume"}
        audit = build_run_audit(_record(), artifact=_artifact(warnings=(warning,)))
        self.assertEqual(audit.warnings, (warning,))
        text = audit.readable()
        self.assertIn("warnings (1)", text)
        self.assertIn("low volume", text)

    def test_readable_with_artifact(self) -> None:
        text = build_run_audit(_record(), artifact=_artifact()).readable()
        self.assertIn("artifacts: present", text)
        self.assertIn("result counts:", text)
        self.assertIn("day count: 2", text)

    def test_run_id_mismatch_raises(self) -> None:
        with self.assertRaisesRegex(AuditError, "does not match"):
            build_run_audit(_record(), artifact=_artifact(run_id="run-other"))

    def test_malformed_artifact_raises(self) -> None:
        artifact = _artifact()
        del artifact["net_values"]
        with self.assertRaisesRegex(AuditError, "SP 2.58"):
            build_run_audit(_record(), artifact=artifact)


class ParseErrorTests(unittest.TestCase):
    """Verify malformed configuration snapshots raise a dedicated error."""

    def test_invalid_start_date_raises(self) -> None:
        record = _record(config_snapshot=_config_dict(start="not-a-date"))
        with self.assertRaisesRegex(AuditError, "start_date"):
            build_run_audit(record)

    def test_unknown_base_currency_raises(self) -> None:
        record = _record(config_snapshot=_config_dict(base="XYZ"))
        with self.assertRaisesRegex(AuditError, "base currency"):
            build_run_audit(record)


if __name__ == "__main__":
    unittest.main()
