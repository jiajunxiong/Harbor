# 示例策略配置 (Example Strategy Configurations)

本目录提供 **保守的季度调仓研究示例**（SP 2.72），用于演示 Harbor 研究回测引擎的
配置格式与运行方式。它们**不是投资建议，也不表示任何未来收益或回撤承诺**。

## 文件说明

| 文件 | 市场 | 目标持仓 | 基准币种 | 备注 |
| :--- | :--- | :--- | :--- | :--- |
| `hk_quarterly.yaml` | 港股 (HK) | 15 | HKD | 港股成本/手数示例（`lot_size=100`） |
| `us_quarterly.yaml` | 美股 (US) | 15 | USD | 美股碎股 + 卖出监管费示例（`lot_size=1`） |
| `us_quarterly.json` | 美股 (US) | 15 | USD | 与 `us_quarterly.yaml` 等价的 JSON 格式示例 |
| `cross_market_quarterly.yaml` | 港股 + 美股 | 10 + 10 = 20 | HKD | 跨市场配额示例（HK 50% / US 50%） |

## 运行方式

```bash
# 单市场（港股）
harbor-cli backtest run --config examples/configs/hk_quarterly.yaml

# 单市场（美股，JSON 格式）
harbor-cli backtest run --config examples/configs/us_quarterly.json

# 跨市场（需先采集逐日 HKD↔USD 汇率；缺 FX 时引擎会拒绝生成跨市场净值）
harbor-cli backtest run --config examples/configs/cross_market_quarterly.yaml

# 查看运行状态 / 导出报告
harbor-cli backtest show <run-id>
harbor-cli backtest report <run-id> --format html
```

## 研究用途与关键假设

这些配置刻意保持**保守**，作为研究引擎的可复现起点：

- **策略形态**：季度调仓、长仓（long-only）、单股上限 10%、2% 现金缓冲；
  单市场 15 只、跨市场 20 只（符合 MVP 2 的目标范围 15–20 只）。
- **选股**：当前使用股票池默认等权选择（SP 2.67）；因子管线
  （SP 2.15–2.28 的股东回报、低波动、盈利质量）**尚未接入**。
- **成本**：佣金、印花税、交易费、监管费与滑点为**研究假设，不是市场事实**；
  请结合 SP 2.73（默认参数说明）与 SP 2.74（回测限制说明）核对真实值。
- **日历**：交易日历使用内置演示节假日（SP 2.11），不代表交易所实际假期。
- **汇率**：跨市场组合必须提供逐日 FX 数据；缺失时拒绝 1:1 换算（SP 2.12/2.27/2.45）。
- **数据区间**：`2019-01-01` 至 `2024-12-31` 为演示区间，可自行调整；配置经过
  SP 2.4 校验、SP 2.5 生成稳定哈希，同配置始终得到同一研究运行。

> 每个 YAML 文件头部均以注释形式重申研究用途与关键假设，便于随文件一起被版本管理。
