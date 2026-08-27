# 样本外验证示例配置 (Example Validation Configurations, MVP 3 / SP 3.72)

本目录提供 HK、US 与跨市场（HK+US）的小规模 **Mock 验证配置**，显式演示样本外
验证的四个维度：**冻结切分**、**试验预算**、**覆盖门槛** 与 **压力情景**。
所有示例仅用于 Harbor 样本外验证管线（MVP 3）的演示、验证与教学，**不构成投资
建议，也不表示任何未来收益或回撤承诺**；配置中的切分、预算、覆盖率阈值与压力
情景均为研究假设，不是市场事实。

| 文件 | 市场 | 基准币种 | 演示重点 |
| :--- | :--- | :--- | :--- |
| `hk_validation.yaml` | HK | HKD | 单一市场：成本 + 流动性压力情景 |
| `us_validation.yaml` | US | USD | 单一市场；`us_validation.json` 为其 JSON 孪生 |
| `us_validation.json` | US | USD | 与 YAML 配置哈希一致（SP 3.3 跨格式确定性） |
| `cross_market_validation.yaml` | HK + US | HKD | 跨市场：新增 FX 压力情景（`fx-stress-usd`） |

## 四个演示维度（SP 3.72）

1. **冻结切分 (frozen split, SP 3.3 / 3.4)**：`split` 显式给出 `train / validation /
   test` 六条边界，严格满足 `train_end < validation_start <= validation_end <
   test_start`，且 `test_end <= data_cutoff`。每个配置在加载时即被冻结校验，其
   `config_hash`（SP 3.3，SHA-256 over 规范化 JSON）固化该冻结切分——相同配置、
   相同格式（YAML/JSON）恒得到相同哈希。
2. **试验预算 (trial budget, SP 3.15–3.17 / 3.30)**：`tuning.max_trials` 为参数
   搜索预算上限、`random_seed` 保证确定性、`primary_metric` 为预注册主指标
   （`sharpe`）。基线参数与基线指标在参数搜索**前**固定（SP 3.30 预注册基线）：
   例如 `cash_weight=0.05`、基线指标 `sharpe=0.10`——搜索后不得回改。
3. **覆盖门槛 (coverage thresholds, SP 3.10)**：`coverage` 显式给出最低价格 /
   股票池 / 财报覆盖率（95 / 90 / 70），且 `fx_required`、`historical_stock_pool_required`、
   `action_terms_required` 全部开启——缺失 FX、未知历史股票池、缺失企业行动条款
   不会静默通过，而是阻断或降级结论。
4. **压力情景 (stress scenarios, SP 3.51–3.59)**：`stress` 显式预注册压力情景
   （`cost-stress-2x` 成本×2、`liquidity-stress-50bps` 滑点 50bps / 参与率 5%、
   跨市场 `fx-stress-usd` FX -300bps）。情景名称按前缀映射到 SP 3.59 类别
   （`cost-`→COST、`liquidity-`→LIQUIDITY、`fx-`→FX、`calendar-`→CALENDAR、
   `corporate-action-`→CORPORATE_ACTION、`stock-pool-`→STOCK_POOL、
   `parameter-neighborhood-`→PARAMETER_NEIGHBORHOOD）。未登记情景不得进入结论
   （SP 3.59 禁止未登记情景进入结论）。

## 使用方式

```bash
# 运行验证（默认创建 DRAFT 并返回验证运行 ID 与状态，SP 3.69）
harbor-cli validation run --config examples/configs/validation/hk_validation.yaml
harbor-cli validation run --config examples/configs/validation/us_validation.yaml
harbor-cli validation run --config examples/configs/validation/cross_market_validation.yaml
```

## 关键假设（研究边界）

- 数据为小规模 Mock：训练 2019–2020、验证 2021、测试 2022（`data_cutoff = 2022-12-31`），
  测试期每年一折（`step_days: 252`）。
- 跨市场组合以 HKD 为基准币种，要求每日 HKD↔USD 汇率（SP 2.12）；缺失 FX 拒绝计算。
- 冻结切分哈希、试验预算、覆盖阈值与压力情景均为示例性研究假设，不是市场事实；
  正式研究前请按 SP 2.73 默认参数说明与 SP 2.74 回测限制说明逐条核对。
