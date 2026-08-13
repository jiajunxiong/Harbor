"""Parameter-neighborhood sensitivity (MVP 3 / SP 3.57).

Runs a finite grid over a pre-registered neighborhood around the selected
parameters and outputs the plateau (台面), cliffs (悬崖) and infeasible regions
(不可行区域); it does NOT re-select parameters (不进行二次选参).

- :class:`ParameterNeighborhoodConfig` is the pre-registered neighborhood:
  ``steps`` grid steps per side on each parameter's declared step, a
  ``plateau_tolerance`` (metric band that counts as a plateau) and a
  ``cliff_threshold`` (metric drop that counts as a cliff; kept strictly above
  the plateau tolerance so the classification is unambiguous).
- :func:`compute_parameter_neighborhood` varies one stepped parameter at a time
  (each neighbor differs from the selected set in exactly one parameter by
  ``+/- k*step`` within its declared bounds), validates every neighbor through
  the SP 3.16 gate (a violated constraint / market applicability / invalid
  value is recorded as an INFEASIBLE region, never silently dropped), evaluates
  the feasible neighbors with the injected metric and classifies each point.

The selected trial stays fixed: the grid only classifies the surroundings and
never chooses a new best parameter set (不进行二次选参).

Pure core layer: depends only on the SP 3.15 space, the SP 3.16 gate, the SP
3.1 trial and the domain types, never on storage, services or CLI.
"""

import hashlib
import json
import math
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from harbor.core.backtest_domain import Market
from harbor.core.parameter_constraints import (
    ParameterConstraint,
    validate_parameter_set,
)
from harbor.core.parameter_space import ParameterSpace
from harbor.core.validation_domain import Parameter, ParameterTrial


class ParameterNeighborhoodError(ValueError):
    """Raised when a parameter-neighborhood input is invalid (SP 3.57)."""


class NeighborhoodClassification(StrEnum):
    """How a neighbor compares to the selected parameter set (SP 3.57)."""

    INFEASIBLE = "infeasible"
    PLATEAU = "plateau"
    CLIFF = "cliff"
    IMPROVEMENT = "improvement"
    NEUTRAL = "neutral"


@dataclass(frozen=True)
class ParameterNeighborhoodConfig:
    """The pre-registered parameter neighborhood (SP 3.57).

    ``steps`` is the number of grid steps on each side of the selected value
    (on each stepped parameter's declared step); ``plateau_tolerance`` the
    metric band that counts as a plateau (台面); ``cliff_threshold`` the metric
    drop that counts as a cliff (悬崖), kept strictly above the plateau
    tolerance so no neighbor can be both.
    """

    version: str
    source: str
    steps: int
    plateau_tolerance: float
    cliff_threshold: float
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.version:
            raise ParameterNeighborhoodError("parameter neighborhood version must be non-empty.")
        if not self.source:
            raise ParameterNeighborhoodError("parameter neighborhood source must be non-empty.")
        if self.steps < 1:
            raise ParameterNeighborhoodError(
                "the neighborhood grid must use at least one step per side."
            )
        if self.plateau_tolerance < 0:
            raise ParameterNeighborhoodError("plateau tolerance must be non-negative.")
        if self.cliff_threshold <= self.plateau_tolerance:
            raise ParameterNeighborhoodError(
                "cliff threshold must exceed the plateau tolerance so a cliff "
                "is never also a plateau."
            )
        if not self.fingerprint:
            raise ParameterNeighborhoodError(
                "parameter neighborhood fingerprint must be non-empty."
            )

    def readable(self) -> str:
        """Render the config as one line."""
        return (
            f"parameter neighborhood {self.version} ({self.source}): "
            f"{self.steps} step(s) plateau {self.plateau_tolerance} "
            f"cliff {self.cliff_threshold} fp {self.fingerprint}"
        )


def default_neighborhood_config() -> ParameterNeighborhoodConfig:
    """Return the pre-registered parameter neighborhood (SP 3.57)."""
    return build_neighborhood_config(
        version="neighborhood-default",
        steps=2,
        plateau_tolerance=0.01,
        cliff_threshold=0.05,
    )


def build_neighborhood_config(
    *,
    version: str,
    source: str = "pre-registered",
    steps: int,
    plateau_tolerance: float,
    cliff_threshold: float,
) -> ParameterNeighborhoodConfig:
    """Assemble a versioned, fingerprint-stamped neighborhood config (SP 3.57)."""
    config = ParameterNeighborhoodConfig(
        version=version,
        source=source,
        steps=steps,
        plateau_tolerance=plateau_tolerance,
        cliff_threshold=cliff_threshold,
        fingerprint="unfingerprinted",
    )
    return replace(config, fingerprint=neighborhood_config_fingerprint(config))


@dataclass(frozen=True)
class NeighborhoodPoint:
    """One finite-grid neighbor of the selected parameters (SP 3.57).

    ``parameter_name`` / ``offset_steps`` identify the neighbor (the varied
    parameter and its signed step offset); ``parameters`` is the validated full
    parameter set. A neighbor that fails the SP 3.16 gate is recorded as an
    INFEASIBLE region with its reason — never silently dropped (不可行区域).
    """

    parameter_name: str
    offset_steps: int
    parameters: tuple[Parameter, ...]
    feasible: bool
    infeasible_reason: str | None
    metric: float | None
    classification: NeighborhoodClassification

    def __post_init__(self) -> None:
        if not self.parameter_name:
            raise ParameterNeighborhoodError("a neighborhood point must name the varied parameter.")
        if self.offset_steps == 0:
            raise ParameterNeighborhoodError(
                "a neighborhood point must differ from the selected parameters."
            )
        if self.classification is NeighborhoodClassification.INFEASIBLE:
            if self.feasible:
                raise ParameterNeighborhoodError("an infeasible point must not be marked feasible.")
            if not self.infeasible_reason:
                raise ParameterNeighborhoodError(
                    "an infeasible point must carry an infeasible reason."
                )
            if self.metric is not None:
                raise ParameterNeighborhoodError("an infeasible point must not carry a metric.")
        else:
            if not self.feasible:
                raise ParameterNeighborhoodError("a classified point must be feasible.")
            if self.infeasible_reason is not None:
                raise ParameterNeighborhoodError(
                    "a feasible point must not carry an infeasible reason."
                )
            if self.metric is None:
                raise ParameterNeighborhoodError(
                    "a feasible point must carry its evaluated metric."
                )

    def readable(self) -> str:
        """Render the point as one line."""
        if not self.feasible:
            return (
                f"{self.parameter_name} {self.offset_steps:+d}: infeasible "
                f"({self.infeasible_reason})"
            )
        return (
            f"{self.parameter_name} {self.offset_steps:+d}: "
            f"{self.classification.value} metric {self.metric}"
        )


@dataclass(frozen=True)
class NeighborhoodSensitivityReport:
    """The finite-grid neighborhood around the selected parameters (SP 3.57)."""

    config: ParameterNeighborhoodConfig
    trial_id: str
    selected_metric: float
    selected_parameters: tuple[Parameter, ...]
    points: tuple[NeighborhoodPoint, ...]
    dataset_fingerprint: str
    code_version: str
    plateau_count: int
    cliff_count: int
    infeasible_count: int
    improvement_count: int
    neutral_count: int
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.trial_id:
            raise ParameterNeighborhoodError(
                "the neighborhood report must record the selected trial id."
            )
        if not self.selected_parameters:
            raise ParameterNeighborhoodError("the selected parameter set must not be empty.")
        if not self.points:
            raise ParameterNeighborhoodError(
                "a neighborhood report requires at least one grid point."
            )
        if not math.isfinite(self.selected_metric):
            raise ParameterNeighborhoodError("the selected metric must be finite.")
        if not self.dataset_fingerprint:
            raise ParameterNeighborhoodError("dataset fingerprint must be non-empty.")
        if not self.code_version:
            raise ParameterNeighborhoodError("code version must be non-empty.")
        if not self.fingerprint:
            raise ParameterNeighborhoodError(
                "parameter neighborhood report fingerprint must be non-empty."
            )
        counts = {
            NeighborhoodClassification.PLATEAU: 0,
            NeighborhoodClassification.CLIFF: 0,
            NeighborhoodClassification.INFEASIBLE: 0,
            NeighborhoodClassification.IMPROVEMENT: 0,
            NeighborhoodClassification.NEUTRAL: 0,
        }
        for point in self.points:
            counts[point.classification] += 1
        if self.plateau_count != counts[NeighborhoodClassification.PLATEAU]:
            raise ParameterNeighborhoodError("plateau count is inconsistent.")
        if self.cliff_count != counts[NeighborhoodClassification.CLIFF]:
            raise ParameterNeighborhoodError("cliff count is inconsistent.")
        if self.infeasible_count != counts[NeighborhoodClassification.INFEASIBLE]:
            raise ParameterNeighborhoodError("infeasible count is inconsistent.")
        if self.improvement_count != counts[NeighborhoodClassification.IMPROVEMENT]:
            raise ParameterNeighborhoodError("improvement count is inconsistent.")
        if self.neutral_count != counts[NeighborhoodClassification.NEUTRAL]:
            raise ParameterNeighborhoodError("neutral count is inconsistent.")

    def __len__(self) -> int:
        return len(self.points)

    def __iter__(self) -> Iterator[NeighborhoodPoint]:
        return iter(self.points)

    def __getitem__(self, index: int) -> NeighborhoodPoint:
        return self.points[index]

    @property
    def point_count(self) -> int:
        """Total number of grid points in the neighborhood."""
        return len(self.points)

    def readable(self) -> str:
        """Render the report as one line."""
        return (
            f"neighborhood of trial {self.trial_id}: {len(self.points)} points, "
            f"{self.plateau_count} plateau, {self.cliff_count} cliff, "
            f"{self.infeasible_count} infeasible, {self.improvement_count} "
            f"improvement, {self.neutral_count} neutral — no re-selection "
            f"fp {self.fingerprint}"
        )


def no_reselection_statement() -> str:
    """Return the standard no-re-selection statement (SP 3.57)."""
    return (
        "The parameter neighborhood classifies the selected parameters' "
        "surroundings (plateaus, cliffs, infeasible regions); it does not "
        "re-select parameters (SP 3.57)."
    )


def _grid_neighbors(
    selected_values: Mapping[str, object],
    space: ParameterSpace,
    config: ParameterNeighborhoodConfig,
) -> list[tuple[dict[str, object], str, int]]:
    """Return the finite-grid neighbors (one stepped parameter varied at a time).

    Each neighbor differs from the selected set in exactly one stepped
    parameter, shifted by ``offset*step`` (offset in ``+/-1 .. +/-steps``)
    within the parameter's declared bounds; out-of-bounds shifts are dropped.
    """
    neighbors: list[tuple[dict[str, object], str, int]] = []
    for name, value in selected_values.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        declared = space.require_declared(name)
        step = declared.step
        if step is None or step <= 0:
            continue
        minimum = declared.minimum
        maximum = declared.maximum
        for offset in range(-config.steps, config.steps + 1):
            if offset == 0:
                continue
            shifted = value + offset * step
            if isinstance(value, int) and float(shifted).is_integer():
                shifted = int(shifted)
            if minimum is not None and shifted < minimum:
                continue
            if maximum is not None and shifted > maximum:
                continue
            neighbor = dict(selected_values)
            neighbor[name] = shifted
            neighbors.append((neighbor, name, offset))
    return neighbors


def _classify(
    metric: float,
    selected_metric: float,
    config: ParameterNeighborhoodConfig,
) -> NeighborhoodClassification:
    """Classify one neighbor against the selected metric (SP 3.57).

    A plateau (台面) is within the plateau tolerance of the selected metric, an
    improvement above it, a cliff (悬崖) below the cliff threshold, and anything
    in between is neutral.
    """
    difference = metric - selected_metric
    if abs(difference) <= config.plateau_tolerance:
        return NeighborhoodClassification.PLATEAU
    if difference > config.plateau_tolerance:
        return NeighborhoodClassification.IMPROVEMENT
    if difference < -config.cliff_threshold:
        return NeighborhoodClassification.CLIFF
    return NeighborhoodClassification.NEUTRAL


def compute_parameter_neighborhood(
    selected: ParameterTrial,
    *,
    config: ParameterNeighborhoodConfig,
    space: ParameterSpace,
    market: Market,
    evaluate: Callable[[Mapping[str, object]], float],
    constraints: Sequence[ParameterConstraint] = (),
) -> NeighborhoodSensitivityReport:
    """Run the finite grid around the selected parameters (SP 3.57).

    Every neighbor is validated through the SP 3.16 gate (an invalid /
    constraint-violating neighbor is recorded as an INFEASIBLE region, never
    silently dropped) and the feasible ones are evaluated and classified. The
    selected trial is never re-selected (不进行二次选参).

    Args:
        selected: The SP 3.21 selected parameter trial (its metric is the
            neighborhood reference).
        config: The pre-registered neighborhood grid.
        space: The declared parameter space (SP 3.15).
        market: The market the selected parameters apply to.
        evaluate: Returns the metric for one neighbor parameter set.
        constraints: The SP 3.16 combination constraints to enforce.
    """
    if selected.metric is None:
        raise ParameterNeighborhoodError(
            "the selected trial must carry a metric to build a neighborhood."
        )
    selected_values: dict[str, object] = {
        parameter.name: parameter.value for parameter in selected.parameters
    }
    points: list[NeighborhoodPoint] = []
    for neighbor, name, offset in _grid_neighbors(selected_values, space, config):
        try:
            validated = validate_parameter_set(
                space, neighbor, market=market, constraints=constraints
            )
        except ValueError as error:
            points.append(
                NeighborhoodPoint(
                    parameter_name=name,
                    offset_steps=offset,
                    parameters=(),
                    feasible=False,
                    infeasible_reason=str(error),
                    metric=None,
                    classification=NeighborhoodClassification.INFEASIBLE,
                )
            )
            continue
        metric = evaluate(neighbor)
        points.append(
            NeighborhoodPoint(
                parameter_name=name,
                offset_steps=offset,
                parameters=validated,
                feasible=True,
                infeasible_reason=None,
                metric=metric,
                classification=_classify(metric, selected.metric, config),
            )
        )
    counts = {classification: 0 for classification in NeighborhoodClassification}
    for point in points:
        counts[point.classification] += 1
    report = NeighborhoodSensitivityReport(
        config=config,
        trial_id=selected.trial_id,
        selected_metric=selected.metric,
        selected_parameters=selected.parameters,
        points=tuple(points),
        dataset_fingerprint=selected.dataset_fingerprint,
        code_version=selected.code_version,
        plateau_count=counts[NeighborhoodClassification.PLATEAU],
        cliff_count=counts[NeighborhoodClassification.CLIFF],
        infeasible_count=counts[NeighborhoodClassification.INFEASIBLE],
        improvement_count=counts[NeighborhoodClassification.IMPROVEMENT],
        neutral_count=counts[NeighborhoodClassification.NEUTRAL],
        fingerprint="unfingerprinted",
    )
    return replace(report, fingerprint=neighborhood_fingerprint(report))


def _config_payload(config: ParameterNeighborhoodConfig) -> dict[str, object]:
    """The config's JSON payload (its own fingerprint excluded)."""
    return {
        "version": config.version,
        "source": config.source,
        "steps": config.steps,
        "plateau_tolerance": config.plateau_tolerance,
        "cliff_threshold": config.cliff_threshold,
    }


def _parameters_payload(parameters: Sequence[Parameter]) -> list[dict[str, object]]:
    """Serialize a parameter set as name/value pairs."""
    return [{"name": parameter.name, "value": parameter.value} for parameter in parameters]


def _point_payload(point: NeighborhoodPoint) -> dict[str, object]:
    """Serialize one neighborhood point."""
    return {
        "parameter_name": point.parameter_name,
        "offset_steps": point.offset_steps,
        "parameters": _parameters_payload(point.parameters),
        "feasible": point.feasible,
        "infeasible_reason": point.infeasible_reason,
        "metric": point.metric,
        "classification": point.classification.value,
    }


def neighborhood_config_json(config: ParameterNeighborhoodConfig) -> str:
    """Return a stable, key-sorted JSON serialization of a neighborhood config."""
    return json.dumps(
        _config_payload(config), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def neighborhood_config_fingerprint(config: ParameterNeighborhoodConfig) -> str:
    """Return the stable SHA-256 fingerprint of a neighborhood config (SP 3.57)."""
    return hashlib.sha256(neighborhood_config_json(config).encode("utf-8")).hexdigest()


def neighborhood_json(report: NeighborhoodSensitivityReport) -> str:
    """Return a stable, key-sorted JSON serialization of a neighborhood report.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style).
    """
    payload: dict[str, object] = {
        "config": _config_payload(report.config),
        "trial_id": report.trial_id,
        "selected_metric": report.selected_metric,
        "selected_parameters": _parameters_payload(report.selected_parameters),
        "dataset_fingerprint": report.dataset_fingerprint,
        "code_version": report.code_version,
        "plateau_count": report.plateau_count,
        "cliff_count": report.cliff_count,
        "infeasible_count": report.infeasible_count,
        "improvement_count": report.improvement_count,
        "neutral_count": report.neutral_count,
        "points": [_point_payload(point) for point in report.points],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def neighborhood_fingerprint(report: NeighborhoodSensitivityReport) -> str:
    """Return the stable SHA-256 fingerprint of a neighborhood report (SP 3.57)."""
    return hashlib.sha256(neighborhood_json(report).encode("utf-8")).hexdigest()


__all__: tuple[str, ...] = (
    "ParameterNeighborhoodError",
    "NeighborhoodClassification",
    "ParameterNeighborhoodConfig",
    "NeighborhoodPoint",
    "NeighborhoodSensitivityReport",
    "default_neighborhood_config",
    "build_neighborhood_config",
    "no_reselection_statement",
    "compute_parameter_neighborhood",
    "neighborhood_config_json",
    "neighborhood_config_fingerprint",
    "neighborhood_json",
    "neighborhood_fingerprint",
)
