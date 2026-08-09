"""Independent holdout registration tests (MVP 3 / SP 3.5).

Verifies that the final test set is registered with its purpose, UTC creation
time, authorization stage (``TEST_LOCKED``) and first-read audit, and that it
is guarded: parameter selection can never read the test interval before final
evaluation, and final-evaluation reads are only allowed once the run is
authorized. The recorded ``config_hash`` ties to the SP 3.3 frozen-split hash
and the guarded boundaries come from the SP 3.1/3.4 split.
"""

import unittest
from dataclasses import FrozenInstanceError
from datetime import date, datetime, timedelta, timezone

from harbor.core.backtest_domain import Currency, Market
from harbor.core.holdout_registry import (
    HoldoutAccessError,
    HoldoutPurpose,
    HoldoutRegistration,
    HoldoutRegistrationError,
    guard_final_evaluation_read,
    guard_parameter_selection,
    mark_first_read,
    register_test_set,
)
from harbor.core.validation_config import SplitConfig, ValidationConfig
from harbor.core.validation_config_loader import config_hash
from harbor.core.validation_domain import EvaluationSplit, ValidationStatus

_UTC = timezone.utc


def _split(**overrides: object) -> EvaluationSplit:
    """Return a valid split with overridable boundaries."""
    fields: dict[str, object] = {
        "train_start": date(2019, 1, 1),
        "train_end": date(2021, 12, 31),
        "validation_start": date(2022, 1, 3),
        "validation_end": date(2022, 12, 30),
        "test_start": date(2023, 1, 2),
        "test_end": date(2024, 12, 31),
    }
    fields.update(overrides)
    return EvaluationSplit(**fields)  # type: ignore[arg-type]


def _registration(**overrides: object) -> HoldoutRegistration:
    """Return a valid registration with overridable fields."""
    fields: dict[str, object] = {
        "test_set_id": "ts-1",
        "purpose": HoldoutPurpose.FINAL_EVALUATION,
        "created_at": datetime(2026, 1, 1, tzinfo=_UTC),
        "authorized_stage": ValidationStatus.TEST_LOCKED,
        "split": _split(),
        "config_hash": "abc123",
    }
    fields.update(overrides)
    return HoldoutRegistration(**fields)  # type: ignore[arg-type]


def _frozen_config() -> ValidationConfig:
    """Return a validated config whose split can be registered (SP 3.3)."""
    return ValidationConfig(
        markets=(Market.HK, Market.US),
        base_currency=Currency.HKD,
        split=SplitConfig(
            train_start=date(2019, 1, 1),
            train_end=date(2021, 12, 31),
            validation_start=date(2022, 1, 3),
            validation_end=date(2022, 12, 30),
            test_start=date(2023, 1, 2),
            test_end=date(2024, 12, 31),
        ),
    )


class HoldoutPurposeTests(unittest.TestCase):
    """Verify the holdout purpose vocabulary (SP 3.5)."""

    def test_final_evaluation_purpose(self) -> None:
        self.assertEqual(HoldoutPurpose.FINAL_EVALUATION.value, "final_evaluation")


class HoldoutRegistrationTests(unittest.TestCase):
    """Verify the immutable registration record."""

    def test_valid_registration_records_fields(self) -> None:
        registration = _registration()
        self.assertEqual(registration.test_set_id, "ts-1")
        self.assertIs(registration.purpose, HoldoutPurpose.FINAL_EVALUATION)
        self.assertEqual(registration.created_at, datetime(2026, 1, 1, tzinfo=_UTC))
        self.assertIs(registration.authorized_stage, ValidationStatus.TEST_LOCKED)
        self.assertEqual(registration.config_hash, "abc123")
        self.assertEqual(registration.test_start, date(2023, 1, 2))
        self.assertEqual(registration.test_end, date(2024, 12, 31))
        self.assertIsNone(registration.first_read_at)

    def test_rejects_empty_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be non-empty"):
            _registration(test_set_id="")

    def test_rejects_naive_created_at(self) -> None:
        with self.assertRaisesRegex(ValueError, "UTC-aware"):
            _registration(created_at=datetime(2026, 1, 1))

    def test_rejects_non_utc_created_at(self) -> None:
        offset = timezone(timedelta(hours=8))
        with self.assertRaisesRegex(ValueError, "UTC-aware"):
            _registration(created_at=datetime(2026, 1, 1, tzinfo=offset))

    def test_rejects_authorization_before_test_lock(self) -> None:
        with self.assertRaisesRegex(ValueError, "only authorized at TEST_LOCKED"):
            _registration(authorized_stage=ValidationStatus.DRAFT)

    def test_requires_utc_first_read(self) -> None:
        with self.assertRaisesRegex(ValueError, "UTC-aware"):
            _registration(first_read_at=datetime(2026, 6, 1))

    def test_rejects_first_read_before_creation(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not precede"):
            _registration(
                first_read_at=datetime(2025, 12, 31, tzinfo=_UTC),
            )

    def test_is_frozen(self) -> None:
        registration = _registration()
        with self.assertRaises(FrozenInstanceError):
            registration.config_hash = "changed"  # type: ignore[misc]

    def test_readable_contains_identity(self) -> None:
        readable = _registration().readable()
        self.assertIn("ts-1", readable)
        self.assertIn("final_evaluation", readable)
        self.assertIn("not read", readable)


class RegisterTestSetTests(unittest.TestCase):
    """Verify the registration factory (SP 3.5)."""

    def test_defaults_purpose_and_authorized_stage(self) -> None:
        registration = register_test_set(
            "ts-1",
            created_at=datetime(2026, 1, 1, tzinfo=_UTC),
        )
        self.assertIs(registration.purpose, HoldoutPurpose.FINAL_EVALUATION)
        self.assertIs(registration.authorized_stage, ValidationStatus.TEST_LOCKED)

    def test_records_split_and_config_hash(self) -> None:
        split = _split()
        registration = register_test_set(
            "ts-1",
            split=split,
            config_hash="abc123",
            created_at=datetime(2026, 1, 1, tzinfo=_UTC),
        )
        self.assertEqual(registration.split, split)
        self.assertEqual(registration.config_hash, "abc123")

    def test_defaults_created_at_to_utc_now(self) -> None:
        registration = register_test_set("ts-1")
        self.assertIsNotNone(registration.created_at.tzinfo)
        self.assertEqual(registration.created_at.utcoffset(), timedelta(0))

    def test_preserves_explicit_created_at(self) -> None:
        created = datetime(2026, 1, 1, 12, 30, tzinfo=_UTC)
        registration = register_test_set("ts-1", created_at=created)
        self.assertEqual(registration.created_at, created)

    def test_registers_with_frozen_config_hash(self) -> None:
        # Ties the SP 3.3 hash and the SP 3.1/3.4 split into the registration.
        config = _frozen_config()
        registration = register_test_set(
            "ts-1",
            split=config.split.to_evaluation_split(),
            config_hash=config_hash(config),
            created_at=datetime(2026, 1, 1, tzinfo=_UTC),
        )
        self.assertEqual(registration.config_hash, config_hash(config))
        self.assertEqual(registration.test_start, date(2023, 1, 2))


class MarkFirstReadTests(unittest.TestCase):
    """Verify the one-time first-read audit event (SP 3.5)."""

    def test_records_first_read_on_new_record(self) -> None:
        registration = _registration()
        read_at = datetime(2026, 6, 1, tzinfo=_UTC)
        marked = mark_first_read(registration, ValidationStatus.TEST_LOCKED, read_at)
        self.assertEqual(marked.first_read_at, read_at)
        # The original immutable record is unchanged.
        self.assertIsNone(registration.first_read_at)

    def test_first_read_requires_test_lock(self) -> None:
        with self.assertRaises(HoldoutAccessError):
            mark_first_read(_registration(), ValidationStatus.DRAFT)

    def test_first_read_allowed_at_test_locked(self) -> None:
        marked = mark_first_read(
            _registration(),
            ValidationStatus.TEST_LOCKED,
            datetime(2026, 6, 1, tzinfo=_UTC),
        )
        self.assertIsNotNone(marked.first_read_at)

    def test_first_read_allowed_at_evaluated(self) -> None:
        marked = mark_first_read(
            _registration(),
            ValidationStatus.EVALUATED,
            datetime(2026, 6, 1, tzinfo=_UTC),
        )
        self.assertIsNotNone(marked.first_read_at)

    def test_second_read_is_rejected(self) -> None:
        registration = mark_first_read(
            _registration(),
            ValidationStatus.TEST_LOCKED,
            datetime(2026, 6, 1, tzinfo=_UTC),
        )
        with self.assertRaisesRegex(HoldoutRegistrationError, "already read"):
            mark_first_read(
                registration,
                ValidationStatus.TEST_LOCKED,
                datetime(2026, 6, 2, tzinfo=_UTC),
            )

    def test_read_at_before_creation_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not precede"):
            mark_first_read(
                _registration(),
                ValidationStatus.TEST_LOCKED,
                datetime(2025, 12, 31, tzinfo=_UTC),
            )


class GuardFinalEvaluationReadTests(unittest.TestCase):
    """Verify the final-evaluation read authorization guard (SP 3.5/3.41)."""

    def test_no_registration_is_rejected(self) -> None:
        with self.assertRaisesRegex(HoldoutAccessError, "No test set is registered"):
            guard_final_evaluation_read(None, ValidationStatus.TEST_LOCKED)

    def test_tuning_is_rejected(self) -> None:
        with self.assertRaisesRegex(HoldoutAccessError, "not readable at stage TUNING"):
            guard_final_evaluation_read(_registration(), ValidationStatus.TUNING)

    def test_draft_is_rejected(self) -> None:
        with self.assertRaises(HoldoutAccessError):
            guard_final_evaluation_read(_registration(), ValidationStatus.DRAFT)

    def test_test_locked_allows_read(self) -> None:
        guard_final_evaluation_read(_registration(), ValidationStatus.TEST_LOCKED)

    def test_evaluated_allows_read(self) -> None:
        guard_final_evaluation_read(_registration(), ValidationStatus.EVALUATED)


class GuardParameterSelectionTests(unittest.TestCase):
    """Verify the test set is never usable by parameter selection (SP 3.5)."""

    def test_rejects_at_tuning(self) -> None:
        with self.assertRaises(HoldoutAccessError) as ctx:
            guard_parameter_selection(_registration(), ValidationStatus.TUNING)
        message = str(ctx.exception)
        self.assertIn("ts-1", message)
        self.assertIn("TUNING", message)
        self.assertIn("training and validation", message)

    def test_rejects_even_at_test_locked(self) -> None:
        # Parameter selection never uses the holdout, even after it is locked.
        with self.assertRaises(HoldoutAccessError):
            guard_parameter_selection(_registration(), ValidationStatus.TEST_LOCKED)

    def test_rejects_without_registration(self) -> None:
        with self.assertRaisesRegex(HoldoutAccessError, "unregistered test set"):
            guard_parameter_selection(None, ValidationStatus.TUNING)


if __name__ == "__main__":
    unittest.main()
