"""Database model contract tests."""

import unittest

from harbor.storage.models import (
    ActionTerm,
    AdjustedFactor,
    CorporateAction,
    DailyQuote,
    Dividend,
    EquityEvent,
    Financial,
    Fundamental,
    IngestionRun,
    Position,
    QualityIssue,
    RawPayload,
    Security,
    v_quality_summary_hk,
    v_quality_summary_us,
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

    def test_daily_quotes_has_hk_partial_index(self) -> None:
        table = DailyQuote.__table__

        index = next(
            index for index in table.indexes if index.name == "ix_daily_quotes_hk_symbol_date"
        )
        self.assertEqual(tuple(index.columns.keys()), ("symbol", "date"))


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


class EquityEventModelTests(unittest.TestCase):
    """Verify the equity events schema contract."""

    def test_equity_events_has_required_fields_and_composite_primary_key(self) -> None:
        table = EquityEvent.__table__

        self.assertEqual(table.name, "equity_events")
        self.assertEqual(
            tuple(table.primary_key.columns.keys()),
            ("market", "symbol", "position_date", "action_id"),
        )
        self.assertEqual(
            set(table.columns.keys()),
            {
                "market",
                "symbol",
                "position_date",
                "action_id",
                "entitled_quantity",
                "cash_amount",
                "processed_at",
            },
        )
        self.assertEqual(
            {foreign_key.target_fullname for foreign_key in table.foreign_keys},
            {
                "positions.market",
                "positions.symbol",
                "positions.date",
                "corporate_actions.market",
                "corporate_actions.symbol",
                "corporate_actions.action_id",
            },
        )


class AdjustedFactorModelTests(unittest.TestCase):
    """Verify the adjusted factors schema contract."""

    def test_adjusted_factors_has_required_fields_and_composite_primary_key(self) -> None:
        table = AdjustedFactor.__table__

        self.assertEqual(table.name, "adjusted_factors")
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
                "cumulative_factor",
                "daily_factor",
            },
        )
        self.assertEqual(
            {foreign_key.target_fullname for foreign_key in table.foreign_keys},
            {
                "daily_quotes.market",
                "daily_quotes.symbol",
                "daily_quotes.date",
            },
        )


class IngestionRunModelTests(unittest.TestCase):
    """Verify the ingestion runs schema contract."""

    def test_ingestion_runs_has_required_fields_and_primary_key(self) -> None:
        table = IngestionRun.__table__

        self.assertEqual(table.name, "ingestion_runs")
        self.assertEqual(tuple(table.primary_key.columns.keys()), ("run_id",))
        self.assertEqual(
            set(table.columns.keys()),
            {
                "run_id",
                "start_time",
                "end_time",
                "status",
                "market",
                "source",
                "records_processed",
                "errors",
            },
        )
        self.assertFalse(table.columns["run_id"].nullable)
        self.assertFalse(table.columns["start_time"].nullable)
        self.assertFalse(table.columns["status"].nullable)
        self.assertFalse(table.columns["market"].nullable)
        self.assertFalse(table.columns["source"].nullable)
        self.assertFalse(table.columns["records_processed"].nullable)
        constraint_names = {constraint.name for constraint in table.constraints}
        self.assertIn("ck_ingestion_runs_market", constraint_names)
        self.assertIn("ck_ingestion_runs_status", constraint_names)


class RawPayloadModelTests(unittest.TestCase):
    """Verify the raw payloads schema contract."""

    def test_raw_payloads_has_required_fields_and_primary_key(self) -> None:
        table = RawPayload.__table__

        self.assertEqual(table.name, "raw_payloads")
        self.assertEqual(tuple(table.primary_key.columns.keys()), ("id",))
        self.assertEqual(
            set(table.columns.keys()),
            {
                "id",
                "run_id",
                "market",
                "symbol",
                "endpoint",
                "payload",
                "retrieved_at",
            },
        )
        self.assertEqual(
            {foreign_key.target_fullname for foreign_key in table.foreign_keys},
            {"ingestion_runs.run_id"},
        )
        self.assertFalse(table.columns["run_id"].nullable)
        self.assertFalse(table.columns["market"].nullable)
        self.assertTrue(table.columns["symbol"].nullable)
        self.assertFalse(table.columns["endpoint"].nullable)
        self.assertFalse(table.columns["payload"].nullable)
        self.assertFalse(table.columns["retrieved_at"].nullable)


class QualityIssueModelTests(unittest.TestCase):
    """Verify the quality issues schema contract."""

    def test_quality_issues_has_required_fields_and_primary_key(self) -> None:
        table = QualityIssue.__table__

        self.assertEqual(table.name, "quality_issues")
        self.assertEqual(tuple(table.primary_key.columns.keys()), ("id",))
        self.assertEqual(
            set(table.columns.keys()),
            {
                "id",
                "run_id",
                "market",
                "symbol",
                "check_name",
                "severity",
                "details",
                "resolved",
            },
        )
        self.assertEqual(
            {foreign_key.target_fullname for foreign_key in table.foreign_keys},
            {"ingestion_runs.run_id"},
        )
        constraint_names = {constraint.name for constraint in table.constraints}
        self.assertIn("ck_quality_issues_severity", constraint_names)
        self.assertFalse(table.columns["run_id"].nullable)
        self.assertTrue(table.columns["symbol"].nullable)
        self.assertFalse(table.columns["severity"].nullable)
        self.assertFalse(table.columns["resolved"].nullable)


class QualitySummaryViewTests(unittest.TestCase):
    """Verify the market quality summary view contracts."""

    def test_quality_summary_hk_has_expected_columns(self) -> None:
        self.assertEqual(v_quality_summary_hk.name, "v_quality_summary_hk")
        self.assertEqual(
            set(v_quality_summary_hk.columns.keys()),
            {
                "check_name",
                "severity",
                "issue_count",
                "resolved_count",
                "unresolved_count",
            },
        )

    def test_quality_summary_us_has_expected_columns(self) -> None:
        self.assertEqual(v_quality_summary_us.name, "v_quality_summary_us")
        self.assertEqual(
            set(v_quality_summary_us.columns.keys()),
            {
                "check_name",
                "severity",
                "issue_count",
                "resolved_count",
                "unresolved_count",
            },
        )
