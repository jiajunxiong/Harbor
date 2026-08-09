"""Pydantic configuration model for out-of-sample validation (MVP 3 / SP 3.2).

The model captures the train / validation / test boundaries (SP 3.4), the
rolling-window mode and retrain frequency (SP 3.31), the parameter-search
budget and selection metric (SP 3.15-3.17), the minimum data-coverage
thresholds (SP 3.10), the pre-registered stress scenarios (SP 3.51-3.57) and
the conclusion rules (SP 3.58). It is frozen so a validated configuration is
immutable, matching the replayable-domain philosophy of SP 3.1.
:meth:`ValidationConfig.canonical_json` produces a stable, key-sorted
serialization that SP 3.3 hashes to identify a frozen split.

The core layer never imports database or CLI code; this module depends only on
the backtest and validation-domain types and Pydantic.
"""

import json
from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from harbor.core.backtest_domain import Currency, Market
from harbor.core.validation_domain import EvaluationSplit


class RollingWindowMode(StrEnum):
    """How the rolling training window grows over folds (SP 3.31).

    ``EXPANDING`` uses all history up to each fold's boundary; ``FIXED`` keeps
    a constant ``train_length_days``.
    """

    EXPANDING = "expanding"
    FIXED = "fixed"


class RetrainFrequency(StrEnum):
    """How often a fold re-fits and re-selects parameters (SP 3.33)."""

    EVERY_FOLD = "every_fold"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"


class MetricDirection(StrEnum):
    """Whether the primary metric is maximized or minimized (SP 3.21)."""

    HIGHER_BETTER = "higher_better"
    LOWER_BETTER = "lower_better"


class CoverageSeverity(StrEnum):
    """How a missing coverage item is treated (SP 3.10)."""

    ERROR = "error"
    WARNING = "warning"
    NOT_QUALIFIED = "not_qualified"


class SplitConfig(BaseModel):
    """The frozen train / validation / test boundaries (SP 3.4).

    The strict, non-overlapping ordering ``train_end < validation_start <=
    validation_end < test_start`` is enforced by building an
    :class:`EvaluationSplit` (SP 3.1), which rejects reversed, overlapping,
    touching or empty ranges with :class:`SplitBoundaryError`.
    """

    model_config = ConfigDict(frozen=True)

    train_start: date
    train_end: date
    validation_start: date
    validation_end: date
    test_start: date
    test_end: date

    @model_validator(mode="after")
    def _validate_boundaries(self) -> "SplitConfig":
        self.to_evaluation_split()
        return self

    def to_evaluation_split(self) -> EvaluationSplit:
        """Return the equivalent immutable split value (SP 3.1)."""
        return EvaluationSplit(
            train_start=self.train_start,
            train_end=self.train_end,
            validation_start=self.validation_start,
            validation_end=self.validation_end,
            test_start=self.test_start,
            test_end=self.test_end,
        )


class RollingWindowConfig(BaseModel):
    """Rolling-window and retrain rules for walk-forward folds (SP 3.31)."""

    model_config = ConfigDict(frozen=True)

    mode: RollingWindowMode = RollingWindowMode.EXPANDING
    train_length_days: int | None = Field(
        default=None, gt=0, description="固定窗口训练长度（天，仅 FIXED）"
    )
    step_days: int = Field(default=252, gt=0, description="每折向前推进的天数")
    retrain_frequency: RetrainFrequency = RetrainFrequency.EVERY_FOLD

    @model_validator(mode="after")
    def _validate_window(self) -> "RollingWindowConfig":
        if self.mode is RollingWindowMode.FIXED and self.train_length_days is None:
            raise ValueError("A fixed rolling window requires train_length_days.")
        if self.mode is RollingWindowMode.EXPANDING and self.train_length_days is not None:
            raise ValueError("An expanding rolling window cannot carry train_length_days.")
        return self


class TuningConfig(BaseModel):
    """Parameter-search budget and pre-registered selection metric (SP 3.15-3.17)."""

    model_config = ConfigDict(frozen=True)

    primary_metric: str = Field(default="sharpe", description="预注册主指标名称")
    metric_direction: MetricDirection = MetricDirection.HIGHER_BETTER
    max_trials: int = Field(default=100, gt=0, description="最大试验数")
    random_seed: int = Field(default=42, description="确定性随机种子")
    min_validation_days: int = Field(default=63, gt=0, description="验证期最低样本天数")
    early_stop_trials: int | None = Field(default=None, gt=0, description="提前停止所需连续试验数")

    @model_validator(mode="after")
    def _validate_metric(self) -> "TuningConfig":
        if not self.primary_metric.strip():
            raise ValueError("primary_metric must be non-empty.")
        return self


class CoverageThresholdConfig(BaseModel):
    """Minimum data-coverage thresholds (SP 3.10).

    ``fx_required``, ``historical_stock_pool_required`` and
    ``action_terms_required`` declare that a missing FX rate, an unknown
    historical stock pool or missing corporate-action terms blocks (or
    disqualifies) the conclusion instead of being silently assumed.
    """

    model_config = ConfigDict(frozen=True)

    min_price_coverage_pct: float = Field(default=95.0, ge=0, le=100)
    min_stock_pool_coverage_pct: float = Field(default=90.0, ge=0, le=100)
    min_fundamental_coverage_pct: float = Field(default=70.0, ge=0, le=100)
    fx_required: bool = Field(default=True, description="缺失 FX 时阻断结论")
    historical_stock_pool_required: bool = Field(default=True, description="历史成分未知时阻断结论")
    action_terms_required: bool = Field(default=True, description="企业行动条款缺失时阻断结论")


class StressScenario(BaseModel):
    """One pre-registered stress scenario (SP 3.51-3.57).

    Every scenario records its assumptions so it can be replayed against the
    same baseline; only registered scenarios may influence a conclusion
    (SP 3.59).
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="压力情景名称")
    cost_multiplier: float = Field(default=2.0, ge=1.0, description="成本倍数")
    slippage_bps: float = Field(default=50.0, ge=0, description="压力滑点（基点）")
    participation_rate: float = Field(default=0.05, ge=0, le=1, description="压力成交参与率")
    fx_shift_bps: float = Field(default=0.0, description="FX 冲击（基点，可负）")

    @model_validator(mode="after")
    def _validate_name(self) -> "StressScenario":
        if not self.name.strip():
            raise ValueError("Stress scenario name must be non-empty.")
        return self


class ConclusionRulesConfig(BaseModel):
    """Rules that derive the OOS conclusion (SP 3.58).

    The conclusion is a pre-registered rule applied to fold dispersion,
    drawdown and stress loss; a covered-but-unstable strategy cannot claim
    ``QUALIFIED``.
    """

    model_config = ConfigDict(frozen=True)

    min_qualified_fold_ratio: float = Field(default=0.8, ge=0, le=1)
    max_allowed_drawdown_pct: float = Field(default=30.0, gt=0)
    max_allowed_stress_drawdown_pct: float = Field(default=40.0, gt=0)
    max_parameter_dispersion: float = Field(default=0.5, ge=0)


class ValidationConfig(BaseModel):
    """Validated, immutable configuration for one validation run (SP 3.2)."""

    model_config = ConfigDict(frozen=True)

    strategy: str = "shareholder-return"
    strategy_version: str = "1.0.0"
    description: str = ""

    markets: tuple[Market, ...]
    base_currency: Currency
    data_cutoff: date | None = None
    code_version: str = "1.0.0"

    split: SplitConfig
    rolling: RollingWindowConfig = Field(default_factory=RollingWindowConfig)
    tuning: TuningConfig = Field(default_factory=TuningConfig)
    coverage: CoverageThresholdConfig = Field(default_factory=CoverageThresholdConfig)
    stress: tuple[StressScenario, ...] = ()
    conclusion: ConclusionRulesConfig = Field(default_factory=ConclusionRulesConfig)

    @model_validator(mode="after")
    def _validate_markets(self) -> "ValidationConfig":
        if not self.markets:
            raise ValueError("At least one market must be configured.")
        if len(set(self.markets)) != len(self.markets):
            raise ValueError("Markets must not contain duplicates.")
        return self

    @model_validator(mode="after")
    def _validate_stress_names(self) -> "ValidationConfig":
        names = [scenario.name for scenario in self.stress]
        if len(set(names)) != len(names):
            raise ValueError("Stress scenario names must be unique.")
        return self

    @model_validator(mode="after")
    def _validate_split_within_cutoff(self) -> "ValidationConfig":
        if self.data_cutoff is not None and self.split.test_end > self.data_cutoff:
            raise ValueError("The test interval must end on or before data_cutoff.")
        return self

    def canonical_json(self) -> str:
        """Return a stable, key-sorted JSON representation for hashing (SP 3.3).

        The output is deterministic for equal configurations regardless of
        declaration order; SP 3.3 hashes it to identify a frozen split.
        """
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
