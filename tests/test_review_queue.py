"""Abnormal corporate action review queue tests."""

import json
import unittest

from harbor.config import MarketTarget
from harbor.core.review_queue import (
    build_review_report,
    classify_corporate_action,
    render_review_report,
)


class HkReviewQueueTests(unittest.TestCase):
    """SP 1.81: Hong Kong abnormal event review queue."""

    def test_hk_unknown_action_type_is_queued(self) -> None:
        row = {
            "market": "HK",
            "symbol": "0001.HK",
            "action_id": "0001.HK-ca-1",
            "action_type": "buyback",
        }
        item = classify_corporate_action(MarketTarget.HK, row)
        self.assertIsNotNone(item)
        self.assertEqual(item["market"], "HK")
        self.assertEqual(item["symbol"], "0001.HK")
        self.assertEqual(item["action_id"], "0001.HK-ca-1")
        self.assertEqual(item["action_type"], "buyback")
        self.assertEqual(item["reason"], "unknown_action_type")

    def test_hk_split_event_is_queued_as_unsupported(self) -> None:
        row = {
            "market": "HK",
            "symbol": "0001.HK",
            "action_id": "0001.HK-ca-2",
            "action_type": "split",
            "ratio": 2.0,
        }
        item = classify_corporate_action(MarketTarget.HK, row)
        self.assertEqual(item["reason"], "action_type_not_supported")
        self.assertIn("not supported for HK", item["details"]["error"])

    def test_hk_rights_issue_missing_ratio_is_queued(self) -> None:
        row = {
            "market": "HK",
            "symbol": "0700.HK",
            "action_id": "0700.HK-ca-1",
            "action_type": "供股",
            "price": 90.0,
        }
        item = classify_corporate_action(MarketTarget.HK, row)
        self.assertEqual(item["reason"], "missing_terms")
        self.assertIn("ratio", item["details"]["missing"])

    def test_hk_valid_rights_issue_is_auto_processable(self) -> None:
        row = {
            "market": "HK",
            "symbol": "0700.HK",
            "action_id": "0700.HK-ca-2",
            "action_type": "供股",
            "ratio": 0.5,
            "price": 90.0,
        }
        self.assertIsNone(classify_corporate_action(MarketTarget.HK, row))

    def test_hk_valid_dividend_is_auto_processable(self) -> None:
        row = {
            "market": "HK",
            "symbol": "0001.HK",
            "action_id": "0001.HK-ca-3",
            "action_type": "股息",
            "price": 2.0,
        }
        self.assertIsNone(classify_corporate_action(MarketTarget.HK, row))

    def test_hk_market_mismatch_is_queued(self) -> None:
        row = {
            "market": "US",
            "symbol": "0001.HK",
            "action_id": "0001.HK-ca-4",
            "action_type": "split",
        }
        item = classify_corporate_action(MarketTarget.HK, row)
        self.assertEqual(item["reason"], "market_mismatch")

    def test_hk_report_summary_and_json_rendering(self) -> None:
        rows = [
            {
                "market": "HK",
                "symbol": "0001.HK",
                "action_id": "1",
                "action_type": "buyback",
            },
            {
                "market": "HK",
                "symbol": "0700.HK",
                "action_id": "2",
                "action_type": "供股",
                "ratio": 0.5,
                "price": 90.0,
            },
            {
                "market": "HK",
                "symbol": "0005.HK",
                "action_id": "3",
                "action_type": "合股",
                "ratio": 0.2,
            },
        ]
        report = build_review_report(MarketTarget.HK, rows)
        self.assertEqual(report["market"], "HK")
        self.assertEqual(report["total_events"], 3)
        self.assertEqual(report["review_count"], 1)
        self.assertEqual(report["items"][0]["action_id"], "1")

        text = render_review_report(MarketTarget.HK, rows)
        self.assertEqual(json.loads(text), report)


class UsReviewQueueTests(unittest.TestCase):
    """SP 1.82: United States abnormal event review queue."""

    def test_us_unknown_action_type_is_queued(self) -> None:
        row = {
            "market": "US",
            "symbol": "AAPL",
            "action_id": "AAPL-ca-1",
            "action_type": "buyback",
        }
        item = classify_corporate_action(MarketTarget.US, row)
        self.assertEqual(item["reason"], "unknown_action_type")

    def test_us_rights_issue_event_is_queued_as_unsupported(self) -> None:
        row = {
            "market": "US",
            "symbol": "AAPL",
            "action_id": "AAPL-ca-2",
            "action_type": "rights_issue",
            "ratio": 0.5,
            "price": 90.0,
        }
        item = classify_corporate_action(MarketTarget.US, row)
        self.assertEqual(item["reason"], "action_type_not_supported")
        self.assertIn("not supported for US", item["details"]["error"])

    def test_us_split_missing_ratio_is_queued(self) -> None:
        row = {
            "market": "US",
            "symbol": "AAPL",
            "action_id": "AAPL-ca-3",
            "action_type": "split",
        }
        item = classify_corporate_action(MarketTarget.US, row)
        self.assertEqual(item["reason"], "missing_terms")
        self.assertIn("ratio", item["details"]["missing"])

    def test_us_valid_split_is_auto_processable(self) -> None:
        row = {
            "market": "US",
            "symbol": "AAPL",
            "action_id": "AAPL-ca-4",
            "action_type": "split",
            "ratio": 2.0,
        }
        self.assertIsNone(classify_corporate_action(MarketTarget.US, row))

    def test_us_valid_merger_is_auto_processable(self) -> None:
        row = {
            "market": "US",
            "symbol": "TSLA",
            "action_id": "TSLA-ca-1",
            "action_type": "merger",
            "ratio": 0.5,
        }
        self.assertIsNone(classify_corporate_action(MarketTarget.US, row))

    def test_us_report_summary(self) -> None:
        rows = [
            {
                "market": "US",
                "symbol": "AAPL",
                "action_id": "1",
                "action_type": "split",
                "ratio": 2.0,
            },
            {
                "market": "US",
                "symbol": "MSFT",
                "action_id": "2",
                "action_type": "spin-off",
                "ratio": 0.1,
            },
            {
                "market": "US",
                "symbol": "NVDA",
                "action_id": "3",
                "action_type": "供股",
                "ratio": 0.5,
                "price": 90.0,
            },
        ]
        report = build_review_report(MarketTarget.US, rows)
        self.assertEqual(report["market"], "US")
        self.assertEqual(report["total_events"], 3)
        self.assertEqual(report["review_count"], 1)
        self.assertEqual(report["items"][0]["action_id"], "3")
        self.assertEqual(report["items"][0]["reason"], "action_type_not_supported")
