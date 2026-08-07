"""Pydantic configuration model for a backtest run (MVP 2 / SP 2.4).

The model captures the strategy version, markets and quotas, date range, base
currency, rebalance frequency, initial capital, cost-model parameters and risk
constraints. It is frozen so a validated configuration is immutable, matching
the replayable-domain philosophy of SP 2.2. :meth:`BacktestConfig.canonical_json`
produces a stable, key-sorted serialization that SP 2.5 hashes to identify
idempotent runs.

The core layer never imports database or CLI code; this module depends only on
the backtest domain types and Pydantic.
"""

import json
from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from harbor.core.backtest_domain import Currency, Market

_WEIGHT_TOLERANCE = 1e-6


class RebalanceFrequency(StrEnum):
    """How often the portfolio is rebalanced (see SP 2.33)."""

    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"


class FillRule(StrEnum):
    """When an order is filled relative to the quote bar (SP 2.39).

    ``OPEN`` fills at the same-day open, ``CLOSE`` at the same-day close and
    ``NEXT_OPEN`` at the next trading day's open (e.g. for decisions made after
    the close). The rule is fixed in the configuration so a run is replayable.
    """

    OPEN = "open"
    CLOSE = "close"
    NEXT_OPEN = "next_open"


class UnfilledPolicy(StrEnum):
    """How an order that cannot fully fill is handled (SP 2.40).

    ``CANCEL`` drops the unfilled portion; ``DEFER`` carries it to the next
    trading day. The policy is fixed in the configuration so a run is
    replayable.
    """

    CANCEL = "cancel"
    DEFER = "defer"


class SuspensionValuation(StrEnum):
    """How a suspended position is valued (SP 2.41).

    ``LAST_PRICE`` carries the last available close forward while a symbol has
    no quote for the day. The rule is fixed in the configuration so a run is
    replayable.
    """

    LAST_PRICE = "last_price"


class MarketQuota(BaseModel):
    """Per-market participation: target holdings count and portfolio weight.

    Weight is expressed against the benchmark portfolio (SP 2.27); the weights
    across all quotas must sum to 1.0.
    """

    model_config = ConfigDict(frozen=True)

    market: Market
    target_count: int = Field(gt=0, description="目标持仓数量")
    weight: float = Field(gt=0, le=1, description="该市场在基准组合中的权重")


class CostConfig(BaseModel):
    """Trade cost parameters (implemented per market in SP 2.37 / 2.38).

    Rates are fractional (e.g. ``0.001`` is 0.1%). ``slippage_bps`` is in basis
    points. ``lot_size`` captures the HK board lot; US fractional shares set it
    to 1.
    """

    model_config = ConfigDict(frozen=True)

    commission_rate: float = Field(default=0.0005, ge=0, description="佣金费率")
    min_commission: float = Field(default=0.0, ge=0, description="平台最低佣金")
    stamp_duty_rate: float = Field(default=0.001, ge=0, description="印花税（HK）")
    transaction_levy_rate: float = Field(default=0.000027, ge=0, description="交易征费（HK）")
    trading_fee_rate: float = Field(default=0.0000565, ge=0, description="交易费（HK）")
    regulatory_fee_rate: float = Field(default=0.0000278, ge=0, description="监管费（US）")
    slippage_bps: float = Field(default=0.0, ge=0, description="滑点（基点）")
    lot_size: int = Field(default=100, gt=0, description="最小交易手数")


class RiskConfig(BaseModel):
    """Concentration and cash constraints (implemented in SP 2.35)."""

    model_config = ConfigDict(frozen=True)

    max_position_pct: float = Field(default=0.2, gt=0, le=1, description="单股上限")
    max_market_pct: float = Field(default=1.0, gt=0, le=1, description="单市场上限")
    min_cash_pct: float = Field(default=0.0, ge=0, lt=1, description="最小现金比例")


class FillConfig(BaseModel):
    """Execution timing for fills (SP 2.39).

    ``fill_rule`` picks which price bar fills orders: the same-day open, the
    same-day close, or the next trading day's open. The rule is part of the
    validated configuration (and therefore of the run hash, SP 2.5) so the
    execution timing of a run is fixed and replayable.
    """

    model_config = ConfigDict(frozen=True)

    fill_rule: FillRule = Field(default=FillRule.CLOSE, description="成交时点规则")


class VolumeConfig(BaseModel):
    """Volume-participation constraint on fills (SP 2.40).

    ``participation_rate`` caps the value an order may consume as a fraction of
    the day's traded value (price x volume); ``on_unfilled`` decides whether an
    order that cannot fully fill is cancelled or deferred to the next trading
    day. The rule is part of the validated configuration (and therefore of the
    run hash, SP 2.5) so execution is fixed and replayable.
    """

    model_config = ConfigDict(frozen=True)

    participation_rate: float = Field(default=0.1, ge=0, le=1, description="成交额参与率")
    on_unfilled: UnfilledPolicy = Field(default=UnfilledPolicy.CANCEL, description="未成交处理")


class SuspensionConfig(BaseModel):
    """Suspension / untradeable handling (SP 2.41).

    ``valuation`` is the explicit rule for valuing a position whose symbol has
    no quote for the day: the last available close is carried forward
    (``LAST_PRICE``). ``warn`` controls whether a carry-forward valuation
    produces a warning. The rule is part of the validated configuration (and
    therefore of the run hash, SP 2.5) so suspension handling is replayable.
    """

    model_config = ConfigDict(frozen=True)

    valuation: SuspensionValuation = Field(
        default=SuspensionValuation.LAST_PRICE, description="停牌估值规则"
    )
    warn: bool = Field(default=True, description="停牌估值是否产生告警")


class DividendConfig(BaseModel):
    """Dividend processing (SP 2.43).

    ``include_special`` controls whether special dividends (``is_special``)
    are paid into the ledger; special dividends follow the strategy
    configuration (特别股息遵循策略配置). The rule is part of the validated
    configuration (and therefore of the run hash, SP 2.5) so dividend handling
    is replayable.
    """

    model_config = ConfigDict(frozen=True)

    include_special: bool = Field(default=True, description="特别股息是否计入账本")


class BenchmarkKind(StrEnum):
    """The kind of benchmark a run is compared against (SP 2.52)."""

    CASH = "cash"
    MARKET_INDEX = "market_index"
    BLENDED = "blended"


class BenchmarkComponent(BaseModel):
    """One market-index leg of a blended benchmark (SP 2.52).

    ``weight`` is the component's share of the blended benchmark; the weights
    across all components plus the blended cash weight must sum to 1.0.
    """

    model_config = ConfigDict(frozen=True)

    market: Market
    symbol: str
    weight: float = Field(gt=0, le=1, description="该分项在混合基准中的权重")


class BenchmarkConfig(BaseModel):
    """Benchmark definition (SP 2.52): cash, a single market index, or a blend.

    ``kind`` selects the benchmark; ``market``/``symbol`` identify the index
    for :attr:`BenchmarkKind.MARKET_INDEX`; ``components`` (plus the optional
    ``cash_weight``) define a blended benchmark. The definition is frozen and
    part of the run hash (SP 2.5), so a run is compared against a fixed,
    replayable benchmark.
    """

    model_config = ConfigDict(frozen=True)

    kind: BenchmarkKind = BenchmarkKind.CASH
    market: Market | None = None
    symbol: str = ""
    cash_weight: float = Field(default=0.0, ge=0, lt=1, description="混合基准中的现金权重")
    components: tuple[BenchmarkComponent, ...] = ()

    @model_validator(mode="after")
    def _validate_kind(self) -> "BenchmarkConfig":
        if self.kind is BenchmarkKind.MARKET_INDEX:
            if self.market is None or not self.symbol:
                raise ValueError("A market-index benchmark requires a market and symbol.")
            if self.components or self.cash_weight != 0.0:
                raise ValueError("A market-index benchmark cannot carry components or cash weight.")
        elif self.kind is BenchmarkKind.CASH:
            if self.market is not None or self.symbol or self.components or self.cash_weight != 0.0:
                raise ValueError("A cash benchmark cannot carry index or component data.")
        else:  # BLENDED
            if self.market is not None or self.symbol:
                raise ValueError("A blended benchmark cannot carry a single market/symbol.")
            if not self.components:
                raise ValueError("A blended benchmark requires at least one component.")
            total = self.cash_weight + sum(component.weight for component in self.components)
            if abs(total - 1.0) > _WEIGHT_TOLERANCE:
                raise ValueError("Blended benchmark weights (cash + components) must sum to 1.0.")
        return self


class BacktestConfig(BaseModel):
    """Validated, immutable configuration for one backtest run."""

    model_config = ConfigDict(frozen=True)

    strategy: str = "shareholder-return"
    strategy_version: str = "1.0.0"
    description: str = ""

    markets: tuple[Market, ...]
    market_quotas: tuple[MarketQuota, ...]

    start_date: date
    end_date: date
    base_currency: Currency
    rebalance_frequency: RebalanceFrequency = RebalanceFrequency.QUARTERLY

    initial_capital: float = Field(default=1_000_000.0, gt=0)
    cost: CostConfig = Field(default_factory=CostConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    fill: FillConfig = Field(default_factory=FillConfig)
    volume: VolumeConfig = Field(default_factory=VolumeConfig)
    suspension: SuspensionConfig = Field(default_factory=SuspensionConfig)
    dividend: DividendConfig = Field(default_factory=DividendConfig)
    benchmark: BenchmarkConfig = Field(default_factory=BenchmarkConfig)

    @model_validator(mode="after")
    def _validate_date_range(self) -> "BacktestConfig":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date.")
        return self

    @model_validator(mode="after")
    def _validate_markets_and_quotas(self) -> "BacktestConfig":
        if not self.markets:
            raise ValueError("At least one market must be configured.")
        if not self.market_quotas:
            raise ValueError("At least one market quota must be configured.")
        if len(set(self.markets)) != len(self.markets):
            raise ValueError("Markets must not contain duplicates.")
        quota_markets = {quota.market for quota in self.market_quotas}
        if quota_markets != set(self.markets):
            raise ValueError("Market quotas must cover exactly the configured markets.")
        if len(quota_markets) != len(self.market_quotas):
            raise ValueError("Each market may have at most one quota.")
        total_weight = sum(quota.weight for quota in self.market_quotas)
        if abs(total_weight - 1.0) > _WEIGHT_TOLERANCE:
            raise ValueError("Market quota weights must sum to 1.0.")
        return self

    def canonical_json(self) -> str:
        """Return a stable, key-sorted JSON representation for hashing (SP 2.5).

        The output is deterministic for equal configurations regardless of
        declaration order, which SP 2.5 hashes to identify a research run.
        """
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
