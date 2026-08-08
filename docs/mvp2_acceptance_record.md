# MVP 2 验收记录（MVP 2 Acceptance Record）

> 本文件固化 MVP 2 研究回测（研究回测闭环）的验收运行结果，便于后续 MVP 3 复核
> （SP 2.87）。验收记录基于**固定 Mock 数据 + 固定策略配置**的可复现运行：相同的
> 配置哈希、数据截点与代码版本可重放得到完全相同的产物（SP 2.82）。
>
> 回测结果仅用于研究，不构成投资建议，也不表示未来收益或回撤。

## 验收命令（Commands）

以下命令在 Docker 环境（SP 2.86）中完成“迁移 → Mock 数据准备 → 回测 → 报告导出 →
结果查询”的完整验收流：

```bash
# 迁移数据库到最新 schema（含 backtest_runs / 结果表 / fx_rates / factor_snapshots）
alembic upgrade head

# Mock 数据准备（港股股票池 + 日线）
harbor-cli fetch securities --market HK
harbor-cli fetch daily --market HK --symbol 0001.HK --start 2024-01-01 --end 2024-01-08

# 回测（从版本化策略配置运行）
harbor-cli backtest run --config examples/configs/...yaml

# 结果查询
harbor-cli backtest show <run-id>

# 报告导出（JSON / CSV / HTML）
harbor-cli backtest report <run-id> --format json
harbor-cli backtest report <run-id> --format html
```

## 数据版本（Data Version）

验收运行使用的数据版本（SP 2.5 / 2.48 / 2.61 口径）：

| 项 | 值 |
| :--- | :--- |
| 策略配置哈希（config_hash） | `c7f761bcee1e689c141ccc79ff62d9649b9c47aa27aecedb8acd9c8fa160b825` |
| 数据截点（data_cutoff） | 2024-01-08 |
| 代码版本（code_version） | 1.0.0 |
| 市场 | HK + US（跨市场，基准币种 HKD） |
| 固定 FX | USD→HKD 7.8（2024-01-02 至 2024-01-08） |

## 运行 ID（Run ID）

| 项 | 值 |
| :--- | :--- |
| 运行 ID（run_id） | `mvp2-acceptance-001` |
| 状态（status） | COMPLETED |

## 结果摘要（Result Summary）

| 指标 | 值 |
| :--- | :--- |
| 交易日数 | 5 |
| 每日对账失败 | 0 |
| 期末净值（HKD） | 999010.33 |
| 期初净值（HKD） | 999010.33 |

## 已知限制（Known Limitations）

- 完整限制清单见 `docs/backtest_limitations.md`（SP 2.74）：数据覆盖、历史股票池
  （幸存者偏差）、企业行动、汇率（缺失即拒绝，绝不 1:1）、交易日历（演示节假日，
  非市场事实）、停牌估值、基准数据。
- CLI 选股当前使用股票池默认等权（SP 2.67）；因子管线（SP 2.15–2.28）作为独立的
  纯核心模块已实现并测试，但尚未接入 CLI 的选股路径。
- 回测只读研究数据并写入本地结果，不创建模拟盘或券商订单（SP 2.89）。

## 便于 MVP 3 复核（Reproduction for MVP 3）

- 使用与本文相同的固定 Mock 数据、策略配置哈希、数据截点与代码版本，可重放得到
  完全相同的成交、净值与指标（SP 2.82）。
- 进入 MVP 3 前的前置条件（SP 2.90）：独立保留期数据集且训练/验证/测试边界在配置
  中冻结；历史股票池、财报可得日期、企业行动条款、日历与 FX 覆盖范围量化。
