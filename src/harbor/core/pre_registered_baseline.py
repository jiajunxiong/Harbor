"""Pre-registered baseline and comparison (MVP 3 / SP 3.30).

The baseline configuration and baseline metric are fixed BEFORE parameter
search (预注册基线), so a later report can contrast the baseline against the
selected parameters (报告中对比基准与所选参数的差异). The baseline is an
immutable, fingerprinted value validated against the SP 3.15 parameter space;
the comparison computes every parameter difference and the metric gap under
the pre-registered direction.

- :class:`PreRegisteredBaseline` fixes the baseline parameter configuration
  (基准配置) and baseline metric (基准指标) ahead of the search, tied to the
  frozen dataset fingerprint (SP 3.7) and code version.
- :func:`pre_register_baseline` validates the baseline parameters through the
  SP 3.15 space (rejecting undeclared / out-of-range / off-step values) and
  fingerprints the fixed record — a baseline cannot be silently adjusted after
  the fact.
- :class:`ParameterDifference` and :class:`BaselineComparison` build the
  report: every parameter value difference between the baseline and the
  selected parameters, the baseline vs selected metric gap and whether the
  selection improved on the baseline under the pre-registered direction.
  Because the report derives from the frozen baseline, a baseline that was
  re-registered to match the selection is exposed as all-unchanged with no
  improvement.

Pure core layer: depends on the SP 3.15 parameter space, the SP 3.1 trial
domain and the SP 3.2 metric direction, never on storage, services or CLI.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace

from harbor.core.parameter_space import ParameterSpace
from harbor.core.validation_config import MetricDirection
from harbor.core.validation_domain import Parameter, ParameterTrial


class PreRegisteredBaselineError(ValueError):
    """Raised when a baseline or its comparison is invalid (SP 3.30)."""


@dataclass(frozen=True)
class PreRegisteredBaseline:
    """The baseline configuration and metric fixed before search (SP 3.30).

    ``parameters`` is the key-sorted baseline configuration (基准配置);
    ``metric_name`` names the baseline metric (基准指标) and ``metric`` its
    fixed value. ``fingerprint`` is the derived SHA-256 digest, so the baseline
    is immutable and cannot be silently adjusted after registration.
    """

    parameters: tuple[Parameter, ...]
    metric_name: str
    metric: float
    dataset_fingerprint: str
    code_version: str
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.parameters:
            raise PreRegisteredBaselineError("a baseline requires at least one parameter.")
        names = [parameter.name for parameter in self.parameters]
        if sorted(names) != names:
            raise PreRegisteredBaselineError("baseline parameters must be key-sorted by name.")
        if len(set(names)) != len(names):
            raise PreRegisteredBaselineError("baseline parameters must be unique.")
        if not self.metric_name.strip():
            raise PreRegisteredBaselineError("baseline metric name must be non-empty.")
        if not self.dataset_fingerprint:
            raise PreRegisteredBaselineError("dataset fingerprint must be non-empty.")
        if not self.code_version:
            raise PreRegisteredBaselineError("code version must be non-empty.")
        if not self.fingerprint:
            raise PreRegisteredBaselineError("baseline fingerprint must be non-empty.")

    def readable(self) -> str:
        """Render the baseline as one line."""
        values = ", ".join(f"{parameter.name}={parameter.value}" for parameter in self.parameters)
        return (
            f"baseline [{values}] metric {self.metric_name} {self.metric:.4g} fp {self.fingerprint}"
        )


@dataclass(frozen=True)
class ParameterDifference:
    """One parameter's difference between the baseline and the selection (SP 3.30).

    ``baseline_value`` / ``selected_value`` are ``None`` when the parameter is
    absent on that side; ``changed`` is false only when both sides carry the
    same value (both absent counts as unchanged).
    """

    name: str
    baseline_value: object | None
    selected_value: object | None
    changed: bool

    def __post_init__(self) -> None:
        if not self.name:
            raise PreRegisteredBaselineError("parameter name must be non-empty.")

    def readable(self) -> str:
        """Render the difference as one line."""
        if not self.changed:
            return f"{self.name}: unchanged ({self.baseline_value!r})"
        return f"{self.name}: {self.baseline_value!r} -> {self.selected_value!r}"


@dataclass(frozen=True)
class BaselineComparison:
    """The baseline vs selected comparison report (SP 3.30).

    ``differences`` covers the union of parameter names; ``selected_metric`` is
    the selected trial's metric (``None`` when nothing was selected),
    ``metric_gap`` its signed difference from the baseline metric and
    ``improved`` whether the selection beat the baseline under the
    pre-registered direction.
    """

    baseline: PreRegisteredBaseline
    selected_parameters: tuple[Parameter, ...]
    differences: tuple[ParameterDifference, ...]
    baseline_metric: float
    selected_metric: float | None
    metric_gap: float | None
    improved: bool | None
    fingerprint: str

    def __post_init__(self) -> None:
        selected_names = [parameter.name for parameter in self.selected_parameters]
        if sorted(selected_names) != selected_names:
            raise PreRegisteredBaselineError("selected parameters must be key-sorted by name.")
        if len(set(selected_names)) != len(selected_names):
            raise PreRegisteredBaselineError("selected parameters must be unique.")
        difference_names = [difference.name for difference in self.differences]
        if sorted(difference_names) != difference_names:
            raise PreRegisteredBaselineError("differences must be key-sorted by parameter name.")
        if len(set(difference_names)) != len(difference_names):
            raise PreRegisteredBaselineError("differences must be unique per parameter name.")
        if (self.metric_gap is None) != (self.selected_metric is None):
            raise PreRegisteredBaselineError(
                "metric_gap must be set exactly when a selected metric is present."
            )
        if (self.improved is None) != (self.selected_metric is None):
            raise PreRegisteredBaselineError(
                "improved must be set exactly when a selected metric is present."
            )
        if not self.fingerprint:
            raise PreRegisteredBaselineError("comparison fingerprint must be non-empty.")

    def readable(self) -> str:
        """Render the comparison report as one line."""
        selected = "n/a" if self.selected_metric is None else f"{self.selected_metric:.4g}"
        improved = "n/a" if self.improved is None else str(self.improved)
        return (
            f"baseline comparison: {len(self.differences)} parameter "
            f"differences, baseline metric {self.baseline_metric:.4g}, "
            f"selected metric {selected}, improved {improved}"
        )


def pre_register_baseline(
    *,
    space: ParameterSpace,
    parameters: Mapping[str, object],
    metric_name: str,
    metric: float,
    dataset_fingerprint: str,
    code_version: str,
) -> PreRegisteredBaseline:
    """Fix the baseline configuration and metric before search (SP 3.30).

    The parameters are validated through the SP 3.15 space (undeclared,
    out-of-range and off-step values are rejected) and normalized to key-sorted
    order; the fixed record carries a derived fingerprint so it cannot be
    silently adjusted.
    """
    if not metric_name.strip():
        raise PreRegisteredBaselineError("baseline metric name must be non-empty.")
    validated = tuple(
        sorted(space.validate_values(parameters), key=lambda parameter: parameter.name)
    )
    if not validated:
        raise PreRegisteredBaselineError("a baseline requires at least one parameter.")
    baseline = PreRegisteredBaseline(
        parameters=validated,
        metric_name=metric_name,
        metric=metric,
        dataset_fingerprint=dataset_fingerprint,
        code_version=code_version,
        fingerprint="unfingerprinted",
    )
    return replace(baseline, fingerprint=baseline_fingerprint(baseline))


def compare_baseline_selection(
    baseline: PreRegisteredBaseline,
    selected: ParameterTrial | None,
    *,
    direction: MetricDirection = MetricDirection.HIGHER_BETTER,
) -> BaselineComparison:
    """Build the baseline vs selected comparison report (SP 3.30).

    Computes one :class:`ParameterDifference` per union parameter name (an
    absent value is ``None``), the signed metric gap and the direction-aware
    improvement flag. A baseline that was re-registered to match the selection
    yields all-unchanged differences and no improvement, exposing the cheat.
    """
    selected_parameters = (
        tuple(sorted(selected.parameters, key=lambda parameter: parameter.name))
        if selected is not None
        else ()
    )
    baseline_values = {parameter.name: parameter.value for parameter in baseline.parameters}
    selected_values = {parameter.name: parameter.value for parameter in selected_parameters}
    names = sorted(set(baseline_values) | set(selected_values))
    differences = tuple(
        ParameterDifference(
            name=name,
            baseline_value=baseline_values.get(name),
            selected_value=selected_values.get(name),
            changed=baseline_values.get(name) != selected_values.get(name),
        )
        for name in names
    )
    selected_metric = selected.metric if selected is not None else None
    metric_gap: float | None = None
    if selected_metric is not None:
        metric_gap = selected_metric - baseline.metric
    improved: bool | None = None
    if selected_metric is not None:
        if direction is MetricDirection.HIGHER_BETTER:
            improved = selected_metric > baseline.metric
        else:
            improved = selected_metric < baseline.metric
    comparison = BaselineComparison(
        baseline=baseline,
        selected_parameters=selected_parameters,
        differences=differences,
        baseline_metric=baseline.metric,
        selected_metric=selected_metric,
        metric_gap=metric_gap,
        improved=improved,
        fingerprint="unfingerprinted",
    )
    return replace(comparison, fingerprint=comparison_fingerprint(comparison))


def baseline_json(baseline: PreRegisteredBaseline) -> str:
    """Return a stable, key-sorted JSON serialization of a baseline.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style).
    """
    payload: dict[str, object] = {
        "parameters": {parameter.name: parameter.value for parameter in baseline.parameters},
        "metric_name": baseline.metric_name,
        "metric": baseline.metric,
        "dataset_fingerprint": baseline.dataset_fingerprint,
        "code_version": baseline.code_version,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def baseline_fingerprint(baseline: PreRegisteredBaseline) -> str:
    """Return the stable SHA-256 fingerprint of a baseline (SP 3.30)."""
    return hashlib.sha256(baseline_json(baseline).encode("utf-8")).hexdigest()


def comparison_json(comparison: BaselineComparison) -> str:
    """Return a stable, key-sorted JSON serialization of a comparison report.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style).
    """
    payload: dict[str, object] = {
        "baseline": {
            "fingerprint": comparison.baseline.fingerprint,
            "parameters": {
                parameter.name: parameter.value for parameter in comparison.baseline.parameters
            },
            "metric_name": comparison.baseline.metric_name,
            "metric": comparison.baseline.metric,
        },
        "selected_parameters": {
            parameter.name: parameter.value for parameter in comparison.selected_parameters
        },
        "differences": [
            {
                "name": difference.name,
                "baseline_value": difference.baseline_value,
                "selected_value": difference.selected_value,
                "changed": difference.changed,
            }
            for difference in comparison.differences
        ],
        "baseline_metric": comparison.baseline_metric,
        "selected_metric": comparison.selected_metric,
        "metric_gap": comparison.metric_gap,
        "improved": comparison.improved,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def comparison_fingerprint(comparison: BaselineComparison) -> str:
    """Return the stable SHA-256 fingerprint of a comparison report (SP 3.30)."""
    return hashlib.sha256(comparison_json(comparison).encode("utf-8")).hexdigest()
