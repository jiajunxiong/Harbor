"""Time-split legality validation tests (MVP 3 / SP 3.4).

Verifies the explicit split-boundary rule ``train_end < validation_start <=
validation_end < test_start``: every overlapping, inverted or empty range is
enumerated with a readable reason, both the raising validator and the
non-raising report reject them, and a valid split round-trips to the frozen
:class:`EvaluationSplit` (SP 3.1). The config-level entry points (SP 3.2
dependency) re-verify a :class:`SplitConfig` at the validator layer.
"""

import unittest
from dataclasses import FrozenInstanceError
from datetime import date

from pydantic import ValidationError

from harbor.core.validation_config import SplitConfig
from harbor.core.validation_domain import EvaluationSplit, SplitBoundaryError
from harbor.core.validation_split import (
    SPLIT_ORDER_RULE,
    SplitValidityReport,
    check_split_boundaries,
    check_split_config,
    collect_split_boundary_issues,
    validate_split_boundaries,
    validate_split_config,
)


def _args(**overrides: object) -> dict[str, object]:
    """Return valid split boundary keyword arguments with overridable fields."""
    fields: dict[str, object] = {
        "train_start": date(2019, 1, 1),
        "train_end": date(2021, 12, 31),
        "validation_start": date(2022, 1, 3),
        "validation_end": date(2022, 12, 30),
        "test_start": date(2023, 1, 2),
        "test_end": date(2024, 12, 31),
    }
    fields.update(overrides)
    return fields


def _split(**overrides: object) -> SplitConfig:
    """Return a valid split config with overridable boundaries."""
    return SplitConfig(**_args(**overrides))  # type: ignore[arg-type]


class SplitOrderRuleTests(unittest.TestCase):
    """Verify the enforced ordering is explicit and documented (SP 3.4)."""

    def test_rule_documents_the_strict_ordering(self) -> None:
        self.assertEqual(
            SPLIT_ORDER_RULE,
            "train_end < validation_start <= validation_end < test_start",
        )


class CollectSplitBoundaryIssuesTests(unittest.TestCase):
    """Verify every violation is enumerated with a readable reason."""

    def test_valid_split_has_no_issues(self) -> None:
        self.assertEqual(collect_split_boundary_issues(**_args()), ())  # type: ignore[arg-type]

    def test_reversed_train_range_is_reported(self) -> None:
        issues = collect_split_boundary_issues(
            train_start=date(2022, 1, 1),
            train_end=date(2019, 1, 1),
            validation_start=date(2022, 1, 3),
            validation_end=date(2022, 12, 30),
            test_start=date(2023, 1, 2),
            test_end=date(2024, 12, 31),
        )
        self.assertEqual(len(issues), 1)
        self.assertIn("train range is empty or reversed", issues[0])

    def test_reversed_validation_range_is_reported(self) -> None:
        issues = collect_split_boundary_issues(
            **_args(
                validation_start=date(2022, 12, 30),
                validation_end=date(2022, 1, 3),
            )
        )  # type: ignore[arg-type]
        self.assertEqual(len(issues), 1)
        self.assertIn("validation range is empty or reversed", issues[0])

    def test_empty_test_range_is_reported(self) -> None:
        issues = collect_split_boundary_issues(
            **_args(
                test_start=date(2024, 12, 31),
                test_end=date(2023, 1, 2),
            )
        )  # type: ignore[arg-type]
        self.assertEqual(len(issues), 1)
        self.assertIn("test range is empty or reversed", issues[0])

    def test_touching_train_validation_is_reported(self) -> None:
        issues = collect_split_boundary_issues(
            **_args(
                train_end=date(2022, 1, 3),
                validation_start=date(2022, 1, 3),
            )
        )  # type: ignore[arg-type]
        self.assertEqual(len(issues), 1)
        self.assertIn("train must end before validation starts", issues[0])

    def test_overlapping_validation_test_is_reported(self) -> None:
        issues = collect_split_boundary_issues(
            **_args(
                validation_end=date(2023, 1, 2),
                test_start=date(2023, 1, 2),
            )
        )  # type: ignore[arg-type]
        self.assertEqual(len(issues), 1)
        self.assertIn("validation must end before test starts", issues[0])

    def test_all_violations_are_enumerated(self) -> None:
        # A reversed train range AND a touching validation/test boundary must
        # both be reported so a config can be fixed in one pass.
        issues = collect_split_boundary_issues(
            train_start=date(2022, 1, 1),
            train_end=date(2019, 1, 1),
            validation_start=date(2022, 1, 3),
            validation_end=date(2023, 1, 2),
            test_start=date(2023, 1, 2),
            test_end=date(2024, 12, 31),
        )
        self.assertEqual(len(issues), 2)
        self.assertIn("train range is empty or reversed", issues[0])
        self.assertIn("validation must end before test starts", issues[1])

    def test_single_day_validation_is_not_reported(self) -> None:
        issues = collect_split_boundary_issues(
            **_args(
                validation_start=date(2022, 1, 3),
                validation_end=date(2022, 1, 3),
            )
        )  # type: ignore[arg-type]
        self.assertEqual(issues, ())

    def test_messages_include_the_offending_dates(self) -> None:
        issues = collect_split_boundary_issues(
            train_start=date(2022, 1, 1),
            train_end=date(2019, 1, 1),
            validation_start=date(2022, 1, 3),
            validation_end=date(2022, 12, 30),
            test_start=date(2023, 1, 2),
            test_end=date(2024, 12, 31),
        )
        self.assertIn("2022-01-01", issues[0])
        self.assertIn("2019-01-01", issues[0])


class CheckSplitBoundariesTests(unittest.TestCase):
    """Verify the non-raising validity report."""

    def test_valid_split_reports_valid(self) -> None:
        report = check_split_boundaries(**_args())  # type: ignore[arg-type]
        self.assertIsInstance(report, SplitValidityReport)
        self.assertTrue(report.valid)
        self.assertEqual(report.issues, ())

    def test_invalid_split_reports_invalid_with_reasons(self) -> None:
        report = check_split_boundaries(
            **_args(
                train_end=date(2022, 1, 3),
                validation_start=date(2022, 1, 3),
            )
        )  # type: ignore[arg-type]
        self.assertFalse(report.valid)
        self.assertEqual(len(report.issues), 1)

    def test_report_is_frozen(self) -> None:
        report = check_split_boundaries(**_args())  # type: ignore[arg-type]
        with self.assertRaises(FrozenInstanceError):
            report.valid = False  # type: ignore[misc]

    def test_readable_mentions_the_rule_when_valid(self) -> None:
        report = check_split_boundaries(**_args())  # type: ignore[arg-type]
        self.assertIn(SPLIT_ORDER_RULE, report.readable())

    def test_readable_lists_issues_when_invalid(self) -> None:
        report = check_split_boundaries(
            **_args(
                train_end=date(2022, 1, 3),
                validation_start=date(2022, 1, 3),
            )
        )  # type: ignore[arg-type]
        self.assertIn("train must end before validation starts", report.readable())


class ValidateSplitBoundariesTests(unittest.TestCase):
    """Verify the raising validator returns the frozen split."""

    def test_valid_split_returns_evaluation_split(self) -> None:
        split = validate_split_boundaries(**_args())  # type: ignore[arg-type]
        self.assertIsInstance(split, EvaluationSplit)
        self.assertEqual(split.train_days, 1096)  # 2019-01-01..2021-12-31
        self.assertIn("train", split.readable())

    def test_reversed_split_raises_split_boundary_error(self) -> None:
        with self.assertRaises(SplitBoundaryError):
            validate_split_boundaries(
                **_args(
                    train_start=date(2022, 1, 1),
                    train_end=date(2019, 1, 1),
                )
            )  # type: ignore[arg-type]

    def test_empty_range_raises_split_boundary_error(self) -> None:
        with self.assertRaises(SplitBoundaryError):
            validate_split_boundaries(
                **_args(
                    validation_start=date(2022, 12, 30),
                    validation_end=date(2022, 1, 3),
                )
            )  # type: ignore[arg-type]

    def test_touching_boundaries_raise_split_boundary_error(self) -> None:
        with self.assertRaises(SplitBoundaryError):
            validate_split_boundaries(
                **_args(
                    train_end=date(2022, 1, 3),
                    validation_start=date(2022, 1, 3),
                )
            )  # type: ignore[arg-type]

    def test_overlapping_boundaries_raise_split_boundary_error(self) -> None:
        with self.assertRaises(SplitBoundaryError):
            validate_split_boundaries(
                **_args(
                    validation_end=date(2023, 1, 2),
                    test_start=date(2023, 1, 2),
                )
            )  # type: ignore[arg-type]

    def test_error_message_aggregates_all_issues(self) -> None:
        with self.assertRaises(SplitBoundaryError) as ctx:
            validate_split_boundaries(
                train_start=date(2022, 1, 1),
                train_end=date(2019, 1, 1),
                validation_start=date(2022, 1, 3),
                validation_end=date(2023, 1, 2),
                test_start=date(2023, 1, 2),
                test_end=date(2024, 12, 31),
            )
        message = str(ctx.exception)
        self.assertIn("train range is empty or reversed", message)
        self.assertIn("validation must end before test starts", message)

    def test_error_message_names_the_enforced_rule(self) -> None:
        with self.assertRaises(SplitBoundaryError) as ctx:
            validate_split_boundaries(
                **_args(
                    train_end=date(2022, 1, 3),
                    validation_start=date(2022, 1, 3),
                )
            )  # type: ignore[arg-type]
        self.assertIn(SPLIT_ORDER_RULE, str(ctx.exception))


class ValidateSplitConfigTests(unittest.TestCase):
    """Verify the config-level entry points (deps SP 3.2)."""

    def test_valid_split_config_returns_its_evaluation_split(self) -> None:
        value = validate_split_config(_split())
        self.assertIsInstance(value, EvaluationSplit)
        self.assertEqual(value.test_start, date(2023, 1, 2))

    def test_validated_config_matches_domain_value(self) -> None:
        config = _split()
        self.assertEqual(validate_split_config(config), config.to_evaluation_split())

    def test_check_split_config_reports_valid(self) -> None:
        report = check_split_config(_split())
        self.assertTrue(report.valid)
        self.assertEqual(report.issues, ())

    def test_split_config_construction_rejects_invalid_boundaries(self) -> None:
        with self.assertRaises(ValidationError):
            _split(
                train_end=date(2022, 1, 3),
                validation_start=date(2022, 1, 3),
            )


if __name__ == "__main__":
    unittest.main()
