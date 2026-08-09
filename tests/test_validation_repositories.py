"""Validation run repository tests (MVP 3 / SP 3.12)."""

import unittest
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy.dialects import postgresql

from harbor.core.validation_domain import OOSConclusion, ValidationStatus
from harbor.storage.models import (
    ValidationConclusion,
    ValidationFold,
    ValidationManifest,
    ValidationSplit,
    ValidationStressResult,
    ValidationTrial,
    ValidationWarning,
)
from harbor.storage.validation_repositories import (
    _VALIDATION_STATUSES,
    ValidationRepository,
)


class ValidationRepositoryTests(unittest.TestCase):
    """Verify the validation runs repository contract."""

    def setUp(self) -> None:
        self.repository = ValidationRepository(connection=object())  # type: ignore[arg-type]
        self.arguments: dict[str, Any] = {
            "run_id": "validation-001",
            "config_hash": "b" * 64,
            "config_snapshot": {
                "strategy": "shareholder-return",
                "strategy_version": "1.0.0",
                "base_currency": "HKD",
            },
            "code_version": "1.0.0",
            "created_at": datetime(2026, 8, 9, 1, 0, tzinfo=timezone.utc),
            "status": ValidationStatus.DRAFT.value,
        }

    def test_status_vocabulary_matches_domain_enum(self) -> None:
        self.assertEqual(_VALIDATION_STATUSES, {status.value for status in ValidationStatus})

    def test_create_run_conflicts_on_run_id(self) -> None:
        statement = self.repository._create_statement(**self.arguments)
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("INSERT INTO validation_runs", sql)
        self.assertIn("ON CONFLICT", sql)
        self.assertIn("(run_id)", sql)

    def test_create_run_captures_config_hash_and_snapshot(self) -> None:
        statement = self.repository._create_statement(**self.arguments)
        compiled = statement.compile(dialect=postgresql.dialect())
        self.assertEqual(compiled.params["config_hash"], self.arguments["config_hash"])
        self.assertIn("config_snapshot", compiled.string)

    def test_create_run_defaults_to_draft(self) -> None:
        statement = self.repository._create_statement(**self.arguments)
        compiled = statement.compile(dialect=postgresql.dialect())
        self.assertEqual(compiled.params["status"], "DRAFT")

    def test_create_run_records_test_set_id(self) -> None:
        statement = self.repository._create_statement(**self.arguments, test_set_id="holdout-v1")
        compiled = statement.compile(dialect=postgresql.dialect())
        self.assertEqual(compiled.params["test_set_id"], "holdout-v1")

    def test_create_run_rejects_unknown_status(self) -> None:
        arguments = dict(self.arguments)
        arguments.pop("status")
        with self.assertRaisesRegex(ValueError, "Unknown validation status"):
            self.repository._create_statement(**arguments, status="PENDING")

    def test_update_run_sets_status_and_diagnostics(self) -> None:
        statement = self.repository._update_statement(
            run_id="validation-001",
            status=ValidationStatus.EVALUATED.value,
            updated_at=datetime(2026, 8, 9, 2, 0, tzinfo=timezone.utc),
            error_summary=None,
        )
        compiled = statement.compile(dialect=postgresql.dialect())
        self.assertIn("UPDATE validation_runs", compiled.string)
        self.assertIn("validation_runs.run_id = %(run_id_1)s", compiled.string)
        self.assertEqual(compiled.params["status"], "EVALUATED")

    def test_update_run_rejects_unknown_status(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown validation status"):
            self.repository._update_statement(
                run_id="validation-001",
                status="PENDING",
                updated_at=None,
                error_summary=None,
            )

    def test_get_run_filters_by_run_id(self) -> None:
        statement = self.repository.get_run("validation-001")
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FROM validation_runs", sql)
        self.assertIn("validation_runs.run_id = %(run_id_1)s", sql)


class ValidationArtifactRepositoryTests(unittest.TestCase):
    """Verify the validation artifact tables repository (MVP 3 / SP 3.12)."""

    def setUp(self) -> None:
        self.repository = ValidationRepository(connection=object())  # type: ignore[arg-type]

    @staticmethod
    def _manifest_values() -> dict[str, Any]:
        return {
            "markets": ["HK", "US"],
            "base_currency": "HKD",
            "start_date": date(2019, 1, 1),
            "end_date": date(2024, 12, 31),
            "data_cutoff": date(2024, 12, 31),
            "config_hash": "c" * 64,
            "code_version": "1.0.0",
            "calendar_version": "v1",
            "fx_source": "mock",
            "fingerprint": "d" * 64,
            "random_seed": 42,
            "components": [{"component": "prices", "source": "mock", "version": "v1"}],
        }

    @staticmethod
    def _split_values() -> dict[str, Any]:
        return {
            "split_hash": "e" * 64,
            "train_start": date(2019, 1, 1),
            "train_end": date(2020, 12, 31),
            "validation_start": date(2021, 1, 1),
            "validation_end": date(2022, 12, 31),
            "test_start": date(2023, 1, 1),
            "test_end": date(2024, 12, 31),
        }

    @staticmethod
    def _conclusion_values() -> dict[str, Any]:
        return {
            "conclusion": OOSConclusion.QUALIFIED.value,
            "rule_version": "1.0.0",
            "evidence": {"fold_count": 4},
            "limitations": [{"area": "calendar", "detail": "illustrative holidays"}],
            "created_at": datetime(2026, 8, 9, 3, 0, tzinfo=timezone.utc),
        }

    @staticmethod
    def _trial_rows() -> list[dict[str, Any]]:
        return [
            {
                "trial_id": "trial-1",
                "parameters": [{"name": "lookback", "value": 252}],
                "dataset_fingerprint": "d" * 64,
                "train_start": date(2019, 1, 1),
                "train_end": date(2020, 12, 31),
                "validation_start": date(2021, 1, 1),
                "validation_end": date(2022, 12, 31),
                "seed": 42,
                "code_version": "1.0.0",
                "metric": 0.05,
                "failed_reason": None,
                "backtest_run_id": "run-001",
            }
        ]

    @staticmethod
    def _fold_rows() -> list[dict[str, Any]]:
        return [
            {
                "fold_index": 0,
                "train_start": date(2019, 1, 1),
                "train_end": date(2020, 12, 31),
                "validation_start": date(2021, 1, 1),
                "validation_end": date(2022, 12, 31),
                "test_start": date(2023, 1, 1),
                "test_end": date(2023, 12, 31),
                "retrain_date": date(2023, 1, 1),
                "dataset_fingerprint": "d" * 64,
                "backtest_run_id": "run-001",
            }
        ]

    @staticmethod
    def _stress_rows() -> list[dict[str, Any]]:
        return [
            {
                "scenario_name": "cost-shock",
                "scenario_type": "cost",
                "assumptions": {"cost_multiplier": 2.0},
                "applicable_markets": ["HK", "US"],
                "run_fingerprint": "d" * 64,
                "baseline_backtest_run_id": "run-001",
                "stressed_backtest_run_id": "run-002",
                "delta": {"net_value_change": -0.03},
                "notes": None,
            }
        ]

    @staticmethod
    def _warning_rows() -> list[dict[str, Any]]:
        return [
            {
                "warning_code": "coverage_gap",
                "severity": "warning",
                "message": "calendar coverage is incomplete",
                "context": {"market": "HK"},
                "created_at": datetime(2026, 8, 9, 3, 30, tzinfo=timezone.utc),
            }
        ]

    def test_artifact_models_are_linked_to_validation_runs(self) -> None:
        models = (
            ValidationManifest,
            ValidationSplit,
            ValidationTrial,
            ValidationFold,
            ValidationStressResult,
            ValidationConclusion,
            ValidationWarning,
        )
        for model in models:
            references = {fk.column.table.name for fk in model.__table__.foreign_keys}
            self.assertIn("validation_runs", references, msg=model.__name__)

    def test_trial_and_fold_and_stress_link_to_backtest_runs(self) -> None:
        models = (ValidationTrial, ValidationFold, ValidationStressResult)
        for model in models:
            references = {fk.column.table.name for fk in model.__table__.foreign_keys}
            self.assertIn("backtest_runs", references, msg=model.__name__)

    def test_upsert_manifest_conflicts_on_validation_run_id(self) -> None:
        statement = self.repository._upsert_on_run_statement(
            ValidationManifest, "validation-001", self._manifest_values()
        )
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("INSERT INTO validation_manifests", sql)
        self.assertIn("ON CONFLICT", sql)
        self.assertIn("(validation_run_id)", sql)

    def test_upsert_manifest_tags_validation_run_id(self) -> None:
        statement = self.repository._upsert_on_run_statement(
            ValidationManifest, "validation-001", self._manifest_values()
        )
        compiled = statement.compile(dialect=postgresql.dialect())
        self.assertIn("validation_run_id", compiled.string)
        self.assertIn("validation-001", compiled.params.values())
        self.assertIn(self._manifest_values()["fingerprint"], compiled.params.values())

    def test_upsert_split_conflicts_on_validation_run_id(self) -> None:
        statement = self.repository._upsert_on_run_statement(
            ValidationSplit, "validation-001", self._split_values()
        )
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("INSERT INTO validation_splits", sql)
        self.assertIn("ON CONFLICT", sql)
        self.assertIn("(validation_run_id)", sql)

    def test_upsert_conclusion_conflicts_on_validation_run_id(self) -> None:
        statement = self.repository._upsert_on_run_statement(
            ValidationConclusion, "validation-001", self._conclusion_values()
        )
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("INSERT INTO validation_conclusions", sql)
        self.assertIn("ON CONFLICT", sql)
        self.assertIn("(validation_run_id)", sql)

    def test_insert_trials_is_idempotent_on_trial_key(self) -> None:
        statement = self.repository._insert_rows_statement(
            ValidationTrial, "validation-001", self._trial_rows(), ("validation_run_id", "trial_id")
        )
        assert statement is not None
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("INSERT INTO validation_trials", sql)
        self.assertIn("ON CONFLICT", sql)
        self.assertIn("(validation_run_id, trial_id)", sql)
        self.assertIn("run-001", statement.compile(dialect=postgresql.dialect()).params.values())

    def test_insert_folds_is_idempotent_on_fold_key(self) -> None:
        statement = self.repository._insert_rows_statement(
            ValidationFold,
            "validation-001",
            self._fold_rows(),
            ("validation_run_id", "fold_index"),
        )
        assert statement is not None
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("INSERT INTO validation_folds", sql)
        self.assertIn("ON CONFLICT", sql)
        self.assertIn("(validation_run_id, fold_index)", sql)

    def test_insert_stress_results_is_idempotent_on_scenario(self) -> None:
        statement = self.repository._insert_rows_statement(
            ValidationStressResult,
            "validation-001",
            self._stress_rows(),
            ("validation_run_id", "scenario_name"),
        )
        assert statement is not None
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("INSERT INTO validation_stress_results", sql)
        self.assertIn("ON CONFLICT", sql)
        self.assertIn("(validation_run_id, scenario_name)", sql)

    def test_insert_warnings_tags_validation_run_id(self) -> None:
        statement = self.repository._insert_rows_statement(
            ValidationWarning, "validation-001", self._warning_rows(), ("id",)
        )
        assert statement is not None
        compiled = statement.compile(dialect=postgresql.dialect())
        self.assertIn("INSERT INTO validation_warnings", compiled.string)
        self.assertIn("validation_run_id", compiled.string)
        self.assertIn("validation-001", compiled.params.values())
        self.assertIn("coverage_gap", compiled.params.values())

    def test_insert_rows_returns_zero_for_empty_rows(self) -> None:
        statement = self.repository._insert_rows_statement(
            ValidationWarning, "validation-001", [], ("id",)
        )
        self.assertIsNone(statement)

    def test_get_manifest_filters_by_validation_run_id(self) -> None:
        statement = self.repository.get_manifest("validation-001")
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FROM validation_manifests", sql)
        self.assertIn("validation_manifests.validation_run_id = %(validation_run_id_1)s", sql)

    def test_get_split_filters_by_validation_run_id(self) -> None:
        statement = self.repository.get_split("validation-001")
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FROM validation_splits", sql)
        self.assertIn("validation_splits.validation_run_id = %(validation_run_id_1)s", sql)

    def test_get_conclusion_filters_by_validation_run_id(self) -> None:
        statement = self.repository.get_conclusion("validation-001")
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FROM validation_conclusions", sql)
        self.assertIn("validation_conclusions.validation_run_id = %(validation_run_id_1)s", sql)

    def test_list_trials_filters_by_validation_run_id(self) -> None:
        statement = self.repository.list_trials("validation-001")
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FROM validation_trials", sql)
        self.assertIn("validation_trials.validation_run_id = %(validation_run_id_1)s", sql)

    def test_list_folds_filters_by_validation_run_id(self) -> None:
        statement = self.repository.list_folds("validation-001")
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FROM validation_folds", sql)
        self.assertIn("validation_folds.validation_run_id = %(validation_run_id_1)s", sql)

    def test_list_stress_results_filters_by_validation_run_id(self) -> None:
        statement = self.repository.list_stress_results("validation-001")
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FROM validation_stress_results", sql)
        self.assertIn("validation_stress_results.validation_run_id = %(validation_run_id_1)s", sql)

    def test_list_warnings_filters_by_validation_run_id(self) -> None:
        statement = self.repository.list_warnings("validation-001")
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("FROM validation_warnings", sql)
        self.assertIn("validation_warnings.validation_run_id = %(validation_run_id_1)s", sql)
        self.assertNotIn(".backtest_run_id =", sql)
