"""Run-failure usability tests (MVP 2 / SP 2.76).

Verifies that the CLI and services give ACTIONABLE errors (a specific,
human-readable reason, surfaced as exit code 2 at the CLI) for the five
failure classes: invalid config, insufficient data, missing FX, unknown
market and a missing run id. Where possible the real code path is exercised
(the CLI tests only stub the database engine); precheck and the run service
run with real fakes, so no database is required.
"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from harbor.core.backtest_config import BacktestConfig, MarketQuota
from harbor.core.backtest_domain import BacktestStatus, Currency, Market
from harbor.core.backtest_interfaces import (
    BacktestDataReader,
    DailyQuote,
    Dividend,
    FundamentalRecord,
    TradingCalendar,
)
from harbor.core.backtest_runner import MockUniverse
from harbor.core.data_readiness import PrecheckReport, run_precheck
from harbor.core.stock_pool import StockPool, StockPoolMembership
from harbor.core.trading_calendar import MarketTradingCalendar
from harbor.services.backtest import (
    BacktestCancelError,
    BacktestReportError,
    BacktestResumeError,
    BacktestServiceError,
    BacktestShowError,
    cancel_backtest,
    report_backtest,
    resume_backtest_from_config,
    run_backtest_command,
    show_backtest,
)

HKD = Currency.HKD
USD = Currency.USD
HK = Market.HK
US = Market.US

_ENVIRONMENT = {
    "DATABASE_URL": "postgresql+psycopg://harbor:secret@localhost:5432/harbor",
    "DATA_PROVIDER_HK": "mock",
    "DATA_PROVIDER_US": "mock",
}


def _config(market: Market = HK) -> BacktestConfig:
    """A minimal valid single-market config for precheck tests."""
    return BacktestConfig(
        markets=(market,),
        market_quotas=(MarketQuota(market=market, target_count=2, weight=1.0),),
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        base_currency=HKD,
    )


class _FakeCalendar(TradingCalendar):
    """Weekday-only calendar for deterministic precheck tests."""

    def is_trading_day(self, market: Market, day: date) -> bool:
        return day.weekday() < 5

    def next_trading_day(self, market: Market, day: date) -> date:
        while not self.is_trading_day(market, day):
            day += timedelta(days=1)
        return day

    def previous_trading_day(self, market: Market, day: date) -> date:
        while not self.is_trading_day(market, day):
            day -= timedelta(days=1)
        return day

    def trading_days(self, market: Market, start: date, end: date) -> list[date]:
        days: list[date] = []
        cursor = self.next_trading_day(market, start)
        while cursor <= end:
            days.append(cursor)
            cursor = self.next_trading_day(market, cursor + timedelta(days=1))
        return days

    def rebalance_days(self, market: Market, start: date, end: date) -> list[date]:
        return self.trading_days(market, start, end)


class _FakeReader(BacktestDataReader):
    """A reader returning one quote per symbol and nothing else."""

    def list_securities(self, market: Market, as_of: date) -> tuple[str, ...]:
        return ("AAPL",)

    def daily_quotes(
        self, market: Market, symbol: str, start: date, end: date
    ) -> tuple[DailyQuote, ...]:
        return (
            DailyQuote(
                market=market,
                symbol=symbol,
                day=start,
                open=1.0,
                high=1.0,
                low=1.0,
                close=1.0,
                volume=100,
                adjusted_close=1.0,
            ),
        )

    def dividends(
        self, market: Market, symbol: str, start: date, end: date
    ) -> tuple[Dividend, ...]:
        return ()

    def fundamentals(
        self, market: Market, symbol: str, as_of: date
    ) -> tuple[FundamentalRecord, ...]:
        return ()

    def corporate_actions(
        self, market: Market, symbol: str, start: date, end: date
    ) -> tuple[object, ...]:
        return ()

    def adjustment_factors(
        self, market: Market, symbol: str, start: date, end: date
    ) -> tuple[object, ...]:
        return ()


def _pool(*, symbols: tuple[str, ...] = ("AAPL",)) -> StockPool:
    memberships = tuple(
        StockPoolMembership(
            market=HK,
            symbol=symbol,
            effective_date=date(2010, 1, 1),
            expiry_date=None,
            source="hkex_universe",
        )
        for symbol in symbols
    )
    return StockPool(
        market=HK,
        as_of=date(2026, 1, 1),
        source="hkex_universe",
        memberships=memberships,
        survivorship_bias_risk=False,
    )


def _precheck(
    config: BacktestConfig,
    *,
    reader: BacktestDataReader,
    symbols: tuple[str, ...] = ("AAPL",),
    fx_rate,
) -> PrecheckReport:
    return run_precheck(
        config,
        reader,
        _FakeCalendar(),
        fx_rate=fx_rate,
        stock_pool=lambda market, as_of: _pool(symbols=symbols),
    )


class InvalidConfigCliTests(unittest.TestCase):
    """The CLI turns invalid config into actionable exit-code-2 errors."""

    def _run_backtest(self, content: str) -> tuple[int, str]:
        from harbor.cli import main

        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "strategy.yaml"
            path.write_text(content, encoding="utf-8")
            with (
                patch.dict(os.environ, _ENVIRONMENT, clear=True),
                patch("harbor.cli.create_engine"),
            ):
                with self.assertRaises(SystemExit) as exit_context:
                    with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                        main(["backtest", "run", "--config", str(path)])
        return exit_context.exception.code, stderr.getvalue()

    def test_malformed_yaml_is_actionable(self) -> None:
        code, stderr = self._run_backtest("strategy: [unclosed\n")
        self.assertEqual(code, 2)
        self.assertIn("Backtest run failed", stderr)
        self.assertIn("Invalid YAML", stderr)

    def test_unknown_market_is_actionable(self) -> None:
        content = "markets: [JP]\nmarket_quotas: [{market: JP, target_count: 1, weight: 1.0}]\n"
        code, stderr = self._run_backtest(content)
        self.assertEqual(code, 2)
        self.assertIn("Backtest run failed", stderr)
        self.assertIn("HK", stderr)
        self.assertIn("US", stderr)

    def test_inverted_date_range_is_actionable(self) -> None:
        content = (
            "markets: [US]\n"
            "market_quotas: [{market: US, target_count: 1, weight: 1.0}]\n"
            'start_date: "2024-01-01"\n'
            'end_date: "2023-12-31"\n'
            "base_currency: USD\n"
        )
        code, stderr = self._run_backtest(content)
        self.assertEqual(code, 2)
        self.assertIn("Backtest run failed", stderr)
        self.assertIn("end_date must be on or after", stderr)

    def test_missing_markets_is_actionable(self) -> None:
        content = (
            "markets: []\n"
            "market_quotas: [{market: US, target_count: 1, weight: 1.0}]\n"
            'start_date: "2024-01-01"\n'
            'end_date: "2024-12-31"\n'
            "base_currency: USD\n"
        )
        code, stderr = self._run_backtest(content)
        self.assertEqual(code, 2)
        self.assertIn("Backtest run failed", stderr)
        self.assertIn("At least one market", stderr)


class InsufficientDataPrecheckTests(unittest.TestCase):
    """The precheck surfaces insufficient data as readable errors."""

    def test_empty_stock_pool_is_an_error(self) -> None:
        report = _precheck(
            _config(), reader=_FakeReader(), symbols=(), fx_rate=lambda f, t, d: 0.128
        )
        self.assertTrue(report.has_errors)
        self.assertIn("stock pool is empty", report.readable())

    def test_missing_fx_is_an_error(self) -> None:
        report = _precheck(
            _config(market=US),
            reader=_FakeReader(),
            fx_rate=lambda f, t, d: None,
        )
        self.assertTrue(report.has_errors)
        self.assertIn("missing FX USD->HKD", report.readable())
        self.assertIn("refusing to assume 1:1", report.readable())


class MissingFxRunTests(unittest.TestCase):
    """A cross-market run without FX fails with an actionable summary."""

    def test_cross_market_without_fx_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_cross_market_config(Path(tmp))
            repository = _RecordingRepository()
            result = run_backtest_command(
                config_path=config_path,
                code_version="2.0.0",
                data_cutoff=None,
                repository=repository,
                universe=_cross_market_universe(),
            )
        self.assertEqual(result.status, BacktestStatus.FAILED)
        self.assertEqual(repository.updates[-1]["status"], BacktestStatus.FAILED.value)
        summary = repository.updates[-1]["error_summary"]
        self.assertTrue(summary)
        self.assertIn("cross-market", summary)


class MissingRunIdTests(unittest.TestCase):
    """A missing run id produces an actionable 'No backtest run found' error."""

    def _service(self, fn, **kwargs):
        repository = _EmptyRepository()
        connection = _EmptyConnection()
        with patch("harbor.services.backtest.BacktestRepository", return_value=repository):
            return fn(connection=connection, **kwargs)

    def test_show_missing_run_id_is_actionable(self) -> None:
        with self.assertRaisesRegex(BacktestShowError, "No backtest run found for run id 'nope'"):
            self._service(show_backtest, run_id="nope")

    def test_report_missing_run_id_is_actionable(self) -> None:
        with self.assertRaisesRegex(BacktestReportError, "No backtest run found for run id 'nope'"):
            self._service(report_backtest, run_id="nope", report_format="json")

    def test_cancel_missing_run_id_is_actionable(self) -> None:
        with self.assertRaisesRegex(BacktestCancelError, "No backtest run found for run id 'nope'"):
            self._service(cancel_backtest, run_id="nope")

    def test_resume_missing_run_id_is_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = _write_us_config(Path(tmp))
            with self.assertRaisesRegex(
                BacktestResumeError, "No backtest run found for run id 'nope'"
            ):
                resume_backtest_from_config(
                    config_path=config_path,
                    code_version="2.0.0",
                    data_cutoff=None,
                    connection=_EmptyConnection(),
                    original_run_id="nope",
                )


class CliActionableErrorMatrixTests(unittest.TestCase):
    """Every backtest CLI command surfaces an actionable exit-code-2 error."""

    def test_all_commands_report_actionable_errors(self) -> None:
        from harbor.cli import main

        cases = (
            (
                "run",
                BacktestServiceError("boom"),
                "Backtest run failed",
                "boom",
            ),
            (
                "show",
                BacktestShowError("No backtest run found for run id 'nope'."),
                "Backtest show failed",
                "No backtest run found",
            ),
            (
                "report",
                BacktestReportError("No backtest run found for run id 'nope'."),
                "Backtest report failed",
                "No backtest run found",
            ),
            (
                "cancel",
                BacktestCancelError("No backtest run found for run id 'nope'."),
                "Backtest cancel failed",
                "No backtest run found",
            ),
            (
                "resume",
                BacktestResumeError("No backtest run found for run id 'nope'."),
                "Backtest resume failed",
                "No backtest run found",
            ),
        )
        for command, error, prefix, reason in cases:
            with self.subTest(command=command):
                stderr = io.StringIO()
                args = ["backtest", command]
                if command == "run":
                    with tempfile.TemporaryDirectory() as tmp:
                        config_path = _write_us_config(Path(tmp))
                        args += ["--config", config_path]
                elif command in ("show", "report", "cancel"):
                    args += ["nope"]
                elif command == "resume":
                    with tempfile.TemporaryDirectory() as tmp:
                        config_path = _write_us_config(Path(tmp))
                        args += ["--config", config_path, "--resume-of", "nope"]
                target = f"harbor.cli.{command}_backtest_from_config"
                if command in ("show", "report", "cancel"):
                    target = f"harbor.cli.{command}_backtest"
                with (
                    patch.dict(os.environ, _ENVIRONMENT, clear=True),
                    patch("harbor.cli.create_engine"),
                    patch(target, side_effect=error),
                ):
                    with self.assertRaises(SystemExit) as exit_context:
                        with redirect_stdout(io.StringIO()), redirect_stderr(stderr):
                            main(args)
                self.assertEqual(exit_context.exception.code, 2)
                self.assertIn(prefix, stderr.getvalue())
                self.assertIn(reason, stderr.getvalue())


# --- helpers reused from the run-service test conventions (SP 2.67) ---

_DAYS = (
    date(2024, 1, 2),
    date(2024, 1, 3),
    date(2024, 1, 4),
    date(2024, 1, 5),
    date(2024, 1, 8),
)


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


def _write_us_config(tmp: Path) -> str:
    path = tmp / "us.yaml"
    path.write_text(
        """\
strategy: shareholder-return
strategy_version: "1.0.0"
markets:
  - US
market_quotas:
  - market: US
    target_count: 1
    weight: 1.0
start_date: "2024-01-02"
end_date: "2024-01-08"
base_currency: USD
rebalance_frequency: quarterly
initial_capital: 100000
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


def _cross_market_universe() -> MockUniverse:
    """A HK+US universe with NO FX rates (cross-market merge must refuse)."""
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


class _RecordingRepository:
    """Records create_run / update_run calls (the run fails before persistence)."""

    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.updates: list[dict[str, object]] = []

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


class _EmptyRepository:
    """A repository with no runs (every lookup comes back empty)."""

    def get_run(self, run_id: str) -> tuple[str, str]:
        return ("run", run_id)


class _EmptyResult:
    def mappings(self) -> list[object]:
        return []


class _EmptyConnection:
    def execute(self, statement: object) -> _EmptyResult:
        return _EmptyResult()


if __name__ == "__main__":
    unittest.main()
