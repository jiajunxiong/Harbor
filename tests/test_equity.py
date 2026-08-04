"""Equity entitlement calculation tests."""

import unittest
from datetime import date

from harbor.config import MarketTarget
from harbor.core.adjustments import ActionTerms
from harbor.core.equity import EntitlementEvent, compute_equity_entitlement
from harbor.core.market_registry import CorporateActionType


class HkEquityTests(unittest.TestCase):
    """SP 1.79: Hong Kong rights issue, consolidation, and dividend entitlements."""

    def test_hk_dividend_pays_cash_based_on_record_date(self) -> None:
        rows = compute_equity_entitlement(
            MarketTarget.HK,
            "0001.HK",
            date(2026, 1, 5),
            100.0,
            [
                EntitlementEvent(
                    "0001.HK-div-1",
                    CorporateActionType.DIVIDEND,
                    terms=ActionTerms(price=2.0),
                    record_date=date(2026, 1, 7),
                )
            ],
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["market"], "HK")
        self.assertEqual(row["symbol"], "0001.HK")
        self.assertEqual(row["position_date"], date(2026, 1, 5))
        self.assertEqual(row["action_id"], "0001.HK-div-1")
        self.assertEqual(row["entitled_quantity"], 0.0)
        self.assertEqual(row["cash_amount"], 200.0)

    def test_hk_rights_issue_grants_subscription_shares(self) -> None:
        rows = compute_equity_entitlement(
            MarketTarget.HK,
            "0700.HK",
            date(2026, 1, 5),
            100.0,
            [
                EntitlementEvent(
                    "0700.HK-rights-1",
                    CorporateActionType.RIGHTS_ISSUE,
                    terms=ActionTerms(ratio=0.5, price=90.0),
                    record_date=date(2026, 1, 7),
                )
            ],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entitled_quantity"], 50.0)
        self.assertEqual(rows[0]["cash_amount"], 0.0)

    def test_hk_consolidation_reduces_share_count(self) -> None:
        rows = compute_equity_entitlement(
            MarketTarget.HK,
            "0005.HK",
            date(2026, 1, 5),
            100.0,
            [
                EntitlementEvent(
                    "0005.HK-consol-1",
                    CorporateActionType.CONSOLIDATION,
                    terms=ActionTerms(ratio=0.2),
                    record_date=date(2026, 1, 7),
                )
            ],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entitled_quantity"], 20.0)

    def test_hk_position_after_record_date_is_not_entitled(self) -> None:
        rows = compute_equity_entitlement(
            MarketTarget.HK,
            "0001.HK",
            date(2026, 1, 9),
            100.0,
            [
                EntitlementEvent(
                    "0001.HK-div-1",
                    CorporateActionType.DIVIDEND,
                    terms=ActionTerms(price=2.0),
                    record_date=date(2026, 1, 7),
                )
            ],
        )
        self.assertEqual(rows, [])


class UsEquityTests(unittest.TestCase):
    """SP 1.80: United States split, merger, and spin-off entitlements."""

    def test_us_split_grant_shares(self) -> None:
        rows = compute_equity_entitlement(
            MarketTarget.US,
            "AAPL",
            date(2026, 1, 5),
            100.0,
            [
                EntitlementEvent(
                    "AAPL-split-1",
                    CorporateActionType.SPLIT,
                    terms=ActionTerms(ratio=2.0),
                    record_date=date(2026, 1, 7),
                )
            ],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entitled_quantity"], 200.0)
        self.assertEqual(rows[0]["cash_amount"], 0.0)

    def test_us_merger_exchanges_into_acquirer_shares(self) -> None:
        rows = compute_equity_entitlement(
            MarketTarget.US,
            "TSLA",
            date(2026, 1, 5),
            100.0,
            [
                EntitlementEvent(
                    "TSLA-merger-1",
                    CorporateActionType.MERGER,
                    terms=ActionTerms(ratio=0.5),
                    record_date=date(2026, 1, 7),
                )
            ],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entitled_quantity"], 50.0)

    def test_us_spin_off_grants_spin_shares(self) -> None:
        rows = compute_equity_entitlement(
            MarketTarget.US,
            "MSFT",
            date(2026, 1, 5),
            100.0,
            [
                EntitlementEvent(
                    "MSFT-spin-1",
                    CorporateActionType.SPIN_OFF,
                    terms=ActionTerms(ratio=0.1),
                    record_date=date(2026, 1, 7),
                )
            ],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entitled_quantity"], 10.0)

    def test_us_dividend_pays_cash(self) -> None:
        rows = compute_equity_entitlement(
            MarketTarget.US,
            "NVDA",
            date(2026, 1, 5),
            100.0,
            [
                EntitlementEvent(
                    "NVDA-div-1",
                    CorporateActionType.DIVIDEND,
                    terms=ActionTerms(price=1.5),
                    record_date=date(2026, 1, 7),
                )
            ],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cash_amount"], 150.0)

    def test_us_position_after_record_date_is_not_entitled(self) -> None:
        rows = compute_equity_entitlement(
            MarketTarget.US,
            "AAPL",
            date(2026, 1, 9),
            100.0,
            [
                EntitlementEvent(
                    "AAPL-split-1",
                    CorporateActionType.SPLIT,
                    terms=ActionTerms(ratio=2.0),
                    record_date=date(2026, 1, 7),
                )
            ],
        )
        self.assertEqual(rows, [])

    def test_record_date_falls_back_to_ex_date(self) -> None:
        rows = compute_equity_entitlement(
            MarketTarget.US,
            "AAPL",
            date(2026, 1, 5),
            100.0,
            [
                EntitlementEvent(
                    "AAPL-split-1",
                    CorporateActionType.SPLIT,
                    terms=ActionTerms(ratio=2.0),
                    ex_date=date(2026, 1, 7),
                )
            ],
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entitled_quantity"], 200.0)


class EquityValidationTests(unittest.TestCase):
    """Verify market scoping and error handling in entitlement calculation."""

    def test_hk_rejects_split_event(self) -> None:
        with self.assertRaisesRegex(ValueError, "not supported for HK"):
            compute_equity_entitlement(
                MarketTarget.HK,
                "0001.HK",
                date(2026, 1, 5),
                100.0,
                [
                    EntitlementEvent(
                        "0001.HK-split-1",
                        CorporateActionType.SPLIT,
                        terms=ActionTerms(ratio=2.0),
                        record_date=date(2026, 1, 7),
                    )
                ],
            )

    def test_us_rejects_rights_issue_event(self) -> None:
        with self.assertRaisesRegex(ValueError, "not supported for US"):
            compute_equity_entitlement(
                MarketTarget.US,
                "AAPL",
                date(2026, 1, 5),
                100.0,
                [
                    EntitlementEvent(
                        "AAPL-rights-1",
                        CorporateActionType.RIGHTS_ISSUE,
                        terms=ActionTerms(ratio=0.5, price=90.0),
                        record_date=date(2026, 1, 7),
                    )
                ],
            )

    def test_dividend_missing_amount_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "price term"):
            compute_equity_entitlement(
                MarketTarget.US,
                "NVDA",
                date(2026, 1, 5),
                100.0,
                [
                    EntitlementEvent(
                        "NVDA-div-1",
                        CorporateActionType.DIVIDEND,
                        record_date=date(2026, 1, 7),
                    )
                ],
            )

    def test_split_missing_ratio_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "ratio"):
            compute_equity_entitlement(
                MarketTarget.US,
                "AAPL",
                date(2026, 1, 5),
                100.0,
                [
                    EntitlementEvent(
                        "AAPL-split-1",
                        CorporateActionType.SPLIT,
                        record_date=date(2026, 1, 7),
                    )
                ],
            )

    def test_negative_quantity_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-negative"):
            compute_equity_entitlement(
                MarketTarget.US,
                "AAPL",
                date(2026, 1, 5),
                -10.0,
                [
                    EntitlementEvent(
                        "AAPL-split-1",
                        CorporateActionType.SPLIT,
                        terms=ActionTerms(ratio=2.0),
                        record_date=date(2026, 1, 7),
                    )
                ],
            )
