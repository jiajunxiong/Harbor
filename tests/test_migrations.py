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


class MigrationChainTests(unittest.TestCase):
    """Verify the Alembic migration chain is complete and linear."""

    def test_revision_chain_is_linear_from_base_to_head(self) -> None:
        from alembic.script import ScriptDirectory

        script = ScriptDirectory(str(_ALEMBIC_DIR))
        heads = script.get_heads()
        bases = script.get_bases()
        self.assertEqual(len(heads), 1)
        self.assertEqual(len(bases), 1)
        self.assertEqual(heads[0], "0019_add_financials_disclosure")
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
        from alembic.config import Config

        from alembic import command

        engine = create_engine(_TEST_DATABASE_URL)
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))

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
