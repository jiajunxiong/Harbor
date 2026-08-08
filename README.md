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
| **MVP 2：研究回测** | 可配置的选股、组合和回测引擎 | 可重复运行；成本、停牌和企业行动纳入结果；输出完整绩效指标 |
| **MVP 3：样本外验证** | 滚动回测、压力测试和参数稳定性报告 | 明确训练/验证区间并输出风险指标；确认策略在不同市场环境下表现稳定 |
| **MVP 4：模拟盘闭环** | 信号、风控审批、订单和成交对账 | 模拟盘执行与策略记录可审计、可重放；日熔断/月熔断机制验证通过 |
| **MVP 5：实盘评估** | 券商适配、人工审批与应急流程 | 仅在长期模拟盘通过后单独评审；初期采用人工确认模式 |

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
│   ├── api/                     # FastAPI 路由
│   └── scheduler/               # 定时任务配置
├── backtest/                    # 回测脚本与Notebook
├── frontend/                    # React 监控面板（MVP 4+）
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
harbor-cli fetch securities --market HK
harbor-cli fetch securities --market US

# 采集单只标的的日线
harbor-cli fetch daily --market HK --symbol 0700.HK
harbor-cli fetch daily --market US --symbol AAPL
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

## 📊 开发状态

| MVP 阶段 | 状态 | 预计完成 |
| :--- | :--- | :--- |
| MVP 1：数据基础 | ✅ 已完成 | 2026年8月 |
| MVP 2：研究回测 | 📋 进行中 | — |
| MVP 3：样本外验证 | 📋 规划中 | — |
| MVP 4：模拟盘闭环 | 📋 规划中 | — |
| MVP 5：实盘评估 | 📋 规划中 | — |

> MVP 1（数据基础）已完成：港股与美股数据的采集、标准化、质量校验、复权因子与权益计算均已落地并通过自动化测试。MVP 1 尚未提供可用于实盘交易的策略或基础设施。

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
