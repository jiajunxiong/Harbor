# MVP 4 验收记录（MVP 4 Acceptance Record）

> 本文件固化 MVP 4 模拟盘闭环（SP 4.1–4.95）的验收运行结果，便于后续 MVP 5 复核。
> 验收记录基于**固定 Mock 数据 + 固定模拟盘配置**的可复现运行：相同的配置哈希、数据
> 指纹、代码版本与随机种子可重放得到完全相同的订单、成交、净值、对账与差异结果
> （SP 4.9 / 4.59 / 4.81）。
>
> 模拟盘输出仅用于研究，不构成投资建议，也不表示未来收益或回撤。MVP 4 只产生本地
> 模拟盘订单与对账记录，不创建券商凭据、不下真实订单（SP 4.83 / 4.95 发布前边界复核）。

## 验收命令（Commands）

以下命令在 Docker 环境（SP 4.92）中完成“迁移 → 模拟盘运行 → 对账 → 报告导出”的
完整验收流：

> `<dataset-fingerprint>`、`<run-id>`、`<order-id>`、`<approver>` 均为**占位符**，请替换为真实值
> （不要连同尖括号一起复制，否则 bash 会把 `<` 当作输入重定向而报语法错误）。
> `--dataset-fingerprint` 为运行的可重放标识（SP 4.9），最长 64 字符；优先使用 MVP 3 冻结数据集
> 清单指纹（SP 3.7），冒烟/演示可用稳定短标识（如 `hk-paper-demo-2026`）。

```bash
# 迁移数据库到最新 schema（含模拟盘运行/订单/成交/审批/熔断/净值/差异表，SP 4.5–4.8）
alembic upgrade head

# 初始化模拟盘运行（SP 4.84）：返回 run_id 与 DRAFT 状态
harbor-cli paper init --config examples/configs/paper/hk_paper.yaml \
  --dataset-fingerprint hk-paper-demo-2026
harbor-cli paper start <run-id> --approver <approver>

# 信号→订单（SP 4.85）
harbor-cli paper signal <run-id> --rebalance-date 2026-01-02 \
  --target 0001.HK:0.5 --price 0001.HK:50.0
harbor-cli paper order list <run-id>
harbor-cli paper order show <run-id> <order-id>

# 审批（SP 4.86，审批记录可审计）
harbor-cli paper approve <run-id> --order-id <order-id> --approver <approver>

# 对账（SP 4.87）：重建账户并与净值快照比对，差异落表不静默修正
harbor-cli paper reconcile <run-id> --as-of 2026-01-02

# 报告导出（SP 4.87）
harbor-cli paper report <run-id> --format json
harbor-cli paper report <run-id> --format csv
harbor-cli paper report <run-id> --format html

# 状态与停止（SP 4.84）
harbor-cli paper status <run-id>
harbor-cli paper stop <run-id>
```

## 示例配置哈希（Example Config Hashes）

示例配置的稳定配置哈希（SP 4.2 / 4.9，SHA-256 over 规范化 JSON）：

| 示例 | 配置哈希（config_hash） | 市场 | 基准币种 | 初始资金 |
| :--- | :--- | :--- | :--- | :--- |
| `examples/configs/paper/hk_paper.yaml` | `58ad8fdb69c858965e77f196c2aac4eae2a9f469c291da7a9e41de4784609c3a` | HK | HKD | 1,000,000 |
| `examples/configs/paper/us_paper.yaml` | `7db708537e4666755cbdc2b891cffb72b5dc90c65707e1a83a71b49429cdc799` | US | USD | 130,000 |
| `examples/configs/paper/cross_market_paper.yaml` | `3c73896d577b48502827e4583744bcf52d8a528b09180e32c34006de419d32ad` | HK + US | HKD | 1,000,000 |

## 运行记录（Run Record）

验收运行（HK 示例，SP 4.5 / 4.9）：

| 项 | 值 |
| :--- | :--- |
| 运行 ID（run_id） | 由 `harbor-cli paper init` 生成（UUID hex），全链路通过 `paper_run_id` 追溯 |
| 状态（status） | `DRAFT → APPROVED → ACTIVE → STOPPED`（SP 4.10 状态机） |
| 配置哈希（config_hash） | `58ad8fdb…c3a`（见上表） |
| 数据集指纹（dataset_fingerprint） | 由调用方提供（验收运行使用固定值） |
| 代码版本（code_version） | 1.0.0 |

## 审批记录（Approval Records）

人工审批流程（SP 4.39 / 4.86）记录审批人、决策、规则与时间：

- `paper start` 记录一条 `scope="run"` 的 `APPROVED` 审批。
- `paper approve/reject <run-id> --order-id <order-id>` 记录一条
  `scope="order:<order-id>"` 的 `APPROVED`/`REJECTED` 审批。
- 审批记录写入 `risk_approvals` 表（SP 4.7），可审计、可通过 `paper_run_id` 追溯。

## 对账摘要（Reconciliation Summary）

对账（SP 4.57 / 4.58 / 4.61 / 4.87）重建账户并与净值快照比对：

| 检查 | 说明 | 结果 |
| :--- | :--- | :--- |
| `assets_close` | 重建账户总资产 vs 净值快照 | 一致时 `reconciled=True`；差异时落表并告警 |
| `cash_close` | 重建现金 vs 快照现金 | 差异落表 |
| `securities_close` | 重建持仓市值 vs 快照市值 | 差异落表 |

对账差异写入 `paper_reconciliation_differences` 表（SP 4.8），**不静默修正**；进入
MVP 5 前要求无未解决对账差异（SP 4.77）。

## 差异验证结果（Difference Verification）

模拟盘成交与 OOS 研究假设的差异被量化、对照并告警（SP 4.69–4.83）：

- **研究假设快照（SP 4.69）**：滑点、成本、点差、成交量参与率、成交规则与执行延迟，
  指纹排除来源运行 id（可重放）。
- **实际参数采集（SP 4.70）**：每笔成交的实际成交价、已实现滑点、点差、执行延迟与参与。
- **差异指标（SP 4.71）**：按市场、调仓日与标的计算点差/滑点/成交价/执行延迟/成本差异。
- **对照报告（SP 4.72 / 4.82）**：JSON/CSV 对照表，字段稳定。
- **阈值与告警（SP 4.73 / 4.74）**：超过预注册阈值时记录告警（回落即恢复）；
  覆盖不足或数据缺失被标注，不下结论。
- **监控（SP 4.75 / 4.76）**：日度快照（净值、回撤、集中度、差异、对账）与日/周/月摘要。
- **准入登记（SP 4.77）**：最少运行 12 个月、每启用市场至少 4 次完整调仓和 30 笔成交、
  覆盖率与未解决对账差异；全部通过或独立豁免后才允许进入 MVP 5 单独评审。

## 已知限制（Known Limitations）

- **模拟盘为研究环境**：成交价/滑点/点差为模拟值，不等于真实券商成交；执行延迟为
  信号→订单→成交的时间戳差（SP 4.23），不代表真实撮合延迟。
- **未接入真实券商/行情**：MVP 4 不持有券商凭据、不下真实订单；行情来自配置的数据源。
- **准入需长期运行**：差异验证准入（SP 4.77）需要最少 12 个月运行、每市场至少 4 次
  完整调仓与 30 笔成交；验收记录仅为流程验收，不构成准入通过。
- **不构成收益承诺**：`QUALIFIED` 仅表示样本外证据充分，不构成投资建议，也不表示
  未来收益或回撤；模拟盘表现不构成收益承诺。

## 发布前研究边界复核（SP 4.95）

发布前研究边界复核确认：

- 模拟盘路径**不创建券商凭据、不下真实订单**（`paper_research_boundary.py`）。
- 报告**不含收益或回撤承诺**（复用 SP 3.64 声明）。
- **差异验证未通过不得升级**进入 MVP 5 实盘评估（SP 4.77 / 4.83）。
