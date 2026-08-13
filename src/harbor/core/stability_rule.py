"""Stability adjudication rules (MVP 3 / SP 3.58).

Aggregates the robustness evidence — fold dispersion (SP 3.39), parameter
neighborhood (SP 3.57), environment segmentation (SP 3.50), stress losses
(SP 3.51–3.56) and the coverage gate (SP 3.10) — into the pre-registered OOS
conclusion ``QUALIFIED``, ``NOT_QUALIFIED`` or ``INCONCLUSIVE``.

- :class:`StabilityRuleConfig` is the pre-registered rule: one threshold per
  evidence dimension (max fold-return spread, max neighborhood cliff /
  infeasible ratios, max environment-insufficient ratio, max stress-loss
  percent). The rule is versioned and fingerprinted so a recorded conclusion
  can always be re-audited against the exact rule that produced it.
- :class:`StabilitySignals` carries the derived robustness inputs, one signal
  per dimension; a ``None`` signal means the evidence is missing so that
  dimension is INCONCLUSIVE rather than silently assumed to pass.
- :func:`adjudicate_stability` scores every dimension (PASS / FAIL /
  INSUFFICIENT) and aggregates: any FAIL degrades the conclusion to
  ``NOT_QUALIFIED`` (a fail dominates and is never hidden by missing
  evidence), otherwise any INSUFFICIENT degrades it to ``INCONCLUSIVE``, and
  only an all-PASS result is ``QUALIFIED``.

Pure core layer: depends only on the domain types and the SP 3.1 OOS
conclusion; never touches storage, services or CLI.
"""

import hashlib
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from harbor.core.backtest_domain import Market
from harbor.core.validation_domain import OOSConclusion


class StabilityRuleError(ValueError):
    """Raised when a stability-rule input or conclusion is invalid (SP 3.58)."""


class StabilityDimension(StrEnum):
    """The five evidence dimensions adjudicated by the stability rule (SP 3.58)."""

    FOLD_DISPERSION = "FOLD_DISPERSION"
    PARAMETER_NEIGHBORHOOD = "PARAMETER_NEIGHBORHOOD"
    ENVIRONMENT_SEGMENTATION = "ENVIRONMENT_SEGMENTATION"
    STRESS_LOSS = "STRESS_LOSS"
    COVERAGE = "COVERAGE"


class StabilityVerdict(StrEnum):
    """How one evidence dimension scores against the stability rule (SP 3.58)."""

    PASS = "PASS"
    FAIL = "FAIL"
    INSUFFICIENT = "INSUFFICIENT"


@dataclass(frozen=True)
class StabilitySignals:
    """The derived robustness evidence consumed by the stability rule (SP 3.58).

    Each field is the scalar signal the caller derives from the SP 3.39 fold
    dispersion report (``fold_spread`` / ``fold_failure_count``), the SP 3.57
    neighborhood report (cliff / infeasible ratios), the SP 3.50 environment
    segments (insufficient ratio), the SP 3.51–3.56 stress reports (worst loss
    and unquantifiable flag) and the SP 3.10 coverage gate (blocked). A ratio
    or spread of ``None`` means the evidence is missing: the dimension is
    INCONCLUSIVE, never silently treated as a pass.
    """

    market: Market
    dataset_fingerprint: str
    code_version: str
    fold_spread: float | None = None
    fold_count: int = 0
    fold_failure_count: int = 0
    neighborhood_cliff_ratio: float | None = None
    neighborhood_infeasible_ratio: float | None = None
    environment_insufficient_ratio: float | None = None
    max_stress_loss_pct: float | None = None
    stress_unquantifiable: bool = False
    coverage_blocked: bool = False

    def __post_init__(self) -> None:
        if not self.dataset_fingerprint:
            raise StabilityRuleError("dataset fingerprint must be non-empty.")
        if not self.code_version:
            raise StabilityRuleError("code version must be non-empty.")
        if self.fold_spread is not None and (
            not math.isfinite(self.fold_spread) or self.fold_spread < 0
        ):
            raise StabilityRuleError("fold spread must be a finite non-negative value.")
        if self.fold_count < 0:
            raise StabilityRuleError("fold count must be non-negative.")
        if self.fold_failure_count < 0:
            raise StabilityRuleError("fold failure count must be non-negative.")
        if self.fold_failure_count > self.fold_count:
            raise StabilityRuleError("fold failure count cannot exceed the number of folds.")
        for name, value in (
            ("neighborhood cliff ratio", self.neighborhood_cliff_ratio),
            ("neighborhood infeasible ratio", self.neighborhood_infeasible_ratio),
            ("environment insufficient ratio", self.environment_insufficient_ratio),
        ):
            if value is not None and not 0 <= value <= 1:
                raise StabilityRuleError(f"{name} must be within [0, 1].")
        if self.max_stress_loss_pct is not None and (
            not math.isfinite(self.max_stress_loss_pct) or self.max_stress_loss_pct < 0
        ):
            raise StabilityRuleError("max stress loss percent must be a finite non-negative value.")


@dataclass(frozen=True)
class StabilityRuleConfig:
    """The pre-registered stability-adjudication rule (SP 3.58).

    One threshold per evidence dimension: ``max_fold_spread`` (折叠离散度 fold
    return spread), ``max_neighborhood_cliff_ratio`` and
    ``max_neighborhood_infeasible_ratio`` (参数邻域 cliffs and infeasible
    regions), ``max_environment_insufficient_ratio`` (环境分段 insufficient
    segments) and ``max_stress_loss_pct`` (压力损失 worst net-value loss). The
    coverage gate (覆盖门槛) is a blocking check and carries no threshold.
    """

    version: str
    source: str
    max_fold_spread: float
    max_neighborhood_cliff_ratio: float
    max_neighborhood_infeasible_ratio: float
    max_environment_insufficient_ratio: float
    max_stress_loss_pct: float
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.version:
            raise StabilityRuleError("stability rule version must be non-empty.")
        if not self.source:
            raise StabilityRuleError("stability rule source must be non-empty.")
        if not math.isfinite(self.max_fold_spread) or self.max_fold_spread < 0:
            raise StabilityRuleError("max fold spread must be a finite non-negative value.")
        for name, value in (
            ("max neighborhood cliff ratio", self.max_neighborhood_cliff_ratio),
            ("max neighborhood infeasible ratio", self.max_neighborhood_infeasible_ratio),
            ("max environment insufficient ratio", self.max_environment_insufficient_ratio),
        ):
            if not 0 <= value <= 1:
                raise StabilityRuleError(f"{name} must be within [0, 1].")
        if not math.isfinite(self.max_stress_loss_pct) or self.max_stress_loss_pct < 0:
            raise StabilityRuleError("max stress loss percent must be a finite non-negative value.")
        if not self.fingerprint:
            raise StabilityRuleError("stability rule fingerprint must be non-empty.")

    def readable(self) -> str:
        """Render the rule as one line."""
        return (
            f"stability rule {self.version} ({self.source}): "
            f"fold spread {self.max_fold_spread} "
            f"cliff {self.max_neighborhood_cliff_ratio} "
            f"infeasible {self.max_neighborhood_infeasible_ratio} "
            f"environment {self.max_environment_insufficient_ratio} "
            f"stress loss {self.max_stress_loss_pct}% fp {self.fingerprint}"
        )


def default_stability_rule() -> StabilityRuleConfig:
    """Return the pre-registered stability-adjudication rule (SP 3.58)."""
    return build_stability_rule(
        version="stability-default",
        max_fold_spread=0.20,
        max_neighborhood_cliff_ratio=0.50,
        max_neighborhood_infeasible_ratio=0.50,
        max_environment_insufficient_ratio=0.50,
        max_stress_loss_pct=10.0,
    )


def build_stability_rule(
    *,
    version: str,
    source: str = "pre-registered",
    max_fold_spread: float,
    max_neighborhood_cliff_ratio: float,
    max_neighborhood_infeasible_ratio: float,
    max_environment_insufficient_ratio: float,
    max_stress_loss_pct: float,
) -> StabilityRuleConfig:
    """Assemble a versioned, fingerprint-stamped stability rule (SP 3.58)."""
    config = StabilityRuleConfig(
        version=version,
        source=source,
        max_fold_spread=max_fold_spread,
        max_neighborhood_cliff_ratio=max_neighborhood_cliff_ratio,
        max_neighborhood_infeasible_ratio=max_neighborhood_infeasible_ratio,
        max_environment_insufficient_ratio=max_environment_insufficient_ratio,
        max_stress_loss_pct=max_stress_loss_pct,
        fingerprint="unfingerprinted",
    )
    return replace(config, fingerprint=stability_rule_config_fingerprint(config))


@dataclass(frozen=True)
class StabilityAssessment:
    """How one evidence dimension scores against the rule (SP 3.58)."""

    dimension: StabilityDimension
    verdict: StabilityVerdict
    detail: str

    def __post_init__(self) -> None:
        if not self.detail:
            raise StabilityRuleError("a stability assessment must carry a detail.")

    def readable(self) -> str:
        """Render the assessment as one line."""
        return f"{self.dimension.value}: {self.verdict.value} — {self.detail}"


@dataclass(frozen=True)
class StabilityConclusion:
    """The adjudicated stability conclusion (SP 3.58).

    ``conclusion`` is the SP 3.1 OOS conclusion: ``QUALIFIED`` only when every
    dimension PASSes, ``NOT_QUALIFIED`` when any dimension FAILs (a fail
    dominates) and ``INCONCLUSIVE`` when evidence is missing but nothing fails.
    ``reasons`` holds the fail/insufficient details so the downgrade is
    auditable; ``rule`` and ``signals`` capture everything needed to re-derive
    the conclusion from its fingerprint.
    """

    conclusion: OOSConclusion
    rule: StabilityRuleConfig
    signals: StabilitySignals
    assessments: tuple[StabilityAssessment, ...]
    reasons: tuple[str, ...]
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.assessments:
            raise StabilityRuleError("a stability conclusion requires at least one assessment.")
        expected = _aggregate(tuple(assessment.verdict for assessment in self.assessments))
        if self.conclusion is not expected:
            raise StabilityRuleError("stability conclusion is inconsistent with its assessments.")
        expected_reasons = tuple(
            assessment.detail
            for assessment in self.assessments
            if assessment.verdict is not StabilityVerdict.PASS
        )
        if tuple(self.reasons) != expected_reasons:
            raise StabilityRuleError(
                "stability conclusion reasons are inconsistent with its assessments."
            )
        if not self.fingerprint:
            raise StabilityRuleError("stability conclusion fingerprint must be non-empty.")

    @property
    def market(self) -> Market:
        """The market the stability conclusion applies to."""
        return self.signals.market

    @property
    def dataset_fingerprint(self) -> str:
        """The dataset fingerprint the conclusion was adjudicated on."""
        return self.signals.dataset_fingerprint

    @property
    def code_version(self) -> str:
        """The code version that produced the conclusion."""
        return self.signals.code_version

    def readable(self) -> str:
        """Render the conclusion as one line."""
        verdicts = ", ".join(
            f"{assessment.dimension.value}={assessment.verdict.value}"
            for assessment in self.assessments
        )
        return (
            f"stability {self.conclusion.value} ({self.signals.market.value}): "
            f"{verdicts} fp {self.fingerprint}"
        )


def _aggregate(verdicts: Sequence[StabilityVerdict]) -> OOSConclusion:
    """Aggregate per-dimension verdicts into the OOS conclusion (SP 3.58).

    Any FAIL dominates to ``NOT_QUALIFIED``; otherwise any INSUFFICIENT
    degrades to ``INCONCLUSIVE``; only an all-PASS result is ``QUALIFIED``.
    """
    if any(verdict is StabilityVerdict.FAIL for verdict in verdicts):
        return OOSConclusion.NOT_QUALIFIED
    if any(verdict is StabilityVerdict.INSUFFICIENT for verdict in verdicts):
        return OOSConclusion.INCONCLUSIVE
    return OOSConclusion.QUALIFIED


def _assess_dimensions(
    signals: StabilitySignals,
    config: StabilityRuleConfig,
) -> list[StabilityAssessment]:
    """Score the five evidence dimensions against the rule (SP 3.58)."""
    assessments: list[StabilityAssessment] = []
    # 1. 折叠离散度 (fold dispersion, SP 3.39).
    if signals.fold_count == 0:
        assessments.append(
            StabilityAssessment(
                dimension=StabilityDimension.FOLD_DISPERSION,
                verdict=StabilityVerdict.INSUFFICIENT,
                detail="no fold was executed",
            )
        )
    elif signals.fold_failure_count > 0:
        assessments.append(
            StabilityAssessment(
                dimension=StabilityDimension.FOLD_DISPERSION,
                verdict=StabilityVerdict.FAIL,
                detail=(
                    f"{signals.fold_failure_count} of {signals.fold_count} "
                    "fold(s) failed to execute"
                ),
            )
        )
    elif signals.fold_spread is None:
        assessments.append(
            StabilityAssessment(
                dimension=StabilityDimension.FOLD_DISPERSION,
                verdict=StabilityVerdict.INSUFFICIENT,
                detail="fold return spread is unavailable",
            )
        )
    elif signals.fold_spread > config.max_fold_spread:
        assessments.append(
            StabilityAssessment(
                dimension=StabilityDimension.FOLD_DISPERSION,
                verdict=StabilityVerdict.FAIL,
                detail=(
                    f"fold return spread {signals.fold_spread:.2%} exceeds "
                    f"{config.max_fold_spread:.2%}"
                ),
            )
        )
    else:
        assessments.append(
            StabilityAssessment(
                dimension=StabilityDimension.FOLD_DISPERSION,
                verdict=StabilityVerdict.PASS,
                detail=(
                    f"fold return spread {signals.fold_spread:.2%} within "
                    f"{config.max_fold_spread:.2%}"
                ),
            )
        )
    # 2. 参数邻域 (parameter neighborhood, SP 3.57).
    cliff_ratio = signals.neighborhood_cliff_ratio
    infeasible_ratio = signals.neighborhood_infeasible_ratio
    if cliff_ratio is None and infeasible_ratio is None:
        assessments.append(
            StabilityAssessment(
                dimension=StabilityDimension.PARAMETER_NEIGHBORHOOD,
                verdict=StabilityVerdict.INSUFFICIENT,
                detail="parameter neighborhood evidence is unavailable",
            )
        )
    elif cliff_ratio is not None and cliff_ratio > config.max_neighborhood_cliff_ratio:
        assessments.append(
            StabilityAssessment(
                dimension=StabilityDimension.PARAMETER_NEIGHBORHOOD,
                verdict=StabilityVerdict.FAIL,
                detail=(
                    f"neighborhood cliff ratio {cliff_ratio:.0%} exceeds "
                    f"{config.max_neighborhood_cliff_ratio:.0%}"
                ),
            )
        )
    elif (
        infeasible_ratio is not None and infeasible_ratio > config.max_neighborhood_infeasible_ratio
    ):
        assessments.append(
            StabilityAssessment(
                dimension=StabilityDimension.PARAMETER_NEIGHBORHOOD,
                verdict=StabilityVerdict.FAIL,
                detail=(
                    f"neighborhood infeasible ratio {infeasible_ratio:.0%} exceeds "
                    f"{config.max_neighborhood_infeasible_ratio:.0%}"
                ),
            )
        )
    else:
        assessments.append(
            StabilityAssessment(
                dimension=StabilityDimension.PARAMETER_NEIGHBORHOOD,
                verdict=StabilityVerdict.PASS,
                detail=(
                    "parameter neighborhood is stable (no excessive cliffs or infeasible regions)"
                ),
            )
        )
    # 3. 环境分段 (environment segmentation, SP 3.50).
    insufficient = signals.environment_insufficient_ratio
    if insufficient is None:
        assessments.append(
            StabilityAssessment(
                dimension=StabilityDimension.ENVIRONMENT_SEGMENTATION,
                verdict=StabilityVerdict.INSUFFICIENT,
                detail="environment segmentation evidence is unavailable",
            )
        )
    elif insufficient > config.max_environment_insufficient_ratio:
        assessments.append(
            StabilityAssessment(
                dimension=StabilityDimension.ENVIRONMENT_SEGMENTATION,
                verdict=StabilityVerdict.FAIL,
                detail=(
                    f"environment insufficient ratio {insufficient:.0%} exceeds "
                    f"{config.max_environment_insufficient_ratio:.0%}"
                ),
            )
        )
    else:
        assessments.append(
            StabilityAssessment(
                dimension=StabilityDimension.ENVIRONMENT_SEGMENTATION,
                verdict=StabilityVerdict.PASS,
                detail=(
                    f"environment insufficient ratio {insufficient:.0%} within "
                    f"{config.max_environment_insufficient_ratio:.0%}"
                ),
            )
        )
    # 4. 压力损失 (stress losses, SP 3.51–3.56).
    if signals.stress_unquantifiable:
        assessments.append(
            StabilityAssessment(
                dimension=StabilityDimension.STRESS_LOSS,
                verdict=StabilityVerdict.FAIL,
                detail="a stress scenario could not be quantified",
            )
        )
    elif signals.max_stress_loss_pct is None:
        assessments.append(
            StabilityAssessment(
                dimension=StabilityDimension.STRESS_LOSS,
                verdict=StabilityVerdict.INSUFFICIENT,
                detail="stress-loss evidence is unavailable",
            )
        )
    elif signals.max_stress_loss_pct > config.max_stress_loss_pct:
        assessments.append(
            StabilityAssessment(
                dimension=StabilityDimension.STRESS_LOSS,
                verdict=StabilityVerdict.FAIL,
                detail=(
                    f"worst stress loss {signals.max_stress_loss_pct:.2f}% exceeds "
                    f"{config.max_stress_loss_pct:.2f}%"
                ),
            )
        )
    else:
        assessments.append(
            StabilityAssessment(
                dimension=StabilityDimension.STRESS_LOSS,
                verdict=StabilityVerdict.PASS,
                detail=(
                    f"worst stress loss {signals.max_stress_loss_pct:.2f}% within "
                    f"{config.max_stress_loss_pct:.2f}%"
                ),
            )
        )
    # 5. 覆盖门槛 (coverage gate, SP 3.10).
    if signals.coverage_blocked:
        assessments.append(
            StabilityAssessment(
                dimension=StabilityDimension.COVERAGE,
                verdict=StabilityVerdict.FAIL,
                detail="the coverage gate blocked the conclusion",
            )
        )
    else:
        assessments.append(
            StabilityAssessment(
                dimension=StabilityDimension.COVERAGE,
                verdict=StabilityVerdict.PASS,
                detail="the coverage gate passed",
            )
        )
    return assessments


def adjudicate_stability(
    signals: StabilitySignals,
    *,
    config: StabilityRuleConfig,
) -> StabilityConclusion:
    """Adjudicate the stability conclusion from the robustness signals (SP 3.58).

    Each dimension is scored against the pre-registered rule; any FAIL
    dominates to ``NOT_QUALIFIED``, otherwise any missing evidence degrades to
    ``INCONCLUSIVE``, and only an all-PASS result is ``QUALIFIED``. The
    conclusion embeds the rule and the signals so it can always be re-derived
    from its fingerprint.

    Args:
        signals: The derived robustness evidence (fold dispersion, parameter
            neighborhood, environment segmentation, stress losses, coverage).
        config: The pre-registered stability-adjudication rule.
    """
    assessments = _assess_dimensions(signals, config)
    conclusion = StabilityConclusion(
        conclusion=_aggregate(tuple(assessment.verdict for assessment in assessments)),
        rule=config,
        signals=signals,
        assessments=tuple(assessments),
        reasons=tuple(
            assessment.detail
            for assessment in assessments
            if assessment.verdict is not StabilityVerdict.PASS
        ),
        fingerprint="unfingerprinted",
    )
    return replace(conclusion, fingerprint=stability_fingerprint(conclusion))


def _config_payload(config: StabilityRuleConfig) -> dict[str, object]:
    """The rule's JSON payload (its own fingerprint excluded)."""
    return {
        "version": config.version,
        "source": config.source,
        "max_fold_spread": config.max_fold_spread,
        "max_neighborhood_cliff_ratio": config.max_neighborhood_cliff_ratio,
        "max_neighborhood_infeasible_ratio": config.max_neighborhood_infeasible_ratio,
        "max_environment_insufficient_ratio": config.max_environment_insufficient_ratio,
        "max_stress_loss_pct": config.max_stress_loss_pct,
    }


def _signals_payload(signals: StabilitySignals) -> dict[str, object]:
    """The signals' JSON payload."""
    return {
        "market": signals.market.value,
        "dataset_fingerprint": signals.dataset_fingerprint,
        "code_version": signals.code_version,
        "fold_spread": signals.fold_spread,
        "fold_count": signals.fold_count,
        "fold_failure_count": signals.fold_failure_count,
        "neighborhood_cliff_ratio": signals.neighborhood_cliff_ratio,
        "neighborhood_infeasible_ratio": signals.neighborhood_infeasible_ratio,
        "environment_insufficient_ratio": signals.environment_insufficient_ratio,
        "max_stress_loss_pct": signals.max_stress_loss_pct,
        "stress_unquantifiable": signals.stress_unquantifiable,
        "coverage_blocked": signals.coverage_blocked,
    }


def _assessment_payload(assessment: StabilityAssessment) -> dict[str, str]:
    """Serialize one assessment as dimension / verdict / detail."""
    return {
        "dimension": assessment.dimension.value,
        "verdict": assessment.verdict.value,
        "detail": assessment.detail,
    }


def stability_rule_config_json(config: StabilityRuleConfig) -> str:
    """Return a stable, key-sorted JSON serialization of a stability rule."""
    return json.dumps(
        _config_payload(config), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def stability_rule_config_fingerprint(config: StabilityRuleConfig) -> str:
    """Return the stable SHA-256 fingerprint of a stability rule (SP 3.58)."""
    return hashlib.sha256(stability_rule_config_json(config).encode("utf-8")).hexdigest()


def stability_json(conclusion: StabilityConclusion) -> str:
    """Return a stable, key-sorted JSON serialization of a stability conclusion.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style);
    the rule and the signals are embedded so the conclusion is fully auditable.
    """
    payload: dict[str, object] = {
        "conclusion": conclusion.conclusion.value,
        "rule": _config_payload(conclusion.rule),
        "signals": _signals_payload(conclusion.signals),
        "assessments": [_assessment_payload(assessment) for assessment in conclusion.assessments],
        "reasons": list(conclusion.reasons),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stability_fingerprint(conclusion: StabilityConclusion) -> str:
    """Return the stable SHA-256 fingerprint of a stability conclusion (SP 3.58)."""
    return hashlib.sha256(stability_json(conclusion).encode("utf-8")).hexdigest()


__all__: tuple[str, ...] = (
    "StabilityRuleError",
    "StabilityDimension",
    "StabilityVerdict",
    "StabilitySignals",
    "StabilityRuleConfig",
    "StabilityAssessment",
    "StabilityConclusion",
    "default_stability_rule",
    "build_stability_rule",
    "adjudicate_stability",
    "stability_rule_config_json",
    "stability_rule_config_fingerprint",
    "stability_json",
    "stability_fingerprint",
)
