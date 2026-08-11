"""Historical environment attribution (MVP 3 / SP 3.49).

For every out-of-sample trading day and every fold, records the market
environment label — the active SP 3.48 pre-registered regime names — and the
reason a label is missing when the measurement is unavailable (为每个 OOS 交易
日和折叠记录市场环境标签及标签缺失原因).

Per OOS trading day, per measured dimension (trend / volatility / liquidity /
FX), the data-dependent ``measure_for`` callback returns the value measured
over the regime's lookback window ending at that day, and the SP 3.48
:func:`~harbor.core.market_environment.active_regimes` classification maps it
to the pre-registered regime labels. A ``None`` value — e.g. insufficient
history for the window — produces a missing label with a recorded reason,
never a silently assumed label (reject-don't-assume).

- :class:`EnvironmentLabel` is one (day, dimension) attribution;
- :class:`FoldEnvironmentAttribution` collects one fold's labels in date /
  dimension order;
- :class:`EnvironmentAttributionReport` spans all folds and records the
  definition-set version/fingerprint (SP 3.48) plus the frozen dataset
  fingerprint and code version, with a re-derivable SHA-256 fingerprint for
  replayability (SP 3.46).

Pure core layer: depends only on the SP 3.35 run, the SP 3.48 definitions and
the domain types, never on storage, services or CLI.
"""

import hashlib
import json
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import date

from harbor.core.market_environment import (
    EnvironmentDefinitionSet,
    EnvironmentDimension,
    active_regimes,
)
from harbor.core.rolling_oos import RollingOosRun
from harbor.core.validation_domain import WalkForwardFold


class EnvironmentAttributionError(ValueError):
    """Raised when environment attribution is invalid (SP 3.49)."""


@dataclass(frozen=True)
class EnvironmentLabel:
    """One OOS day's environment label for one dimension (SP 3.49).

    ``as_of`` is the OOS trading day, ``dimension`` the measured quantity and
    ``regime_names`` the active pre-registered SP 3.48 regimes (possibly
    empty when the value falls in no regime). A measurable day carries
    ``measured_value`` and no ``missing_reason``; an unmeasurable day carries
    no value and a recorded ``missing_reason`` (标签缺失原因) — never a
    silently assumed label.
    """

    as_of: date
    fold_index: int
    dimension: EnvironmentDimension
    regime_names: tuple[str, ...]
    measured_value: float | None
    missing_reason: str | None

    def __post_init__(self) -> None:
        if self.fold_index < 0:
            raise EnvironmentAttributionError("fold index must be non-negative.")
        if self.measured_value is None:
            if not self.missing_reason:
                raise EnvironmentAttributionError("a missing label must carry a missing reason.")
            if self.regime_names:
                raise EnvironmentAttributionError("a missing label must not carry regime names.")
        else:
            if self.missing_reason is not None:
                raise EnvironmentAttributionError(
                    "a measured label must not carry a missing reason."
                )

    def readable(self) -> str:
        """Render the label as one line."""
        if self.measured_value is None:
            return (
                f"{self.as_of.isoformat()} fold {self.fold_index} "
                f"{self.dimension.value}: {self.missing_reason}"
            )
        names = ", ".join(self.regime_names) or "no active regime"
        return (
            f"{self.as_of.isoformat()} fold {self.fold_index} "
            f"{self.dimension.value}: {names} ({self.measured_value})"
        )


@dataclass(frozen=True)
class FoldEnvironmentAttribution:
    """One fold's per-day environment labels (SP 3.49).

    ``labels`` are ordered by date then dimension, all belonging to
    ``fold_index``. ``days`` lists the fold's OOS trading days and
    ``missing_count`` the labels whose measurement was unavailable.
    """

    fold_index: int
    labels: tuple[EnvironmentLabel, ...]

    def __post_init__(self) -> None:
        if self.fold_index < 0:
            raise EnvironmentAttributionError("fold index must be non-negative.")
        if not self.labels:
            raise EnvironmentAttributionError("a fold attribution requires at least one label.")
        previous: tuple[date, str] | None = None
        for label in self.labels:
            if label.fold_index != self.fold_index:
                raise EnvironmentAttributionError(
                    "a fold attribution's labels must belong to the fold."
                )
            key = (label.as_of, label.dimension.value)
            if previous is not None and key <= previous:
                raise EnvironmentAttributionError(
                    "a fold attribution's labels must be ordered by date then dimension."
                )
            previous = key

    @property
    def days(self) -> tuple[date, ...]:
        """The fold's OOS trading days, in ascending order."""
        seen: list[date] = []
        for label in self.labels:
            if not seen or seen[-1] != label.as_of:
                seen.append(label.as_of)
        return tuple(seen)

    @property
    def missing_count(self) -> int:
        """Number of labels whose measurement was unavailable."""
        return sum(1 for label in self.labels if label.measured_value is None)

    def labels_for(self, as_of: date) -> tuple[EnvironmentLabel, ...]:
        """Return the labels for one OOS day, in dimension order."""
        return tuple(label for label in self.labels if label.as_of == as_of)

    def readable(self) -> str:
        """Render the fold attribution as one line."""
        return (
            f"fold {self.fold_index}: {len(self.days)} OOS days, "
            f"{len(self.labels)} labels, {self.missing_count} missing"
        )


@dataclass(frozen=True)
class EnvironmentAttributionReport:
    """The environment attribution across all folds (SP 3.49).

    ``definition_version`` / ``definition_fingerprint`` identify the SP 3.48
    pre-registered regime set used; ``dataset_fingerprint`` / ``code_version``
    the frozen evaluation context; ``fingerprint`` is the re-derivable SHA-256
    digest.
    """

    folds: tuple[FoldEnvironmentAttribution, ...]
    definition_version: str
    definition_fingerprint: str
    dataset_fingerprint: str
    code_version: str
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.folds:
            raise EnvironmentAttributionError(
                "an environment attribution report requires at least one fold."
            )
        for index, fold in enumerate(self.folds):
            if fold.fold_index != index:
                raise EnvironmentAttributionError(
                    f"attribution fold {index} must carry fold_index {index}."
                )
        if not self.definition_version:
            raise EnvironmentAttributionError("definition version must be non-empty.")
        if not self.definition_fingerprint:
            raise EnvironmentAttributionError("definition fingerprint must be non-empty.")
        if not self.dataset_fingerprint:
            raise EnvironmentAttributionError("dataset fingerprint must be non-empty.")
        if not self.code_version:
            raise EnvironmentAttributionError("code version must be non-empty.")
        if not self.fingerprint:
            raise EnvironmentAttributionError(
                "environment attribution fingerprint must be non-empty."
            )

    def __len__(self) -> int:
        return len(self.folds)

    def __iter__(self) -> Iterator[FoldEnvironmentAttribution]:
        return iter(self.folds)

    def __getitem__(self, index: int) -> FoldEnvironmentAttribution:
        return self.folds[index]

    @property
    def label_count(self) -> int:
        """Total number of (day, dimension) labels across all folds."""
        return sum(len(fold.labels) for fold in self.folds)

    @property
    def missing_count(self) -> int:
        """Total number of missing labels across all folds."""
        return sum(fold.missing_count for fold in self.folds)

    @property
    def day_count(self) -> int:
        """Total number of OOS trading days across all folds."""
        return sum(len(fold.days) for fold in self.folds)

    def readable(self) -> str:
        """Render the report as one line."""
        return (
            f"{len(self.folds)} folds, {self.day_count} OOS days, "
            f"{self.label_count} labels, {self.missing_count} missing "
            f"env {self.definition_version} fp {self.fingerprint}"
        )


def _dimension_order(definition_set: EnvironmentDefinitionSet) -> tuple[EnvironmentDimension, ...]:
    """Return the measured dimensions in first-appearance order."""
    order: list[EnvironmentDimension] = []
    for regime in definition_set.regimes:
        if regime.dimension not in order:
            order.append(regime.dimension)
    return tuple(order)


def attribute_environment(
    oos_run: RollingOosRun,
    *,
    definition_set: EnvironmentDefinitionSet,
    measure_for: Callable[[EnvironmentDimension, date, int], float | None],
    trading_days_for: Callable[[WalkForwardFold], Sequence[date]],
) -> EnvironmentAttributionReport:
    """Attribute the market environment to every OOS day and fold (SP 3.49).

    For each fold's OOS trading days and each measured dimension, the value
    over the regime's lookback window is measured and classified into the
    pre-registered SP 3.48 regimes; an unmeasurable value records the missing
    reason instead of assuming a label.

    Args:
        oos_run: The SP 3.35 rolling OOS run (its folds define the OOS days).
        definition_set: The pre-registered environment regimes (SP 3.48).
        measure_for: Returns the value for a dimension measured over
            ``window_days`` ending at ``as_of``, or ``None`` when unmeasurable.
        trading_days_for: The fold's actual OOS trading days.
    """
    dimensions = _dimension_order(definition_set)
    windows = {
        dimension: definition_set.for_dimension(dimension)[0].window_days
        for dimension in dimensions
    }
    fold_attributions: list[FoldEnvironmentAttribution] = []
    for index, result in enumerate(oos_run.results):
        fold = result.validation.fold
        labels: list[EnvironmentLabel] = []
        for day in trading_days_for(fold):
            for dimension in dimensions:
                window = windows[dimension]
                value = measure_for(dimension, day, window)
                if value is None:
                    labels.append(
                        EnvironmentLabel(
                            as_of=day,
                            fold_index=index,
                            dimension=dimension,
                            regime_names=(),
                            measured_value=None,
                            missing_reason=(
                                f"cannot measure {dimension.value} over "
                                f"{window} days ending {day.isoformat()}"
                            ),
                        )
                    )
                else:
                    names = active_regimes(definition_set, value=value, dimension=dimension)
                    labels.append(
                        EnvironmentLabel(
                            as_of=day,
                            fold_index=index,
                            dimension=dimension,
                            regime_names=names,
                            measured_value=value,
                            missing_reason=None,
                        )
                    )
        ordered = sorted(labels, key=lambda label: (label.as_of, label.dimension.value))
        fold_attributions.append(
            FoldEnvironmentAttribution(fold_index=index, labels=tuple(ordered))
        )
    report = EnvironmentAttributionReport(
        folds=tuple(fold_attributions),
        definition_version=definition_set.version,
        definition_fingerprint=definition_set.fingerprint,
        dataset_fingerprint=oos_run.dataset_fingerprint,
        code_version=oos_run.code_version,
        fingerprint="unfingerprinted",
    )
    return replace(report, fingerprint=environment_attribution_fingerprint(report))


def environment_attribution_json(report: EnvironmentAttributionReport) -> str:
    """Return a stable, key-sorted JSON serialization of an attribution report.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style).
    """
    payload: dict[str, object] = {
        "definition_version": report.definition_version,
        "definition_fingerprint": report.definition_fingerprint,
        "dataset_fingerprint": report.dataset_fingerprint,
        "code_version": report.code_version,
        "folds": [
            {
                "fold_index": fold.fold_index,
                "labels": [
                    {
                        "as_of": label.as_of.isoformat(),
                        "dimension": label.dimension.value,
                        "regime_names": list(label.regime_names),
                        "measured_value": label.measured_value,
                        "missing_reason": label.missing_reason,
                    }
                    for label in fold.labels
                ],
            }
            for fold in report.folds
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def environment_attribution_fingerprint(report: EnvironmentAttributionReport) -> str:
    """Return the stable SHA-256 fingerprint of an attribution report (SP 3.49)."""
    return hashlib.sha256(environment_attribution_json(report).encode("utf-8")).hexdigest()


__all__: tuple[str, ...] = (
    "EnvironmentAttributionError",
    "EnvironmentAttributionReport",
    "EnvironmentLabel",
    "FoldEnvironmentAttribution",
    "attribute_environment",
    "environment_attribution_fingerprint",
    "environment_attribution_json",
)
