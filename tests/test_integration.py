"""End-to-end integration tests using the MockProvider (SP 1.100 HK, 1.101 US).

Runs the full pipeline against a live PostgreSQL: ingestion (securities, daily
quotes, dividends, financials, corporate actions) -> storage -> adjusted price
factors -> equity entitlement. The tests require ``HARBOR_TEST_DATABASE_URL``
to point at a disposable PostgreSQL and are skipped otherwise.
"""

import os
import unittest
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text

from harbor.config import MarketTarget
from harbor.core.adjustments import (
    ActionTerms,
    AdjustmentEvent,
    compute_adjustment_factors,
)
from harbor.core.equity import EntitlementEvent, compute_equity_entitlement
from harbor.core.ingestion import (
    CorporateActionIngestor,
    DailyQuoteIngestor,
    DividendIngestor,
    FinancialIngestor,
    SecuritiesIngestor,
)
from harbor.core.market_registry import CorporateActionType
from harbor.infrastructure.data_providers.factory import create_provider
from harbor.storage.repositories import Repository

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_TEST_DATABASE_URL = os.getenv("HARBOR_TEST_DATABASE_URL")

_QUOTE_START = date(2020, 11, 2)
_EVENT_START = date(2021, 1, 4)
_RANGE_END = date(2025, 12, 31)
_POSITION_QUANTITY = 100.0

_TERMS_BY_TYPE: dict[CorporateActionType, ActionTerms] = {
    CorporateActionType.SPLIT: ActionTerms(ratio=2.0),
    CorporateActionType.CONSOLIDATION: ActionTerms(ratio=0.2),
    CorporateActionType.RIGHTS_ISSUE: ActionTerms(ratio=0.5, price=90.0),
    CorporateActionType.MERGER: ActionTerms(ratio=0.5),
    CorporateActionType.SPIN_OFF: ActionTerms(ratio=0.1),
    CorporateActionType.DIVIDEND: ActionTerms(price=1.0),
    CorporateActionType.TENDER_OFFER: ActionTerms(price=50.0),
}

_TRUNCATE_TABLES = (
    "securities",
    "daily_quotes",
    "dividends",
    "financials",
    "fundamentals",
    "corporate_actions",
    "action_terms",
    "positions",
    "equity_events",
    "adjusted_factors",
    "ingestion_runs",
    "raw_payloads",
    "quality_issues",
)


def _ensure_migrated() -> None:
    """Apply the migration chain up to head against the test database."""
    from alembic.config import Config

    from alembic import command

    config = Config(str(_PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_PROJECT_ROOT / "alembic"))
    original_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = _TEST_DATABASE_URL
    try:
        command.upgrade(config, "head")
    finally:
        if original_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original_url


def _reset_database(engine: object) -> None:
    """Empty the project tables so the pipeline starts from a clean slate."""
    with engine.begin() as connection:  # type: ignore[union-attr]
        connection.execute(text(f"TRUNCATE {', '.join(_TRUNCATE_TABLES)} RESTART IDENTITY CASCADE"))


def _as_adjustment_event(row: dict[str, object]) -> AdjustmentEvent:
    action_type = CorporateActionType(str(row["action_type"]))
    return AdjustmentEvent(
        ex_date=row["ex_date"],  # type: ignore[arg-type]
        action_type=action_type,
        terms=_TERMS_BY_TYPE.get(action_type, ActionTerms()),
    )


def _as_entitlement_event(row: dict[str, object]) -> EntitlementEvent:
    action_type = CorporateActionType(str(row["action_type"]))
    return EntitlementEvent(
        action_id=str(row["action_id"]),
        action_type=action_type,
        terms=_TERMS_BY_TYPE.get(action_type, ActionTerms()),
        record_date=row.get("record_date"),  # type: ignore[arg-type]
        ex_date=row.get("ex_date"),  # type: ignore[arg-type]
    )


def _run_pipeline(market: MarketTarget) -> dict[str, object]:
    """Run the full MockProvider pipeline for a market and return the results."""
    engine = create_engine(_TEST_DATABASE_URL)
    _ensure_migrated()
    _reset_database(engine)

    provider = create_provider(market, "mock")
    with engine.begin() as connection:
        repository = Repository(connection)
        run_id = uuid.uuid4().hex
        repository.create_ingestion_run(market.value, run_id, "mock", datetime.now(timezone.utc))
        symbols = [str(row["symbol"]) for row in provider.list_securities(market)]
        counts: dict[str, int] = {
            "securities": SecuritiesIngestor(repository, run_id=run_id).ingest(provider, market)
        }
        for symbol in symbols:
            counts["daily_quotes"] = counts.get("daily_quotes", 0) + DailyQuoteIngestor(
                repository, run_id=run_id
            ).ingest(provider, market, symbol, _QUOTE_START, _RANGE_END)
            counts["dividends"] = counts.get("dividends", 0) + DividendIngestor(
                repository, run_id=run_id
            ).ingest(provider, market, symbol, _EVENT_START, _RANGE_END)
            counts["financials"] = counts.get("financials", 0) + FinancialIngestor(
                repository, run_id=run_id
            ).ingest(provider, market, symbol)
            counts["corporate_actions"] = counts.get("corporate_actions", 0) + (
                CorporateActionIngestor(repository, run_id=run_id).ingest(
                    provider, market, symbol, _EVENT_START, _RANGE_END
                )
            )

    symbol = symbols[0]
    with engine.connect() as connection:
        repository = Repository(connection)
        quote_rows = [
            dict(row)
            for row in connection.execute(
                repository.list_daily_quotes(market.value, symbol=symbol)
            ).mappings()
        ]
        action_rows = [
            dict(row)
            for row in connection.execute(
                repository.list_corporate_actions(market.value, symbol=symbol)
            ).mappings()
        ]

    trading_days = sorted(row["date"] for row in quote_rows)  # type: ignore[arg-type]
    close_prices = {row["date"]: float(row["close"]) for row in quote_rows}  # type: ignore[arg-type]
    factors = compute_adjustment_factors(
        market,
        symbol,
        trading_days,
        close_prices,
        [_as_adjustment_event(row) for row in action_rows],
    )

    position_date = _EVENT_START
    with engine.begin() as connection:
        repository = Repository(connection)
        repository.upsert_positions(
            market.value,
            [
                {
                    "market": market.value,
                    "symbol": symbol,
                    "date": position_date,
                    "quantity": _POSITION_QUANTITY,
                    "cost_basis": 1000.0,
                    "market_value": 1500.0,
                }
            ],
        )
    equity_rows = compute_equity_entitlement(
        market,
        symbol,
        position_date,
        _POSITION_QUANTITY,
        [_as_entitlement_event(row) for row in action_rows],
    )

    return {
        "market": market.value,
        "symbol": symbol,
        "counts": counts,
        "trading_day_count": len(trading_days),
        "action_count": len(action_rows),
        "adjusted_factors": factors,
        "equity_events": equity_rows,
    }


@unittest.skipUnless(_TEST_DATABASE_URL, "HARBOR_TEST_DATABASE_URL is not set")
class MockHkIntegrationTests(unittest.TestCase):
    """SP 1.100: full MockProvider pipeline for Hong Kong."""

    def test_full_pipeline_hk(self) -> None:
        result = _run_pipeline(MarketTarget.HK)
        counts = result["counts"]

        self.assertEqual(counts["securities"], 16)
        self.assertGreater(counts["daily_quotes"], 1000)
        self.assertGreater(counts["dividends"], 0)
        self.assertEqual(counts["financials"], 96)
        self.assertGreater(counts["corporate_actions"], 0)
        self.assertGreater(result["trading_day_count"], 1000)
        self.assertGreater(result["action_count"], 0)

        factors = result["adjusted_factors"]
        self.assertEqual(len(factors), result["trading_day_count"])
        self.assertTrue(all(float(row["cumulative_factor"]) > 0 for row in factors))
        self.assertTrue(any(float(row["daily_factor"]) != 1.0 for row in factors))

        equity = result["equity_events"]
        self.assertGreater(len(equity), 0)
        self.assertTrue(any(float(row["entitled_quantity"]) > 0 for row in equity))
        self.assertTrue(any(float(row["cash_amount"]) > 0 for row in equity))


@unittest.skipUnless(_TEST_DATABASE_URL, "HARBOR_TEST_DATABASE_URL is not set")
class MockUsIntegrationTests(unittest.TestCase):
    """SP 1.101: full MockProvider pipeline for United States."""

    def test_full_pipeline_us(self) -> None:
        result = _run_pipeline(MarketTarget.US)
        counts = result["counts"]

        self.assertEqual(counts["securities"], 16)
        self.assertGreater(counts["daily_quotes"], 1000)
        self.assertGreater(counts["dividends"], 0)
        self.assertEqual(counts["financials"], 96)
        self.assertGreater(counts["corporate_actions"], 0)
        self.assertGreater(result["trading_day_count"], 1000)
        self.assertGreater(result["action_count"], 0)

        factors = result["adjusted_factors"]
        self.assertEqual(len(factors), result["trading_day_count"])
        self.assertTrue(all(float(row["cumulative_factor"]) > 0 for row in factors))
        self.assertTrue(any(float(row["daily_factor"]) != 1.0 for row in factors))

        equity = result["equity_events"]
        self.assertGreater(len(equity), 0)
        self.assertTrue(any(float(row["entitled_quantity"]) > 0 for row in equity))
        self.assertTrue(any(float(row["cash_amount"]) > 0 for row in equity))
