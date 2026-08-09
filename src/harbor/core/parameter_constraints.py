"""Parameter constraint validation (MVP 3 / SP 3.16).

Layers combination and governance rules on top of the SP 3.15 parameter
space. Each SP 3.16 acceptance dimension has an explicit guard:

- 组合约束: declarative :class:`ParameterConstraint` rules (sum-to-target,
  max, implies, exclusive) checked against a searched parameter set.
- 市场适用性: a parameter declared for specific markets cannot be searched
  for a market it does not apply to (:class:`MarketApplicabilityError`).
- 拒绝无界搜索: searching a continuous parameter without a declared step
  would enumerate an infinite grid and is rejected
  (:class:`UnboundedSearchError`).
- 测试集专用参数: a parameter declared ``for_evaluation_only`` is reserved
  for the final test-set evaluation and cannot be part of a train/validation
  search (:class:`EvaluationOnlyParameterError`) — the SP 3.24 isolation
  guard.

:func:`validate_parameter_set` is the single entry point a tuner calls before
recording a trial: it runs the SP 3.15 type/range/step + undeclared checks,
then the bounded / market / test-specific / combination guards, and returns
the validated SP 3.1 ``Parameter`` records for SP 3.18 persistence. Pure core
layer, depends only on the parameter-space and backtest-domain types.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from harbor.core.backtest_domain import Market
from harbor.core.parameter_space import ParameterDomain, ParameterSpace
from harbor.core.validation_domain import Parameter

_TOLERANCE = 1e-6


def _as_number(value: object, name: str) -> float:
    """Return ``value`` as a float, rejecting non-numeric values.

    Combination constraints apply after the SP 3.15 value validation, so a
    non-numeric value here is a programming error; the check also satisfies
    mypy strict (``float(object)`` is rejected).
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ParameterConstraintError(
            f"constraint parameter {name!r} expects a number, got {value!r}."
        )
    return float(value)


class ParameterConstraintError(ValueError):
    """Raised when a combination constraint is violated (SP 3.16)."""


class UnboundedSearchError(ParameterConstraintError):
    """Raised when a search would enumerate an unbounded grid (SP 3.16)."""


class MarketApplicabilityError(ParameterConstraintError):
    """Raised when a parameter is searched for a market it does not apply to."""


class EvaluationOnlyParameterError(ParameterConstraintError):
    """Raised when a test-set-specific parameter is used in a search (SP 3.24)."""


class ConstraintKind(StrEnum):
    """The supported declarative combination constraints (SP 3.16)."""

    SUM_TO_TARGET = "sum_to_target"
    MAX_VALUE = "max_value"
    IMPLIES = "implies"
    EXCLUSIVE = "exclusive"


@dataclass(frozen=True)
class ParameterConstraint:
    """One declarative combination rule over a searched parameter set.

    ``parameters`` names the involved parameters and ``validate`` returns an
    error message when the rule is violated, or ``None`` when it holds:
    ``SUM_TO_TARGET`` requires every named parameter to be searched and their
    sum to equal ``target``; ``MAX_VALUE`` requires the sum of the searched
    named parameters not to exceed ``target``; ``IMPLIES`` requires
    ``implied`` to be searched whenever ``parameters[0]`` is; ``EXCLUSIVE``
    allows at most one of the named parameters to be searched.
    """

    name: str
    kind: ConstraintKind
    parameters: tuple[str, ...]
    target: float | None = None
    implied: str | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Constraint name must be non-empty.")
        if not self.parameters:
            raise ValueError(f"constraint {self.name!r} requires at least one parameter.")
        if len(set(self.parameters)) != len(self.parameters):
            raise ValueError(f"constraint {self.name!r} parameter names must be unique.")
        if self.kind in (ConstraintKind.SUM_TO_TARGET, ConstraintKind.MAX_VALUE):
            if self.target is None:
                raise ValueError(f"constraint {self.name!r} requires a target.")
        if self.kind is ConstraintKind.IMPLIES and not self.implied:
            raise ValueError(f"implies constraint {self.name!r} requires an implied parameter.")

    def validate(self, values: Mapping[str, object]) -> str | None:
        """Return an error message when ``values`` violates the rule, else None."""
        if self.kind in (ConstraintKind.SUM_TO_TARGET, ConstraintKind.MAX_VALUE):
            if self.target is None:
                raise ParameterConstraintError(f"constraint {self.name!r} requires a target.")
            target = float(self.target)
            if self.kind is ConstraintKind.SUM_TO_TARGET:
                missing = [name for name in self.parameters if name not in values]
                if missing:
                    return (
                        f"constraint {self.name!r}: searching must set all of "
                        f"{list(self.parameters)}; missing {missing}."
                    )
                total = sum(_as_number(values[name], name) for name in self.parameters)
                if abs(total - target) > _TOLERANCE:
                    return (
                        f"constraint {self.name!r}: sum of {list(self.parameters)} "
                        f"is {total}, expected {target}."
                    )
                return None
            total = sum(
                _as_number(values[name], name) for name in self.parameters if name in values
            )
            if total > target:
                return f"constraint {self.name!r}: sum {total} exceeds {target}."
            return None
        if self.kind is ConstraintKind.IMPLIES:
            if self.parameters[0] in values and self.implied not in values:
                return (
                    f"constraint {self.name!r}: searching {self.parameters[0]} "
                    f"requires also searching {self.implied}."
                )
            return None
        present = [name for name in self.parameters if name in values]
        if len(present) > 1:
            return (
                f"constraint {self.name!r}: at most one of {list(self.parameters)} may be searched."
            )
        return None

    def readable(self) -> str:
        """Render the constraint as one line."""
        if self.reason:
            return f"constraint {self.name!r} ({self.kind.value}): {self.reason}"
        return f"constraint {self.name!r} ({self.kind.value})"


def constraint(
    name: str,
    kind: ConstraintKind,
    *parameters: str,
    target: float | None = None,
    implied: str | None = None,
    reason: str = "",
) -> ParameterConstraint:
    """Build one declarative combination constraint (SP 3.16)."""
    return ParameterConstraint(
        name=name,
        kind=kind,
        parameters=parameters,
        target=target,
        implied=implied,
        reason=reason,
    )


def validate_combination(
    constraints: Sequence[ParameterConstraint], values: Mapping[str, object]
) -> tuple[str, ...]:
    """Return every combination-constraint violation message (non-raising)."""
    messages: list[str] = []
    for rule in constraints:
        message = rule.validate(values)
        if message is not None:
            messages.append(message)
    return tuple(messages)


def require_combination(
    constraints: Sequence[ParameterConstraint], values: Mapping[str, object]
) -> None:
    """Raise :class:`ParameterConstraintError` on any combination violation."""
    messages = validate_combination(constraints, values)
    if messages:
        raise ParameterConstraintError("; ".join(messages))


def validate_bounded(space: ParameterSpace, values: Mapping[str, object]) -> None:
    """Reject searching a continuous parameter without a step (无界搜索).

    A continuous parameter without a declared step has an uncountably
    infinite candidate grid, so searching it is unbounded and is rejected.
    Integer parameters are already finite within their bounds; a continuous
    parameter that is not being searched (absent from ``values``) keeps its
    default and is not part of the search.

    Raises:
        UnboundedSearchError: If a searched continuous parameter lacks a step.
    """
    for name in values:
        parameter = space.require_declared(name)
        if parameter.domain is ParameterDomain.CONTINUOUS and parameter.step is None:
            raise UnboundedSearchError(
                f"searching continuous parameter {name!r} without a step is "
                "unbounded; declare a step to bound the search."
            )


def validate_market_applicability(
    space: ParameterSpace, market: Market, values: Mapping[str, object]
) -> None:
    """Reject searching a parameter that does not apply to ``market``.

    A parameter declared with an explicit market list only applies to those
    markets; an empty market list means the parameter applies everywhere.

    Raises:
        MarketApplicabilityError: If a searched parameter does not apply.
    """
    for name in values:
        parameter = space.require_declared(name)
        if parameter.markets and market not in parameter.markets:
            applicable = ", ".join(item.value for item in parameter.markets)
            raise MarketApplicabilityError(
                f"parameter {name!r} does not apply to market {market.value} "
                f"(applicable: {applicable})."
            )


def validate_test_specific(space: ParameterSpace, values: Mapping[str, object]) -> None:
    """Reject searching a parameter reserved for the final evaluation.

    A parameter declared ``for_evaluation_only`` is test-set-specific and may
    only influence the final evaluation, never a train/validation search
    (SP 3.24 isolation).

    Raises:
        EvaluationOnlyParameterError: If a searched parameter is evaluation-only.
    """
    for name in values:
        parameter = space.require_declared(name)
        if parameter.for_evaluation_only:
            raise EvaluationOnlyParameterError(
                f"parameter {name!r} is reserved for the final evaluation and "
                "cannot be searched on training/validation data."
            )


def validate_parameter_set(
    space: ParameterSpace,
    values: Mapping[str, object],
    *,
    market: Market,
    constraints: Sequence[ParameterConstraint] = (),
) -> tuple[Parameter, ...]:
    """Validate a searched parameter set for one market (SP 3.16 gate).

    Runs, in order: the SP 3.15 type/range/step + undeclared checks
    (:meth:`ParameterSpace.validate_values`), the bounded-search guard, the
    market-applicability guard, the test-set-specific guard and the
    combination constraints. Returns the validated SP 3.1 ``Parameter``
    records in declaration order for SP 3.18 trial persistence.

    Raises:
        UndeclaredParameterError: If any key is outside the declared space.
        ParameterSpaceError: If any declared value is invalid.
        UnboundedSearchError: If a searched continuous parameter has no step.
        MarketApplicabilityError: If a parameter does not apply to ``market``.
        EvaluationOnlyParameterError: If a parameter is reserved for the test set.
        ParameterConstraintError: If a combination constraint is violated.
    """
    parameters = space.validate_values(values)
    validate_bounded(space, values)
    validate_market_applicability(space, market, values)
    validate_test_specific(space, values)
    require_combination(constraints, values)
    return parameters
