"""Database model contract tests."""

import unittest

from harbor.storage.models import (
    ActionTerm,
    CorporateAction,
    DailyQuote,
    Dividend,
    Financial,
    Fundamental,
    Position,
    Security,
)


class SecuritiesModelTests(unittest.TestCase):
    """Verify the securities master-data schema contract."""

    def test_securities_has_required_fields_and_composite_primary_key(self) -> None:
        table = Security.__table__

        self.assertEqual(table.name, "securities")
        self.assertEqual(
            tuple(table.primary_key.columns.keys()),
            ("market", "symbol"),
        )
        self.assertEqual(
            set(table.columns.keys()),
            {
                "market",
                "symbol",
                "name",
                "exchange",
                "list_date",
                "delist_date",
                "is_active",
            },
        )
        self.assertFalse(table.columns["market"].nullable)
        self.assertFalse(table.columns["symbol"].nullable)
        self.assertFalse(table.columns["is_active"].nullable)


class DailyQuoteModelTests(unittest.TestCase):
    """Verify the daily quotes schema contract."""

    def test_daily_quotes_has_required_fields_and_composite_primary_key(self) -> None:
        table = DailyQuote.__table__

        self.assertEqual(table.name, "daily_quotes")
        self.assertEqual(
            tuple(table.primary_key.columns.keys()),
            ("market", "symbol", "date"),
        )
        self.assertEqual(
            set(table.columns.keys()),
            {
                "market",
                "symbol",
                "date",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "adjusted_close",
                "source",
            },
        )
        self.assertEqual(
            {foreign_key.target_fullname for foreign_key in table.foreign_keys},
            {"securities.market", "securities.symbol"},
        )


class DividendModelTests(unittest.TestCase):
    """Verify the dividends schema contract."""

    def test_dividends_has_required_fields_and_composite_primary_key(self) -> None:
        table = Dividend.__table__

        self.assertEqual(table.name, "dividends")
        self.assertEqual(
            tuple(table.primary_key.columns.keys()),
            ("market", "symbol", "ex_date"),
        )
        self.assertEqual(
            set(table.columns.keys()),
            {
                "market",
                "symbol",
                "ex_date",
                "record_date",
                "payment_date",
                "amount",
                "type",
                "currency",
            },
        )
        self.assertEqual(
            {foreign_key.target_fullname for foreign_key in table.foreign_keys},
            {"securities.market", "securities.symbol"},
        )
        self.assertIn("ck_dividends_type", {constraint.name for constraint in table.constraints})


class FinancialModelTests(unittest.TestCase):
    """Verify the financials schema contract."""

    def test_financials_has_required_fields_and_composite_primary_key(self) -> None:
        table = Financial.__table__

        self.assertEqual(table.name, "financials")
        self.assertEqual(
            tuple(table.primary_key.columns.keys()),
            ("market", "symbol", "report_date", "fiscal_period"),
        )
        self.assertEqual(
            set(table.columns.keys()),
            {
                "market",
                "symbol",
                "report_date",
                "fiscal_period",
                "roe",
                "net_income",
                "total_equity",
                "revenue",
            },
        )
        self.assertEqual(
            {foreign_key.target_fullname for foreign_key in table.foreign_keys},
            {"securities.market", "securities.symbol"},
        )


class FundamentalsModelTests(unittest.TestCase):
    """Verify the fundamentals schema contract."""

    def test_fundamentals_has_required_fields_and_composite_primary_key(self) -> None:
        table = Fundamental.__table__

        self.assertEqual(table.name, "fundamentals")
        self.assertEqual(
            tuple(table.primary_key.columns.keys()),
            ("market", "symbol", "date"),
        )
        self.assertEqual(
            set(table.columns.keys()),
            {
                "market",
                "symbol",
                "date",
                "dividend_yield",
                "payout_ratio",
                "pe_ratio",
                "pb_ratio",
            },
        )
        self.assertEqual(
            {foreign_key.target_fullname for foreign_key in table.foreign_keys},
            {"securities.market", "securities.symbol"},
        )


class CorporateActionModelTests(unittest.TestCase):
    """Verify the corporate actions schema contract."""

    def test_corporate_actions_has_required_fields_and_composite_primary_key(self) -> None:
        table = CorporateAction.__table__

        self.assertEqual(table.name, "corporate_actions")
        self.assertEqual(
            tuple(table.primary_key.columns.keys()),
            ("market", "symbol", "action_id"),
        )
        self.assertEqual(
            set(table.columns.keys()),
            {
                "market",
                "symbol",
                "action_id",
                "announce_date",
                "ex_date",
                "record_date",
                "effective_date",
                "action_type",
                "status",
                "source",
            },
        )
        self.assertEqual(
            {foreign_key.target_fullname for foreign_key in table.foreign_keys},
            {"securities.market", "securities.symbol"},
        )
        self.assertIn(
            "ck_corporate_actions_action_type",
            {constraint.name for constraint in table.constraints},
        )


class ActionTermModelTests(unittest.TestCase):
    """Verify the action terms schema contract."""

    def test_action_terms_has_required_fields_and_composite_primary_key(self) -> None:
        table = ActionTerm.__table__

        self.assertEqual(table.name, "action_terms")
        self.assertEqual(
            tuple(table.primary_key.columns.keys()),
            ("market", "symbol", "action_id", "term_type"),
        )
        self.assertEqual(
            set(table.columns.keys()),
            {
                "market",
                "symbol",
                "action_id",
                "term_type",
                "value",
                "description",
            },
        )
        self.assertEqual(
            {foreign_key.target_fullname for foreign_key in table.foreign_keys},
            {
                "corporate_actions.market",
                "corporate_actions.symbol",
                "corporate_actions.action_id",
            },
        )
        self.assertIn(
            "ck_action_terms_term_type",
            {constraint.name for constraint in table.constraints},
        )


class PositionModelTests(unittest.TestCase):
    """Verify the positions schema contract."""

    def test_positions_has_required_fields_and_composite_primary_key(self) -> None:
        table = Position.__table__

        self.assertEqual(table.name, "positions")
        self.assertEqual(
            tuple(table.primary_key.columns.keys()),
            ("market", "symbol", "date"),
        )
        self.assertEqual(
            set(table.columns.keys()),
            {
                "market",
                "symbol",
                "date",
                "quantity",
                "cost_basis",
                "market_value",
            },
        )
        self.assertEqual(
            {foreign_key.target_fullname for foreign_key in table.foreign_keys},
            {"securities.market", "securities.symbol"},
        )
