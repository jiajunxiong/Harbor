"""Per-event-type equity entitlement unit tests (SP 1.107 HK, 1.108 US).

Each corporate action type is tested individually through
``compute_equity_entitlement``: the entitled quantity and cash amount produced
for a held position on the record date.
"""

import unittest
from datetime import date

from harbor.config import MarketTarget
from harbor.core.adjustments import ActionTerms
from harbor.core.equity import EntitlementEvent, compute_equity_entitlement
from harbor.core.market_registry import CorporateActionType

_POSITION_DATE = date(2026, 1, 5)
_RECORD_DATE = date(2026, 1, 7)
_QUANTITY = 100.0


def _entitlements(
    market: MarketTarget,
    symbol: str,
    event: EntitlementEvent,
) -> list[dict[str, object]]:
    return compute_equity_entitlement(market, symbol, _POSITION_DATE, _QUANTITY, [event])


def _assert_entitlement(
    test_case: unittest.TestCase,
    rows: list[dict[str, object]],
    expected_quantity: float,
    expected_cash: float,
) -> None:
    """Assert a single entitled event's quantity and cash amounts."""
    test_case.assertEqual(len(rows), 1)
    row = rows[0]
    test_case.assertAlmostEqual(float(row["entitled_quantity"]), expected_quantity)
    test_case.assertAlmostEqual(float(row["cash_amount"]), expected_cash)


class HkEquityEventTests(unittest.TestCase):
    """SP 1.107: Hong Kong per-event-type equity entitlements."""

    def test_hk_rights_issue_grants_subscription_shares(self) -> None:
        event = EntitlementEvent(
            "0700.HK-rights-1",
            CorporateActionType.RIGHTS_ISSUE,
            terms=ActionTerms(ratio=0.5, price=90.0),
            record_date=_RECORD_DATE,
        )
        rows = _entitlements(MarketTarget.HK, "0700.HK", event)
        _assert_entitlement(self, rows, 50.0, 0.0)

    def test_hk_consolidation_reduces_share_count(self) -> None:
        event = EntitlementEvent(
            "0005.HK-consol-1",
            CorporateActionType.CONSOLIDATION,
            terms=ActionTerms(ratio=0.2),
            record_date=_RECORD_DATE,
        )
        rows = _entitlements(MarketTarget.HK, "0005.HK", event)
        _assert_entitlement(self, rows, 20.0, 0.0)

    def test_hk_dividend_pays_cash(self) -> None:
        event = EntitlementEvent(
            "0001.HK-div-1",
            CorporateActionType.DIVIDEND,
            terms=ActionTerms(price=2.0),
            record_date=_RECORD_DATE,
        )
        rows = _entitlements(MarketTarget.HK, "0001.HK", event)
        _assert_entitlement(self, rows, 0.0, 200.0)

    def test_hk_tender_offer_pays_cash(self) -> None:
        event = EntitlementEvent(
            "0011.HK-tender-1",
            CorporateActionType.TENDER_OFFER,
            terms=ActionTerms(price=50.0),
            record_date=_RECORD_DATE,
        )
        rows = _entitlements(MarketTarget.HK, "0011.HK", event)
        _assert_entitlement(self, rows, 0.0, 5000.0)


class UsEquityEventTests(unittest.TestCase):
    """SP 1.108: United States per-event-type equity entitlements."""

    def test_us_split_grant_shares(self) -> None:
        event = EntitlementEvent(
            "AAPL-split-1",
            CorporateActionType.SPLIT,
            terms=ActionTerms(ratio=2.0),
            record_date=_RECORD_DATE,
        )
        rows = _entitlements(MarketTarget.US, "AAPL", event)
        _assert_entitlement(self, rows, 200.0, 0.0)

    def test_us_merger_exchanges_into_acquirer_shares(self) -> None:
        event = EntitlementEvent(
            "TSLA-merger-1",
            CorporateActionType.MERGER,
            terms=ActionTerms(ratio=0.5),
            record_date=_RECORD_DATE,
        )
        rows = _entitlements(MarketTarget.US, "TSLA", event)
        _assert_entitlement(self, rows, 50.0, 0.0)

    def test_us_spin_off_grants_spin_shares(self) -> None:
        event = EntitlementEvent(
            "MSFT-spin-1",
            CorporateActionType.SPIN_OFF,
            terms=ActionTerms(ratio=0.1),
            record_date=_RECORD_DATE,
        )
        rows = _entitlements(MarketTarget.US, "MSFT", event)
        _assert_entitlement(self, rows, 10.0, 0.0)

    def test_us_dividend_pays_cash(self) -> None:
        event = EntitlementEvent(
            "NVDA-div-1",
            CorporateActionType.DIVIDEND,
            terms=ActionTerms(price=1.0),
            record_date=_RECORD_DATE,
        )
        rows = _entitlements(MarketTarget.US, "NVDA", event)
        _assert_entitlement(self, rows, 0.0, 100.0)
