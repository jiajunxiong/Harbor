# MVP 3 验收记录（MVP 3 Acceptance Record）

> 本文件固化 MVP 3 样本外验证管线的验收运行结果，便于后续 MVP 4 复核（SP 3.86）。
> 验收记录基于**固定 Mock 数据 + 固定验证配置**的可复现运行：相同的配置哈希、数据
> 清单指纹、代码版本与随机种子可重放得到完全相同的试验、OOS、压力与报告产物
> （SP 3.28 / 3.46 / 3.63 / 3.80）。
>
> 验证输出仅用于研究，不构成投资建议，也不表示未来收益或回撤；本文记录的结论为
> **INCONCLUSIVE**（证据不足以做出资格判断，SP 3.58 / 3.64），不是资格结论。

## 验收命令（Commands）

以下命令在 Docker 环境（SP 3.85）中完成“迁移 → Mock 数据准备 → 冻结 → 调参 →
最终评估 → 报告导出”的完整验收流：

```bash
# 迁移数据库到最新 schema（含验证运行/清单/试验/折叠/压力/结论表，SP 3.12）
alembic upgrade head

# Mock 数据准备（港股股票池 + 日线）
harbor-cli fetch securities --market HK
harbor-cli fetch daily --market HK --symbol 0001.HK --start 2024-01-01 --end 2024-01-08

# 创建 DRAFT 验证运行并推进状态机（SP 3.69 / 3.70）
harbor-cli validation run --config examples/configs/validation/hk_validation.yaml
harbor-cli validation freeze <run-id>
harbor-cli validation tune <run-id>
harbor-cli validation lock <run-id>
harbor-cli validation evaluate <run-id>

# 结果查询 + 报告导出（SP 3.71）
harbor-cli validation show <run-id>
harbor-cli validation report <run-id> --format json
harbor-cli validation report <run-id> --format html
```

## 清单指纹（Dataset Fingerprint）

验收运行的数据清单指纹（SP 3.6 / 3.7）：

| 项 | 值 |
| :--- | :--- |
| 数据清单指纹（dataset_fingerprint） | `f2400c7e8b858b82553a9836a4411cfca039220f3ec4fbe25ba14d5fd25fbca5` |
| 市场 | HK（基准币种 HKD） |
| 数据窗口 | 2019-01-01 → 2024-12-31（2192 天） |
| 数据截点（data_cutoff） | 2022-12-31 |
| 组件数量 | 9（PRICES / STOCK_POOL / FUNDAMENTALS / CORPORATE_ACTIONS / CALENDAR / FX / BENCHMARK / DIVIDENDS / QUALITY_ISSUES） |

## 切分哈希（Split Hash）

冻结切分由 SP 3.3 的 `config_hash` 固化（SHA-256 over 规范化 JSON，YAML/JSON 格式无关）：

| 项 | 值 |
| :--- | :--- |
| 配置哈希（config_hash） | `34bfecfe33f31c839e62729087ecb89518008050321709c6ea99258810ff6e8a` |
| 训练区间 | 2019-01-01 → 2020-12-31 |
| 验证区间 | 2021-01-01 → 2021-12-31 |
| 测试区间 | 2022-01-01 → 2022-12-31 |
| 代码版本（code_version） | 1.0.0 |

## 参数预算（Parameter Budget）

| 项 | 值 |
| :--- | :--- |
| 预注册主指标（primary_metric） | sharpe（HIGHER_BETTER） |
| 最大试验数（max_trials） | 20 |
| 随机种子（random_seed） | 42 |
| 最低验证样本（min_validation_days） | 63 |

## 测试集版本（Test-Set Version）

| 项 | 值 |
| :--- | :--- |
| 测试集版本（test_set_id） | `mvp3-acceptance-001` |
| 访问政策 | 测试集仅一次性解锁用于最终评估（SP 3.5 / 3.41）；实质变化要求新测试集版本 + 新验证运行（SP 3.42） |

## 运行 ID（Run ID）

| 项 | 值 |
| :--- | :--- |
| 运行 ID（run_id） | `mvp3-acceptance-001` |
| 状态机 | DRAFT → DATA_FROZEN → TUNING → TEST_LOCKED → EVALUATED（SP 3.13 / 3.70） |

## 结论（Conclusion）

| 项 | 值 |
| :--- | :--- |
| 结论（overall） | `INCONCLUSIVE` |
| 稳定性规则（SP 3.58） | 各证据维度 PASS（折叠离散度 / 参数邻域 / 环境分段 / 压力损失 / 覆盖门槛） |
| 未解决限制数量 | 2 |
| 判定口径 | 任何 FAIL → `NOT_QUALIFIED`；缺失证据或未解决限制 → `INCONCLUSIVE`；全部 PASS 且无限制 → `QUALIFIED`（SP 3.64 `_aggregate`） |

## 未解决限制（Unresolved Limitations）

- `limited OOS horizon (illustrative acceptance run)`：验收运行使用小规模 Mock 数据，
  样本外时段有限，不足以做出资格判断（SP 3.82 / 3.83）。
- `no real broker order execution`：验证管线不产生模拟盘或券商订单（SP 3.87），
  结论不含收益承诺（SP 3.64 `no_return_promise_statement`）。

## 便于 MVP 4 复核（Reproduction for MVP 4）

- 使用与本文相同的固定 Mock 数据、验证配置哈希、数据清单指纹、代码版本与随机种子，
  可重放得到完全相同的试验、OOS、压力与报告产物（SP 3.80）。
- 进入 MVP 4 前的条件（SP 3.88 规划）：模拟盘闭环、风控审批、实盘差异验证；验证管线
  只读研究数据，不创建模拟盘或券商订单（SP 3.87）。
