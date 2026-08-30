"""Pydantic configuration model for a paper-trading run (MVP 4 / SP 4.2).

The model captures the strategy identity/version, the market scope, the
rebalance frequency, the initial capital, the multi-currency ledger
(SP 4.4), the pre-registered risk parameters (SP 4.31: 单笔风险 / 单股 / 单行业
and the 5% / 8% / 10% drawdown tiers), the stop conditions and the running
mode (manual / auto). It is frozen so a validated configuration is immutable,
matching the replayable-domain philosophy of SP 4.1. :meth:`PaperConfig.canonical_json`
produces a stable, key-sorted serialization that :mod:`harbor.core.paper_config_loader`
hashes to identify a paper run (SP 4.9 replay identity).

The core layer never imports database or CLI code; this module depends only on
the backtest and paper-domain types and Pydantic.
"""

import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from harbor.core.backtest_domain import Currency, Market
from harbor.core.paper_domain import PaperRunMode


class PaperRebalanceFrequency(StrEnum):
    """How often a paper run rebalances (SP 4.2)."""

    QUARTERLY = "QUARTERLY"
    MONTHLY = "MONTHLY"
    ANNUAL = "ANNUAL"


class PaperRiskConfig(BaseModel):
    """Pre-registered risk parameters of a paper run (SP 4.2 / 4.31).

    ``max_position_pct`` is the per-trade risk (单笔风险, default 0.5%),
    ``max_single_stock_pct`` the single-stock cap (单股, 5%) and
    ``max_industry_pct`` the single-industry cap (单行业, 20%). The three
    drawdown tiers (5% 预警 / 8% 防御 / 10% 熔断, SP 4.34-4.36) must be strictly
    increasing.
    """

    model_config = ConfigDict(frozen=True)

    max_position_pct: float = Field(default=0.005, ge=0, le=1, description="单笔风险上限")
    max_single_stock_pct: float = Field(default=0.05, ge=0, le=1, description="单股上限")
    max_industry_pct: float = Field(default=0.20, ge=0, le=1, description="单行业上限")
    drawdown_warn_pct: float = Field(default=0.05, ge=0, le=1, description="回撤预警阈值")
    drawdown_defend_pct: float = Field(default=0.08, ge=0, le=1, description="回撤防御阈值")
    drawdown_circuit_pct: float = Field(default=0.10, ge=0, le=1, description="回撤熔断阈值")
    daily_loss_limit_pct: float = Field(default=0.05, ge=0, le=1, description="单日损失上限")
    monthly_loss_limit_pct: float = Field(default=0.10, ge=0, le=1, description="月度损失上限")

    @model_validator(mode="after")
    def _validate_drawdown_tiers(self) -> "PaperRiskConfig":
        if not (self.drawdown_warn_pct < self.drawdown_defend_pct < self.drawdown_circuit_pct):
            raise ValueError("Drawdown tiers must be strictly increasing: warn < defend < circuit.")
        return self


class PaperStopConfig(BaseModel):
    """Stop conditions of a paper run (SP 4.2).

    A run stops after ``max_days`` of activity or once its drawdown reaches
    ``max_drawdown_pct``; either may be unset (no limit).
    """

    model_config = ConfigDict(frozen=True)

    max_days: int | None = Field(default=None, gt=0, description="最长运行天数")
    max_drawdown_pct: float | None = Field(default=None, gt=0, description="停止回撤阈值")


class PaperConfig(BaseModel):
    """Validated, immutable configuration for one paper run (SP 4.2)."""

    model_config = ConfigDict(frozen=True)

    strategy: str
    strategy_version: str
    description: str = ""

    markets: tuple[Market, ...]
    base_currency: Currency
    currencies: tuple[Currency, ...]
    initial_capital: float = Field(gt=0, description="初始资金")

    rebalance_frequency: PaperRebalanceFrequency = PaperRebalanceFrequency.QUARTERLY
    run_mode: PaperRunMode = PaperRunMode.MANUAL
    risk: PaperRiskConfig = Field(default_factory=PaperRiskConfig)
    stop: PaperStopConfig = Field(default_factory=PaperStopConfig)

    calendar_version: str | None = Field(default=None, description="权威日历版本")
    fx_source: str | None = Field(default=None, description="FX 数据来源")
    random_seed: int | None = Field(default=None, ge=0, description="确定性随机种子")

    @model_validator(mode="after")
    def _validate_identity(self) -> "PaperConfig":
        if not self.strategy.strip():
            raise ValueError("strategy must be non-empty.")
        if not self.strategy_version.strip():
            raise ValueError("strategy_version must be non-empty.")
        return self

    @model_validator(mode="after")
    def _validate_markets(self) -> "PaperConfig":
        if not self.markets:
            raise ValueError("At least one market must be configured.")
        if len(set(self.markets)) != len(self.markets):
            raise ValueError("Markets must not contain duplicates.")
        return self

    @model_validator(mode="after")
    def _validate_currencies(self) -> "PaperConfig":
        if not self.currencies:
            raise ValueError("At least one ledger currency must be configured.")
        if len(set(self.currencies)) != len(self.currencies):
            raise ValueError("Ledger currencies must not contain duplicates.")
        if self.base_currency not in self.currencies:
            raise ValueError("base_currency must be one of the ledger currencies.")
        return self

    def canonical_json(self) -> str:
        """Return a stable, key-sorted JSON representation for hashing (SP 4.2).

        The output is deterministic for equal configurations regardless of
        declaration order; :mod:`harbor.core.paper_config_loader` hashes it to
        identify a paper run (SP 4.9).
        """
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
