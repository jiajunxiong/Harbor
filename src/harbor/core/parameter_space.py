"""Parameter space declaration (MVP 3 / SP 3.15).

Explicitly declares the searchable parameters of a validation run — factor
weights, look-back windows, candidate-filter thresholds, position counts,
cost assumptions and risk parameters — before any tuning begins. A parameter
that is not declared cannot be changed: :meth:`ParameterSpace.validate_values`
rejects any key outside the declared space with
:class:`UndeclaredParameterError`, and :meth:`ParameterSpace.require_declared`
is the guard every tuner must use before mutating a knob (未声明参数不可变更).

Each declared parameter also carries its own constraint vocabulary (numeric
domain with minimum/maximum/step, boolean or categorical with allowed values)
so the space is a single source of truth for what a valid value looks like;
SP 3.16 layers combination and market constraints on top. The validated value
set is emitted as the SP 3.1 :class:`~harbor.core.validation_domain.Parameter`
records that SP 3.18 persists as a parameter trial.

Frozen Pydantic models, matching the SP 3.2 validation-config conventions;
pure core layer, depends only on the validation-domain types.
"""

from collections.abc import Mapping
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from harbor.core.backtest_domain import Market
from harbor.core.validation_domain import Parameter

ParameterValue = float | int | bool | str | None


class ParameterKind(StrEnum):
    """The six categories of searchable parameter (SP 3.15)."""

    FACTOR_WEIGHT = "factor_weight"
    WINDOW = "window"
    FILTER_THRESHOLD = "filter_threshold"
    POSITION_COUNT = "position_count"
    COST = "cost"
    RISK = "risk"


class ParameterDomain(StrEnum):
    """The value type of a declared parameter (SP 3.15)."""

    CONTINUOUS = "continuous"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    CATEGORICAL = "categorical"


_NUMERIC_DOMAINS = (ParameterDomain.CONTINUOUS, ParameterDomain.INTEGER)


class ParameterSpaceError(ValueError):
    """Raised when a declared parameter or its value is invalid (SP 3.15)."""


class UndeclaredParameterError(ParameterSpaceError):
    """Raised when a parameter outside the declared space is changed (SP 3.15)."""


class DeclaredParameter(BaseModel):
    """One declared searchable parameter (SP 3.15).

    Records the parameter name, its category (SP 3.15 acceptance), its value
    domain and, for numeric domains, the minimum/maximum bounds and an
    optional step grid. Boolean and categorical parameters carry an optional
    default. The declaration is frozen and validated so an invalid space is
    rejected at declaration time rather than silently producing bad trials.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="参数名（在空间中唯一）")
    kind: ParameterKind = Field(description="参数类别")
    domain: ParameterDomain = ParameterDomain.CONTINUOUS
    minimum: float | int | None = None
    maximum: float | int | None = None
    step: float | None = None
    allowed: tuple[ParameterValue, ...] = ()
    default: ParameterValue = None
    markets: tuple[Market, ...] = ()
    for_evaluation_only: bool = False

    @model_validator(mode="after")
    def _validate_declaration(self) -> "DeclaredParameter":
        """Reject an ill-formed declaration (SP 3.15)."""
        if not self.name.strip():
            raise ValueError("Parameter name must be non-empty.")
        if self.domain in _NUMERIC_DOMAINS:
            if self.minimum is None or self.maximum is None:
                raise ValueError(f"numeric parameter {self.name!r} requires minimum and maximum.")
            if self.minimum > self.maximum:
                raise ValueError(f"parameter {self.name!r} minimum must not exceed its maximum.")
            if self.step is not None and self.step <= 0:
                raise ValueError(f"parameter {self.name!r} step must be positive.")
            if self.default is not None:
                if isinstance(self.default, bool) or not isinstance(self.default, (int, float)):
                    raise ValueError(f"numeric parameter {self.name!r} default must be a number.")
                if not (self.minimum <= self.default <= self.maximum):
                    raise ValueError(
                        f"parameter {self.name!r} default {self.default} must lie within "
                        f"[{self.minimum}, {self.maximum}]."
                    )
        else:
            if self.minimum is not None or self.maximum is not None or self.step is not None:
                raise ValueError(
                    f"non-numeric parameter {self.name!r} cannot carry numeric bounds."
                )
            if self.domain is ParameterDomain.BOOLEAN:
                if self.default is not None and not isinstance(self.default, bool):
                    raise ValueError(f"boolean parameter {self.name!r} default must be a bool.")
            if self.domain is ParameterDomain.CATEGORICAL:
                if not self.allowed:
                    raise ValueError(
                        f"categorical parameter {self.name!r} requires allowed values."
                    )
                if self.default is not None and self.default not in self.allowed:
                    raise ValueError(
                        f"categorical parameter {self.name!r} default must be one of "
                        f"{list(self.allowed)}."
                    )
        return self

    def validate_value(self, value: object) -> ParameterValue:
        """Validate a candidate value against this declaration (SP 3.16 basis).

        Enforces the domain: a real number within ``[minimum, maximum]`` (and
        on the step grid when declared) for continuous, an integer within the
        bounds for integer, a bool for boolean and one of the allowed values
        for categorical.

        Raises:
            ParameterSpaceError: If ``value`` does not satisfy the declaration.
        """
        if self.domain is ParameterDomain.CONTINUOUS:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ParameterSpaceError(
                    f"parameter {self.name!r} expects a number, got {value!r}."
                )
            number = float(value)
            minimum, maximum = self._numeric_bounds()
            if number < float(minimum) or number > float(maximum):
                raise ParameterSpaceError(
                    f"parameter {self.name!r} value {number} is outside [{minimum}, {maximum}]."
                )
            self._require_on_grid(number, float(minimum))
            return number
        if self.domain is ParameterDomain.INTEGER:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ParameterSpaceError(
                    f"parameter {self.name!r} expects an integer, got {value!r}."
                )
            minimum, maximum = self._numeric_bounds()
            if value < minimum or value > maximum:
                raise ParameterSpaceError(
                    f"parameter {self.name!r} value {value} is outside [{minimum}, {maximum}]."
                )
            self._require_on_grid(float(value), float(minimum))
            return value
        if self.domain is ParameterDomain.BOOLEAN:
            if not isinstance(value, bool):
                raise ParameterSpaceError(f"parameter {self.name!r} expects a bool, got {value!r}.")
            return value
        if value not in self.allowed:
            raise ParameterSpaceError(
                f"parameter {self.name!r} value {value!r} must be one of {list(self.allowed)}."
            )
        return value

    def _numeric_bounds(self) -> tuple[float | int, float | int]:
        """Return the narrowed numeric bounds, raising if not declared."""
        if self.minimum is None or self.maximum is None:
            raise ParameterSpaceError(f"parameter {self.name!r} is missing its numeric bounds.")
        return self.minimum, self.maximum

    def _require_on_grid(self, number: float, minimum: float) -> None:
        """Reject a value that is not an integer multiple of ``step`` from min."""
        if self.step is None:
            return
        grid_steps = (number - minimum) / self.step
        if abs(grid_steps - round(grid_steps)) > 1e-9:
            raise ParameterSpaceError(
                f"parameter {self.name!r} value {number} is not on the "
                f"step grid (step {self.step})."
            )

    def readable(self) -> str:
        """Render the declaration as one line (SP 3.15 / 3.16)."""
        bounds = ""
        if self.domain in _NUMERIC_DOMAINS:
            bounds = f" [{self.minimum}, {self.maximum}]"
            if self.step is not None:
                bounds += f" step {self.step}"
        elif self.domain is ParameterDomain.CATEGORICAL:
            bounds = f" {list(self.allowed)}"
        default = f" default {self.default}" if self.default is not None else ""
        markets = ""
        if self.markets:
            markets = " markets " + ",".join(market.value for market in self.markets)
        evaluation = " (evaluation only)" if self.for_evaluation_only else ""
        return (
            f"{self.name} ({self.kind.value}, {self.domain.value}){bounds}{default}"
            f"{markets}{evaluation}"
        )


class ParameterSpace(BaseModel):
    """The ordered, unique set of searchable parameters (SP 3.15).

    ``parameters`` is the complete allow-list for tuning: every name is
    unique, and any parameter not declared here cannot be changed. Absent
    declared parameters keep their defaults, so a partial override set is
    valid as long as every key is declared.
    """

    model_config = ConfigDict(frozen=True)

    parameters: tuple[DeclaredParameter, ...]

    @model_validator(mode="after")
    def _validate_unique(self) -> "ParameterSpace":
        """Reject duplicate parameter names (SP 3.15)."""
        names = [parameter.name for parameter in self.parameters]
        if len(set(names)) != len(names):
            raise ValueError("Parameter names must be unique.")
        return self

    def declared(self, name: str) -> bool:
        """Whether ``name`` is declared in the space."""
        return any(parameter.name == name for parameter in self.parameters)

    def require_declared(self, name: str) -> DeclaredParameter:
        """Return the declaration for ``name``, rejecting undeclared names.

        Raises:
            UndeclaredParameterError: If ``name`` is not declared — a tuner
                must never mutate an undeclared knob (未声明参数不可变更).
        """
        for parameter in self.parameters:
            if parameter.name == name:
                return parameter
        raise UndeclaredParameterError(
            f"parameter {name!r} is not declared in the parameter space; "
            "undeclared parameters cannot be changed."
        )

    def validate_value(self, name: str, value: object) -> ParameterValue:
        """Validate one value against its declared parameter (SP 3.16 basis)."""
        return self.require_declared(name).validate_value(value)

    def validate_values(self, values: Mapping[str, object]) -> tuple[Parameter, ...]:
        """Validate a parameter override set and emit ordered trial parameters.

        Rejects ANY key that is not declared (未声明参数不可变更) and validates
        each declared value; absent declared parameters keep their defaults.
        Returns the SP 3.1 ``Parameter`` records in declaration order, ready
        for SP 3.18 trial persistence.

        Raises:
            UndeclaredParameterError: If any key is outside the declared space.
            ParameterSpaceError: If any declared value is invalid.
        """
        undeclared = [name for name in values if not self.declared(name)]
        if undeclared:
            raise UndeclaredParameterError(
                "parameters not declared in the space cannot be changed: "
                + ", ".join(sorted(undeclared))
            )
        result: list[Parameter] = []
        for declared in self.parameters:
            if declared.name in values:
                result.append(
                    Parameter(
                        name=declared.name,
                        value=declared.validate_value(values[declared.name]),
                    )
                )
        return tuple(result)

    def readable(self) -> str:
        """Render the whole space as human-readable lines."""
        lines = [f"parameter space ({len(self.parameters)} declared)"]
        for parameter in self.parameters:
            lines.append(f"  {parameter.readable()}")
        return "\n".join(lines)


def declare_parameter(
    name: str,
    kind: ParameterKind,
    *,
    domain: ParameterDomain = ParameterDomain.CONTINUOUS,
    minimum: float | int | None = None,
    maximum: float | int | None = None,
    step: float | None = None,
    allowed: tuple[ParameterValue, ...] = (),
    default: ParameterValue = None,
    markets: tuple[Market, ...] = (),
    for_evaluation_only: bool = False,
) -> DeclaredParameter:
    """Build one declared parameter with ergonomic keyword arguments (SP 3.15)."""
    return DeclaredParameter(
        name=name,
        kind=kind,
        domain=domain,
        minimum=minimum,
        maximum=maximum,
        step=step,
        allowed=allowed,
        default=default,
        markets=markets,
        for_evaluation_only=for_evaluation_only,
    )


def build_parameter_space(*parameters: DeclaredParameter) -> ParameterSpace:
    """Assemble an ordered, validated parameter space (SP 3.15)."""
    return ParameterSpace(parameters=parameters)
