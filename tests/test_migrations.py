"""Alembic migration chain and schema tests (SP 1.99).

Verifies that the migration chain is complete and linear and that every table
in the ORM metadata (including composite primary keys) is declared by a
migration. These checks are database-free and run everywhere. A live
integration test additionally applies the migrations to a fresh PostgreSQL when
``HARBOR_TEST_DATABASE_URL`` is set (skipped otherwise).
"""

import ast
import os
import re
import unittest
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from harbor.storage.models import Base

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_ALEMBIC_DIR = _PROJECT_ROOT / "alembic"
_VERSIONS_DIR = _ALEMBIC_DIR / "versions"

_TEST_DATABASE_URL = os.getenv("HARBOR_TEST_DATABASE_URL")


def _migration_files() -> list[Path]:
    """Return the migration modules in filename order."""
    return sorted(path for path in _VERSIONS_DIR.glob("*.py") if path.name != "__init__.py")


def _constant_value(node: ast.AST) -> object:
    """Return the value of a constant AST node, or ``None``."""
    if isinstance(node, ast.Constant):
        return node.value
    return None


def _module_assignment(tree: ast.Module, name: str) -> object:
    """Return the value assigned to a module-level name, or ``None``."""
    for node in tree.body:
        if isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                return _constant_value(node.value)
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return _constant_value(node.value)
    return None


def _create_table_defs(tree: ast.Module) -> dict[str, list[str]]:
    """Return ``{table_name: [primary key columns]}`` for a migration module."""
    tables: dict[str, list[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "create_table" or not node.args:
            continue
        name = _constant_value(node.args[0])
        if not isinstance(name, str):
            continue
        primary_key: list[str] = []
        for arg in node.args[1:]:
            if (
                isinstance(arg, ast.Call)
                and isinstance(arg.func, ast.Attribute)
                and arg.func.attr == "PrimaryKeyConstraint"
            ):
                primary_key = [
                    column
                    for item in arg.args
                    if isinstance((column := _constant_value(item)), str)
                ]
        tables[name] = primary_key
    return tables


def _create_view_names(source: str) -> set[str]:
    """Return the view names created by a migration's ``CREATE VIEW`` SQL."""
    return set(re.findall(r"CREATE\s+VIEW\s+(\w+)", source, re.IGNORECASE))


def _migrated_schema() -> tuple[dict[str, list[str]], set[str]]:
    """Return the tables (with PK columns) and views created by all migrations."""
    tables: dict[str, list[str]] = {}
    views: set[str] = set()
    for path in _migration_files():
        source = path.read_text(encoding="utf-8")
        tables.update(_create_table_defs(ast.parse(source)))
        views.update(_create_view_names(source))
    return tables, views


def _fresh_engine() -> Engine:
    """Return an engine bound to a disposable, freshly-emptied schema."""
    engine = create_engine(_TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    return engine


def _upgrade_to_head(engine: Engine) -> None:
    """Apply all Alembic migrations to ``engine``'s database (SP 1.99 / 2.77)."""
    from alembic.config import Config

    from alembic import command

    config = Config(str(_PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_ALEMBIC_DIR))
    original_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = _TEST_DATABASE_URL
    try:
        command.upgrade(config, "head")
    finally:
        if original_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = original_url


class MigrationChainTests(unittest.TestCase):
    """Verify the Alembic migration chain is complete and linear."""

    def test_revision_chain_is_linear_from_base_to_head(self) -> None:
        from alembic.script import ScriptDirectory

        script = ScriptDirectory(str(_ALEMBIC_DIR))
        heads = script.get_heads()
        bases = script.get_bases()
        self.assertEqual(len(heads), 1)
        self.assertEqual(len(bases), 1)
        self.assertEqual(heads[0], "0023_create_validation_tables")
        self.assertEqual(bases[0], "0001_create_securities")

        versions = list(script.walk_revisions())
        chain = [version.revision for version in versions]
        expected = [path.stem for path in _migration_files()]
        self.assertEqual(chain, list(reversed(expected)))

        for index, version in enumerate(versions):
            expected_down = versions[index + 1].revision if index + 1 < len(versions) else None
            self.assertEqual(version.down_revision, expected_down, msg=version.revision)

    def test_all_migration_files_are_registered(self) -> None:
        from alembic.script import ScriptDirectory

        script = ScriptDirectory(str(_ALEMBIC_DIR))
        registered = {version.revision for version in script.walk_revisions()}
        stems = {path.stem for path in _migration_files()}
        self.assertEqual(stems, registered)

    def test_modules_declare_matching_revision_and_links(self) -> None:
        files = _migration_files()
        for index, path in enumerate(files):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            self.assertEqual(_module_assignment(tree, "revision"), path.stem)
            expected_down = files[index - 1].stem if index > 0 else None
            self.assertEqual(
                _module_assignment(tree, "down_revision"),
                expected_down,
                msg=path.name,
            )


class MigrationSchemaTests(unittest.TestCase):
    """Verify every table and composite primary key is migrated (SP 1.99)."""

    def test_every_metadata_table_is_created_by_a_migration(self) -> None:
        tables, views = _migrated_schema()
        for name, table in Base.metadata.tables.items():
            if not table.primary_key.columns:
                self.assertIn(name, views, msg=f"view {name}")
            else:
                self.assertIn(name, tables, msg=f"table {name}")

    def test_composite_primary_keys_match_migrations(self) -> None:
        tables, _ = _migrated_schema()
        for name, table in Base.metadata.tables.items():
            expected = tuple(table.primary_key.columns.keys())
            if len(expected) <= 1:
                continue
            self.assertIn(name, tables, msg=f"table {name}")
            self.assertEqual(tuple(tables[name]), expected, msg=f"composite PK for {name}")

    def test_securities_and_daily_quotes_have_expected_composite_pks(self) -> None:
        tables, _ = _migrated_schema()
        self.assertEqual(tuple(tables["securities"]), ("market", "symbol"))
        self.assertEqual(
            tuple(tables["daily_quotes"]),
            ("market", "symbol", "date"),
        )


@unittest.skipUnless(_TEST_DATABASE_URL, "HARBOR_TEST_DATABASE_URL is not set")
class MigrationRunTests(unittest.TestCase):
    """Apply migrations to a fresh PostgreSQL and verify the resulting schema.

    ``HARBOR_TEST_DATABASE_URL`` must point to a disposable PostgreSQL database:
    the ``public`` schema is dropped and recreated before the migration run.
    """

    def test_upgrade_head_creates_all_tables_with_composite_pks(self) -> None:
        engine = _fresh_engine()
        _upgrade_to_head(engine)

        inspector = inspect(engine)
        created_tables = set(inspector.get_table_names())
        created_views = set(inspector.get_view_names())
        for name, table in Base.metadata.tables.items():
            if not table.primary_key.columns:
                self.assertIn(name, created_views, msg=f"view {name}")
            else:
                self.assertIn(name, created_tables, msg=f"table {name}")
                expected = tuple(table.primary_key.columns.keys())
                if len(expected) > 1:
                    pk = inspector.get_pk_constraint(name)["constrained_columns"]
                    self.assertEqual(tuple(pk), expected, msg=f"composite PK for {name}")


class BacktestAndFxSchemaDeclarationTests(unittest.TestCase):
    """Verify the backtest + FX migrations declare their constraints and indexes.

    These checks are database-free and run everywhere; the live equivalent is
    :class:`BacktestAndFxMigrationRunTests` (SP 2.77).
    """

    def _source(self, stem: str) -> str:
        return (_VERSIONS_DIR / f"{stem}.py").read_text(encoding="utf-8")

    def test_backtest_runs_declares_status_check_and_pk(self) -> None:
        source = self._source("0017_create_backtest_runs")
        self.assertIn("ck_backtest_runs_status", source)
        self.assertIn("pk_backtest_runs", source)

    def test_backtest_results_declare_constraints_and_indexes(self) -> None:
        source = self._source("0018_create_backtest_results")
        for name in (
            "fk_backtest_net_values_run",
            "uq_backtest_net_values_day_currency",
            "ck_backtest_fills_side",
            "ix_backtest_fills_run_date",
            "fk_backtest_positions_run",
            "uq_backtest_positions_holding",
            "ck_backtest_rejected_trades_side",
            "ix_backtest_rejected_trades_run_market",
            "uq_backtest_rebalances_day",
            "uq_backtest_metrics_name_date",
        ):
            with self.subTest(name=name):
                self.assertIn(name, source)

    def test_fx_rates_declares_constraints_and_composite_pk(self) -> None:
        source = self._source("0020_create_fx_rates")
        for name in (
            "pk_fx_rates",
            "ck_fx_rates_from_currency",
            "ck_fx_rates_to_currency",
            "ck_fx_rates_quality",
            "ck_fx_rates_rate_positive",
        ):
            with self.subTest(name=name):
                self.assertIn(name, source)

    def test_factor_snapshots_declare_constraints_and_indexes(self) -> None:
        source = self._source("0021_create_factor_snapshots")
        for name in (
            "ck_backtest_factor_snapshots_market",
            "fk_backtest_factor_snapshots_run",
            "uq_backtest_factor_snapshots_symbol",
            "ix_backtest_factor_snapshots_run_date",
        ):
            with self.subTest(name=name):
                self.assertIn(name, source)

    def test_resume_of_column_is_migrated(self) -> None:
        self.assertIn("resume_of", self._source("0022_add_backtest_runs_resume_of"))


class ValidationSchemaDeclarationTests(unittest.TestCase):
    """Verify the validation tables migration declares its constraints.

    These checks are database-free and run everywhere; the live equivalent is
    :class:`ValidationMigrationRunTests` (SP 3.75).
    """

    def _source(self) -> str:
        return (_VERSIONS_DIR / "0023_create_validation_tables.py").read_text(encoding="utf-8")

    def test_validation_runs_declares_status_check_and_pk(self) -> None:
        source = self._source()
        self.assertIn("ck_validation_runs_status", source)
        self.assertIn("pk_validation_runs", source)
        for status in (
            "DRAFT",
            "DATA_FROZEN",
            "TUNING",
            "TEST_LOCKED",
            "EVALUATED",
            "NOT_QUALIFIED",
            "FAILED",
        ):
            with self.subTest(status=status):
                self.assertIn(status, source)

    def test_manifest_and_split_declare_run_fks_and_pks(self) -> None:
        source = self._source()
        for name in (
            "fk_validation_manifests_run",
            "pk_validation_manifests",
            "fk_validation_splits_run",
            "pk_validation_splits",
        ):
            with self.subTest(name=name):
                self.assertIn(name, source)

    def test_trials_and_folds_declare_constraints_and_indexes(self) -> None:
        source = self._source()
        for name in (
            "fk_validation_trials_run",
            "fk_validation_trials_backtest_run",
            "uq_validation_trials_trial",
            "ix_validation_trials_run",
            "fk_validation_folds_run",
            "fk_validation_folds_backtest_run",
            "uq_validation_folds_index",
            "ix_validation_folds_run",
        ):
            with self.subTest(name=name):
                self.assertIn(name, source)

    def test_stress_results_declare_constraints_and_indexes(self) -> None:
        source = self._source()
        for name in (
            "ck_validation_stress_results_type",
            "fk_validation_stress_results_run",
            "fk_validation_stress_baseline_run",
            "fk_validation_stress_stressed_run",
            "uq_validation_stress_results_scenario",
            "ix_validation_stress_results_run",
        ):
            with self.subTest(name=name):
                self.assertIn(name, source)
        for scenario_type in (
            "cost",
            "liquidity",
            "fx",
            "calendar",
            "corporate_action",
            "stock_pool",
            "parameter_neighborhood",
        ):
            with self.subTest(scenario_type=scenario_type):
                self.assertIn(scenario_type, source)

    def test_conclusion_and_warnings_declare_constraints(self) -> None:
        source = self._source()
        for name in (
            "ck_validation_conclusions_conclusion",
            "fk_validation_conclusions_run",
            "pk_validation_conclusions",
            "ck_validation_warnings_severity",
            "fk_validation_warnings_run",
            "ix_validation_warnings_run",
        ):
            with self.subTest(name=name):
                self.assertIn(name, source)
        for conclusion in ("QUALIFIED", "NOT_QUALIFIED", "INCONCLUSIVE"):
            with self.subTest(conclusion=conclusion):
                self.assertIn(conclusion, source)


@unittest.skipUnless(_TEST_DATABASE_URL, "HARBOR_TEST_DATABASE_URL is not set")
class BacktestAndFxMigrationRunTests(unittest.TestCase):
    """Upgrade a fresh DB and verify backtest + FX constraints and indexes (SP 2.77)."""

    _TABLES = (
        "backtest_runs",
        "backtest_net_values",
        "backtest_positions",
        "backtest_fills",
        "backtest_rebalances",
        "backtest_metrics",
        "backtest_rejected_trades",
        "backtest_factor_snapshots",
        "fx_rates",
    )
    _RESULT_TABLES = (
        "backtest_net_values",
        "backtest_positions",
        "backtest_fills",
        "backtest_rebalances",
        "backtest_metrics",
        "backtest_rejected_trades",
        "backtest_factor_snapshots",
    )

    def setUp(self) -> None:
        self.engine = _fresh_engine()
        _upgrade_to_head(self.engine)
        self.inspector = inspect(self.engine)

    def test_all_backtest_and_fx_tables_exist(self) -> None:
        created = set(self.inspector.get_table_names())
        for table in self._TABLES:
            with self.subTest(table=table):
                self.assertIn(table, created)

    def test_primary_keys_are_created(self) -> None:
        self.assertEqual(
            self.inspector.get_pk_constraint("backtest_runs")["constrained_columns"],
            ["run_id"],
        )
        self.assertEqual(
            self.inspector.get_pk_constraint("fx_rates")["constrained_columns"],
            ["from_currency", "to_currency", "date"],
        )
        for table in self._RESULT_TABLES:
            with self.subTest(table=table):
                self.assertEqual(
                    self.inspector.get_pk_constraint(table)["constrained_columns"], ["id"]
                )

    def test_check_constraints_are_created(self) -> None:
        names = {
            check["name"]
            for table in self._TABLES
            for check in self.inspector.get_check_constraints(table)
        }
        for expected in (
            "ck_backtest_runs_status",
            "ck_backtest_fills_side",
            "ck_backtest_rejected_trades_side",
            "ck_backtest_factor_snapshots_market",
            "ck_fx_rates_from_currency",
            "ck_fx_rates_to_currency",
            "ck_fx_rates_quality",
            "ck_fx_rates_rate_positive",
        ):
            with self.subTest(name=expected):
                self.assertIn(expected, names)

    def test_foreign_keys_are_created(self) -> None:
        names = {
            fk["name"] for table in self._TABLES for fk in self.inspector.get_foreign_keys(table)
        }
        for expected in (
            "fk_backtest_net_values_run",
            "fk_backtest_positions_run",
            "fk_backtest_fills_run",
            "fk_backtest_rebalances_run",
            "fk_backtest_metrics_run",
            "fk_backtest_rejected_trades_run",
            "fk_backtest_factor_snapshots_run",
        ):
            with self.subTest(name=expected):
                self.assertIn(expected, names)

    def test_unique_constraints_are_created(self) -> None:
        names = {
            unique["name"]
            for table in self._TABLES
            for unique in self.inspector.get_unique_constraints(table)
        }
        for expected in (
            "uq_backtest_net_values_day_currency",
            "uq_backtest_positions_holding",
            "uq_backtest_rebalances_day",
            "uq_backtest_metrics_name_date",
            "uq_backtest_factor_snapshots_symbol",
        ):
            with self.subTest(name=expected):
                self.assertIn(expected, names)

    def test_explicit_indexes_are_created(self) -> None:
        names = {
            index["name"] for table in self._TABLES for index in self.inspector.get_indexes(table)
        }
        for expected in (
            "ix_backtest_fills_run_date",
            "ix_backtest_rejected_trades_run_market",
            "ix_backtest_factor_snapshots_run_date",
        ):
            with self.subTest(name=expected):
                self.assertIn(expected, names)


@unittest.skipUnless(_TEST_DATABASE_URL, "HARBOR_TEST_DATABASE_URL is not set")
class ValidationMigrationRunTests(unittest.TestCase):
    """Upgrade a fresh DB and verify the validation tables (SP 3.75)."""

    _TABLES = (
        "validation_runs",
        "validation_manifests",
        "validation_splits",
        "validation_trials",
        "validation_folds",
        "validation_stress_results",
        "validation_conclusions",
        "validation_warnings",
    )

    def setUp(self) -> None:
        self.engine = _fresh_engine()
        _upgrade_to_head(self.engine)
        self.inspector = inspect(self.engine)

    def test_all_validation_tables_exist(self) -> None:
        created = set(self.inspector.get_table_names())
        for table in self._TABLES:
            with self.subTest(table=table):
                self.assertIn(table, created)

    def test_primary_keys_are_created(self) -> None:
        self.assertEqual(
            self.inspector.get_pk_constraint("validation_runs")["constrained_columns"],
            ["run_id"],
        )
        for table in (
            "validation_manifests",
            "validation_splits",
            "validation_conclusions",
        ):
            with self.subTest(table=table):
                self.assertEqual(
                    self.inspector.get_pk_constraint(table)["constrained_columns"],
                    ["validation_run_id"],
                )
        for table in (
            "validation_trials",
            "validation_folds",
            "validation_stress_results",
            "validation_warnings",
        ):
            with self.subTest(table=table):
                self.assertEqual(
                    self.inspector.get_pk_constraint(table)["constrained_columns"], ["id"]
                )

    def test_foreign_keys_are_created(self) -> None:
        names = {
            fk["name"] for table in self._TABLES for fk in self.inspector.get_foreign_keys(table)
        }
        for expected in (
            "fk_validation_manifests_run",
            "fk_validation_splits_run",
            "fk_validation_trials_run",
            "fk_validation_trials_backtest_run",
            "fk_validation_folds_run",
            "fk_validation_folds_backtest_run",
            "fk_validation_stress_results_run",
            "fk_validation_stress_baseline_run",
            "fk_validation_stress_stressed_run",
            "fk_validation_conclusions_run",
            "fk_validation_warnings_run",
        ):
            with self.subTest(name=expected):
                self.assertIn(expected, names)

    def test_check_constraints_are_created(self) -> None:
        names = {
            check["name"]
            for table in self._TABLES
            for check in self.inspector.get_check_constraints(table)
        }
        for expected in (
            "ck_validation_runs_status",
            "ck_validation_stress_results_type",
            "ck_validation_conclusions_conclusion",
            "ck_validation_warnings_severity",
        ):
            with self.subTest(name=expected):
                self.assertIn(expected, names)

    def test_unique_constraints_are_created(self) -> None:
        names = {
            unique["name"]
            for table in self._TABLES
            for unique in self.inspector.get_unique_constraints(table)
        }
        for expected in (
            "uq_validation_trials_trial",
            "uq_validation_folds_index",
            "uq_validation_stress_results_scenario",
        ):
            with self.subTest(name=expected):
                self.assertIn(expected, names)

    def test_explicit_indexes_are_created(self) -> None:
        names = {
            index["name"] for table in self._TABLES for index in self.inspector.get_indexes(table)
        }
        for expected in (
            "ix_validation_trials_run",
            "ix_validation_folds_run",
            "ix_validation_stress_results_run",
            "ix_validation_warnings_run",
        ):
            with self.subTest(name=expected):
                self.assertIn(expected, names)
