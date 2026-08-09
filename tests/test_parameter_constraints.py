"""Parameter constraint validation tests (MVP 3 / SP 3.16).

Verifies the SP 3.16 acceptance guards layered on the SP 3.15 parameter
space: combination constraints (sum-to-target, max, implies, exclusive),
market applicability, rejection of unbounded searches (continuous without a
step) and rejection of test-set-specific parameters in a train/validation
search, plus the single :func:`validate_parameter_set` entry point.
"""

import unittest
from dataclasses import FrozenInstanceError

from harbor.core.backtest_domain import Market
from harbor.core.parameter_constraints import (
    ConstraintKind,
    EvaluationOnlyParameterError,
    MarketApplicabilityError,
    ParameterConstraint,
    ParameterConstraintError,
    UnboundedSearchError,
    constraint,
    require_combination,
    validate_bounded,
    validate_combination,
    validate_market_applicability,
    validate_parameter_set,
    validate_test_specific,
)
from harbor.core.parameter_space import (
    ParameterDomain,
    ParameterKind,
    ParameterSpace,
    UndeclaredParameterError,
    build_parameter_space,
    declare_parameter,
)


def _space() -> ParameterSpace:
    """Return a parameter space covering all SP 3.16 governance cases."""
    return build_parameter_space(
        declare_parameter(
            "weight_a",
            ParameterKind.FACTOR_WEIGHT,
            minimum=0.0,
            maximum=1.0,
            step=0.1,
            default=0.4,
        ),
        declare_parameter(
            "weight_b",
            ParameterKind.FACTOR_WEIGHT,
            minimum=0.0,
            maximum=1.0,
            step=0.1,
            default=0.6,
        ),
        declare_parameter(
            "position_count",
            ParameterKind.POSITION_COUNT,
            domain=ParameterDomain.INTEGER,
            minimum=5,
            maximum=20,
            default=10,
        ),
        declare_parameter(
            "hk_lot_size",
            ParameterKind.COST,
            domain=ParameterDomain.INTEGER,
            minimum=100,
            maximum=2000,
            default=100,
            markets=(Market.HK,),
        ),
        declare_parameter(
            "us_slippage_bps",
            ParameterKind.COST,
            minimum=0.0,
            maximum=100.0,
            step=5.0,
            default=10.0,
            markets=(Market.US,),
        ),
        declare_parameter(
            "test_window",
            ParameterKind.WINDOW,
            minimum=20.0,
            maximum=60.0,
            step=5.0,
            default=40.0,
            for_evaluation_only=True,
        ),
        declare_parameter(
            "unbounded_ratio",
            ParameterKind.FILTER_THRESHOLD,
            minimum=0.0,
            maximum=0.5,
            default=0.25,
        ),
    )


_WEIGHTS_SUM = constraint(
    "weights-sum", ConstraintKind.SUM_TO_TARGET, "weight_a", "weight_b", target=1.0
)
_RISK_BUDGET = constraint(
    "risk-budget", ConstraintKind.MAX_VALUE, "weight_a", "weight_b", target=0.8
)
_POSITION_IMPLIES = constraint(
    "position-implies", ConstraintKind.IMPLIES, "position_count", implied="weight_a"
)
_EXCLUSIVE = constraint("exclusive-mode", ConstraintKind.EXCLUSIVE, "weight_a", "weight_b")


class ConstraintKindTests(unittest.TestCase):
    """Verify the four combination constraint kinds."""

    def test_all_four_kinds_are_declared(self) -> None:
        self.assertEqual(
            tuple(ConstraintKind),
            (
                ConstraintKind.SUM_TO_TARGET,
                ConstraintKind.MAX_VALUE,
                ConstraintKind.IMPLIES,
                ConstraintKind.EXCLUSIVE,
            ),
        )


class ParameterConstraintTests(unittest.TestCase):
    """Verify constraint declaration validation and immutability."""

    def test_valid_sum_to_target(self) -> None:
        rule = constraint("s", ConstraintKind.SUM_TO_TARGET, "a", "b", target=1.0)
        self.assertEqual(rule.kind, ConstraintKind.SUM_TO_TARGET)
        self.assertEqual(rule.parameters, ("a", "b"))
        self.assertEqual(rule.target, 1.0)

    def test_valid_implies(self) -> None:
        rule = constraint("i", ConstraintKind.IMPLIES, "a", implied="b")
        self.assertEqual(rule.implied, "b")

    def test_empty_name_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "name must be non-empty"):
            ParameterConstraint(
                name="  ",
                kind=ConstraintKind.EXCLUSIVE,
                parameters=("a",),
            )

    def test_empty_parameters_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one parameter"):
            ParameterConstraint(
                name="c",
                kind=ConstraintKind.EXCLUSIVE,
                parameters=(),
            )

    def test_duplicate_parameters_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "must be unique"):
            ParameterConstraint(
                name="c",
                kind=ConstraintKind.EXCLUSIVE,
                parameters=("a", "a"),
            )

    def test_sum_requires_target(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires a target"):
            constraint("s", ConstraintKind.SUM_TO_TARGET, "a", "b")

    def test_max_requires_target(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires a target"):
            constraint("m", ConstraintKind.MAX_VALUE, "a")

    def test_implies_requires_implied(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires an implied parameter"):
            constraint("i", ConstraintKind.IMPLIES, "a")

    def test_constraint_is_frozen(self) -> None:
        rule = constraint("e", ConstraintKind.EXCLUSIVE, "a", "b")
        with self.assertRaises(FrozenInstanceError):
            rule.name = "renamed"

    def test_readable(self) -> None:
        rule = constraint(
            "weights-sum",
            ConstraintKind.SUM_TO_TARGET,
            "weight_a",
            "weight_b",
            target=1.0,
            reason="factor weights must sum to one",
        )
        summary = rule.readable()
        self.assertIn("weights-sum", summary)
        self.assertIn("sum_to_target", summary)
        self.assertIn("factor weights must sum to one", summary)


class ConstraintValidationTests(unittest.TestCase):
    """Verify each combination rule's value check."""

    def test_sum_to_target_passes_when_sum_matches(self) -> None:
        self.assertIsNone(_WEIGHTS_SUM.validate({"weight_a": 0.4, "weight_b": 0.6}))

    def test_sum_to_target_fails_when_sum_off(self) -> None:
        message = _WEIGHTS_SUM.validate({"weight_a": 0.4, "weight_b": 0.5})
        self.assertIsNotNone(message)
        self.assertIn("expected 1.0", message)

    def test_sum_to_target_fails_when_parameter_missing(self) -> None:
        message = _WEIGHTS_SUM.validate({"weight_a": 0.4})
        self.assertIsNotNone(message)
        self.assertIn("missing", message)
        self.assertIn("weight_b", message)

    def test_max_value_passes_within_budget(self) -> None:
        self.assertIsNone(_RISK_BUDGET.validate({"weight_a": 0.3, "weight_b": 0.4}))

    def test_max_value_fails_over_budget(self) -> None:
        message = _RISK_BUDGET.validate({"weight_a": 0.5, "weight_b": 0.4})
        self.assertIsNotNone(message)
        self.assertIn("exceeds 0.8", message)

    def test_implies_fails_without_implied(self) -> None:
        message = _POSITION_IMPLIES.validate({"position_count": 12})
        self.assertIsNotNone(message)
        self.assertIn("weight_a", message)

    def test_implies_passes_with_implied(self) -> None:
        self.assertIsNone(_POSITION_IMPLIES.validate({"position_count": 12, "weight_a": 0.4}))

    def test_implies_passes_when_antecedent_absent(self) -> None:
        self.assertIsNone(_POSITION_IMPLIES.validate({"weight_a": 0.4}))

    def test_exclusive_fails_with_two_present(self) -> None:
        message = _EXCLUSIVE.validate({"weight_a": 0.4, "weight_b": 0.6})
        self.assertIsNotNone(message)
        self.assertIn("at most one", message)

    def test_exclusive_passes_with_one_present(self) -> None:
        self.assertIsNone(_EXCLUSIVE.validate({"weight_a": 0.4}))

    def test_exclusive_passes_with_none_present(self) -> None:
        self.assertIsNone(_EXCLUSIVE.validate({"position_count": 10}))


class CombinationTests(unittest.TestCase):
    """Verify the combination guards."""

    def test_validate_combination_returns_all_violations(self) -> None:
        messages = validate_combination(
            (_WEIGHTS_SUM, _RISK_BUDGET),
            {"weight_a": 0.6, "weight_b": 0.6},
        )
        self.assertEqual(len(messages), 2)

    def test_validate_combination_empty_when_clean(self) -> None:
        messages = validate_combination(
            (_WEIGHTS_SUM,),
            {"weight_a": 0.4, "weight_b": 0.6},
        )
        self.assertEqual(messages, ())

    def test_require_combination_raises_on_violation(self) -> None:
        with self.assertRaisesRegex(ParameterConstraintError, "expected 1.0"):
            require_combination((_WEIGHTS_SUM,), {"weight_a": 0.4, "weight_b": 0.5})

    def test_require_combination_passes_when_clean(self) -> None:
        require_combination((_WEIGHTS_SUM,), {"weight_a": 0.4, "weight_b": 0.6})


class MarketApplicabilityTests(unittest.TestCase):
    """Verify the market applicability guard (SP 3.16)."""

    def test_hk_parameter_applies_to_hk(self) -> None:
        validate_market_applicability(_space(), Market.HK, {"hk_lot_size": 100})

    def test_hk_parameter_rejected_for_us(self) -> None:
        with self.assertRaisesRegex(MarketApplicabilityError, "does not apply to market US"):
            validate_market_applicability(_space(), Market.US, {"hk_lot_size": 100})

    def test_us_parameter_rejected_for_hk(self) -> None:
        with self.assertRaisesRegex(MarketApplicabilityError, "does not apply to market HK"):
            validate_market_applicability(_space(), Market.HK, {"us_slippage_bps": 10.0})

    def test_unrestricted_parameter_applies_everywhere(self) -> None:
        validate_market_applicability(_space(), Market.HK, {"weight_a": 0.4})
        validate_market_applicability(_space(), Market.US, {"weight_a": 0.4})

    def test_error_names_the_applicable_markets(self) -> None:
        with self.assertRaisesRegex(MarketApplicabilityError, "applicable: US"):
            validate_market_applicability(_space(), Market.HK, {"us_slippage_bps": 10.0})


class UnboundedSearchTests(unittest.TestCase):
    """Verify unbounded search rejection (SP 3.16)."""

    def test_continuous_without_step_is_unbounded(self) -> None:
        with self.assertRaisesRegex(UnboundedSearchError, "without a step is unbounded"):
            validate_bounded(_space(), {"unbounded_ratio": 0.25})

    def test_continuous_with_step_is_bounded(self) -> None:
        validate_bounded(_space(), {"weight_a": 0.4})

    def test_integer_without_step_is_finite(self) -> None:
        validate_bounded(_space(), {"position_count": 10})

    def test_unbounded_parameter_not_searched_is_allowed(self) -> None:
        # An unbounded parameter that keeps its default is not part of the search.
        validate_bounded(_space(), {"weight_a": 0.4})


class TestSetSpecificTests(unittest.TestCase):
    """Verify test-set-specific parameters are excluded from search (SP 3.24)."""

    def test_evaluation_only_parameter_is_rejected(self) -> None:
        with self.assertRaisesRegex(
            EvaluationOnlyParameterError, "reserved for the final evaluation"
        ):
            validate_test_specific(_space(), {"test_window": 40.0})

    def test_searchable_parameter_is_allowed(self) -> None:
        validate_test_specific(_space(), {"weight_a": 0.4})

    def test_mixed_set_rejects_the_evaluation_parameter(self) -> None:
        with self.assertRaises(EvaluationOnlyParameterError):
            validate_test_specific(_space(), {"weight_a": 0.4, "test_window": 40.0})


class ValidateParameterSetTests(unittest.TestCase):
    """Verify the single SP 3.16 validation gate."""

    def test_valid_set_for_hk_returns_parameters(self) -> None:
        parameters = validate_parameter_set(
            _space(),
            {"weight_a": 0.4, "weight_b": 0.6, "position_count": 12},
            market=Market.HK,
            constraints=(_WEIGHTS_SUM,),
        )
        self.assertEqual(
            [parameter.name for parameter in parameters],
            ["weight_a", "weight_b", "position_count"],
        )

    def test_valid_set_for_us(self) -> None:
        parameters = validate_parameter_set(
            _space(),
            {"weight_a": 0.4, "us_slippage_bps": 10.0},
            market=Market.US,
        )
        self.assertEqual(len(parameters), 2)

    def test_undeclared_parameter_is_rejected(self) -> None:
        with self.assertRaises(UndeclaredParameterError):
            validate_parameter_set(_space(), {"weight_momentum": 0.3}, market=Market.HK)

    def test_unbounded_search_is_rejected(self) -> None:
        with self.assertRaises(UnboundedSearchError):
            validate_parameter_set(_space(), {"unbounded_ratio": 0.25}, market=Market.HK)

    def test_market_mismatch_is_rejected(self) -> None:
        with self.assertRaises(MarketApplicabilityError):
            validate_parameter_set(_space(), {"hk_lot_size": 100}, market=Market.US)

    def test_test_specific_parameter_is_rejected(self) -> None:
        with self.assertRaises(EvaluationOnlyParameterError):
            validate_parameter_set(_space(), {"test_window": 40.0}, market=Market.HK)

    def test_combination_violation_is_rejected(self) -> None:
        with self.assertRaisesRegex(ParameterConstraintError, "expected 1.0"):
            validate_parameter_set(
                _space(),
                {"weight_a": 0.4, "weight_b": 0.5},
                market=Market.HK,
                constraints=(_WEIGHTS_SUM,),
            )

    def test_partial_weight_search_breaks_sum_to_target(self) -> None:
        # Searching one factor weight without the other cannot keep the sum.
        with self.assertRaisesRegex(ParameterConstraintError, "missing"):
            validate_parameter_set(
                _space(),
                {"weight_a": 0.4},
                market=Market.HK,
                constraints=(_WEIGHTS_SUM,),
            )


if __name__ == "__main__":
    unittest.main()
