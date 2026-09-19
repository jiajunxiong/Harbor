# ⚓ Harbor

> 面向个人投资者的港股与美股低频量化研究与模拟交易系统。

Harbor 用于研究基于股息、波动率和盈利质量的港股及美股季度调仓策略，并提供可复现的回测、风险监控与模拟盘执行能力。

---

## 📌 项目目标

- 建立可追溯的**港股**和**美股**日线、分红、企业行动和基本面数据集。
- 对低频多因子策略进行可复现、计入成本的回测，覆盖港股与美股两个市场。
- 在模拟盘中验证策略信号、风控规则和订单执行的一致性。
- 在人工确认与独立风控的前提下，为后续实盘接入提供基础。

**Harbor 不承诺固定年化收益或最大回撤**。历史回测结果不代表未来表现，任何实盘使用均应先经过充分的样本外测试和模拟盘验证。

---

## 📍 当前范围

首个版本聚焦以下能力：

- **港股**和**美股**日线、分红、企业行动和基本面数据的采集、标准化与质量校验（企业行动处理需区分两地规则，不混用统一逻辑）。
- 季度调仓、长仓、15–20 只股票的因子策略回测，支持跨市场选股。
- 交易成本、滑点、停牌和企业行动的建模（港股与美股的成本结构不同）。
- 组合层面的风险限额、人工熔断和审计记录。
- 模拟盘订单生成与执行结果对账。

暂不包含自动实盘交易、高频策略或收益率保证。

---

## 📊 策略研究原则

候选策略使用以下维度进行研究，具体定义和权重须在策略配置中版本化：

| 维度 | 研究方向 |
| :--- | :--- |
| **股东回报** | 常规股息、回购与可持续性；特别股息单独处理 |
| **风险** | 年化波动率、回撤、流动性与行业集中度 |
| **基本面质量** | ROE 稳定性、盈利质量与财报披露时点 |
| **可交易性** | 成交额、上市时间、停牌与公司行动 |

回测必须避免未来函数和幸存者偏差，使用当时可获得的数据及其披露日期，并纳入佣金、印花税、交易费和合理滑点。港股与美股的成本结构和企业行动披露规则不同，需分别建模。

---

## 🎯 交易目标与风险预算

Harbor 不把固定年化收益作为交易目标。长期目标是在完整市场周期内、扣除全部成本后取得正的超额回报，同时把可承受回撤和避免永久性资本损失置于盈利之前。

这一顺序借鉴了以下投资与交易原则：

- **沃伦·巴菲特**：风险的核心是永久性资本损失，而不只是短期价格波动；应以安全边际和长期价值约束决策。
- **保罗·都铎·琼斯**：资本保护优先于盈利；判断错误时先降低风险，不为回本而扩大仓位。
- **拉里·海特**：任何单一交易或观点都不能威胁组合的持续参与能力；仓位必须服从风险预算。

| 层级 | 目标或限制 | 执行规则 |
| :--- | :--- | :--- |
| **长期评估** | 不设年度收益下限 | 使用滚动三年及以上的样本外结果，评估扣费后的超额回报、回撤和参数稳定性 |
| **单笔风险** | 最大预期损失不超过组合净值的 0.5% | 仓位由止损距离、波动率和流动性共同决定；无法量化风险时不开仓 |
| **集中度** | 单一股票不超过 5%，单一行业不超过 20% | 超限时优先减仓；禁止通过加仓摊薄成本来规避风险 |
| **预警回撤** | 从净值高点回撤达到 5% | 停止增加风险仓位，复核数据质量、交易成本和风险暴露 |
| **防御回撤** | 从净值高点回撤达到 8% | 将总风险仓位降至策略基准的一半，暂停新策略和参数调整 |
| **熔断回撤** | 从净值高点回撤达到 10% | 平掉非必要风险仓位，冻结新订单；独立复盘通过后才可恢复 |

这些阈值是操作纪律，不是最大回撤保证。停牌、跳空、流动性枯竭、市场异常和券商故障都可能使实际损失超过阈值。若希望进一步压低组合回撤，应通过现金、短久期低风险资产或对冲预算进行资产配置，而不只依赖止损。

---

## 🗺️ MVP 路线图

| 阶段 | 交付内容 | 验收标准 |
| :--- | :--- | :--- |
| **MVP 1：数据基础** | 日线、分红、企业行动、基本面入库与数据质量报告（覆盖港股与美股） | 可追溯的数据来源、字段完整性与异常记录；支持通过环境变量切换数据源和股票池 |
| **MVP 2：研究回测** ✅ | 可配置的选股、组合和回测引擎（已完成，见下方开发状态） | 可重复运行；成本、停牌和企业行动纳入结果；输出完整绩效指标 |
| **MVP 3：样本外验证** ✅ | 滚动回测、压力测试和参数稳定性报告（已完成，见下方开发状态） | 明确训练/验证区间并输出风险指标；确认策略在不同市场环境下表现稳定 |
| **MVP 4：模拟盘闭环** ✅ | 模拟盘执行、风控审批、订单成交对账与差异验证（已完成，见下方开发状态） | 模拟盘执行与策略记录可审计、可重放；日熔断/月熔断机制验证通过；成交与 OOS 假设差异量化并告警 |
| **MVP 5：前端开发** | 只读监控看板（回测 / 样本外验证 / 模拟盘 / 数据质量）与只读 API 层 | 补齐只读 API；看板口径与 CLI 一致、只读且不含收益或回撤承诺 |
| **MVP 6：实盘评估** | 券商适配、人工审批与应急流程 | 仅在长期模拟盘通过后单独评审；初期采用人工确认模式 |

---

## 🏗️ 计划中的架构

在 MVP 阶段，项目采用**模块化单体架构**，避免在需求未稳定时引入跨服务运维成本。各模块在代码层面严格分层，为后续拆分为独立容器预留接口：

```text
数据采集 -> PostgreSQL/TimescaleDB -> 策略与回测 -> 风控审批 -> 模拟盘执行
                                      |
                                      -> FastAPI API 与监控界面
```

当模拟盘闭环稳定、边界明确后，再按需拆分为 5 个独立容器：

| 容器 | 职责 |
| :--- | :--- |
| **harbor-data** | 数据采集与清洗（港股 + 美股） |
| **harbor-strategy** | 因子计算与选股（跨市场候选池） |
| **harbor-risk** | 回撤监控与熔断审批 |
| **harbor-trader** | 订单执行（模拟/实盘） |
| **harbor-backtest** | 独立回测引擎（手动触发） |

交易执行服务应独立持有券商凭据，并支持幂等下单、审计日志、人工暂停和故障恢复。

---

## 🛠️ 技术选型

| 领域 | 计划选型 | 说明 |
| :--- | :--- | :--- |
| **后端** | Python 3.11 + FastAPI | 统一技术栈，降低维护成本 |
| **数据处理** | Pandas + NumPy + SQLAlchemy | 金融数据清洗与因子计算 |
| **回测引擎** | VectorBT | 向量化回测，适合低频策略的快速因子挖掘；需验证对美股全市场 7,000+ 只股票 10 年以上日线的容量 |
| **数据库** | PostgreSQL + TimescaleDB | 关系型 + 时序数据分离存储；需评估美股全市场数据量下的存储与查询性能 |
| **缓存** | Redis | 实时行情与参数缓存（按需启用） |
| **前端** | React + TypeScript + ECharts | 监控看板（在监控需求成熟后引入） |
| **部署** | Docker + Docker Compose | 一键启动所有服务 |
| **任务调度** | APScheduler | 定时数据更新与调仓触发 |

### 数据源策略

`yfinance` 作为**港股**和**美股**的原型数据源：

- **港股**：验证 `.HK` 代码映射、除净日与特别股息、历史成分股及退市记录、供股/合股等企业行动的完整性与时效性。
- **美股**：验证 SEC filings、拆股/股息/回购数据的准确性，以及历史成分股（如 S&P 500 历史成分变动）的覆盖度。

AkShare 作为**港股**备选数据源。商业数据源（如 Wind、Polygon.io）需在原型验证后单独评估许可范围和成本。

---

## 🛡️ 风险控制边界

风险参数是操作纪律，**不是收益或回撤保证**。尤其是停牌、跳空、流动性不足、市场异常和券商故障可能导致订单无法按预期执行。

系统至少应支持：

- 单笔和组合级最大预期风险限额，以及按回撤级别执行的降风险规则
- 行业、个股和流动性集中度限制
- 日度、月度和总回撤监控
- 人工熔断、禁止开仓与订单审计
- 实盘与回测/模拟盘之间的差异对账

核心风控参数（可配置）：

| 参数 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `DRAWDOWN_ALERT` | 5% | 预警：停止增加风险仓位 |
| `DRAWDOWN_DEFENSE` | 8% | 防御：总风险仓位降至基准的一半 |
| `DRAWDOWN_CIRCUIT_BREAKER` | 10% | 熔断：冻结新订单并进行独立复盘 |
| `SINGLE_TRADE_RISK` | 0.5% | 单笔最大预期损失占组合净值的比例 |
| `MAX_SINGLE_STOCK` | 5% | 单一股票持仓上限 |
| `MAX_SECTOR` | 20% | 单一行业持仓上限 |

---

## 📂 项目结构（规划）

```
harbor/
├── src/
│   ├── core/                    # 核心业务逻辑
│   │   ├── interfaces.py        # 抽象接口定义
│   │   ├── factors.py           # 因子计算
│   │   ├── strategy.py          # 选股与调仓逻辑
│   │   └── risk_engine.py       # 风控引擎
│   ├── infrastructure/          # 基础设施
│   │   ├── data_providers/      # 数据源实现
│   │   │   ├── base.py          # 抽象基类
│   │   │   ├── hk/              # 港股
│   │   │   │   ├── yfinance_provider.py
│   │   │   │   └── akshare_provider.py
│   │   │   └── us/              # 美股
│   │   │       └── yfinance_provider.py
│   │   ├── db/                  # 数据库模型与操作
│   │   └── broker/              # 券商API封装（模拟/实盘）
│   ├── api/                     # FastAPI 只读监控 API（MVP 5）
│   └── scheduler/               # 定时任务配置
├── backtest/                    # 回测脚本与Notebook
├── frontend/                    # React 只读监控看板（MVP 5）
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## 🚀 快速开始

### 前置条件

- Docker Desktop 24.0+
- Python 3.11+（仅本地开发需要）

### 1. 克隆项目

```bash
git clone https://github.com/your-username/harbor.git
cd harbor
```

### 2. 创建虚拟环境并配置环境变量

```bash
# 创建并激活虚拟环境，安装项目（含 harbor-cli 入口）
python -m venv .venv
source .venv/bin/activate
pip install -e .

# 从模板生成本地配置
cp .env.example .env
# 编辑 .env 文件，设置数据源、数据库密码、目标市场（HK/US/BOTH）等
```

> 后续所有 `alembic` / `harbor-cli` 命令都需要在已激活的虚拟环境中执行（提示符出现 `(.venv)`）。

### 3. 启动基础服务

```bash
docker compose up -d postgres redis
```

### 4. 初始化数据库（应用迁移）

```bash
# alembic 读取 DATABASE_URL 环境变量（不会自动读取 .env），先加载到当前 shell
set -a && source .env && set +a

# 应用数据库迁移
alembic upgrade head
```

### 5. 运行数据采集（MVP 1，双市场）

```bash
# 港股：全量采集（标的 + 日线 + 股息 + 基本面 + 企业行动）
harbor-cli fetch all --market HK

# 美股：全量采集
harbor-cli fetch all --market US

# 仅采集标的列表
# 港股 → 恒生指数 (HSI) 成分股（中文维基百科 恒生指数）；美股 → S&P 500 成分股（英文维基百科）
# （yfinance provider 从维基百科解析，符号与 Yahoo Finance 一致）
harbor-cli fetch securities --market HK
harbor-cli fetch securities --market US

# 采集单只标的的日线
harbor-cli fetch daily --market HK --symbol 0700.HK
harbor-cli fetch daily --market US --symbol AAPL

# 批量采集某市场全部已注册标的的日线（HSI / S&P 500 成分股）
harbor-cli fetch daily --market HK --all --start 2019-01-01 --end 2024-12-31
harbor-cli fetch daily --market US --all --start 2019-01-01 --end 2024-12-31
# 可选：--limit 仅抓前 N 只（冒烟测试），--delay 控制抓取间隔（防 yfinance 限流）
```

### 6. 数据质量报告

```bash
# 查看港股数据质量摘要（可选导出 CSV）
harbor-cli quality report --market HK
harbor-cli quality report --market US --csv issues.csv

# 查看当前配置与数据源能力
harbor-cli config
harbor-cli providers
```

### 7. 验证数据入库

```bash
docker compose exec postgres psql -U harbor -d harbor -c "SELECT market, COUNT(*) FROM daily_quotes GROUP BY market;"
```

---

## 🌏 双市场运行方式

Harbor 在同一个数据库中通过 `market` 字段（`HK` / `US`）严格隔离港股与美股数据，所有采集与校验都按市场独立执行。

### 独立数据源配置

| 环境变量 | 作用 | 可选值 |
| :--- | :--- | :--- |
| `DATA_PROVIDER_HK` | 港股数据源 | `mock`、`yfinance`、`akshare` |
| `DATA_PROVIDER_US` | 美股数据源 | `mock`、`yfinance` |
| `MARKET_TARGET` | 目标市场 | `HK`、`US`、`BOTH` |

港股与美股可同时使用不同数据源，例如 `.env` 中：

```dotenv
DATA_PROVIDER_HK=akshare
DATA_PROVIDER_US=yfinance
```

### 常用命令（按市场）

```bash
# 港股：全量采集 + 质量报告
harbor-cli fetch all --market HK
harbor-cli quality report --market HK

# 美股：全量采集 + 质量报告 + CSV 导出
harbor-cli fetch all --market US
harbor-cli quality report --market US --csv issues.csv
```

### 两地规则差异

企业行动与数据格式按市场分别校验，不混用统一逻辑：

- **港股**：支持供股（`rights_issue`）、合股（`consolidation`）、要约（`tender_offer`）、股息（`dividend`）；股票代码形如 `0700.HK`；股息币种为 `HKD`。
- **美股**：支持拆股（`split`）、并购（`merger`）、分拆（`spin_off`）、股息（`dividend`）；股票代码为纯大写代码（如 `AAPL`）；股息币种为 `USD`。

---

## 🧪 研究回测（MVP 2）

> 回测结果仅用于研究，**不构成投资建议**，也不表示未来收益或回撤。

### 依赖安装（回测可选依赖）

基础安装见上方“快速开始”。回测与因子计算额外依赖 `numba` 与 `vectorbt`（SP 2.1），
开发与静态检查工具属于 `dev` extra：

```bash
# 回测可选依赖（含 harbor-cli 入口）
pip install -e ".[backtest]"

# 开发依赖：mypy / pytest / ruff / types-PyYAML
pip install -e ".[dev]"
```

### 数据库迁移

回测运行主表、结果表与汇率表由 Alembic 迁移创建（SP 2.6 / 2.7 / 2.12）。
在已激活的虚拟环境中（`alembic` 读取 `DATABASE_URL`，不会自动读取 `.env`）：

```bash
set -a && source .env && set +a
alembic upgrade head
```

### 策略配置

回测使用**版本化策略配置**（YAML 或 JSON，SP 2.5），定义市场、日期区间、基准币种、
调仓频率、资金、成本、风控与执行规则。仓库内置**保守示例**（SP 2.72），研究用途与
假设见各文件头部注释：

```text
examples/configs/
├── hk_quarterly.yaml            # 港股 15 只，季度调仓，HKD
├── us_quarterly.yaml            # 美股 15 只，季度调仓，USD
├── us_quarterly.json            # 与 us_quarterly.yaml 等价的 JSON 示例
├── cross_market_quarterly.yaml  # 港股 10 + 美股 10，季度调仓，HKD
└── README.md                    # 示例用途、假设与运行方式
```

配置经校验后生成稳定 `config_hash`（SP 2.5）；**相同配置 + 相同数据截止 + 相同代码
版本 = 同一研究运行**（幂等语义，SP 2.48）。

### 运行与状态

```bash
# 运行回测（返回 run_id 与状态）
harbor-cli backtest run --config examples/configs/hk_quarterly.yaml
harbor-cli backtest run --config examples/configs/us_quarterly.json --code-version 0.1.0

# 展示配置摘要、数据范围、状态与核心指标
harbor-cli backtest show <run-id>
```

### 取消与恢复

```bash
# 运行中（INITIALIZING/RUNNING）可安全取消
harbor-cli backtest cancel <run-id>

# 失败/取消的运行恢复为“新运行”并关联原运行（绝不静默续跑，SP 2.70）
harbor-cli backtest resume --config examples/configs/hk_quarterly.yaml --resume-of <run-id>
```

### 报告导出

```bash
# JSON（默认） / CSV / HTML
harbor-cli backtest report <run-id> --format json
harbor-cli backtest report <run-id> --format csv
harbor-cli backtest report <run-id> --format html
```

### 重放与一致性

- 相同输入（配置哈希、数据边界、代码版本）重复执行 → 一致的信号、成交、净值与指标
  （SP 2.61 可重放清单 / SP 2.62 一致性校验）。
- 结果可通过 `backtest_run_id` 查询与导出（SP 2.66 研究审计 / SP 2.58 JSON 产物），
  结构化日志亦携带 `backtest_run_id`（SP 2.71）。

### 默认参数与研究限制

- **默认参数**（成本、滑点、流动性、汇率与因子缺失的默认处理——是研究假设，**不是
  市场事实**）见 [`docs/backtest_default_parameters.md`](docs/backtest_default_parameters.md)（SP 2.73）。
- **回测限制**（数据覆盖、历史股票池、企业行动、FX、日历、停牌估值与基准数据的限制）
  见 [`docs/backtest_limitations.md`](docs/backtest_limitations.md)（SP 2.74）。

---

## 🧪 样本外验证（MVP 3）

> 验证输出仅用于研究，**不构成投资建议**，也不表示未来收益或回撤。

### 验证前置条件

样本外验证需要一份**冻结**的数据与研究环境（SP 3.6–3.10）：

- **冻结切分**：训练/验证/测试边界在配置中显式冻结（SP 3.4），由 `config_hash` 固化
  （SP 3.3，YAML/JSON 格式无关）。
- **独立保留集**：测试集只能**一次性解锁**用于最终评估（SP 3.5 / 3.41）；参数搜索与
  参数比较**永不**使用测试集（SP 3.21 / 3.24）。
- **数据覆盖**：价格/股票池/财报/企业行动/日历/FX/基准的覆盖口径与门槛（SP 3.9 / 3.10），
  缺失 FX、未知历史股票池、缺失企业行动条款不会静默通过。
- **压力情景**：压力情景在参数搜索前**预注册**（SP 3.59），未登记情景不得进入结论。
- 方法、口径与限制的完整说明见
  [`docs/oos_method_and_limitations.md`](docs/oos_method_and_limitations.md)（SP 3.73）。

### 验证配置

仓库内置**小规模 Mock 验证示例**（SP 3.72），显式演示冻结切分、试验预算、覆盖门槛与
压力情景，研究用途与假设见各文件头部注释与
[`examples/configs/validation/README.md`](examples/configs/validation/README.md)：

```text
examples/configs/validation/
├── hk_validation.yaml            # 港股 / HKD
├── us_validation.yaml            # 美股 / USD
├── us_validation.json            # 与 us_validation.yaml 等价的 JSON 示例
├── cross_market_validation.yaml  # 港股 + 美股 / HKD
└── README.md                     # 示例用途、四个演示维度与运行方式
```

### 运行与状态

验证运行沿状态机推进 `DRAFT → DATA_FROZEN → TUNING → TEST_LOCKED → EVALUATED`
（SP 3.13）；**顺序违反状态机时给出可行动错误**（SP 3.70），不会静默续跑。

```bash
# 创建 DRAFT 验证运行并返回 run_id 与状态（SP 3.69）
harbor-cli validation run --config examples/configs/validation/hk_validation.yaml

# 冻结数据 / 进入调参 / 锁定测试集 / 最终评估（SP 3.70）
harbor-cli validation freeze <run-id>
harbor-cli validation tune <run-id>
harbor-cli validation lock <run-id>
harbor-cli validation evaluate <run-id>
```

### 报告导出

```bash
# 查询运行状态视图（SP 3.71）
harbor-cli validation show <run-id>

# JSON（默认） / CSV / HTML 研究报告（SP 3.71；SP 3.68 渲染，显著展示研究性质）
harbor-cli validation report <run-id> --format json
harbor-cli validation report <run-id> --format csv
harbor-cli validation report <run-id> --format html
```

### 重放与一致性

- 固定数据清单（`dataset_fingerprint`）、冻结配置（`config_hash`）、代码版本与随机种子
  → 参数试验、排名、选择与拟合快照完全一致（SP 3.28），OOS 与压力产物可重放
  （SP 3.46 / 3.63）。
- 结论只能为 `QUALIFIED` / `NOT_QUALIFIED` / `INCONCLUSIVE`（SP 3.58）；报告含结论指纹、
  测试集版本、数据集指纹与审计事件（SP 3.66–3.68）。

### 未通过不等于可调参重测

验证工作流最关键的纪律：

- **`NOT_QUALIFIED` / `INCONCLUSIVE` 不等于“调参后重测即通过”**：一旦测试集已用于
  最终评估，任何策略 / 参数 / 数据 / 代码的实质变化都要求新的测试集版本 + 新的验证运行
  （SP 3.42 再访问政策），不得沿用已定稿保留集结论。
- **INCONCLUSIVE 表示证据不足，不是通过**（SP 3.58 / 3.73）：缺失证据的维度按不足
  处理，绝不静默视为通过；须补充证据后重新评估。
- **参数搜索只用训练 + 验证数据**：测试集只能一次性解锁用于最终评估（SP 3.24 / 3.41），
  禁止用测试期指标选参（SP 3.21）。

---

## 🧪 模拟盘（MVP 4）

> 模拟盘输出仅用于研究，**不构成投资建议**，也不表示未来收益或回撤。MVP 4 只产生本地
> 模拟盘订单与对账记录，**不创建券商凭据、不下真实订单**（SP 4.83 / 4.95 发布前边界复核）。

模拟盘闭环沿 `信号 → 订单草案 → 风控审批 → 模拟盘成交 → 账本/净值对账 → 研究假设差异验证
→ 监控与审计` 运行，每一环都可审计、可重放（SP 4.1–4.95）。

### 模拟盘配置

模拟盘使用**版本化配置**（SP 4.2，YAML/JSON），定义策略版本、市场范围、调仓频率、初始资金、
多币种账本、风控参数、停止条件与运行模式（`MANUAL`/`AUTO`）。仓库内置**保守示例**
（SP 4.88），研究用途与假设见各文件头部注释与
[`examples/configs/paper/README.md`](examples/configs/paper/README.md)：

```text
examples/configs/paper/
├── hk_paper.yaml            # 港股单市场，HKD 基准，港股手数/板位规则（SP 4.17）
├── us_paper.yaml            # 美股单市场，USD 基准，美股整股/小数股规则（SP 4.18）
├── cross_market_paper.yaml  # 港股+美股，HKD 基准、HKD/USD 多币种账本（SP 4.4）
└── README.md                # 示例用途、假设与运行方式
```

配置经校验后生成稳定 `config_hash`（SP 4.2 / 4.9）；**相同配置 + 相同数据指纹 + 相同代码
版本 + 相同随机种子 = 同一模拟盘运行**（可重放标识，SP 4.9）。差异验证与监控说明见
[`docs/paper_examples.md`](docs/paper_examples.md)（SP 4.88）。

### 生命周期与运行状态

模拟盘运行沿状态机推进 `DRAFT → APPROVED → ACTIVE → STOPPED`（SP 4.10）；激活前必须
**人工审批**（SP 4.39），回撤 10% 进入 `CIRCUIT_BROKEN` 冻结新订单（SP 4.36），独立复盘
通过后才可恢复（SP 4.42）。非法迁移拒绝，审批/熔断事件可审计。

### 运行与状态（SP 4.84）

> **`--dataset-fingerprint` 怎么填**：它是本次运行的可重放标识（SP 4.9），**最长 64 字符、无格式校验**。
> 优先填 MVP 3 冻结数据集清单的指纹（SP 3.7，64 位十六进制），可从验证运行查到：
> `SELECT fingerprint FROM validation_manifests WHERE validation_run_id = '<validation-run-id>';`
> 仅做冒烟/演示时，可用任意稳定短标识，例如 `hk-paper-demo-2026`。
>
> `init` 会返回 `run_id`；**建议存进 shell 变量**（下方 `RUN_ID=...`），避免手工复制出错。
> run_id 丢失时用 `harbor-cli paper list` 找回（按创建时间倒序，默认 50 条、上限 200 条，
> `total` / `next_offset` 说明结果是否被截断）。

```bash
# 初始化模拟盘运行，并把 run_id 存进变量（DRAFT 状态）
RUN_ID=$(harbor-cli paper init --config examples/configs/paper/hk_paper.yaml \
  --dataset-fingerprint hk-paper-demo-2026 \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["run_id"])')
echo "$RUN_ID"   # 例：a4565661cbad4f468185716f5c9900e9

# 审批并激活（DRAFT -> APPROVED -> ACTIVE，记录审批）
harbor-cli paper start "$RUN_ID" --approver jjxiong

# 查询状态视图 / 停止（终态）
harbor-cli paper status "$RUN_ID"
harbor-cli paper stop "$RUN_ID"

# 找回已有运行（run_id 丢失时）：按创建时间倒序，默认 50 条、上限 200 条
harbor-cli paper list --limit 20
harbor-cli paper list --limit 20 --offset 20   # next_offset 为 null 表示已到末页
```

### 信号→订单（SP 4.85）

```bash
# 从目标权重派生并持久化订单（不足一手的港股单被跳过并记录，不静默丢弃，SP 4.21）
harbor-cli paper signal "$RUN_ID" --rebalance-date 2026-01-02 \
  --target 0001.HK:0.5 --price 0001.HK:50.0

# 订单列表 / 单笔订单（order_id 同样存变量）
harbor-cli paper order list "$RUN_ID"
ORDER_ID=$(harbor-cli paper order list "$RUN_ID" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["order_id"])')
harbor-cli paper order show "$RUN_ID" "$ORDER_ID"
```

### 审批（SP 4.86）

```bash
# 人工审批/拒绝订单，审批记录（审批人、决策、规则、时间）可审计
harbor-cli paper approve "$RUN_ID" --order-id "$ORDER_ID" --approver jjxiong
harbor-cli paper reject "$RUN_ID" --order-id "$ORDER_ID" --approver jjxiong
```

### 对账与报告（SP 4.87）

```bash
# 对账：重建账户并与净值快照比对，差异写入表并告警、不静默修正（SP 4.58 / 4.61）
harbor-cli paper reconcile "$RUN_ID" --as-of 2026-01-02

# 报告导出：JSON（默认）/ CSV / HTML（含状态、订单、审批与对账差异）
harbor-cli paper report "$RUN_ID" --format json
harbor-cli paper report "$RUN_ID" --format csv
harbor-cli paper report "$RUN_ID" --format html
```

### 差异验证与监控（SP 4.69–4.83）

- **研究假设快照（SP 4.69）**：记录 OOS 假设中的滑点、成本、点差、成交量参与率、成交
  规则与执行延迟，指纹排除来源运行 id（可重放）。
- **实际参数采集（SP 4.70）**：记录每笔成交的实际成交价、已实现滑点、点差、执行延迟与参与。
- **差异指标（SP 4.71 / 4.78）**：按市场、调仓日与标的量化点差/滑点/成交价/执行延迟/成本差异；
  无假设的成交被标注（SP 4.74），不静默忽略。
- **阈值与告警（SP 4.73 / 4.79）**：差异超过预注册阈值时记录告警（回落即恢复）；覆盖
  不足或数据缺失被标注，不下结论。
- **监控与周期摘要（SP 4.75 / 4.76 / 4.80）**：日度快照（净值、回撤、集中度、差异、对账）
  与日/周/月摘要生成与查询。
- **差异验证准入（SP 4.77）**：最少运行 12 个月、每启用市场至少 4 次完整调仓和 30 笔成交、
  覆盖率与未解决对账差异；全部通过或独立豁免后才允许进入 MVP 6 单独评审。

### 重放与一致性

- 相同配置哈希、数据指纹、代码版本与随机种子 → 相同的订单、成交、净值、对账与差异结果
  （SP 4.9 / 4.59 / 4.81 可重放）。
- 订单、成交、审批与熔断事件均可通过 `paper_run_id` 追溯（SP 4.5–4.8 / 4.25）。

### 发布前研究边界（SP 4.83 / 4.95）

- 模拟盘路径只产生本地订单与对账记录，**不创建券商凭据、不下真实订单**。
- 报告明确研究性质与停止条件，**不含收益或回撤承诺**（复用 SP 3.64 声明）。
- **差异验证未通过（或未获独立豁免）不得升级**进入 MVP 6 实盘评估。

---

## 📊 开发状态

| MVP 阶段 | 状态 | 预计完成 |
| :--- | :--- | :--- |
| MVP 1：数据基础 | ✅ 已完成 | 2026年8月 |
| MVP 2：研究回测 | ✅ 已完成 | 2026年8月 |
| MVP 3：样本外验证 | ✅ 已完成 | 2026年8月 |
| MVP 4：模拟盘闭环 | ✅ 已完成 | 2026年8月 |
| MVP 5：前端开发 | � 阶段 1 已完成（SP 5.1–5.12） | — |
| MVP 6：实盘评估 | 📋 规划中 | — |

> MVP 1（数据基础）已完成：港股与美股数据的采集、标准化、质量校验、复权因子与权益计算均已落地并通过自动化测试。MVP 1 尚未提供可用于实盘交易的策略或基础设施。
>
> MVP 2（研究回测）已完成：可配置、可重放、无未来函数与幸存者偏差的季度调仓长仓回测引擎已落地，覆盖港股与美股各自的交易日、币种、成本、停牌、企业行动与跨市场汇率（SP 2.1–2.89）。回测只读研究数据并写入本地结果，不创建模拟盘或券商订单（SP 2.89）；结果仅用于研究，不构成投资建议，也不表示未来收益或回撤。验收记录见 [`docs/mvp2_acceptance_record.md`](docs/mvp2_acceptance_record.md)（SP 2.87）。
>
> MVP 3（样本外验证）已完成：样本外验证管线（冻结切分、参数搜索只用训练+验证、滚动 OOS、压力/覆盖门槛、结论 `QUALIFIED`/`NOT_QUALIFIED`/`INCONCLUSIVE` 可审计）与验证 CLI（`run`/`freeze`/`tune`/`evaluate`/`show`/`report`，SP 3.69–3.71）、示例验证配置（SP 3.72）、样本外方法与限制文档（SP 3.73）、完整测试与压力套件（SP 3.75–3.84）、Docker 验证冒烟测试（SP 3.85）均已落地；验收记录见 [`docs/mvp3_acceptance_record.md`](docs/mvp3_acceptance_record.md)（SP 3.86）。发布前研究边界复核确认：验证路径不创建模拟盘/券商订单、不访问未授权测试集，且报告不含收益或回撤保证（SP 3.87）。验证输出仅用于研究，不构成投资建议，也不表示未来收益或回撤。
>
> MVP 4（模拟盘闭环）已完成：独立、可审计、可重放的本地模拟盘执行闭环（信号→订单草案→风控审批→模拟盘成交→账本/净值对账→研究假设差异验证→监控与审计，SP 4.1–4.95）已落地：模拟盘领域类型/配置/准入/账户/表结构/状态机/审计/数据读取（SP 4.1–4.13）、信号→订单映射（SP 4.14–4.30）、风控审批与日/月/回撤熔断（SP 4.31–4.49）、成交引擎/账本/估值/对账（SP 4.50–4.68）、差异验证与监控（SP 4.69–4.83）与 CLI/示例/迁移/集成/Docker 验收（SP 4.84–4.95）。模拟盘 CLI 提供 `paper init/start/stop/status`、`signal`、`order list/show`、`approve/reject`、`reconcile`、`report`（SP 4.84–4.87）；示例配置见 [`examples/configs/paper/`](examples/configs/paper/README.md)（SP 4.88），差异验证与监控说明见 [`docs/paper_examples.md`](docs/paper_examples.md)；验收记录见 [`docs/mvp4_acceptance_record.md`](docs/mvp4_acceptance_record.md)（SP 4.93）。发布前研究边界复核确认：模拟盘路径只产生本地订单与对账记录、不创建券商凭据、不下真实订单，且报告不含收益或回撤承诺（SP 4.95）。模拟盘输出仅用于研究，不构成投资建议，也不表示未来收益或回撤。

#### 进入 MVP 3（样本外验证）前仍需解决的数据与研究限制

MVP 2 只验证了引擎与单一、预先定义的策略逻辑；进入 MVP 3 前，以下数据与研究限制仍需解决（完整清单见 [`docs/backtest_limitations.md`](docs/backtest_limitations.md) 与 `.github/mvp2.md` 前置条件）：

- **独立保留期数据集**：至少准备一份独立保留期数据集，并将训练、验证和测试区间的边界在策略配置中冻结。
- **覆盖范围量化**：对历史股票池、财报可得日期、企业行动条款、交易日历和 FX 数据的覆盖范围进行量化，并为关键缺口制定处理策略。
- **不得反复调参宣称有效**：不能以同一段历史数据反复调参后宣称策略有效。
- **留给 MVP 3 独立完成**：任何参数搜索、滚动窗口、压力测试和稳定性结论必须由 MVP 3 独立完成。

#### 进入 MVP 4（模拟盘闭环）前所需的条件

MVP 3 只提供研究级验证结论（`QUALIFIED`/`NOT_QUALIFIED`/`INCONCLUSIVE`），验证路径不创建模拟盘或券商订单（SP 3.87）；进入 MVP 4 前，以下条件必须满足（完整清单见 `.github/mvp3.md` 前置条件）：

- **模拟盘闭环（paper trading）**：独立的模拟盘执行环境，信号→订单映射、订单与成交对账可审计、可重放；MVP 4 只能产生本地模拟盘订单与对账记录，券商凭据、外部下单和自动实盘交易仍不属于该阶段。
- **风控审批（risk approval）**：日熔断/月熔断机制与风控审批流程验证通过；拟进入模拟盘的策略、参数、市场范围、调仓频率、风险限额和停止条件已版本化，任何变更须重新完成受影响的样本外验证。
- **实盘差异验证（live diff validation）**：模拟盘成交（点差、滑点、成交价、执行延迟）与 OOS 研究假设的差异量化并对照；只有长期模拟盘稳定通过差异验证后，才进入 MVP 6 实盘评估的单独评审。
- **结论不构成收益承诺**：`INCONCLUSIVE`/`NOT_QUALIFIED` 不得绕过进入模拟盘；`QUALIFIED` 仅表示样本外证据充分，不构成投资建议，也不表示未来收益或回撤（SP 3.64 / 3.87）。

#### 进入 MVP 5（前端开发）前所需的条件

MVP 5 是**只读可视化**阶段，不改变任何研究或风控结论；开工前需满足（完整清单见 [`.github/mvp5.md`](.github/mvp5.md)）：

- **数据已就绪**：回测（MVP 2）、样本外验证（MVP 3）与模拟盘（MVP 4）的结果均已落库，看板口径必须与 CLI 报告同源（复用 storage/领域逻辑，不重算、不近似）。
- **只读边界**：API 默认不提供下单/审批/冻结等写操作端点，不持有券商凭据；看板在任何情况下都不得产生订单或修改运行状态。
- **不改变准入**：看板只展示事实，不提高自动化程度，也不改变进入 MVP 6 的门槛。
- **研究纪律可见**：`INCONCLUSIVE`／覆盖不足／未解决对账差异／超阈值告警必须显著呈现，不得被图表或均值掩盖。

#### 运行监控看板（MVP 5 阶段 1 已交付部分）

阶段 1（SP 5.1–5.12）交付了只读 API 层与前端脚手架，可本地跑通。启动方式：

```bash
# 1) 启动只读 API（另一个终端）
set -a && source .env && set +a
export HARBOR_API_TOKEN=dev-read-token        # 只读令牌；未设置时命令会直接报错并 exit 2
.venv/bin/harbor-cli api serve --port 8000    # http://127.0.0.1:8000/health

# 2) 启动看板（另一个终端）
cd frontend && cp .env.example .env.local     # 填入 VITE_HARBOR_API_TOKEN
npm install && npm run dev                    # http://localhost:5173
```

> 仅本地免认证调试时才需要显式选择 `HARBOR_API_ALLOW_UNAUTHENTICATED=1`；默认必须提供令牌（SP 5.4）。

- **只读边界**：API 只注册 `GET` 路由（由 `tests/test_api_contract.py` 断言），`/health` 是唯一免认证端点，其余 `/api/v1/*` 均需 token。
- **部分完成**：阶段 1 只接入**回测运行列表**（表格 + 当前页状态分布图）；验证 / 模拟盘 / 数据质量看板属阶段 3–5。
- 详见 [`frontend/README.md`](frontend/README.md)。

#### 进入 MVP 6（实盘评估）前所需的模拟盘条件

MVP 4 只提供本地模拟盘执行与对账记录，模拟盘路径不创建券商凭据、不下真实订单（SP 4.95）；进入 MVP 6 实盘评估前，以下条件必须满足（完整清单见 `.github/mvp4.md` 前置条件）：

- **长期稳定运行**：模拟盘已连续稳定运行至少 12 个月，每个启用市场完成至少 4 次完整调仓与 30 笔成交，日/月熔断与对账无未解决差异；任何一次熔断恢复均已完成独立复盘并归档。少于上述样本的豁免必须由独立审批人记录理由与风险接受决定（SP 4.77）。
- **实盘差异验证通过**：模拟盘成交（点差、滑点、成交价与执行延迟）与 OOS 研究假设的差异均在预注册阈值内，覆盖不足与告警已处理，且不得以汇总均值掩盖单一市场或调仓周期的超限（SP 4.71–4.74 / 4.77）。
- **实盘差异风险已量化并确认可控**：券商凭据、人工审批与应急流程仅属于 MVP 6，且 MVP 6 需在长期模拟盘通过后单独评审、初期采用人工确认模式。
- **策略/参数/范围变更重新验证**：任何进入模拟盘的策略、参数、市场范围、调仓频率、风险限额或停止条件变更，都必须重新完成受影响的样本外验证（MVP 3）。
- **结论不构成收益承诺**：`QUALIFIED` 仅表示样本外证据充分，不构成投资建议，也不表示未来收益或回撤；模拟盘或实盘表现均不构成收益承诺。

---

## ⚠️ 已知限制

- **数据源依赖**：`yfinance` / `akshare` 需要外网访问，且免费接口存在限流，可能影响大批量采集的时效性；`mock` 数据源仅用于开发与测试，不含真实行情。
- **交易日历简化**：复权因子、缺口检查等按“周一到周五”近似交易日，尚未纳入交易所休市日历（如港股与美股各自的节假日），长假后可能出现非真实的“缺口”告警。
- **企业行动条款**：复权与权益计算依赖事件条款（`ratio`/`price`）；部分数据源（如 `mock`）不提供条款，缺失条款的事件会进入复核队列（JSON 报告）而非被静默忽略。
- **权益计算模型**：使用持仓快照日期与登记日（`record_date`）判断资格，未建模同一快照区间内的买卖变动，可能与券商实际到账存在差异。
- **实盘/高频**：MVP 1 仅覆盖数据采集与质量基础，不含实盘交易、高频策略或收益保证；回测结果不代表未来表现。

---

## 🔧 故障排查

| 现象 | 可能原因 | 处理方式 |
| :--- | :--- | :--- |
| `harbor-cli fetch ...` 数据库连接失败 | `DATABASE_URL` 端口或凭据不正确；Postgres 未启动 | 确认 `docker compose up -d postgres`；核对 `.env` 中 `DATABASE_URL` 与 `POSTGRES_PORT` 一致 |
| 提示“表不存在” | 尚未执行数据库迁移 | 运行 `alembic upgrade head` |
| `alembic upgrade head` 报 `value too long for character varying(32)` | 迁移版本号超过 32 字符 | 保持迁移 `revision` 长度 ≤ 32；当前迁移链已满足 |
| 采集报 `raw_payloads` 外键错误 | 未先创建 `ingestion_runs` 记录 | 使用 `harbor-cli fetch`（内部会先创建 run），避免直接调用 ingestor |
| 写入后查询为空 | 连接未提交 | 确保使用事务（`engine.begin()`）或执行完整 CLI 命令后再查询 |
| `quality report` 无输出 | 该市场尚无 `quality_issues` 记录 | 先运行一次采集/质量检查，再查看报告 |
| 质量报告显示大量缺口 | 数据源覆盖不全或长假 | 结合“已知限制”中的交易日历简化说明判断，必要时扩大数据范围 |
| 找不到 `harbor-cli` | 未安装项目或未激活虚拟环境 | 执行 `pip install -e .` 后使用 `.venv/bin/harbor-cli` |
| `Command 'alembic' not found` | 虚拟环境未激活 | 先 `source .venv/bin/activate`（或直接使用 `.venv/bin/alembic`） |
| alembic 提示 `Set DATABASE_URL` | alembic 不读取 `.env` 文件 | 先执行 `set -a && source .env && set +a`，再运行迁移 |
| `connection refused` / `Is the server running...` | Postgres 容器未启动或端口不一致 | 运行 `docker compose up -d postgres`；核对 `.env` 中 `POSTGRES_PORT` 与 `DATABASE_URL` 一致 |

---

## 🤝 贡献

这是一个个人项目，欢迎交流与建议。

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/amazing-feature`)
3. 提交改动 (`git commit -m 'Add some amazing feature'`)
4. 推送分支 (`git push origin feature/amazing-feature`)
5. 提交 Pull Request

---

## 📜 许可证

MIT License

---

## ⚠️ 免责声明

本项目**仅用于软件开发和量化研究，不构成投资建议**。本系统不承诺固定年化收益或最大回撤。历史回测结果不代表未来表现。投资有风险，使用者应自行承担决策与交易责任。

**特别提示**：港股与美股市场的数据覆盖、退市记录、企业行动披露规则及交易成本存在显著差异，回测与实盘前需分别验证各数据源在每个市场的适用性。美股全市场数据量远大于港股，建议在回测前评估数据存储与计算容量是否满足需求。

系统作者和贡献者对使用本软件所产生的任何直接或间接损失概不负责。实盘使用前，使用者应：

1. 完成充分的样本外测试和模拟盘验证
2. 了解策略逻辑、风险参数及其局限性
3. 确认已理解停牌、跳空、流动性不足等市场风险
4. 仅投入可承受完全损失的资金

---

<div align="center">
  <sub>Built with ❤️ for the Hong Kong and U.S. stock markets</sub>
</div>

---
