"""Input field validation tests (per market)."""

import unittest
from datetime import date

from harbor.config import MarketTarget
from harbor.core.market_registry import CorporateActionType
from harbor.core.validation import (
    QualityFinding,
    to_quality_records,
    validate_corporate_actions,
    validate_daily_quotes,
    validate_dataset,
    validate_dividends,
    validate_financials,
    validate_fundamentals,
    validate_securities,
)


def _check_names(findings: list[QualityFinding]) -> list[str]:
    return [finding.check_name for finding in findings]


class SecuritiesValidationTests(unittest.TestCase):
    """Verify securities field validation."""

    def test_valid_hk_security_has_no_findings(self) -> None:
        row = {
            "market": "HK",
            "symbol": "0700.HK",
            "name": "Tencent Holdings",
            "exchange": "HKEX",
            "list_date": date(2000, 1, 3),
            "delist_date": None,
            "is_active": True,
        }
        self.assertEqual(validate_securities(MarketTarget.HK, [row]), [])

    def test_valid_us_security_has_no_findings(self) -> None:
        row = {
            "market": "US",
            "symbol": "AAPL",
            "name": "Apple Inc.",
            "exchange": "NASDAQ",
            "list_date": date(2000, 1, 3),
            "delist_date": None,
            "is_active": True,
        }
        self.assertEqual(validate_securities(MarketTarget.US, [row]), [])

    def test_hk_rejects_us_style_symbol(self) -> None:
        row = {"market": "HK", "symbol": "AAPL", "name": "X", "exchange": "HKEX", "is_active": True}
        findings = validate_securities(MarketTarget.HK, [row])
        self.assertIn("symbol_format_invalid", _check_names(findings))

    def test_us_rejects_hk_style_symbol(self) -> None:
        row = {
            "market": "US",
            "symbol": "0700.HK",
            "name": "X",
            "exchange": "NASDAQ",
            "is_active": True,
        }
        findings = validate_securities(MarketTarget.US, [row])
        self.assertIn("symbol_format_invalid", _check_names(findings))

    def test_missing_name_is_flagged(self) -> None:
        row = {
            "market": "HK",
            "symbol": "0700.HK",
            "exchange": "HKEX",
            "list_date": date(2000, 1, 3),
            "is_active": True,
        }
        findings = validate_securities(MarketTarget.HK, [row])
        self.assertIn("required_field_missing", _check_names(findings))

    def test_non_boolean_is_active_is_flagged(self) -> None:
        row = {
            "market": "HK",
            "symbol": "0700.HK",
            "name": "X",
            "exchange": "HKEX",
            "list_date": date(2000, 1, 3),
            "is_active": "yes",
        }
        findings = validate_securities(MarketTarget.HK, [row])
        self.assertIn("field_type_invalid", _check_names(findings))


class DailyQuotesValidationTests(unittest.TestCase):
    """Verify daily quote field validation."""

    def _row(self, **overrides: object) -> dict[str, object]:
        row: dict[str, object] = {
            "market": "HK",
            "symbol": "0700.HK",
            "date": date(2026, 1, 5),
            "open": 100.0,
            "high": 105.0,
            "low": 99.0,
            "close": 102.0,
            "volume": 1000,
            "adjusted_close": 102.0,
            "source": "mock",
        }
        row.update(overrides)
        return row

    def test_valid_quote_has_no_findings(self) -> None:
        self.assertEqual(validate_daily_quotes(MarketTarget.HK, [self._row()]), [])

    def test_negative_close_is_flagged(self) -> None:
        findings = validate_daily_quotes(MarketTarget.HK, [self._row(close=-5.0)])
        self.assertIn("field_out_of_range", _check_names(findings))

    def test_high_below_low_is_flagged(self) -> None:
        findings = validate_daily_quotes(MarketTarget.HK, [self._row(high=90.0, low=95.0)])
        self.assertIn("ohlc_inconsistent", _check_names(findings))

    def test_missing_date_is_flagged(self) -> None:
        findings = validate_daily_quotes(MarketTarget.HK, [self._row(date=None)])
        self.assertIn("required_field_missing", _check_names(findings))

    def test_non_numeric_open_is_flagged(self) -> None:
        findings = validate_daily_quotes(MarketTarget.HK, [self._row(open="one")])
        self.assertIn("field_type_invalid", _check_names(findings))


class DividendsValidationTests(unittest.TestCase):
    """Verify dividend field validation and per-market currency."""

    def test_valid_hk_dividend_has_no_findings(self) -> None:
        row = {
            "market": "HK",
            "symbol": "0001.HK",
            "ex_date": date(2026, 1, 7),
            "record_date": date(2026, 1, 9),
            "payment_date": date(2026, 1, 19),
            "amount": 2.0,
            "type": "regular",
            "currency": "HKD",
        }
        self.assertEqual(validate_dividends(MarketTarget.HK, [row]), [])

    def test_us_dividend_with_wrong_currency_is_flagged(self) -> None:
        row = {
            "market": "US",
            "symbol": "AAPL",
            "ex_date": date(2026, 1, 7),
            "amount": 1.5,
            "type": "regular",
            "currency": "HKD",
        }
        findings = validate_dividends(MarketTarget.US, [row])
        self.assertIn("currency_mismatch", _check_names(findings))

    def test_negative_amount_is_flagged(self) -> None:
        row = {
            "market": "US",
            "symbol": "AAPL",
            "ex_date": date(2026, 1, 7),
            "amount": -1.0,
            "type": "regular",
            "currency": "USD",
        }
        findings = validate_dividends(MarketTarget.US, [row])
        self.assertIn("field_out_of_range", _check_names(findings))

    def test_invalid_type_is_flagged(self) -> None:
        row = {
            "market": "US",
            "symbol": "AAPL",
            "ex_date": date(2026, 1, 7),
            "amount": 1.5,
            "type": "annual",
            "currency": "USD",
        }
        findings = validate_dividends(MarketTarget.US, [row])
        self.assertIn("field_value_invalid", _check_names(findings))


class FinancialAndFundamentalValidationTests(unittest.TestCase):
    """Verify financial and fundamental field validation."""

    def test_valid_financials_has_no_findings(self) -> None:
        row = {
            "market": "US",
            "symbol": "AAPL",
            "report_date": date(2025, 12, 31),
            "fiscal_period": "2025",
            "roe": 0.2,
            "net_income": 10.0,
            "total_equity": 50.0,
            "revenue": 100.0,
        }
        self.assertEqual(validate_financials(MarketTarget.US, [row]), [])

    def test_financials_missing_report_date_is_flagged(self) -> None:
        row = {"market": "US", "symbol": "AAPL", "fiscal_period": "2025"}
        findings = validate_financials(MarketTarget.US, [row])
        self.assertIn("required_field_missing", _check_names(findings))

    def test_valid_fundamentals_has_no_findings(self) -> None:
        row = {
            "market": "HK",
            "symbol": "0700.HK",
            "date": date(2026, 1, 5),
            "dividend_yield": 0.02,
            "payout_ratio": 0.3,
            "pe_ratio": 20.0,
            "pb_ratio": 4.0,
        }
        self.assertEqual(validate_fundamentals(MarketTarget.HK, [row]), [])

    def test_negative_dividend_yield_is_flagged(self) -> None:
        row = {
            "market": "HK",
            "symbol": "0700.HK",
            "date": date(2026, 1, 5),
            "dividend_yield": -0.1,
        }
        findings = validate_fundamentals(MarketTarget.HK, [row])
        self.assertIn("field_out_of_range", _check_names(findings))


class CorporateActionsValidationTests(unittest.TestCase):
    """Verify corporate action field validation per market."""

    def test_valid_hk_rights_issue_has_no_findings(self) -> None:
        row = {
            "market": "HK",
            "symbol": "0700.HK",
            "action_id": "0700.HK-1",
            "announce_date": date(2026, 1, 1),
            "ex_date": date(2026, 1, 7),
            "record_date": date(2026, 1, 9),
            "effective_date": date(2026, 1, 17),
            "action_type": CorporateActionType.RIGHTS_ISSUE.value,
            "status": "completed",
            "source": "mock",
        }
        self.assertEqual(validate_corporate_actions(MarketTarget.HK, [row]), [])

    def test_hk_split_is_flagged_as_unsupported(self) -> None:
        row = {
            "market": "HK",
            "symbol": "0700.HK",
            "action_id": "0700.HK-2",
            "action_type": CorporateActionType.SPLIT.value,
            "status": "completed",
            "source": "mock",
        }
        findings = validate_corporate_actions(MarketTarget.HK, [row])
        self.assertIn("action_type_not_supported", _check_names(findings))

    def test_unknown_action_type_is_flagged(self) -> None:
        row = {
            "market": "HK",
            "symbol": "0700.HK",
            "action_id": "0700.HK-3",
            "action_type": "buyback",
            "status": "completed",
            "source": "mock",
        }
        findings = validate_corporate_actions(MarketTarget.HK, [row])
        self.assertIn("action_type_invalid", _check_names(findings))

    def test_invalid_status_is_flagged(self) -> None:
        row = {
            "market": "US",
            "symbol": "AAPL",
            "action_id": "AAPL-1",
            "action_type": CorporateActionType.SPLIT.value,
            "status": "done",
            "source": "mock",
        }
        findings = validate_corporate_actions(MarketTarget.US, [row])
        self.assertIn("field_value_invalid", _check_names(findings))


class QualityRecordsTests(unittest.TestCase):
    """Verify conversion to quality-issues records."""

    def test_to_quality_records_adds_run_context(self) -> None:
        findings = [
            QualityFinding("field_out_of_range", "error", "AAPL", "'close' must be positive.")
        ]
        records = to_quality_records(MarketTarget.US, "run-123", findings)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["run_id"], "run-123")
        self.assertEqual(record["market"], "US")
        self.assertEqual(record["symbol"], "AAPL")
        self.assertEqual(record["check_name"], "field_out_of_range")
        self.assertEqual(record["severity"], "error")
        self.assertEqual(record["resolved"], False)


class DatasetDispatchTests(unittest.TestCase):
    """Verify the dataset dispatcher."""

    def test_validate_dataset_dispatches(self) -> None:
        row = {
            "market": "US",
            "symbol": "AAPL",
            "name": "Apple",
            "exchange": "NASDAQ",
            "list_date": date(2000, 1, 3),
            "is_active": True,
        }
        self.assertEqual(validate_dataset(MarketTarget.US, "securities", [row]), [])

    def test_validate_dataset_rejects_unknown_dataset(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown dataset"):
            validate_dataset(MarketTarget.US, "splits", [])
