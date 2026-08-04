"""Illegal OHLC and abnormal price move check tests."""

import unittest
from datetime import date

from harbor.config import MarketTarget
from harbor.core.quality_checks import find_abnormal_moves, find_illegal_ohlc
from harbor.core.validation import QualityFinding


def _quote(symbol: str, day: date, **prices: object) -> dict[str, object]:
    row: dict[str, object] = {"market": "US", "symbol": symbol, "date": day}
    row.update(prices)
    return row


def _check_names(findings: list[QualityFinding]) -> list[str]:
    return [finding.check_name for finding in findings]


class IllegalOhlcTests(unittest.TestCase):
    """Verify illegal OHLC detection (SP 1.88)."""

    def test_valid_ohlc_has_no_findings(self) -> None:
        rows = [_quote("AAPL", date(2026, 1, 5), open=100.0, high=105.0, low=99.0, close=102.0)]
        self.assertEqual(find_illegal_ohlc(MarketTarget.US, rows), [])

    def test_high_below_low_is_flagged(self) -> None:
        rows = [_quote("AAPL", date(2026, 1, 5), open=100.0, high=90.0, low=95.0, close=92.0)]
        findings = find_illegal_ohlc(MarketTarget.US, rows)
        self.assertIn("ohlc_invalid", _check_names(findings))
        self.assertEqual(findings[0].severity, "error")
        self.assertIn("high is below low", findings[0].details)

    def test_high_below_close_is_flagged(self) -> None:
        rows = [_quote("AAPL", date(2026, 1, 5), open=100.0, high=98.0, low=97.0, close=102.0)]
        findings = find_illegal_ohlc(MarketTarget.US, rows)
        self.assertIn("high is below open or close", findings[0].details)

    def test_low_above_open_is_flagged(self) -> None:
        rows = [_quote("AAPL", date(2026, 1, 5), open=100.0, high=110.0, low=105.0, close=108.0)]
        findings = find_illegal_ohlc(MarketTarget.US, rows)
        self.assertIn("low is above open or close", findings[0].details)

    def test_non_positive_price_is_flagged(self) -> None:
        rows = [_quote("AAPL", date(2026, 1, 5), open=0.0, high=105.0, low=99.0, close=102.0)]
        findings = find_illegal_ohlc(MarketTarget.US, rows)
        self.assertTrue(
            any("OHLC prices must be positive" in (finding.details or "") for finding in findings)
        )

    def test_hk_illegal_ohlc_is_flagged(self) -> None:
        rows = [_quote("0700.HK", date(2026, 1, 5), open=100.0, high=90.0, low=95.0, close=92.0)]
        findings = find_illegal_ohlc(MarketTarget.HK, rows)
        self.assertGreaterEqual(len(findings), 1)
        self.assertIn("ohlc_invalid", _check_names(findings))


class AbnormalMoveTests(unittest.TestCase):
    """Verify abnormal daily move detection (SP 1.88)."""

    def test_large_down_move_is_flagged(self) -> None:
        rows = [
            _quote("AAPL", date(2026, 1, 5), close=100.0),
            _quote("AAPL", date(2026, 1, 6), close=30.0),
        ]
        findings = find_abnormal_moves(MarketTarget.US, rows)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].check_name, "abnormal_price_move")
        self.assertEqual(findings[0].severity, "warning")
        self.assertIn("70.0%", findings[0].details)

    def test_large_up_move_is_flagged(self) -> None:
        rows = [
            _quote("AAPL", date(2026, 1, 5), close=100.0),
            _quote("AAPL", date(2026, 1, 6), close=160.0),
        ]
        findings = find_abnormal_moves(MarketTarget.US, rows)
        self.assertEqual(len(findings), 1)
        self.assertIn("60.0%", findings[0].details)

    def test_move_below_threshold_is_not_flagged(self) -> None:
        rows = [
            _quote("AAPL", date(2026, 1, 5), close=100.0),
            _quote("AAPL", date(2026, 1, 6), close=140.0),
        ]
        self.assertEqual(find_abnormal_moves(MarketTarget.US, rows), [])

    def test_custom_threshold_is_applied(self) -> None:
        rows = [
            _quote("AAPL", date(2026, 1, 5), close=100.0),
            _quote("AAPL", date(2026, 1, 6), close=140.0),
        ]
        findings = find_abnormal_moves(MarketTarget.US, rows, threshold=0.1)
        self.assertEqual(len(findings), 1)

    def test_rows_are_grouped_and_ordered_by_symbol(self) -> None:
        rows = [
            _quote("MSFT", date(2026, 1, 6), close=90.0),
            _quote("AAPL", date(2026, 1, 6), close=30.0),
            _quote("AAPL", date(2026, 1, 5), close=100.0),
        ]
        findings = find_abnormal_moves(MarketTarget.US, rows)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].symbol, "AAPL")

    def test_non_positive_previous_close_is_skipped(self) -> None:
        rows = [
            _quote("AAPL", date(2026, 1, 5), close=0.0),
            _quote("AAPL", date(2026, 1, 6), close=100.0),
        ]
        self.assertEqual(find_abnormal_moves(MarketTarget.US, rows), [])
