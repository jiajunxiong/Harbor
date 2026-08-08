"""Pre-release data isolation review (MVP 2 / SP 2.88).

Confirms that every securities / price / factor / fill and result query is
bounded by ``market`` and/or the run id (以 ``market`` 及运行 ID 作为边界), so no
query can leak another market's rows or another run's results. The review is
done at two DB-free levels so it runs everywhere:

- signature audit: every market-data repository method declares ``market`` and
  every backtest result method declares ``run_id`` (per-symbol result methods
  also declare ``market``);
- compiled-SQL audit: the actual ``Select`` statements are compiled with bound
  literals and asserted to carry the boundary predicate in the ``WHERE`` clause
  (e.g. ``market = 'HK'`` / ``backtest_run_id = 'run-a'``), so the isolation is
  enforced by the query, not just by a parameter name.

The functional market-isolation of the storage reader is verified separately
against a real database in SP 2.78 (``test_backtest_data_reader_integration``);
this review locks in the repository-level contract.
"""

import inspect
import unittest

from harbor.storage.backtest_repositories import BacktestRepository
from harbor.storage.repositories import Repository

# Market-data repository methods (securities / prices / factors / fills inputs).
_MARKET_SCOPED_METHODS = (
    "upsert_securities",
    "upsert_daily_quotes",
    "upsert_dividends",
    "upsert_financials",
    "upsert_fundamentals",
    "upsert_corporate_actions",
    "upsert_action_terms",
    "upsert_positions",
    "upsert_equity_events",
    "upsert_adjusted_factors",
    "list_securities",
    "list_daily_quotes",
    "list_corporate_actions",
    "list_adjusted_factors",
    "list_action_terms",
    "list_dividends",
    "list_financials",
    "list_positions",
)

# Backtest result methods, all keyed by the run id.
_RUN_ID_SCOPED_METHODS = (
    "create_run",
    "update_run",
    "get_run",
    "insert_net_values",
    "insert_positions",
    "insert_fills",
    "insert_rebalances",
    "insert_metrics",
    "insert_rejected_trades",
    "list_net_values",
    "list_positions",
    "list_fills",
    "list_rebalances",
    "list_metrics",
    "list_rejected_trades",
    "insert_factor_snapshot",
    "list_factor_snapshots",
)

# Per-symbol result methods must ALSO be market-scoped (成交/因子 carry a market).
_MARKET_AND_RUN_ID_METHODS = (
    "insert_positions",
    "insert_fills",
    "insert_rebalances",
    "insert_rejected_trades",
    "list_positions",
    "list_fills",
    "list_rebalances",
    "list_rejected_trades",
    "insert_factor_snapshot",
    "list_factor_snapshots",
)


def _params(func: object) -> set[str]:
    """Return the parameter names of a callable."""
    return set(inspect.signature(func).parameters)  # type: ignore[arg-type]


def _sql(statement: object) -> str:
    """Compile a SQLAlchemy statement to SQL text with bound literals."""
    return str(statement.compile(compile_kwargs={"literal_binds": True}))  # type: ignore[attr-defined]


class MarketBoundarySignatureTests(unittest.TestCase):
    """Every securities / price / factor-input method declares ``market``."""

    def test_every_market_data_method_declares_market(self) -> None:
        for name in _MARKET_SCOPED_METHODS:
            with self.subTest(method=name):
                self.assertIn("market", _params(getattr(Repository, name)))


class RunIdBoundarySignatureTests(unittest.TestCase):
    """Every backtest result method declares the run id boundary."""

    def test_every_result_method_declares_run_id(self) -> None:
        for name in _RUN_ID_SCOPED_METHODS:
            with self.subTest(method=name):
                self.assertIn("run_id", _params(getattr(BacktestRepository, name)))

    def test_per_symbol_result_methods_also_declare_market(self) -> None:
        for name in _MARKET_AND_RUN_ID_METHODS:
            with self.subTest(method=name):
                params = _params(getattr(BacktestRepository, name))
                self.assertIn("run_id", params)
                self.assertIn("market", params)


class CompiledQueryBoundaryTests(unittest.TestCase):
    """The compiled WHERE clauses actually enforce the boundaries."""

    def test_securities_query_is_market_scoped(self) -> None:
        sql = _sql(Repository(None).list_securities("HK"))  # type: ignore[arg-type]
        self.assertIn("market = 'HK'", sql)

    def test_prices_query_is_market_scoped(self) -> None:
        sql = _sql(Repository(None).list_daily_quotes("HK"))  # type: ignore[arg-type]
        self.assertIn("market = 'HK'", sql)

    def test_market_boundary_is_exclusive(self) -> None:
        """A US query must never be able to return HK rows."""
        sql = _sql(Repository(None).list_daily_quotes("US"))  # type: ignore[arg-type]
        self.assertIn("market = 'US'", sql)
        self.assertNotIn("market = 'HK'", sql)

    def test_net_values_query_is_run_scoped(self) -> None:
        sql = _sql(BacktestRepository(None).list_net_values("run-a"))  # type: ignore[arg-type]
        self.assertIn("backtest_run_id = 'run-a'", sql)

    def test_metrics_query_is_run_scoped(self) -> None:
        sql = _sql(BacktestRepository(None).list_metrics("run-a"))  # type: ignore[arg-type]
        self.assertIn("backtest_run_id = 'run-a'", sql)

    def test_fills_query_is_market_and_run_scoped(self) -> None:
        sql = _sql(BacktestRepository(None).list_fills("HK", "run-a"))  # type: ignore[arg-type]
        self.assertIn("backtest_run_id = 'run-a'", sql)
        self.assertIn("market = 'HK'", sql)

    def test_factor_snapshots_query_is_market_and_run_scoped(self) -> None:
        sql = _sql(  # type: ignore[arg-type]
            BacktestRepository(None).list_factor_snapshots("HK", "run-a")
        )
        self.assertIn("backtest_run_id = 'run-a'", sql)
        self.assertIn("market = 'HK'", sql)

    def test_per_symbol_result_queries_are_market_and_run_scoped(self) -> None:
        for name in ("list_positions", "list_rebalances", "list_rejected_trades"):
            with self.subTest(method=name):
                sql = _sql(getattr(BacktestRepository(None), name)("HK", "run-a"))  # type: ignore[arg-type]
                self.assertIn("backtest_run_id = 'run-a'", sql)
                self.assertIn("market = 'HK'", sql)

    def test_run_master_query_is_run_keyed(self) -> None:
        sql = _sql(BacktestRepository(None).get_run("run-a"))  # type: ignore[arg-type]
        self.assertIn("run_id = 'run-a'", sql)


if __name__ == "__main__":
    unittest.main()
