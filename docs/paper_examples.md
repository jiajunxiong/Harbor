# 模拟盘示例与差异验证说明 (Paper Examples and Difference Verification, MVP 4 / SP 4.88)

本说明面向 Harbor 模拟盘闭环（MVP 4）的示例配置（SP 4.88）与差异验证（SP 4.69–4.83），
仅用于研究演示与教学，不构成投资建议，也不表示未来收益或回撤。

## 示例配置

见 `examples/configs/paper/`：

- `hk_paper.yaml`：港股单市场，HKD 基准，港股手数/板位规则（SP 4.17）。
- `us_paper.yaml`：美股单市场，USD 基准，美股整股/小数股规则（SP 4.18）。
- `cross_market_paper.yaml`：港股+美股，HKD 基准、HKD/USD 多币种账本（SP 4.4），
  跨市场订单独立生成与执行、禁止隐式合并（SP 4.24）。

每个示例均采用：

- SP 4.31 预注册风控参数（单笔 ≤ 0.5%、单股 ≤ 5%、单行业 ≤ 20%、回撤 5/8/10%）。
- 明确的停止条件（`stop.max_days` / `stop.max_drawdown_pct`，SP 4.2）。
- `MANUAL` 运行模式：高风险订单/策略经人工审批后才进入执行（SP 4.39 / 4.86）。
- 研究性质声明与停止条件，报告不含收益或回撤承诺（SP 4.83 / 4.95）。

## 差异验证闭环（SP 4.69–4.83）

模拟盘成交与 OOS 研究假设的差异被量化、对照并告警：

1. **研究假设快照（SP 4.69）**：`paper_assumption_snapshot.py` 记录 OOS 假设中的
   滑点、成本、点差、成交量参与率、成交规则与执行延迟，指纹排除来源运行 id（可重放）。
2. **模拟盘实际参数采集（SP 4.70）**：`paper_actual_metrics.py` 采集每笔成交的实际
   成交价、已实现滑点、点差、执行延迟与成交量参与。
3. **差异指标计算（SP 4.71）**：`paper_difference_metrics.py` 按市场、调仓日与标的
   计算点差/滑点/成交价/执行延迟/成本差异；无假设的成交被标注为 `unmatched`，不静默忽略。
4. **差异对照报告（SP 4.72 / 4.82）**：`paper_difference_report.py` 输出可导出的
   JSON/CSV 对照表，字段稳定，单一市场或调仓周期不会藏在均值里。
5. **阈值与告警（SP 4.73 / 4.74）**：`paper_difference_alerts.py` 在差异超过预注册
   阈值时记录告警（回落即恢复）；覆盖不足或数据缺失被标注，不下结论。
6. **监控与周期摘要（SP 4.75 / 4.76）**：`paper_monitoring.py` 生成日度快照
   （净值、回撤、集中度、差异、对账）与日/周/月摘要。
7. **差异验证准入（SP 4.77）**：`paper_admission_registry.py` 记录最少运行 12 个月、
   每启用市场至少 4 次完整调仓和 30 笔成交、覆盖率与未解决对账差异；全部通过或独立
   豁免后才允许进入 MVP 6 单独评审。

## CLI（SP 4.84–4.87）

> **前置**：先激活虚拟环境 `source .venv/bin/activate`，否则会报 `harbor-cli: command not found`。
> `--dataset-fingerprint` 是运行的可重放标识（SP 4.9），最长 64 字符、无格式校验；优先用 MVP 3 冻结
> 数据集清单指纹（SP 3.7），冒烟/演示可用稳定短标识（如 `hk-paper-demo-2026`）。
> **`init` 返回的 `run_id` 请存进 shell 变量**（下方 `RUN_ID=...`），不要手工复制；run_id 丢失时用
> `harbor-cli paper list` 找回（按创建时间倒序；默认 50 条、上限 200 条，`total` 与 `next_offset`
> 会如实说明结果是否被截断）。

```bash
# 初始化并把 run_id 存进变量（避免手工复制出错）
RUN_ID=$(harbor-cli paper init --config examples/configs/paper/hk_paper.yaml \
  --dataset-fingerprint hk-paper-demo-2026 \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')
harbor-cli paper start "$RUN_ID" --approver jjxiong
harbor-cli paper signal "$RUN_ID" --rebalance-date 2026-01-02 --target 0001.HK:0.5 --price 0001.HK:50.0
harbor-cli paper order list "$RUN_ID"
harbor-cli paper approve "$RUN_ID" --order-id <order-id> --approver jjxiong
harbor-cli paper reconcile "$RUN_ID" --as-of 2026-01-02
harbor-cli paper report "$RUN_ID" --format json|csv|html
harbor-cli paper status "$RUN_ID"
harbor-cli paper stop "$RUN_ID"

# 找回已有运行（run_id 丢失时）：按创建时间倒序，默认 50 条、上限 200 条
harbor-cli paper list --limit 20
harbor-cli paper list --limit 20 --offset 20   # next_offset 为 null 表示已到末页
```

## 发布前研究边界（SP 4.83 / 4.95）

- 模拟盘路径只产生本地订单与对账记录，不创建券商凭据、不下真实订单。
- 报告明确研究性质与停止条件，不含收益或回撤承诺。
- 差异验证未通过（或未获独立豁免）不得升级进入 MVP 6 实盘评估。
