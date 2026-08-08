"""Backtest run service tests (MVP 2 / SP 2.67).

Verifies the orchestration of a CLI backtest run: loading the versioned
configuration (SP 2.5), computing the identity (SP 2.48), creating the run
record (SP 2.6), executing the SP 2.47/2.51 pipeline, persisting the day-by-day
results (SP 2.7), updating the status (SP 2.6) and returning the run id and
status. The universe assembly and the default pool-based selection are covered
with fake readers, so no database is required.
"""

import json
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from harbor.core.backtest_config import BacktestConfig
from harbor.core.backtest_domain import BacktestStatus, Currency, Market
from harbor.core.backtest_interfaces import DailyQuote, Dividend
from harbor.core.backtest_runner import MockUniverse
from harbor.core.equity import EntitlementEvent
from harbor.core.stock_pool import StockPoolMembership, evaluate_stock_pool
from harbor.core.trading_calendar import MarketTradingCalendar
from harbor.services.backtest import (
    BacktestCommandResult,
    BacktestReportError,
    BacktestServiceError,
    BacktestShowError,
    BacktestShowResult,
    BacktestUniverseReader,
    _artifact_from_rows,
    _show_backtest_from_rows,
    build_universe,
    pool_selections,
    report_backtest,
    run_backtest_command,
    run_backtest_from_config,
    show_backtest,
)

HK = Market.HK
US = Market.US
HKD = Currency.HKD
USD = Currency.USD

_DAYS = (
    date(2024, 1, 2),
    date(2024, 1, 3),
    date(2024, 1, 4),
    date(2024, 1, 5),
    date(2024, 1, 8),
)


class RecordingBacktestRepository:
    """An in-memory stand-in for the SP 2.6/2.7 backtest repository."""

    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.updates: list[dict[str, object]] = []
        self.net_value_rows: list[dict[str, object]] = []
        self.fill_rows: dict[str, list[dict[str, object]]] = {}
        self.rejected_rows: dict[str, list[dict[str, object]]] = {}

    def create_run(self, **kwargs: object) -> int:
        self.created.append(kwargs)
        return 1

    def update_run(
        self,
        *,
        run_id: str,
        status: str,
        finished_at: object | None = None,
        error_summary: str | None = None,
    ) -> int:
        self.updates.append(
            {
                "run_id": run_id,
                "status": status,
                "finished_at": finished_at,
                "error_summary": error_summary,
            }
        )
        return 1

    def insert_net_values(self, run_id: str, rows: Sequence[Mapping[str, object]]) -> int:
        self.net_value_rows = list(rows)
        return len(rows)

    def insert_fills(self, market: str, run_id: str, rows: Sequence[Mapping[str, object]]) -> int:
        self.fill_rows[market] = list(rows)
        return len(rows)

    def insert_rejected_trades(
        self, market: str, run_id: str, rows: Sequence[Mapping[str, object]]
    ) -> int:
        self.rejected_rows[market] = list(rows)
        return len(rows)


def _calendar() -> MarketTradingCalendar:
    return MarketTradingCalendar({HK: frozenset(), US: frozenset()})


def _quote(*, day: date, close: float, symbol: str = "AAPL", market: Market = US) -> DailyQuote:
    return DailyQuote(
        market=market,
        symbol=symbol,
        day=day,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1_000_000,
        adjusted_close=close,
    )


def _us_universe() -> MockUniverse:
    """A US-only universe with a flat AAPL series and one rebalance day."""
    return MockUniverse(
        calendar=_calendar(),
        quotes={(US, "AAPL"): {day: _quote(day=day, close=100.0) for day in _DAYS}},
        selections={(US, _DAYS[0]): ("AAPL",)},
    )


def _cross_market_universe() -> MockUniverse:
    """A HK+US universe with no FX rates (cross-market merge must refuse)."""
    return MockUniverse(
        calendar=_calendar(),
        quotes={
            (HK, "0001.HK"): {
                day: _quote(day=day, close=50.0, symbol="0001.HK", market=HK) for day in _DAYS
            },
            (US, "AAPL"): {day: _quote(day=day, close=100.0) for day in _DAYS},
        },
        selections={
            (HK, _DAYS[0]): ("0001.HK",),
            (US, _DAYS[0]): ("AAPL",),
        },
    )


_ZERO_COST = (
    "    commission_rate: 0\n"
    "    min_commission: 0\n"
    "    stamp_duty_rate: 0\n"
    "    transaction_levy_rate: 0\n"
    "    trading_fee_rate: 0\n"
    "    regulatory_fee_rate: 0\n"
    "    slippage_bps: 0\n"
    "    lot_size: 1"
)


def _write_config(
    tmp: Path,
    *,
    markets: str = "US",
    quota_market: str = "US",
    base: str = "USD",
    target: int = 1,
    weight: float = 1.0,
    start: str = "2024-01-02",
    end: str = "2024-01-08",
    initial: float = 100_000.0,
    cost: str = _ZERO_COST,
) -> str:
    path = tmp / "strategy.yaml"
    path.write_text(
        f"""\
strategy: shareholder-return
strategy_version: "1.0.0"
markets:
  - {markets}
market_quotas:
  - market: {quota_market}
    target_count: {target}
    weight: {weight}
start_date: "{start}"
end_date: "{end}"
base_currency: {base}
rebalance_frequency: quarterly
initial_capital: {initial}
cost:
{cost}
""",
        encoding="utf-8",
    )
    return str(path)


def _write_cross_market_config(tmp: Path) -> str:
    path = tmp / "cross.yaml"
    path.write_text(
        """\
strategy: shareholder-return
strategy_version: "1.0.0"
markets:
  - HK
  - US
market_quotas:
  - market: HK
    target_count: 1
    weight: 0.5
  - market: US
    target_count: 1
    weight: 0.5
start_date: "2024-01-02"
end_date: "2024-01-08"
base_currency: HKD
rebalance_frequency: quarterly
initial_capital: 1000000
""",
        encoding="utf-8",
    )
    return str(path)


class FakeReader(BacktestUniverseReader):
    """A fake reader returning a fixed pool, quotes, dividends and actions."""

    def __init__(
        self,
        *,
        pool: Mapping[Market, tuple[str, ...]],
        quotes: Mapping[tuple[Market, str], Mapping[date, DailyQuote]] | None = None,
        dividends: Mapping[tuple[Market, str], tuple[Dividend, ...]] | None = None,
        corporate_actions: Mapping[tuple[Market, str], tuple[EntitlementEvent, ...]] | None = None,
        fx_rates: Mapping[tuple[Currency, Currency], Mapping[date, float]] | None = None,
    ) -> None:
        self._pool = pool
        self._quotes = quotes or {}
        self._dividends = dividends or {}
        self._corporate_actions = corporate_actions or {}
        self._fx_rates = fx_rates or {}

    def stock_pool(self, market: Market, as_of: date, *, historical_known: bool):
        memberships = tuple(
            StockPoolMembership(
                market=market,
                symbol=symbol,
                effective_date=as_of,
                expiry_date=None,
                source="test",
            )
            for symbol in self._pool.get(market, ())
        )
        return evaluate_stock_pool(
            market, as_of, memberships, "test", historical_known=historical_known
        )

    def daily_quotes(
        self, market: Market, symbol: str, start: date, end: date
    ) -> Sequence[DailyQuote]:
        return [
            quote
            for day, quote in sorted(self._quotes.get((market, symbol), {}).items())
            if start <= day <= end
        ]

    def dividends(self, market: Market, symbol: str, start: date, end: date) -> Sequence[Dividend]:
        return [
            dividend
            for dividend in self._dividends.get((market, symbol), ())
            if start <= dividend.ex_date <= end
        ]

    def corporate_actions(
        self, market: Market, symbol: str, start: date, end: date
    ) -> Sequence[EntitlementEvent]:
        return [
            event
            for event in self._corporate_actions.get((market, symbol), ())
            if (event.ex_date or event.record_date) is not None
            and start <= (event.ex_date or event.record_date) <= end
        ]

    def fx_rate(self, from_currency: Currency, to_currency: Currency, as_of: date) -> float | None:
        rates = self._fx_rates.get((from_currency, to_currency), {})
        candidates = [day for day in rates if day <= as_of]
        return rates[max(candidates)] if candidates else None


def _config(path: str) -> BacktestConfig:
    from harbor.core.backtest_config_loader import load_backtest_config

    return load_backtest_config(path)


class RunBacktestCommandTests(unittest.TestCase):
    """Verify the run orchestration (SP 2.67)."""

    def test_completed_run_returns_run_id_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_config(Path(tmp))
            repository = RecordingBacktestRepository()
            result = run_backtest_command(
                config_path=config_path,
                code_version="2.0.0",
                data_cutoff=None,
                repository=repository,
                universe=_us_universe(),
            )
        self.assertEqual(result.status, BacktestStatus.COMPLETED)
        self.assertTrue(result.run_id)
        created = repository.created[0]
        self.assertEqual(created["status"], "RUNNING")
        self.assertEqual(created["strategy"], "shareholder-return")
        self.assertEqual(created["code_version"], "2.0.0")
        self.assertEqual(created["data_cutoff"], date(2024, 1, 8))
        # The run is first created RUNNING, then updated to COMPLETED.
        self.assertEqual(repository.updates[-1]["status"], "COMPLETED")
        self.assertIsNone(repository.updates[-1]["error_summary"])

    def test_completed_run_persists_net_values_and_fills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_config(Path(tmp))
            repository = RecordingBacktestRepository()
            run_backtest_command(
                config_path=config_path,
                code_version="2.0.0",
                data_cutoff=None,
                repository=repository,
                universe=_us_universe(),
            )
        self.assertEqual(len(repository.net_value_rows), len(_DAYS))
        self.assertEqual(repository.net_value_rows[0]["as_of_date"], _DAYS[0])
        fills = repository.fill_rows["US"]
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0]["market"], "US")
        self.assertEqual(fills[0]["symbol"], "AAPL")
        self.assertEqual(fills[0]["side"], "BUY")

    def test_explicit_data_cutoff_overrides_config_end(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_config(Path(tmp))
            repository = RecordingBacktestRepository()
            result = run_backtest_command(
                config_path=config_path,
                code_version="2.0.0",
                data_cutoff=date(2024, 1, 5),
                repository=repository,
                universe=_us_universe(),
            )
        self.assertEqual(result.status, BacktestStatus.COMPLETED)
        self.assertEqual(repository.created[0]["data_cutoff"], date(2024, 1, 5))

    def test_cross_market_without_fx_returns_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_cross_market_config(Path(tmp))
            repository = RecordingBacktestRepository()
            result = run_backtest_command(
                config_path=config_path,
                code_version="2.0.0",
                data_cutoff=None,
                repository=repository,
                universe=_cross_market_universe(),
            )
        self.assertEqual(result.status, BacktestStatus.FAILED)
        self.assertEqual(repository.updates[-1]["status"], "FAILED")
        self.assertIsNotNone(repository.updates[-1]["error_summary"])

    def test_invalid_config_raises_service_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.yaml"
            bad.write_text("markets: [unclosed\n", encoding="utf-8")
            repository = RecordingBacktestRepository()
            with self.assertRaisesRegex(BacktestServiceError, "Cannot load backtest config"):
                run_backtest_command(
                    config_path=str(bad),
                    code_version="2.0.0",
                    data_cutoff=None,
                    repository=repository,
                    universe=_us_universe(),
                )
        self.assertEqual(repository.created, [])


class BuildUniverseTests(unittest.TestCase):
    """Verify the storage-backed universe assembly."""

    def test_build_universe_reads_pool_quotes_and_selections(self) -> None:
        quotes = {(US, "AAPL"): {day: _quote(day=day, close=100.0) for day in _DAYS}}
        reader = FakeReader(pool={US: ("AAPL",)}, quotes=quotes)
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(_write_config(Path(tmp)))
            universe = build_universe(
                reader=reader,
                calendar=_calendar(),
                config=config,
                selections={(US, _DAYS[0]): ("AAPL",)},
            )
        self.assertIsInstance(universe, MockUniverse)
        self.assertEqual(set(universe.quotes), {(US, "AAPL")})
        self.assertEqual(set(universe.quotes[(US, "AAPL")]), set(_DAYS))
        self.assertEqual(universe.selections[(US, _DAYS[0])], ("AAPL",))

    def test_build_universe_defaults_empty_selections_and_fx(self) -> None:
        reader = FakeReader(pool={US: ("AAPL",)})
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(_write_config(Path(tmp)))
            universe = build_universe(reader=reader, calendar=_calendar(), config=config)
        self.assertEqual(universe.selections, {})
        self.assertEqual(universe.fx_rates, {})


class PoolSelectionsTests(unittest.TestCase):
    """Verify the default pool-based selection per rebalance day."""

    def test_pool_selections_covers_rebalance_days(self) -> None:
        reader = FakeReader(pool={US: ("AAPL", "MSFT")})
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(
                _write_config(
                    Path(tmp),
                    start="2024-01-01",
                    end="2024-01-05",
                    markets="US",
                    quota_market="US",
                )
            )
            selections = pool_selections(reader=reader, calendar=_calendar(), config=config)
        self.assertEqual(set(selections), {(US, date(2024, 1, 1))})
        self.assertEqual(selections[(US, date(2024, 1, 1))], ("AAPL", "MSFT"))


class RunBacktestFromConfigTests(unittest.TestCase):
    """Verify the CLI glue assembles reader, universe and run together."""

    def test_run_backtest_from_config_wires_components(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_config(Path(tmp))
            connection = object()
            universe = _us_universe()
            with (
                patch("harbor.services.backtest.BacktestRepository") as repo_cls,
                patch("harbor.services.backtest.StorageBacktestDataReader") as reader_cls,
                patch(
                    "harbor.services.backtest.pool_selections",
                    return_value={(US, _DAYS[0]): ("AAPL",)},
                ),
                patch(
                    "harbor.services.backtest.build_universe",
                    return_value=universe,
                ) as build_mock,
                patch(
                    "harbor.services.backtest.run_backtest_command",
                    return_value=BacktestCommandResult(
                        run_id="run-1", status=BacktestStatus.COMPLETED
                    ),
                ) as run_mock,
            ):
                result = run_backtest_from_config(
                    config_path=config_path,
                    code_version="2.0.0",
                    data_cutoff=None,
                    connection=connection,
                )
        self.assertEqual(result.run_id, "run-1")
        repo_cls.assert_called_once_with(connection)
        reader_cls.assert_called_once_with(connection)
        build_mock.assert_called_once()
        run_mock.assert_called_once()
        kwargs = run_mock.call_args.kwargs
        self.assertEqual(kwargs["config_path"], config_path)
        self.assertEqual(kwargs["code_version"], "2.0.0")
        self.assertIs(kwargs["universe"], universe)


def _run_row(
    *,
    run_id: str = "run-1",
    status: str = "COMPLETED",
    error_summary: str | None = None,
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "config_hash": "hash-1",
        "config_snapshot": {
            "markets": ["US"],
            "market_quotas": [{"market": "US", "target_count": 1, "weight": 1.0}],
            "start_date": "2024-01-02",
            "end_date": "2024-01-08",
            "base_currency": "USD",
            "initial_capital": 100_000.0,
        },
        "strategy": "shareholder-return",
        "strategy_version": "1.0.0",
        "code_version": "2.0.0",
        "data_cutoff": date(2024, 1, 8),
        "status": status,
        "started_at": datetime(2024, 1, 2, tzinfo=timezone.utc),
        "finished_at": datetime(2024, 1, 8, tzinfo=timezone.utc),
        "error_summary": error_summary,
    }


class FakeResult:
    def __init__(self, rows: Sequence[Mapping[str, object]]) -> None:
        self._rows = list(rows)

    def mappings(self) -> list[Mapping[str, object]]:
        return list(self._rows)


class FakeRepository:
    def __init__(
        self,
        run_rows: Sequence[Mapping[str, object]] = (),
        net_value_rows: Sequence[Mapping[str, object]] = (),
        metric_rows: Sequence[Mapping[str, object]] = (),
        position_rows: Sequence[Mapping[str, object]] = (),
        fill_rows: Sequence[Mapping[str, object]] = (),
        refused_rows: Sequence[Mapping[str, object]] = (),
    ) -> None:
        self._run_rows = list(run_rows)
        self._net_value_rows = list(net_value_rows)
        self._metric_rows = list(metric_rows)
        self._position_rows = list(position_rows)
        self._fill_rows = list(fill_rows)
        self._refused_rows = list(refused_rows)

    def get_run(self, run_id: str) -> tuple[str, str]:
        return ("run", run_id)

    def list_net_values(self, run_id: str) -> tuple[str, str]:
        return ("net_values", run_id)

    def list_metrics(self, run_id: str) -> tuple[str, str]:
        return ("metrics", run_id)

    def list_positions(self, market: str, run_id: str) -> tuple[str, str, str]:
        return ("positions", market, run_id)

    def list_fills(self, market: str, run_id: str) -> tuple[str, str, str]:
        return ("fills", market, run_id)

    def list_rejected_trades(self, market: str, run_id: str) -> tuple[str, str, str]:
        return ("rejected", market, run_id)


class FakeConnection:
    def __init__(self, repository: FakeRepository) -> None:
        self._repository = repository

    def execute(self, statement: tuple[str, ...]) -> FakeResult:
        kind = statement[0]
        if kind == "run":
            return FakeResult(self._repository._run_rows)
        if kind == "net_values":
            return FakeResult(self._repository._net_value_rows)
        if kind == "metrics":
            return FakeResult(self._repository._metric_rows)
        if kind == "positions":
            return FakeResult(self._repository._position_rows)
        if kind == "fills":
            return FakeResult(self._repository._fill_rows)
        if kind == "rejected":
            return FakeResult(self._repository._refused_rows)
        raise AssertionError(f"unexpected statement {statement!r}")


class ShowBacktestTests(unittest.TestCase):
    """Verify the SP 2.68 status view."""

    def test_show_from_rows_computes_core_metrics(self) -> None:
        net_values = [
            {"as_of_date": date(2024, 1, 2), "total_value": 100_000.0},
            {"as_of_date": date(2024, 1, 3), "total_value": 105_000.0},
        ]
        metrics = [{"metric_name": "sharpe", "value": 1.25}]
        result = _show_backtest_from_rows(_run_row(), net_values, metrics)
        self.assertIsInstance(result, BacktestShowResult)
        self.assertEqual(result.audit.run_id, "run-1")
        self.assertEqual(result.audit.status, BacktestStatus.COMPLETED)
        self.assertEqual(result.day_count, 2)
        self.assertAlmostEqual(result.net_value_first, 100_000.0)
        self.assertAlmostEqual(result.net_value_last, 105_000.0)
        self.assertAlmostEqual(result.cumulative_return, 0.05, places=6)
        self.assertEqual(result.metrics, {"sharpe": 1.25})

    def test_show_from_rows_empty_net_values(self) -> None:
        result = _show_backtest_from_rows(_run_row(), (), ())
        self.assertEqual(result.day_count, 0)
        self.assertIsNone(result.net_value_first)
        self.assertIsNone(result.net_value_last)
        self.assertIsNone(result.cumulative_return)
        self.assertEqual(result.metrics, {})

    def test_show_dict_renders_config_range_status_metrics(self) -> None:
        result = _show_backtest_from_rows(_run_row(), (), ())
        data = result.to_dict()
        self.assertEqual(data["run_id"], "run-1")
        self.assertEqual(data["status"], "COMPLETED")
        self.assertEqual(data["markets"], ["US"])
        self.assertEqual(data["data_range"]["start"], "2024-01-02")
        self.assertEqual(data["data_range"]["end"], "2024-01-08")
        self.assertEqual(data["data_range"]["cutoff"], "2024-01-08")
        self.assertEqual(data["failure_reason"], None)

    def test_show_readable(self) -> None:
        result = _show_backtest_from_rows(_run_row(), (), ())
        text = result.readable()
        self.assertIn("Backtest run run-1", text)
        self.assertIn("status: COMPLETED", text)
        self.assertIn("data range: 2024-01-02 -> 2024-01-08", text)
        self.assertIn("day count: 0", text)

    def test_show_failure_reason_from_error_summary(self) -> None:
        result = _show_backtest_from_rows(_run_row(status="FAILED", error_summary="boom"), (), ())
        self.assertEqual(result.audit.failure_reason, "boom")
        self.assertIn("failure reason: boom", result.readable())

    def test_show_backtest_fetches_run_and_metrics(self) -> None:
        net_values = [
            {"as_of_date": date(2024, 1, 2), "total_value": 100_000.0},
            {"as_of_date": date(2024, 1, 3), "total_value": 102_000.0},
        ]
        repository = FakeRepository(
            [_run_row()], net_values, [{"metric_name": "sharpe", "value": 1.5}]
        )
        connection = FakeConnection(repository)
        with patch("harbor.services.backtest.BacktestRepository", return_value=repository):
            result = show_backtest(connection=connection, run_id="run-1")
        self.assertEqual(result.audit.run_id, "run-1")
        self.assertAlmostEqual(result.cumulative_return, 0.02, places=6)
        self.assertEqual(result.metrics, {"sharpe": 1.5})

    def test_show_backtest_missing_run_raises(self) -> None:
        repository = FakeRepository()
        connection = FakeConnection(repository)
        with patch("harbor.services.backtest.BacktestRepository", return_value=repository):
            with self.assertRaisesRegex(BacktestShowError, "No backtest run found"):
                show_backtest(connection=connection, run_id="nope")


def _net_value_row(*, as_of: date, total: float = 100_000.0) -> dict[str, object]:
    return {
        "as_of_date": as_of,
        "currency": "USD",
        "cash": 80_000.0,
        "securities_value": 20_000.0,
        "fees_paid": 0.0,
        "total_value": total,
    }


def _fill_row(*, day: date = date(2024, 1, 2)) -> dict[str, object]:
    return {
        "trade_date": day,
        "order_ref": "rebalance:2024-01-02",
        "market": "US",
        "symbol": "AAPL",
        "side": "BUY",
        "quantity": 200.0,
        "price": 100.0,
        "currency": "USD",
        "fee": 0.0,
    }


class ReportBacktestTests(unittest.TestCase):
    """Verify the SP 2.69 report assembly and rendering."""

    def test_artifact_from_rows_builds_sections(self) -> None:
        net_values = [_net_value_row(as_of=date(2024, 1, 2))]
        fills = [_fill_row()]
        refused = [
            {
                "market": "US",
                "symbol": "MSFT",
                "side": "BUY",
                "quantity": 10.0,
                "reason": "suspended",
                "order_ref": "r",
            }
        ]
        artifact = _artifact_from_rows(_run_row(), net_values, [], fills, refused, [])
        self.assertEqual(artifact["run"]["run_id"], "run-1")
        self.assertTrue(artifact["run"]["succeeded"])
        self.assertEqual(artifact["run"]["day_count"], 1)
        self.assertEqual(artifact["net_values"][0]["total_value"], 100_000.0)
        self.assertIsNone(artifact["net_values"][0]["fx_pnl"])
        self.assertEqual(artifact["trades"][0]["notional"], 20_000.0)
        self.assertEqual(artifact["trades"][0]["date"], "2024-01-02")
        self.assertEqual(artifact["refused"][0]["reason"], "suspended")
        self.assertIsNone(artifact["refused"][0]["date"])
        self.assertEqual(artifact["positions"], [])
        self.assertEqual(artifact["dividends"], [])
        self.assertEqual(artifact["corporate_actions"], [])
        self.assertEqual(artifact["warnings"], [])
        self.assertIsNone(artifact["metrics"]["performance"])

    def test_report_backtest_renders_json(self) -> None:
        repository = FakeRepository([_run_row()], [_net_value_row(as_of=date(2024, 1, 2))])
        connection = FakeConnection(repository)
        with patch("harbor.services.backtest.BacktestRepository", return_value=repository):
            output = report_backtest(connection=connection, run_id="run-1", report_format="json")
        data = json.loads(output)
        self.assertEqual(data["run"]["run_id"], "run-1")
        self.assertEqual(data["net_values"][0]["total_value"], 100_000.0)

    def test_report_backtest_renders_csv(self) -> None:
        repository = FakeRepository([_run_row()], [_net_value_row(as_of=date(2024, 1, 2))])
        connection = FakeConnection(repository)
        with patch("harbor.services.backtest.BacktestRepository", return_value=repository):
            output = report_backtest(connection=connection, run_id="run-1", report_format="csv")
        self.assertIn("# net_values", output)
        self.assertIn("backtest_run_id", output)
        self.assertIn("# factor_snapshots", output)

    def test_report_backtest_renders_html(self) -> None:
        repository = FakeRepository([_run_row()], [_net_value_row(as_of=date(2024, 1, 2))])
        connection = FakeConnection(repository)
        with patch("harbor.services.backtest.BacktestRepository", return_value=repository):
            output = report_backtest(connection=connection, run_id="run-1", report_format="html")
        self.assertIn("回测研究报告", output)
        self.assertIn("run-1", output)

    def test_report_backtest_missing_run_raises(self) -> None:
        repository = FakeRepository()
        connection = FakeConnection(repository)
        with patch("harbor.services.backtest.BacktestRepository", return_value=repository):
            with self.assertRaisesRegex(BacktestReportError, "No backtest run found"):
                report_backtest(connection=connection, run_id="nope", report_format="json")

    def test_report_backtest_invalid_format_raises(self) -> None:
        repository = FakeRepository([_run_row()], [_net_value_row(as_of=date(2024, 1, 2))])
        connection = FakeConnection(repository)
        with patch("harbor.services.backtest.BacktestRepository", return_value=repository):
            with self.assertRaisesRegex(BacktestReportError, "Unknown report format"):
                report_backtest(connection=connection, run_id="run-1", report_format="xml")


if __name__ == "__main__":
    unittest.main()
