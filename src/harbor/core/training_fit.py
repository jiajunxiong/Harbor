"""Training-period fit and persistable fit snapshot (MVP 3 / SP 3.19).

Out-of-sample validation must fit anything learnable on the training period
only, so a later validation/test application can use frozen fitted state
without ever re-fitting (SP 3.20). This module provides the fitting
interface and the persistable snapshot for that state:

- ``standardization_fit`` pools the training-period raw factor values across
  dates and symbols and fits the SP 2.22 standardization thresholds — the
  winsorize lower/upper clip bounds and, for z-score, the pooled mean and
  standard deviation — so the exact rule can be replayed and later applied.
- ``industry_baseline_fit`` pools per-industry training values and fits a
  mean / standard-deviation baseline per industry, excluding industries with
  too few observations.
- :class:`TrainingFit` is the persistable snapshot: it records the training
  boundaries the fit was computed from (``fit_start``..``fit_end``), the
  frozen dataset fingerprint (SP 3.7), the code version, the fitted
  standardization / industry state and any other fitted constants, plus a
  derived SHA-256 fingerprint. ``require_fit_within_training`` enforces that
  the recorded fit range never extends beyond the SP 3.4 training interval.

Pure core layer: depends on the SP 2.22 standardization config, the SP 3.1
validation-domain split and the SP 3.7 dataset fingerprint, never on storage,
services or CLI code.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from math import fsum, sqrt
from statistics import fmean

from harbor.core.factor_standardization import (
    StandardizationConfig,
    StandardizationMethod,
)
from harbor.core.validation_domain import EvaluationSplit


class TrainingFitError(ValueError):
    """Raised when training-period fit state cannot be built or confined (SP 3.19)."""


@dataclass(frozen=True)
class StandardizationFit:
    """Fitted factor-standardization state from the training period (SP 3.19).

    ``symbols`` are the training symbols that contributed a non-missing value
    (key-sorted) and ``observations`` is the pooled training observation
    count — the evidence that the thresholds came only from the training
    period. ``winsorize_lower``/``winsorize_upper`` are the fitted clip
    thresholds (when the SP 2.22 config winsorizes) and ``mean``/``std`` the
    pooled z-score statistics (when the method is z-score). Rank and plain
    quantile standardization are stateless, so their numeric fields stay
    ``None``.
    """

    config: StandardizationConfig
    symbols: tuple[str, ...]
    observations: int
    winsorize_lower: float | None = None
    winsorize_upper: float | None = None
    mean: float | None = None
    std: float | None = None

    def __post_init__(self) -> None:
        if not self.symbols:
            raise TrainingFitError("standardization fit requires at least one training symbol.")
        if self.observations <= 0:
            raise TrainingFitError("standardization fit requires at least one observation.")
        if (self.winsorize_lower is None) != (self.winsorize_upper is None):
            raise TrainingFitError("winsorize thresholds must be set together.")
        if (
            self.winsorize_lower is not None
            and self.winsorize_upper is not None
            and self.winsorize_lower > self.winsorize_upper
        ):
            raise TrainingFitError("winsorize lower bound must not exceed the upper bound.")
        if (self.mean is None) != (self.std is None):
            raise TrainingFitError("z-score mean and std must be set together.")
        if self.std is not None and self.std < 0:
            raise TrainingFitError("standard deviation must be non-negative.")
        if self.config.winsorize is not None and self.winsorize_lower is None:
            raise TrainingFitError("a winsorizing config requires fitted clip thresholds.")
        if self.config.method is StandardizationMethod.ZSCORE and self.mean is None:
            raise TrainingFitError("a z-score config requires fitted mean and std.")

    def readable(self) -> str:
        """Render the fitted standardization state as one line."""
        winsorize = "no-winsorize"
        if self.winsorize_lower is not None and self.winsorize_upper is not None:
            winsorize = f"clip {self.winsorize_lower:.4g}..{self.winsorize_upper:.4g}"
        stats = "stateless"
        if self.mean is not None and self.std is not None:
            stats = f"mean {self.mean:.4g} std {self.std:.4g}"
        return (
            f"standardization {self.config.method.value} "
            f"symbols {len(self.symbols)} obs {self.observations} "
            f"{winsorize} {stats}"
        )


@dataclass(frozen=True)
class IndustryBaseline:
    """One industry's fitted training-period baseline (SP 3.19)."""

    industry: str
    mean: float
    std: float
    observations: int

    def __post_init__(self) -> None:
        if not self.industry:
            raise TrainingFitError("industry must be non-empty.")
        if self.observations <= 0:
            raise TrainingFitError("industry baseline requires at least one observation.")
        if self.std < 0:
            raise TrainingFitError("standard deviation must be non-negative.")

    def readable(self) -> str:
        """Render one baseline as a line."""
        return f"{self.industry} mean {self.mean:.4g} std {self.std:.4g} n={self.observations}"


@dataclass(frozen=True)
class IndustryBaselineFit:
    """Per-industry baselines fitted from the training period (SP 3.19).

    ``baselines`` are key-sorted by industry with no duplicates; ``symbols``
    is the sorted union of training symbols that contributed; ``excluded``
    lists the industries dropped for having fewer than
    ``minimum_observations`` training values.
    """

    baselines: tuple[IndustryBaseline, ...]
    symbols: tuple[str, ...]
    minimum_observations: int = 1
    excluded: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.baselines:
            raise TrainingFitError("an industry fit requires at least one baseline.")
        if not self.symbols:
            raise TrainingFitError("an industry fit requires at least one symbol.")
        if self.minimum_observations <= 0:
            raise TrainingFitError("minimum_observations must be positive.")
        industries = [baseline.industry for baseline in self.baselines]
        if sorted(industries) != industries:
            raise TrainingFitError("industry baselines must be key-sorted.")
        if len(set(industries)) != len(industries):
            raise TrainingFitError("industry baselines must be unique.")

    def readable(self) -> str:
        """Render the fitted industry state as one line."""
        names = ", ".join(baseline.industry for baseline in self.baselines)
        return (
            f"industry baselines [{names}] symbols {len(self.symbols)} "
            f"excluded {len(self.excluded)}"
        )


@dataclass(frozen=True)
class TrainingFit:
    """A persistable training-period fit snapshot (SP 3.19).

    Records the training boundaries the fit was computed from
    (``fit_start``..``fit_end`` — this is what confines the fitted state to
    the training period), the frozen dataset fingerprint (SP 3.7) and code
    version it belongs to, the fitted standardization and/or per-industry
    baseline state, and any other fitted constants as ``fitted_state``
    (name, value) pairs. ``fingerprint`` is the derived SHA-256 digest and is
    excluded from its own digest so it can be re-derived and verified.
    """

    fit_start: date
    fit_end: date
    dataset_fingerprint: str
    code_version: str
    fingerprint: str
    standardization: StandardizationFit | None = None
    industry_baseline: IndustryBaselineFit | None = None
    fitted_state: tuple[tuple[str, float], ...] = ()

    def __post_init__(self) -> None:
        if self.fit_start > self.fit_end:
            raise TrainingFitError("fit range is empty or reversed.")
        if not self.dataset_fingerprint:
            raise TrainingFitError("dataset fingerprint must be non-empty.")
        if not self.code_version:
            raise TrainingFitError("code version must be non-empty.")
        if not self.fingerprint:
            raise TrainingFitError("fit snapshot fingerprint must be non-empty.")
        if (
            self.standardization is None
            and self.industry_baseline is None
            and not self.fitted_state
        ):
            raise TrainingFitError("a fit snapshot requires at least one fitted artifact.")
        names = [name for name, _ in self.fitted_state]
        if any(not name for name in names):
            raise TrainingFitError("fitted_state names must be non-empty.")
        if len(set(names)) != len(names):
            raise TrainingFitError("fitted_state names must be unique.")

    def readable(self) -> str:
        """Render the snapshot as a single-line summary."""
        parts: list[str] = []
        if self.standardization is not None:
            parts.append("standardization")
        if self.industry_baseline is not None:
            parts.append("industry-baseline")
        if self.fitted_state:
            parts.append(f"custom {len(self.fitted_state)}")
        state = ", ".join(parts) or "none"
        return (
            f"fit {self.fit_start.isoformat()}..{self.fit_end.isoformat()} "
            f"dataset {self.dataset_fingerprint} code {self.code_version} "
            f"state {state} fp {self.fingerprint}"
        )


def _pooled_bound(sorted_values: Sequence[float], quantile: float) -> float:
    """Return the nearest-rank empirical quantile of a sorted value series."""
    if not sorted_values:
        raise TrainingFitError("cannot fit a threshold on an empty training series.")
    index = int(round(quantile * (len(sorted_values) - 1)))
    return sorted_values[index]


def standardization_fit(
    values_by_date: Mapping[date, Mapping[str, float | None]],
    *,
    config: StandardizationConfig,
) -> StandardizationFit:
    """Fit the SP 2.22 standardization thresholds on the training period only.

    Pools the non-missing raw values across every training date and symbol,
    fits the winsorize clip bounds and (for z-score) the pooled mean and
    standard deviation, and records the training symbols and observation
    count. Raises :class:`TrainingFitError` when the training period holds no
    non-missing values.
    """
    pooled: list[float] = []
    symbols: set[str] = set()
    for values in values_by_date.values():
        for symbol, value in values.items():
            if value is not None:
                pooled.append(value)
                symbols.add(symbol)
    if not pooled:
        raise TrainingFitError(
            "cannot fit standardization on an empty training period (no non-missing values)."
        )
    winsorize_lower: float | None = None
    winsorize_upper: float | None = None
    if config.winsorize is not None:
        ordered = sorted(pooled)
        winsorize_lower = _pooled_bound(ordered, config.winsorize)
        winsorize_upper = _pooled_bound(ordered, 1.0 - config.winsorize)
    mean: float | None = None
    std: float | None = None
    if config.method is StandardizationMethod.ZSCORE:
        z_mean = fmean(pooled)
        variance = fsum((value - z_mean) ** 2 for value in pooled) / len(pooled)
        mean = z_mean
        std = sqrt(variance)
    return StandardizationFit(
        config=config,
        symbols=tuple(sorted(symbols)),
        observations=len(pooled),
        winsorize_lower=winsorize_lower,
        winsorize_upper=winsorize_upper,
        mean=mean,
        std=std,
    )


def industry_baseline_fit(
    values_by_industry: Mapping[str, Mapping[str, float | None]],
    *,
    minimum_observations: int = 1,
) -> IndustryBaselineFit:
    """Fit per-industry baselines on the training period only (SP 3.19).

    Pools each industry's non-missing training values and fits a mean /
    standard-deviation baseline; industries with fewer than
    ``minimum_observations`` values are excluded rather than fitted on thin
    data. Raises :class:`TrainingFitError` when no industry can be fitted.
    """
    if minimum_observations <= 0:
        raise TrainingFitError("minimum_observations must be positive.")
    baselines: list[IndustryBaseline] = []
    excluded: list[str] = []
    symbols: set[str] = set()
    for industry in sorted(values_by_industry):
        pooled: list[float] = []
        for symbol, value in values_by_industry[industry].items():
            if value is not None:
                pooled.append(value)
                symbols.add(symbol)
        if len(pooled) < minimum_observations:
            excluded.append(industry)
            continue
        mean = fmean(pooled)
        variance = fsum((value - mean) ** 2 for value in pooled) / len(pooled)
        baselines.append(
            IndustryBaseline(
                industry=industry,
                mean=mean,
                std=sqrt(variance),
                observations=len(pooled),
            )
        )
    if not baselines:
        raise TrainingFitError(
            "no industry baseline could be fitted from the training period "
            "(every industry is below minimum_observations)."
        )
    return IndustryBaselineFit(
        baselines=tuple(baselines),
        symbols=tuple(sorted(symbols)),
        minimum_observations=minimum_observations,
        excluded=tuple(excluded),
    )


def fit_json(fit: TrainingFit) -> str:
    """Return a stable, key-sorted JSON serialization of a fit snapshot.

    Dates and enums serialize to scalars and fitted records keep declaration
    order, so equal snapshots always produce identical text. The derived
    ``fingerprint`` field is intentionally excluded so the digest can be
    re-derived and compared against the recorded value (SP 3.7 style).
    """
    standardization: dict[str, object] | None = None
    if fit.standardization is not None:
        standardization = {
            "config": {
                "method": fit.standardization.config.method.value,
                "direction": fit.standardization.config.direction.value,
                "winsorize": fit.standardization.config.winsorize,
            },
            "symbols": list(fit.standardization.symbols),
            "observations": fit.standardization.observations,
            "winsorize_lower": fit.standardization.winsorize_lower,
            "winsorize_upper": fit.standardization.winsorize_upper,
            "mean": fit.standardization.mean,
            "std": fit.standardization.std,
        }
    industry_baseline: dict[str, object] | None = None
    if fit.industry_baseline is not None:
        industry_baseline = {
            "minimum_observations": fit.industry_baseline.minimum_observations,
            "baselines": [
                {
                    "industry": baseline.industry,
                    "mean": baseline.mean,
                    "std": baseline.std,
                    "observations": baseline.observations,
                }
                for baseline in fit.industry_baseline.baselines
            ],
            "symbols": list(fit.industry_baseline.symbols),
            "excluded": list(fit.industry_baseline.excluded),
        }
    payload: dict[str, object] = {
        "fit_start": fit.fit_start.isoformat(),
        "fit_end": fit.fit_end.isoformat(),
        "dataset_fingerprint": fit.dataset_fingerprint,
        "code_version": fit.code_version,
        "standardization": standardization,
        "industry_baseline": industry_baseline,
        "fitted_state": [[name, value] for name, value in fit.fitted_state],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fit_fingerprint(fit: TrainingFit) -> str:
    """Return the stable SHA-256 fingerprint of a fit snapshot (SP 3.19).

    Identical snapshots always fingerprint identically; the digest excludes
    the derived fingerprint field so it can be re-derived and verified.
    """
    return hashlib.sha256(fit_json(fit).encode("utf-8")).hexdigest()


def build_training_fit(
    *,
    fit_start: date,
    fit_end: date,
    dataset_fingerprint: str,
    code_version: str,
    standardization: StandardizationFit | None = None,
    industry_baseline: IndustryBaselineFit | None = None,
    fitted_state: Sequence[tuple[str, float]] = (),
) -> TrainingFit:
    """Assemble a persistable training-fit snapshot with its fingerprint.

    Fitted constants are normalized to key-sorted, unique (name, value) pairs
    and the derived SHA-256 fingerprint is computed and recorded (SP 3.19),
    so equal inputs always build the same snapshot.
    """
    state = tuple(sorted(fitted_state, key=lambda item: item[0]))
    snapshot = TrainingFit(
        fit_start=fit_start,
        fit_end=fit_end,
        dataset_fingerprint=dataset_fingerprint,
        code_version=code_version,
        fingerprint="unfingerprinted",
        standardization=standardization,
        industry_baseline=industry_baseline,
        fitted_state=state,
    )
    return replace(snapshot, fingerprint=fit_fingerprint(snapshot))


def require_fit_within_training(fit: TrainingFit, split: EvaluationSplit) -> None:
    """Require a fit snapshot to stay within the SP 3.4 training interval.

    The training-period fit (SP 3.19) may only be computed on the frozen
    training interval — never on validation or test data — so a snapshot
    whose recorded range extends before training starts or after training
    ends is rejected with :class:`TrainingFitError`.
    """
    if fit.fit_start < split.train_start:
        raise TrainingFitError(
            f"fit starts {fit.fit_start.isoformat()} before the training period "
            f"starts {split.train_start.isoformat()}."
        )
    if fit.fit_end > split.train_end:
        raise TrainingFitError(
            f"fit ends {fit.fit_end.isoformat()} after the training period "
            f"ends {split.train_end.isoformat()}."
        )
