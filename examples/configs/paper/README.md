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

> **前置**：先激活虚拟环境 `source .venv/bin/activate`，否则会报 `harbor-cli: command not found`。
> 下文 `<run-id>`、`<order-id>`、`<approver>` 均为**占位符**，请替换为真实值，**不要连同尖括号一起复制**。
>
> **`--dataset-fingerprint` 怎么填**：它是本次运行的可重放标识（SP 4.9），**最长 64 字符、无格式校验**。
> 优先填 MVP 3 冻结数据集清单的指纹（SP 3.7，64 位十六进制），可从验证运行查到：
> `SELECT fingerprint FROM validation_manifests WHERE validation_run_id = '<validation-run-id>';`
> 仅做冒烟/演示时，可用任意稳定短标识，例如 `hk-paper-demo-2026`。
>
> `init` 会返回 `run_id`；**建议存进 shell 变量**（下方 `RUN_ID=...`），避免手工复制出错。
> `paper list` 子命令尚未提供，run_id 丢失时可查库：
> `docker compose exec -T postgres psql -U harbor -d harbor -c "SELECT run_id, status, created_at FROM paper_runs ORDER BY created_at DESC;"`

```bash
# 1) 初始化模拟盘运行，并把 run_id 存进变量（DRAFT 状态）
RUN_ID=$(harbor-cli paper init --config examples/configs/paper/hk_paper.yaml \
  --dataset-fingerprint hk-paper-demo-2026 \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')
echo "$RUN_ID"   # 例：a4565661cbad4f468185716f5c9900e9

# 2) 审批并激活（DRAFT -> APPROVED -> ACTIVE，记录审批）
harbor-cli paper start "$RUN_ID" --approver jjxiong

# 3) 从目标权重派生并持久化订单（SP 4.85）
harbor-cli paper signal "$RUN_ID" --rebalance-date 2026-01-02 \
  --target 0001.HK:0.5 --price 0001.HK:50.0

# 4) 订单列表 / 单笔订单（order_id 同样存变量）
harbor-cli paper order list "$RUN_ID"
ORDER_ID=$(harbor-cli paper order list "$RUN_ID" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["order_id"])')
harbor-cli paper order show "$RUN_ID" "$ORDER_ID"

# 5) 审批/拒绝订单（记录可审计审批，SP 4.86）
harbor-cli paper approve "$RUN_ID" --order-id "$ORDER_ID" --approver jjxiong
harbor-cli paper reject "$RUN_ID" --order-id "$ORDER_ID" --approver jjxiong

# 6) 对账（SP 4.87）：重建账户并与净值快照比对，差异落表不静默修正
harbor-cli paper reconcile "$RUN_ID" --as-of 2026-01-02

# 7) 报告（JSON/CSV/HTML）
harbor-cli paper report "$RUN_ID" --format json
harbor-cli paper report "$RUN_ID" --format csv
harbor-cli paper report "$RUN_ID" --format html
```

详细说明见 [`docs/paper_examples.md`](../../docs/paper_examples.md)。
