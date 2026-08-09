"""Parameter space declaration tests (MVP 3 / SP 3.15).

Verifies that the searchable parameter space is explicitly declared (factor
weights, windows, filter thresholds, position counts, cost and risk
parameters), that an ill-formed declaration is rejected, that a value is
validated against its declared domain, and — the acceptance — that any
parameter NOT declared in the space cannot be changed
(未声明参数不可变更).
"""

import unittest

from pydantic import ValidationError

from harbor.core.parameter_space import (
    DeclaredParameter,
    ParameterDomain,
    ParameterKind,
    ParameterSpaceError,
    UndeclaredParameterError,
    build_parameter_space,
    declare_parameter,
)
from harbor.core.validation_domain import Parameter


def _declare(name: str, kind: ParameterKind, **overrides: object) -> DeclaredParameter:
    """Return a declared parameter with sensible numeric defaults."""
    base: dict[str, object] = {
        "domain": ParameterDomain.CONTINUOUS,
        "minimum": 0.0,
        "maximum": 1.0,
    }
    base.update(overrides)
    return DeclaredParameter(name=name, kind=kind, **base)  # type: ignore[arg-type]


class ParameterKindTests(unittest.TestCase):
    """Verify the six searchable parameter categories (SP 3.15 acceptance)."""

    def test_all_six_kinds_are_declared(self) -> None:
        self.assertEqual(
            tuple(ParameterKind),
            (
                ParameterKind.FACTOR_WEIGHT,
                ParameterKind.WINDOW,
                ParameterKind.FILTER_THRESHOLD,
                ParameterKind.POSITION_COUNT,
                ParameterKind.COST,
                ParameterKind.RISK,
            ),
        )

    def test_kind_values_are_lowercase(self) -> None:
        for kind in ParameterKind:
            self.assertEqual(kind.value, kind.name.lower())


class ParameterDomainTests(unittest.TestCase):
    """Verify the four value domains."""

    def test_all_four_domains_are_declared(self) -> None:
        self.assertEqual(
            tuple(ParameterDomain),
            (
                ParameterDomain.CONTINUOUS,
                ParameterDomain.INTEGER,
                ParameterDomain.BOOLEAN,
                ParameterDomain.CATEGORICAL,
            ),
        )


class DeclaredParameterTests(unittest.TestCase):
    """Verify declaration validation, rendering and immutability."""

    def test_valid_continuous_declaration(self) -> None:
        parameter = _declare("weight_dividend_yield", ParameterKind.FACTOR_WEIGHT)
        self.assertEqual(parameter.name, "weight_dividend_yield")
        self.assertEqual(parameter.kind, ParameterKind.FACTOR_WEIGHT)
        self.assertEqual(parameter.domain, ParameterDomain.CONTINUOUS)
        self.assertEqual(parameter.minimum, 0.0)
        self.assertEqual(parameter.maximum, 1.0)
        self.assertIsNone(parameter.step)
        self.assertIsNone(parameter.default)

    def test_valid_integer_declaration_with_step(self) -> None:
        parameter = declare_parameter(
            "lookback_days",
            ParameterKind.WINDOW,
            domain=ParameterDomain.INTEGER,
            minimum=60,
            maximum=504,
            step=21,
        )
        self.assertEqual(parameter.domain, ParameterDomain.INTEGER)
        self.assertEqual(parameter.step, 21.0)

    def test_valid_boolean_declaration(self) -> None:
        parameter = declare_parameter(
            "include_special",
            ParameterKind.RISK,
            domain=ParameterDomain.BOOLEAN,
            default=True,
        )
        self.assertEqual(parameter.domain, ParameterDomain.BOOLEAN)
        self.assertIs(parameter.default, True)

    def test_valid_categorical_declaration(self) -> None:
        parameter = declare_parameter(
            "unfilled_policy",
            ParameterKind.COST,
            domain=ParameterDomain.CATEGORICAL,
            allowed=("cancel", "defer"),
            default="cancel",
        )
        self.assertEqual(parameter.allowed, ("cancel", "defer"))
        self.assertEqual(parameter.default, "cancel")

    def test_empty_name_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            DeclaredParameter(name="  ", kind=ParameterKind.RISK, minimum=0.0, maximum=1.0)

    def test_numeric_requires_minimum(self) -> None:
        with self.assertRaisesRegex(ValidationError, "requires minimum and maximum"):
            DeclaredParameter(
                name="p",
                kind=ParameterKind.COST,
                domain=ParameterDomain.CONTINUOUS,
                minimum=None,
                maximum=1.0,
            )

    def test_numeric_requires_maximum(self) -> None:
        with self.assertRaisesRegex(ValidationError, "requires minimum and maximum"):
            DeclaredParameter(
                name="p",
                kind=ParameterKind.COST,
                domain=ParameterDomain.INTEGER,
                minimum=0,
                maximum=None,
            )

    def test_minimum_above_maximum_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "must not exceed"):
            _declare("p", ParameterKind.COST, minimum=0.9, maximum=0.1)

    def test_non_positive_step_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "step must be positive"):
            declare_parameter(
                "p",
                ParameterKind.WINDOW,
                domain=ParameterDomain.INTEGER,
                minimum=0,
                maximum=100,
                step=0,
            )

    def test_non_numeric_domain_cannot_carry_bounds(self) -> None:
        with self.assertRaisesRegex(ValidationError, "cannot carry numeric bounds"):
            declare_parameter(
                "p",
                ParameterKind.COST,
                domain=ParameterDomain.BOOLEAN,
                minimum=0.0,
                maximum=1.0,
            )

    def test_boolean_default_must_be_bool(self) -> None:
        with self.assertRaisesRegex(ValidationError, "default must be a bool"):
            declare_parameter(
                "p",
                ParameterKind.RISK,
                domain=ParameterDomain.BOOLEAN,
                default="yes",
            )

    def test_categorical_requires_allowed_values(self) -> None:
        with self.assertRaisesRegex(ValidationError, "requires allowed values"):
            declare_parameter(
                "p",
                ParameterKind.COST,
                domain=ParameterDomain.CATEGORICAL,
            )

    def test_categorical_default_must_be_allowed(self) -> None:
        with self.assertRaisesRegex(ValidationError, "default must be one of"):
            declare_parameter(
                "p",
                ParameterKind.COST,
                domain=ParameterDomain.CATEGORICAL,
                allowed=("cancel", "defer"),
                default="fill",
            )

    def test_numeric_default_out_of_range_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "must lie within"):
            _declare("p", ParameterKind.COST, default=1.5)

    def test_numeric_default_must_be_a_number(self) -> None:
        with self.assertRaisesRegex(ValidationError, "default must be a number"):
            _declare("p", ParameterKind.COST, default="high")

    def test_declaration_is_frozen(self) -> None:
        parameter = _declare("p", ParameterKind.COST)
        with self.assertRaises(ValidationError):
            parameter.name = "renamed"

    def test_readable(self) -> None:
        parameter = declare_parameter(
            "lookback_days",
            ParameterKind.WINDOW,
            domain=ParameterDomain.INTEGER,
            minimum=60,
            maximum=504,
            step=21,
            default=252,
        )
        summary = parameter.readable()
        self.assertIn("lookback_days", summary)
        self.assertIn("window", summary)
        self.assertIn("integer", summary)
        self.assertIn("[60, 504]", summary)
        self.assertIn("step 21", summary)
        self.assertIn("default 252", summary)


class DeclaredParameterValueTests(unittest.TestCase):
    """Verify a value is validated against its declared domain."""

    def test_continuous_value_within_range_is_accepted(self) -> None:
        parameter = _declare("weight", ParameterKind.FACTOR_WEIGHT)
        self.assertEqual(parameter.validate_value(0.25), 0.25)

    def test_continuous_out_of_range_is_rejected(self) -> None:
        parameter = _declare("weight", ParameterKind.FACTOR_WEIGHT)
        with self.assertRaisesRegex(ParameterSpaceError, "outside"):
            parameter.validate_value(1.5)

    def test_continuous_non_numeric_is_rejected(self) -> None:
        parameter = _declare("weight", ParameterKind.FACTOR_WEIGHT)
        with self.assertRaisesRegex(ParameterSpaceError, "expects a number"):
            parameter.validate_value("high")

    def test_continuous_bool_is_rejected(self) -> None:
        parameter = _declare("weight", ParameterKind.FACTOR_WEIGHT)
        with self.assertRaisesRegex(ParameterSpaceError, "expects a number"):
            parameter.validate_value(True)

    def test_continuous_step_grid_violation_is_rejected(self) -> None:
        parameter = declare_parameter(
            "slippage_bps",
            ParameterKind.COST,
            domain=ParameterDomain.CONTINUOUS,
            minimum=0.0,
            maximum=100.0,
            step=5.0,
        )
        with self.assertRaisesRegex(ParameterSpaceError, "step grid"):
            parameter.validate_value(12.5)

    def test_continuous_value_on_step_grid_is_accepted(self) -> None:
        parameter = declare_parameter(
            "slippage_bps",
            ParameterKind.COST,
            domain=ParameterDomain.CONTINUOUS,
            minimum=0.0,
            maximum=100.0,
            step=5.0,
        )
        self.assertEqual(parameter.validate_value(15.0), 15.0)

    def test_integer_value_is_accepted(self) -> None:
        parameter = declare_parameter(
            "position_count",
            ParameterKind.POSITION_COUNT,
            domain=ParameterDomain.INTEGER,
            minimum=10,
            maximum=20,
        )
        self.assertEqual(parameter.validate_value(15), 15)

    def test_integer_out_of_range_is_rejected(self) -> None:
        parameter = declare_parameter(
            "position_count",
            ParameterKind.POSITION_COUNT,
            domain=ParameterDomain.INTEGER,
            minimum=10,
            maximum=20,
        )
        with self.assertRaisesRegex(ParameterSpaceError, "outside"):
            parameter.validate_value(30)

    def test_integer_non_int_is_rejected(self) -> None:
        parameter = declare_parameter(
            "position_count",
            ParameterKind.POSITION_COUNT,
            domain=ParameterDomain.INTEGER,
            minimum=10,
            maximum=20,
        )
        with self.assertRaisesRegex(ParameterSpaceError, "expects an integer"):
            parameter.validate_value(15.0)

    def test_boolean_value_is_validated(self) -> None:
        parameter = declare_parameter(
            "include_special",
            ParameterKind.RISK,
            domain=ParameterDomain.BOOLEAN,
        )
        self.assertIs(parameter.validate_value(False), False)
        with self.assertRaisesRegex(ParameterSpaceError, "expects a bool"):
            parameter.validate_value(1)

    def test_categorical_value_is_validated(self) -> None:
        parameter = declare_parameter(
            "unfilled_policy",
            ParameterKind.COST,
            domain=ParameterDomain.CATEGORICAL,
            allowed=("cancel", "defer"),
        )
        self.assertEqual(parameter.validate_value("defer"), "defer")
        with self.assertRaisesRegex(ParameterSpaceError, "must be one of"):
            parameter.validate_value("fill")


class ParameterSpaceTests(unittest.TestCase):
    """Verify the space allow-list and override validation."""

    def setUp(self) -> None:
        self.space = build_parameter_space(
            _declare("weight_dividend_yield", ParameterKind.FACTOR_WEIGHT),
            _declare("weight_earnings_quality", ParameterKind.FACTOR_WEIGHT),
            declare_parameter(
                "position_count",
                ParameterKind.POSITION_COUNT,
                domain=ParameterDomain.INTEGER,
                minimum=10,
                maximum=20,
                default=15,
            ),
        )

    def test_declared(self) -> None:
        self.assertTrue(self.space.declared("weight_dividend_yield"))
        self.assertTrue(self.space.declared("position_count"))
        self.assertFalse(self.space.declared("weight_momentum"))

    def test_require_declared_returns_the_declaration(self) -> None:
        parameter = self.space.require_declared("position_count")
        self.assertEqual(parameter.kind, ParameterKind.POSITION_COUNT)

    def test_require_declared_rejects_undeclared(self) -> None:
        with self.assertRaisesRegex(
            UndeclaredParameterError, "not declared in the parameter space"
        ):
            self.space.require_declared("weight_momentum")

    def test_duplicate_names_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValidationError, "must be unique"):
            build_parameter_space(
                _declare("weight", ParameterKind.FACTOR_WEIGHT),
                _declare("weight", ParameterKind.FACTOR_WEIGHT),
            )

    def test_validate_values_rejects_undeclared_key(self) -> None:
        with self.assertRaisesRegex(UndeclaredParameterError, "weight_momentum"):
            self.space.validate_values({"weight_momentum": 0.3})

    def test_validate_values_emits_ordered_declared_parameters(self) -> None:
        parameters = self.space.validate_values(
            {
                "weight_dividend_yield": 0.4,
                "position_count": 12,
            }
        )
        self.assertEqual(
            [parameter.name for parameter in parameters],
            ["weight_dividend_yield", "position_count"],
        )
        self.assertEqual(parameters[0].value, 0.4)
        self.assertEqual(parameters[1].value, 12)
        self.assertIsInstance(parameters[0], Parameter)

    def test_validate_values_validates_each_declared_value(self) -> None:
        with self.assertRaisesRegex(ParameterSpaceError, "outside"):
            self.space.validate_values({"position_count": 99})

    def test_validate_values_ignores_absent_declared_parameters(self) -> None:
        # Absent declared parameters keep their defaults; only overrides validate.
        parameters = self.space.validate_values({"weight_dividend_yield": 0.2})
        self.assertEqual([p.name for p in parameters], ["weight_dividend_yield"])
        self.assertEqual(self.space.require_declared("position_count").default, 15)

    def test_empty_override_set_is_valid(self) -> None:
        self.assertEqual(self.space.validate_values({}), ())

    def test_space_is_frozen(self) -> None:
        with self.assertRaises(ValidationError):
            self.space.parameters = ()

    def test_readable(self) -> None:
        summary = self.space.readable()
        self.assertIn("parameter space (3 declared)", summary)
        self.assertIn("weight_dividend_yield", summary)
        self.assertIn("position_count", summary)


class ParameterSpaceAcceptanceTests(unittest.TestCase):
    """Verify the acceptance: a full six-kind space is explicitly declared.

    The space declares factor weights, windows, filter thresholds, position
    counts, cost assumptions and risk parameters; undeclared parameters
    cannot be changed.
    """

    def setUp(self) -> None:
        self.space = build_parameter_space(
            declare_parameter(
                "weight_dividend_yield",
                ParameterKind.FACTOR_WEIGHT,
                minimum=0.0,
                maximum=0.5,
                default=0.3,
            ),
            declare_parameter(
                "weight_low_volatility",
                ParameterKind.FACTOR_WEIGHT,
                minimum=0.0,
                maximum=0.5,
                default=0.4,
            ),
            declare_parameter(
                "lookback_days",
                ParameterKind.WINDOW,
                domain=ParameterDomain.INTEGER,
                minimum=60,
                maximum=504,
                default=252,
            ),
            declare_parameter(
                "max_suspension_ratio",
                ParameterKind.FILTER_THRESHOLD,
                minimum=0.0,
                maximum=0.5,
                default=0.3,
            ),
            declare_parameter(
                "position_count",
                ParameterKind.POSITION_COUNT,
                domain=ParameterDomain.INTEGER,
                minimum=10,
                maximum=20,
                default=15,
            ),
            declare_parameter(
                "cost_multiplier",
                ParameterKind.COST,
                minimum=1.0,
                maximum=3.0,
                default=2.0,
            ),
            declare_parameter(
                "max_position_pct",
                ParameterKind.RISK,
                minimum=0.05,
                maximum=0.3,
                default=0.1,
            ),
        )

    def test_every_acceptance_category_is_declared(self) -> None:
        kinds = {parameter.kind for parameter in self.space.parameters}
        self.assertEqual(
            kinds,
            {
                ParameterKind.FACTOR_WEIGHT,
                ParameterKind.WINDOW,
                ParameterKind.FILTER_THRESHOLD,
                ParameterKind.POSITION_COUNT,
                ParameterKind.COST,
                ParameterKind.RISK,
            },
        )

    def test_full_override_set_validates(self) -> None:
        parameters = self.space.validate_values(
            {
                "weight_dividend_yield": 0.2,
                "weight_low_volatility": 0.3,
                "lookback_days": 252,
                "max_suspension_ratio": 0.25,
                "position_count": 18,
                "cost_multiplier": 2.5,
                "max_position_pct": 0.15,
            }
        )
        self.assertEqual(len(parameters), 7)

    def test_undeclared_parameter_cannot_be_changed(self) -> None:
        # 未声明参数不可变更: any knob outside the space is rejected.
        with self.assertRaises(UndeclaredParameterError):
            self.space.validate_values({"weight_momentum": 0.4})

    def test_all_undeclared_names_are_listed(self) -> None:
        with self.assertRaisesRegex(UndeclaredParameterError, "a.*b"):
            self.space.validate_values({"a": 1, "b": 2})

    def test_market_specific_knob_is_rejected_until_declared(self) -> None:
        # A parameter that was never declared cannot influence the run.
        self.assertFalse(self.space.declared("hk_lot_size"))
        with self.assertRaises(UndeclaredParameterError):
            self.space.validate_value("hk_lot_size", 100)


if __name__ == "__main__":
    unittest.main()
