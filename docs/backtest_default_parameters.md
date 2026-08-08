# 回测默认参数说明 (Backtest Default Parameter Documentation)

> **适用版本**：Harbor MVP 2 · SP 2.73 · 2026-08-08
> **依赖**：SP 2.4（配置模型）

本文档说明 Harbor 研究回测引擎的**默认参数**及其在数据缺失时的**默认处理方式**。
**这些默认值只是研究假设，不是交易所、券商或监管机构公布的市场事实**
（不是市场事实 / research assumptions, not market facts）。在用于可信研究前，
请对照真实的市场规则、券商费率与官方日历进行核对，并在策略配置中显式覆盖。

所有参数都通过 `harbor-cli backtest run --config <path>` 读取的版本化策略配置
（SP 2.5）提供；未在配置中指定的字段一律采用本文档所述的默认值。每个默认值都
是**可重放**的：同配置始终产生同一研究运行（SP 2.48），且全部纳入运行哈希
（SP 2.5）。

---

## 1. 成本 (Costs) — `cost`（SP 2.4 / 2.37 / 2.38）

| 字段 | 默认值 | 含义 |
| :--- | :--- | :--- |
| `commission_rate` | `0.0005` | 佣金费率（小数，0.05%） |
| `min_commission` | `0.0` | 平台最低佣金（0 表示不设最低） |
| `stamp_duty_rate` | `0.001` | 印花税率（港股） |
| `transaction_levy_rate` | `0.000027` | 交易征费率（港股） |
| `trading_fee_rate` | `0.0000565` | 交易费率（港股） |
| `regulatory_fee_rate` | `0.0000278` | 监管费率（美股，仅按卖出方向计） |
| `slippage_bps` | `0.0` | 滑点（基点） |
| `lot_size` | `100` | 最小交易手数（港股整手；美股碎股应设为 `1`） |

- **港股成本模型**（SP 2.37）：佣金 = max(成交额×费率, 最低佣金)，并加印花税、
  交易征费与交易费；买卖方向费用相同；手数向下取整到整手（`round_to_lot`）。
- **美股成本模型**（SP 2.38）：佣金 + 监管费（**仅卖出**，SEC 规则）+ 滑点成本；
  支持碎股（`lot_size=1`）。
- 上述费率**不是市场事实**：真实佣金、印花税与平台最低收费会随券商、市场与时间
  变化，请在配置中显式覆盖。

## 2. 滑点 (Slippage) — `cost.slippage_bps`（SP 2.38 / 2.39）

| 字段 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `slippage_bps` | `0.0` | 默认无滑点；仅美股成交模型（SP 2.38）按成交方向计入 |

- 默认 `0.0` 表示**不模拟滑点**（研究上偏乐观）。
- 当设置为非零时：买入按 `价格×(1+bps/10000)` 提高成交价，卖出按
  `价格×(1−bps/10000)` 降低成交价（滑点朝成交方向移动）；滑点成本计入美股
  `total_cost`，**不会重复计入**港股费用（SP 2.37 不应用滑点）。
- 成交时点默认 `fill_rule: close`（当日收盘价成交，SP 2.39）。

## 3. 流动性 (Liquidity) — `volume` 与候选过滤（SP 2.40 / 2.23）

| 字段 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `volume.participation_rate` | `0.1` | 单笔订单最多消耗当日成交额（价×量）的 10% |
| `volume.on_unfilled` | `cancel` | 未成交订单默认**取消**（另一选项 `defer` 顺延至下一交易日） |
| `candidate.min_history_observations` | `60` | 候选标的至少需要 60 个历史价格观测 |
| `candidate.min_average_turnover` | `0.0` | 日均成交额下限（默认不设下限） |
| `candidate.max_suspension_ratio` | `0.3` | 停牌比例上限（超过 30% 剔除） |

- 成交额参与率默认 10%，是**保守研究假设**；真实流动性约束应结合目标组合规模
  与个股成交额核对。
- 流动性不足 / 历史不足 / 停牌过久的标的会被候选过滤剔除，并记录**排除原因**
  （SP 2.23），绝不静默修复。

## 4. 汇率 (FX) — 跨币种换算（SP 2.12 / 2.27 / 2.45）

| 情形 | 默认处理 |
| :--- | :--- |
| 报价币种 == 基准币种 | 汇率恒为 `1.0`，不查询 FX 数据 |
| 报价币种 ≠ 基准币种且当日有 FX | 使用**最近可得**的日度汇率（SP 2.8 / 2.12） |
| 报价币种 ≠ 基准币种且 FX 缺失 | **拒绝**生成跨市场净值 / 成交换算（SP 2.45），**绝不默认 1:1** |
| 跨市场组合缺 FX | 明确**禁止**跨市场组合（SP 2.27） |

- **“缺少 FX 时拒绝而非假设 1:1”是硬约束**（MVP 2 验收标准）；跨市场回测前
  必须采集逐日 HKD↔USD 等汇率。

## 5. 因子缺失 (Missing Factor Data) — 因子管线（SP 2.16–2.24）

| 场景 | 默认处理 |
| :--- | :--- |
| 某因子输入缺失 / 数据不足 | 因子值为 `None`，并给出可读 `missing_reason`，**绝不填 0** |
| 窗口不足最小观测数 | `min_observations=60`（`WindowConfig.lookback_days=252`）；不足则返回缺失 |
| 标准化时因子缺失 | 保持 `None`，不参与标准化，也不参与排名（SP 2.22） |
| 合成评分时部分因子缺失 | 默认 `missing_policy: renormalize`（对可用因子权重归一） |
| 覆盖率不足 | `min_available_weight=0.0`（默认不强制最低覆盖率） |

- 缺失即“未知”，与“数值为 0”严格区分：引擎**不虚构**缺失数据，研究结果如实
  反映数据缺口（SP 2.20 停牌比例、SP 2.21 披露可用日、SP 2.9 点时可用性）。

## 6. 风控 / 成交 / 停牌 / 分红 / 基准默认值

| 配置块 | 字段 | 默认值 |
| :--- | :--- | :--- |
| `risk` (SP 2.35) | `max_position_pct` / `max_market_pct` / `min_cash_pct` | `0.2` / `1.0` / `0.0` |
| `fill` (SP 2.39) | `fill_rule` | `close`（当日收盘成交） |
| `suspension` (SP 2.41) | `valuation` / `warn` | `last_price`（沿用最后可得收盘价）/ `true`（产生告警） |
| `dividend` (SP 2.43) | `include_special` | `true`（特别股息计入账本） |
| `benchmark` (SP 2.52) | `kind` | `cash`（现金基准） |

- `BacktestConfig` 顶层默认：`strategy="shareholder-return"`、`strategy_version="1.0.0"`、
  `rebalance_frequency="quarterly"`（季度调仓）、`initial_capital=1_000_000.0`。

## 7. 如何覆盖默认值

在策略配置（YAML/JSON）中显式给出对应字段即可覆盖，例如：

```yaml
cost:
  commission_rate: 0.001
  min_commission: 15
  slippage_bps: 10
  lot_size: 100
volume:
  participation_rate: 0.2
  on_unfilled: defer
```

覆盖后的配置同样经过 SP 2.4 校验并生成新的运行哈希（SP 2.5）——默认值是否被覆盖
是**可重放、可审计**的。示例见 `examples/configs/`（SP 2.72）。

---

> 本说明中标注“不是市场事实”的费率、比例、日历与停牌估值规则，仅用于研究假设；
> 使用前请以权威来源核对并显式覆盖，研究结果不构成投资建议。
