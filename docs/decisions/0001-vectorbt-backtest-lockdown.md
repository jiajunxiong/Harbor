# ADR-0001：回测依赖锁定与 VectorBT 适用边界（SP 2.1）

- **状态**：已接受（Accepted）
- **日期**：2026-08-05
- **对应**：MVP 2 / SP 2.1（回测依赖与锁定策略）

## 背景

MVP 2 需要引入研究/回测计算栈。MVP 1 仅使用数据库、Pydantic 与 SQLAlchemy，不包含任何数值计算依赖。
SP 2.1 要求：新增 `numpy`、`pandas`；评估并锁定 VectorBT 的版本与适用边界；所有相关依赖给出明确版本区间。

## 决策

### 1. 新增核心依赖（带明确版本区间）

| 包 | 锁定区间 | 说明 |
| :-- | :-- | :-- |
| `numpy` | `>=2.4.6,<3` | 数值数组；下界与 vectorbt 1.1.0 的约束一致 |
| `pandas` | `>=3.0.3,<4` | 面板/因子数据；与 vectorbt 1.1.0 的约束一致 |
| `packaging` | `>=24,<27` | 对已安装版本做 PEP 440 区间校验（本锁定的自检依赖） |

### 2. VectorBT 作为可选依赖锁定

VectorBT 仅回测引擎需要，数据管道（MVP 1 与后续数据维护）不需要，因此放入 `backtest` 可选依赖，避免
向基础镜像引入 matplotlib / scipy / scikit-learn 等重型传递依赖：

```toml
[project.optional-dependencies]
backtest = [
    "numba>=0.66,<0.67",
    "vectorbt>=1.1.0,<2",
]
```

- **锁定版本**：`vectorbt 1.1.0`（2026-07-05 发布；`requires-python <3.15,>=3.11`，与 Harbor
  `>=3.11` 兼容）。
- **主要传递依赖**：`numpy>=2.4.6`、`pandas>=3.0.3,<4`、`numba>=0.66`，以及 scipy、matplotlib、plotly、
  ipywidgets、scikit-learn 等。
- **已验证环境**（2026-08-05，Python 3.12.3）：`numpy 2.4.6`、`pandas 3.0.5`、`numba 0.66.0`、
  `vectorbt 1.1.0`。`numba 0.66` 把 `numpy` 限制在 `<2.5`，故实际解析为 `2.4.6`；`pandas` 保持 `3.0.5`。
  已用最小 `Portfolio.from_signals` 组合回测验证通过。

### 3. 锁定执行机制

- `pyproject.toml` 中的区间是依赖的唯一事实来源。
- `src/harbor/dependencies.py` 以同一组区间校验实际安装的版本（`verify_lock()`）。
- `tests/test_backtest_dependencies.py` 强制该校验：缺失必需包、版本越界都会失败，防止静默升级或降级。

## VectorBT 评估与适用边界

### 版本选择理由

- 当前最新稳定版为 `1.1.0`，支持 Python 3.11–3.14。
- 许可为 **Apache 2.0 + Commons Clause**：可用于研究/内部使用，但不可作为以该软件为主体的商业产品
  售卖。Harbor 属研究用途，可接受，特此记录。
- 与已锁定的 `numpy`/`pandas`/`numba` 组合实测可用（见上）。

### 适用边界（用在哪、不用在哪）

| 场景 | 结论 |
| :-- | :-- |
| 向量化因子研究、跨多标的快速筛选、参数网格的首轮扫描 | ✅ 使用 VectorBT |
| 单市场/跨市场组合指标（收益、波动、回撤、Sharpe、Calmar） | ✅ 使用 VectorBT 输出做交叉验证 |
| 点时（point-in-time）数据可用性、财报披露日对齐 | ❌ 需 Harbor core 自建（SP 2.9） |
| 无幸存者偏差的股票池（含退市标的） | ❌ 需 Harbor core 自建（SP 2.10） |
| 港股手数、印花税、交易费、最低佣金等成本 | ❌ 需 Harbor 自建成本模型（SP 2.37） |
| 多币种现金账本与 FX 换算 | ❌ 需 Harbor 自建（SP 2.42） |
| 市场专属企业行动（供股、并购、分拆） | ❌ 复用 MVP 1 事件映射，Harbor 自建（SP 2.44） |
| 严格可复现（配置哈希、数据截点、代码版本） | ❌ 需 Harbor 编排层保证（SP 2.5 / 2.48） |

### 容量边界（待验证）

README 提出需验证美股全市场 7,000+ 只 × 10 年以上日线在内存中的容量。VectorBT 采用全量内存数组
向量化，日线低频数据规模通常可行，但需在 SP 2.84 记录时间与内存基线；若容量不足，回测读取层可分批或
按市场切分，VectorBT 仅用于单批次因子研究，组合层仍由 Harbor core 负责。

## 后果

- **正面**：研究代码可直接使用 `pandas`/`numpy`；VectorBT 提供快速首轮研究；依赖版本被显式锁定并可被
  测试自动校验。
- **约束**：`numba 0.66` 将 `numpy` 限制在 `<2.5`；VectorBT 引入较大传递依赖，故作为可选依赖以保持数据
  管道镜像精简（Dockerfile 仅 `pip install .`，不装 `[backtest]`）。
- **风险**：VectorBT 不保证点时数据、幸存者偏差、港股成本与市场专属企业行动的建模。MVP 2 的核心正确性
  逻辑必须落在 Harbor 自建代码上，不能以 VectorBT 的默认行为替代（见 `.github/mvp2.md` 的正确性优先级）。
