# 模拟盘示例配置 (Example Paper Configurations, MVP 4 / SP 4.88)

本目录提供保守的港股、美股与跨市场模拟盘示例配置，仅用于 Harbor 模拟盘闭环（MVP 4）
的演示、验证与教学。

**研究性质与边界**：模拟盘只产生本地订单与对账记录，不创建券商凭据、不下真实订单，
报告不含收益或回撤承诺（SP 4.83 / 4.95 发布前边界复核）。任何结果不构成投资建议，
也不表示未来收益或回撤。

## 示例清单

| 文件 | 市场 | 基准币种 | 账本币种 | 说明 |
| :--- | :--- | :--- | :--- | :--- |
| [`hk_paper.yaml`](hk_paper.yaml) | 港股 | HKD | HKD | 单市场，港股手数/板位规则（SP 4.17） |
| [`us_paper.yaml`](us_paper.yaml) | 美股 | USD | USD | 单市场，美股整股/小数股规则（SP 4.18） |
| [`cross_market_paper.yaml`](cross_market_paper.yaml) | 港股+美股 | HKD | HKD+USD | 跨市场，多币种分账（SP 4.4），禁止隐式 1:1 换汇 |

## 保守假设（请在正式研究前逐条核对）

- **准入**：进入模拟盘前，策略必须已通过 MVP 3 `QUALIFIED` 且版本化（SP 4.3）；
  `INCONCLUSIVE`/`NOT_QUALIFIED` 被明确拒绝。
- **风控**：风险参数使用 SP 4.31 预注册默认值——单笔 ≤ 0.5%、单股 ≤ 5%、
  单行业 ≤ 20%，回撤 5% 预警 / 8% 防御 / 10% 熔断（SP 4.34–4.36）。
- **停止条件**：`stop.max_days` 与 `stop.max_drawdown_pct` 明确停止条件；回撤达 10%
  进入 `CIRCUIT_BROKEN` 冻结新订单（SP 4.36 / 4.40）。
- **多币种账本**：跨市场示例的 HKD/USD 分账禁止隐式 1:1 换汇；FX 缺失时拒绝计算
  跨市场净值（SP 2.12）。
- **人工审批**：示例均使用 `MANUAL` 运行模式；所有高风险订单/策略经人工审批后
  才进入执行（SP 4.39 / 4.86）。

## 运行（CLI，SP 4.84–4.87）

```bash
# 1) 初始化模拟盘运行（返回 run_id 与 DRAFT 状态）
harbor-cli paper init --config examples/configs/paper/hk_paper.yaml \
  --dataset-fingerprint <dataset-fingerprint>

# 2) 审批并激活（DRAFT -> APPROVED -> ACTIVE，记录审批）
harbor-cli paper start <run-id> --approver <approver>

# 3) 从目标权重派生并持久化订单（SP 4.85）
harbor-cli paper signal <run-id> --rebalance-date 2026-01-02 \
  --target 0001.HK:0.5 --price 0001.HK:50.0

# 4) 订单列表 / 单笔订单
harbor-cli paper order list <run-id>
harbor-cli paper order show <run-id> <order-id>

# 5) 审批/拒绝订单（记录可审计审批，SP 4.86）
harbor-cli paper approve <run-id> --order-id <order-id> --approver <approver>
harbor-cli paper reject <run-id> --order-id <order-id> --approver <approver>

# 6) 对账（SP 4.87）：重建账户并与净值快照比对，差异落表不静默修正
harbor-cli paper reconcile <run-id> --as-of 2026-01-02

# 7) 报告（JSON/CSV/HTML）
harbor-cli paper report <run-id> --format json
harbor-cli paper report <run-id> --format csv
harbor-cli paper report <run-id> --format html
```

详细说明见 [`docs/paper_examples.md`](../../docs/paper_examples.md)。
