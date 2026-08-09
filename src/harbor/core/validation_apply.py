"""Validation-period application of a frozen training fit (MVP 3 / SP 3.20).

The validation period may only use the frozen training-fit snapshot (SP 3.19)
and the data knowable at the decision date; it must never re-fit. This module
is the application interface for that rule:

- ``apply_standardization`` standardizes a validation cross-section with the
  FROZEN fitted state — the winsorize clip bounds and, for z-score, the
  pooled training mean / standard deviation — so a validation value is scored
  against the training distribution, never the validation cross-section.
  Rank and plain quantile are stateless SP 2.22 methods, so they map the
  validation day's own values (data available at the time) after clipping to
  the fitted thresholds.
- ``apply_industry_baseline`` scores each validation value against its FITTED
  per-industry mean / standard deviation; a symbol with no fitted baseline
  (an industry excluded for thin training data, or never seen) is recorded as
  unapplied rather than silently scored.
- :class:`ValidationApplication` is the auditable application record: it ties
  the decision date to the source fit fingerprint, the frozen dataset
  fingerprint (SP 3.7) and the code version, and carries a derived SHA-256
  fingerprint so equal applications replay identically (SP 3.28).
  ``require_application_in_validation`` enforces the SP 3.20 rule against the
  SP 3.4 split: the fit must be confined to training
  (``require_fit_within_training``, SP 3.19) and the application date must
  fall inside the validation interval.

The apply functions take the fitted state as input and never compute a pooled
statistic from the validation values, so re-fitting on validation is
structurally impossible. Pure core layer: depends on SP 2.22's standardization
helpers, the SP 3.19 fit snapshot and the SP 3.1 split, never on storage,
services or CLI code.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date

from harbor.core.factor_standardization import (
    FactorDirection,
    StandardizationMethod,
    _present,
    _quantile,
    _rank,
)
from harbor.core.training_fit import (
    IndustryBaselineFit,
    StandardizationFit,
    TrainingFit,
    require_fit_within_training,
)
from harbor.core.validation_domain import EvaluationSplit


class ValidationApplyError(ValueError):
    """Raised when a frozen fit cannot be applied on validation (SP 3.20)."""


@dataclass(frozen=True)
class AppliedStandardization:
    """One validation decision date's standardized scores (SP 3.20).

    ``scores`` maps each symbol to its standardized score — computed with the
    frozen training thresholds, never the validation cross-section — with
    missing values kept as ``None``; entries are key-sorted so the record is
    canonical and replayable. ``method`` is recorded so the application is
    self-describing.
    """

    decision_date: date
    scores: tuple[tuple[str, float | None], ...]
    method: StandardizationMethod

    def __post_init__(self) -> None:
        symbols = [symbol for symbol, _ in self.scores]
        if sorted(symbols) != symbols:
            raise ValidationApplyError("scores must be key-sorted by symbol.")
        if len(set(symbols)) != len(symbols):
            raise ValidationApplyError("scores must contain each symbol once.")

    def readable(self) -> str:
        """Render the applied scores as one line."""
        return (
            f"standardized {self.decision_date.isoformat()} "
            f"{self.method.value} symbols {len(self.scores)}"
        )


@dataclass(frozen=True)
class AppliedIndustryBaseline:
    """One validation decision date's industry-standardized scores (SP 3.20).

    ``scores`` maps each symbol to ``(value - fitted_mean) / fitted_std``
    using the FITTED per-industry baseline; ``unapplied`` lists the symbols
    for which no fitted baseline exists (excluded or unseen industry), which
    are scored ``None`` rather than silently standardized.
    """

    decision_date: date
    scores: tuple[tuple[str, float | None], ...]
    unapplied: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        symbols = [symbol for symbol, _ in self.scores]
        if sorted(symbols) != symbols:
            raise ValidationApplyError("scores must be key-sorted by symbol.")
        if len(set(symbols)) != len(symbols):
            raise ValidationApplyError("scores must contain each symbol once.")
        if sorted(self.unapplied) != list(self.unapplied):
            raise ValidationApplyError("unapplied symbols must be key-sorted.")

    def readable(self) -> str:
        """Render the applied industry scores as one line."""
        return (
            f"industry-standardized {self.decision_date.isoformat()} "
            f"symbols {len(self.scores)} unapplied {len(self.unapplied)}"
        )


@dataclass(frozen=True)
class ValidationApplication:
    """The auditable record of applying a frozen fit to validation (SP 3.20).

    Binds the application decision date to the source fit fingerprint, the
    frozen dataset fingerprint (SP 3.7) and the code version it belongs to,
    plus the applied standardization / industry state. ``fingerprint`` is the
    derived SHA-256 digest (SP 3.28) and is excluded from its own digest so
    it can be re-derived and verified.
    """

    fit_fingerprint: str
    decision_date: date
    dataset_fingerprint: str
    code_version: str
    fingerprint: str
    standardization: AppliedStandardization | None = None
    industry_baseline: AppliedIndustryBaseline | None = None

    def __post_init__(self) -> None:
        if not self.fit_fingerprint:
            raise ValidationApplyError("fit fingerprint must be non-empty.")
        if not self.dataset_fingerprint:
            raise ValidationApplyError("dataset fingerprint must be non-empty.")
        if not self.code_version:
            raise ValidationApplyError("code version must be non-empty.")
        if not self.fingerprint:
            raise ValidationApplyError("application fingerprint must be non-empty.")
        if self.standardization is None and self.industry_baseline is None:
            raise ValidationApplyError("an application requires at least one applied artifact.")

    def readable(self) -> str:
        """Render the application record as one line."""
        return (
            f"applied fit {self.fit_fingerprint} on {self.decision_date.isoformat()} "
            f"dataset {self.dataset_fingerprint} code {self.code_version} "
            f"fp {self.fingerprint}"
        )


def apply_standardization(
    values: Mapping[str, float | None],
    *,
    fit: StandardizationFit,
    decision_date: date,
) -> AppliedStandardization:
    """Apply the frozen training standardization to a validation cross-section.

    Winsorizes with the FITTED clip bounds (never recomputed from the
    validation values), then standardizes: z-score uses the FITTED pooled
    training mean and standard deviation (the anti-lookahead core), while
    rank and plain quantile map the validation day's own values — data
    available at the decision date — after clipping. Missing values stay
    ``None`` and never influence the output.
    """
    present = _present(values)
    clipped = present
    if fit.winsorize_lower is not None and fit.winsorize_upper is not None:
        clipped = {
            symbol: min(max(value, fit.winsorize_lower), fit.winsorize_upper)
            for symbol, value in present.items()
        }
    method = fit.config.method
    if method is StandardizationMethod.RANK:
        ranked = _rank(clipped, fit.config.direction)
        standardized: dict[str, float] = {symbol: float(rank) for symbol, rank in ranked.items()}
    elif method is StandardizationMethod.QUANTILE:
        standardized = _quantile(clipped)
        if fit.config.direction is FactorDirection.LOWER_IS_BETTER:
            standardized = {symbol: 1.0 - score for symbol, score in standardized.items()}
    else:
        mean = fit.mean
        std = fit.std
        if mean is None or std is None:
            raise ValidationApplyError("a z-score fit requires fitted mean and std to apply.")
        if std == 0.0:
            standardized = {symbol: 0.0 for symbol in clipped}
        else:
            standardized = {symbol: (value - mean) / std for symbol, value in clipped.items()}
        if fit.config.direction is FactorDirection.LOWER_IS_BETTER:
            standardized = {symbol: -score for symbol, score in standardized.items()}
    result: dict[str, float | None] = {symbol: None for symbol in values}
    for symbol, score in standardized.items():
        result[symbol] = score
    return AppliedStandardization(
        decision_date=decision_date,
        scores=tuple(sorted(result.items())),
        method=method,
    )


def apply_industry_baseline(
    values_by_industry: Mapping[str, Mapping[str, float | None]],
    *,
    fit: IndustryBaselineFit,
    decision_date: date,
) -> AppliedIndustryBaseline:
    """Score a validation cross-section against the FITTED industry baselines.

    Each non-missing value is standardized with its industry's FITTED
    baseline ``(value - mean) / std`` — never recomputed from validation
    data. A symbol whose industry has no fitted baseline (excluded for thin
    training data or unseen) is recorded in ``unapplied`` and scored
    ``None``. A zero-spread baseline yields the neutral ``0.0`` (SP 2.22
    convention).
    """
    baseline_by_industry = {baseline.industry: baseline for baseline in fit.baselines}
    scores: dict[str, float | None] = {}
    unapplied: set[str] = set()
    for industry in sorted(values_by_industry):
        baseline = baseline_by_industry.get(industry)
        for symbol, value in values_by_industry[industry].items():
            if value is None:
                scores[symbol] = None
                continue
            if baseline is None:
                unapplied.add(symbol)
                scores[symbol] = None
                continue
            if baseline.std == 0.0:
                scores[symbol] = 0.0
            else:
                scores[symbol] = (value - baseline.mean) / baseline.std
    return AppliedIndustryBaseline(
        decision_date=decision_date,
        scores=tuple(sorted(scores.items())),
        unapplied=tuple(sorted(unapplied)),
    )


def apply_json(application: ValidationApplication) -> str:
    """Return a stable, key-sorted JSON serialization of an application record.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style).
    """
    standardization: dict[str, object] | None = None
    if application.standardization is not None:
        standardization = {
            "decision_date": application.standardization.decision_date.isoformat(),
            "method": application.standardization.method.value,
            "scores": [[symbol, score] for symbol, score in application.standardization.scores],
        }
    industry_baseline: dict[str, object] | None = None
    if application.industry_baseline is not None:
        industry_baseline = {
            "decision_date": application.industry_baseline.decision_date.isoformat(),
            "scores": [[symbol, score] for symbol, score in application.industry_baseline.scores],
            "unapplied": list(application.industry_baseline.unapplied),
        }
    payload: dict[str, object] = {
        "fit_fingerprint": application.fit_fingerprint,
        "decision_date": application.decision_date.isoformat(),
        "dataset_fingerprint": application.dataset_fingerprint,
        "code_version": application.code_version,
        "standardization": standardization,
        "industry_baseline": industry_baseline,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def apply_fingerprint(application: ValidationApplication) -> str:
    """Return the stable SHA-256 fingerprint of an application record (SP 3.20).

    Identical applications always fingerprint identically; the digest
    excludes the derived fingerprint field so it can be re-derived.
    """
    return hashlib.sha256(apply_json(application).encode("utf-8")).hexdigest()


def build_validation_application(
    *,
    fit: TrainingFit,
    decision_date: date,
    standardization: AppliedStandardization | None = None,
    industry_baseline: AppliedIndustryBaseline | None = None,
) -> ValidationApplication:
    """Assemble an auditable validation-application record (SP 3.20).

    The record inherits the fit's dataset fingerprint and code version and
    records the source fit fingerprint, so the application is provably tied
    to one frozen snapshot. The applied artifacts must match the fit content
    and the decision date; the derived fingerprint is computed and recorded.
    """
    if standardization is None and industry_baseline is None:
        raise ValidationApplyError("an application requires at least one artifact.")
    if standardization is not None and fit.standardization is None:
        raise ValidationApplyError("the fit snapshot carries no standardization state to apply.")
    if industry_baseline is not None and fit.industry_baseline is None:
        raise ValidationApplyError("the fit snapshot carries no industry-baseline state to apply.")
    if standardization is not None and standardization.decision_date != decision_date:
        raise ValidationApplyError("the applied standardization date must match the decision date.")
    if industry_baseline is not None and industry_baseline.decision_date != decision_date:
        raise ValidationApplyError(
            "the applied industry-baseline date must match the decision date."
        )
    application = ValidationApplication(
        fit_fingerprint=fit.fingerprint,
        decision_date=decision_date,
        dataset_fingerprint=fit.dataset_fingerprint,
        code_version=fit.code_version,
        fingerprint="unfingerprinted",
        standardization=standardization,
        industry_baseline=industry_baseline,
    )
    return replace(application, fingerprint=apply_fingerprint(application))


def require_application_in_validation(
    decision_date: date,
    fit: TrainingFit,
    split: EvaluationSplit,
) -> None:
    """Require the fit to be a training fit and the application to be in validation.

    SP 3.20 forbids re-fitting on validation: the fit must be confined to the
    SP 3.4 training interval (``require_fit_within_training``, SP 3.19) and
    the application decision date must fall inside the validation interval —
    never in training or test. A violation raises :class:`TrainingFitError`
    (for the fit) or :class:`ValidationApplyError` (for the date).
    """
    require_fit_within_training(fit, split)
    if decision_date < split.validation_start or decision_date > split.validation_end:
        raise ValidationApplyError(
            f"application date {decision_date.isoformat()} must lie within the "
            f"validation period {split.validation_start.isoformat()}.."
            f"{split.validation_end.isoformat()}."
        )
