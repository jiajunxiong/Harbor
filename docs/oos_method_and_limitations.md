# 样本外方法与限制说明 (Out-of-Sample Method and Limitation Documentation)

> **适用版本**：Harbor MVP 3 · SP 3.73 · 2026-08-27
> **依赖**：SP 3.58（稳定性规则 / 结论等级）、SP 3.68（OOS HTML 研究报告）

本文档说明 Harbor 样本外（out-of-sample, OOS）验证方法如何运行、其关键口径与
**研究限制**，以及系统如何**如实呈现**每个限制（拒绝 / 降级 / 标注），绝不虚构或
静默修复。核心原则：**验证输出仅用于研究，不构成投资建议，也不表示未来收益或
回撤**（SP 3.68 报告中的显著研究横幅同样强调这一原则）。

---

## 1. 切分规则 (Split Rules)

**规则**：训练 / 验证 / 测试区间必须在配置冻结时显式给出，且严格有序
（SP 3.4）：

```text
train_end < validation_start <= validation_end < test_start
```

- 每个区间非空（`start <= end`，允许单日验证期），相邻区间**不得重叠、不得相切**。
- 数据截点（`data_cutoff`）必须不早于测试期末（`test_end <= data_cutoff`），
  冻结后的数据在验证实验中不可再变（SP 3.6 数据清单 / SP 3.7 数据指纹）。
- 冻结切分由 SP 3.3 的 `config_hash`（SHA-256 over 规范化 JSON）固化：相同配置
  （含 YAML/JSON 两种格式）恒得到相同哈希；切分边界、市场、基准币种与策略版本
  均进入该哈希。
- 滚动窗口（SP 3.31）在冻结切分之上生成逐折的 train / validation / test 边界，
  训练窗口可扩展（expanding）或固定长度（fixed），每折测试区间互不重叠且相邻。

**限制与呈现**：边界违规（反转 / 重叠 / 相切 / 空区间）在配置加载时即被拒绝
（`SplitBoundaryError`），不会静默调整；滚动窗口与日历对齐（SP 3.32）把边界
对齐到可交易日，并记录每个市场的实际日期。

## 2. 测试集一次性访问 (Test-Set One-Time Access)

**规则**：独立保留集（测试集）**只能在最终评估阶段解锁一次**，且解锁前**任何**
数据读取、指标计算、报告预览或参数比较都不得触碰测试区间（SP 3.24 测试集访问
守卫）；参数选择**永远**不使用测试集（SP 3.21 预注册选择规则 / SP 3.24）。

- 测试集在 `TEST_LOCKED` 之前不可读：`AccessGuard` 拒绝访问并记录可审计事件
  （`AccessAuditEntry`，UTC 时间戳 + 拒绝原因）。
- 最终评估通过 `mark_first_read`（SP 3.5 保留集登记 / SP 3.41 最终保留执行）
  **恰好解锁一次**；第二次读取被拒绝（“already read”）。
- **测试后调整政策**（SP 3.42 再访问政策）：策略 / 参数 / 数据 / 代码任一实质
  变化都要求**新的测试集版本 + 新的验证运行**，不得沿用已定稿的保留集结论。
- 每次访问（含被拒绝的访问）都进入审计轨迹并计入报告。

**限制与呈现**：任何测试后改动若想复用已定稿测试集，会被 `require_test_reaccess_compliance`
（SP 3.42）明确拒绝，并提示需要新的测试集版本；未授权访问在 `TEST_LOCKED` 前
被 `AccessGuard.require` 以 `AccessGuardError` 拒绝。

## 3. 数据覆盖口径 (Data Coverage Criteria)

**口径**：覆盖评分（SP 3.9）按**市场**量化 7 类数据的覆盖率与缺口：
价格（PRICES）、历史股票池（STOCK_POOL）、财报（FUNDAMENTALS）、企业行动条款
（CORPORATE_ACTIONS）、交易日历（CALENDAR）、汇率（FX）与基准（BENCHMARK）。

- 覆盖率 = 覆盖天数 / 窗口天数（有界的查询范围）或按“是否冻结在清单中”判定；
  缺失的度量项按 **0% + 缺口** 处理，**绝不静默当作完整**。
- 覆盖门槛（SP 3.10 `CoverageThresholdConfig`）默认：
  `min_price_coverage_pct=95`、`min_stock_pool_coverage_pct=90`、
  `min_fundamental_coverage_pct=70`；且三个阻断标志默认开启：
  - `fx_required=True`：**缺失 FX 时**结论降级为 `NOT_QUALIFIED`（或按配置为
    error），绝不默认 1:1（SP 2.12）。
  - `historical_stock_pool_required=True`：**历史成分未知 / 不完整时**不得
    `QUALIFIED`（幸存者偏差风险，SP 2.10）。
  - `action_terms_required=True`：**企业行动条款缺失时**不得 `QUALIFIED`。

**限制与呈现**：低于价格门槛 → ERROR（阻断运行）；低于财报门槛 → WARNING；
FX / 股票池 / 企业行动缺口 → 按配置 `NOT_QUALIFIED` 或 WARNING；日历 / 基准缺口
→ WARNING。缺口通过 `CoverageGateResult`（`.blocked` / `.not_qualified_items`）
显式暴露，并在报告中列出覆盖评分与缺口（SP 3.68）。

## 4. 压力假设 (Stress Assumptions)

**假设**：压力情景在参数搜索前**预注册**（SP 3.51–3.57），每类情景显式记录其
**研究假设与参数**：

- 成本压力（COST）：`cost_multiplier`（成本倍数，默认 2.0）。
- 流动性压力（LIQUIDITY）：`slippage_bps`（滑点基点）与 `participation_rate`
  （成交参与率）。
- FX 压力（FX）：`fx_shift_bps`（汇率冲击基点，可负）。
- 日历 / 企业行动 / 股票池 / 参数邻域（CALENDAR / CORPORATE_ACTION /
  STOCK_POOL / PARAMETER_NEIGHBORHOOD）各有其假设与参数。

**登记与限制**：每个压力情景必须登记为 SP 3.59 的 `StressScenarioRegistration`
（类别 / scenario_id / 适用市场 / **假设** / 参数 / 运行指纹 / 与基线的差异），
且**未登记情景不得进入结论**（SP 3.59 `require_scenarios_registered` 拒绝并点名
缺失情景）。压力损失通过稳定性规则参与结论等级（见下节）；无法量化的情景记录
差异摘要，绝不静默丢弃。

## 5. INCONCLUSIVE 含义 (Meaning of INCONCLUSIVE)

结论等级 `OOSConclusion` 只允许三种取值（SP 3.58 稳定性规则）：

| 结论 | 含义 |
| :--- | :--- |
| `QUALIFIED` | **全部**证据维度 PASS（折叠离散度 / 参数邻域 / 环境分段 / 压力损失 / 覆盖门槛均满足预注册阈值） |
| `NOT_QUALIFIED` | **任一**证据维度 FAIL（折叠失败、参数悬崖、环境不足、压力损失超限、覆盖门槛阻断）——失败优先，**不会被缺失证据掩盖** |
| `INCONCLUSIVE` | 无任何 FAIL，但**至少一个维度的证据缺失 / 不足**（如折叠未执行、折叠离散度不可得、压力损失未量化、邻域证据缺失） |

**关键含义**：`INCONCLUSIVE` **不是通过**（`≠ QUALIFIED`），也不是失败
（`≠ NOT_QUALIFIED`）——它表示**证据不足以做出资格判断**。缺失证据的维度按
`INSUFFICIENT` 处理，**绝不静默视为通过**；只有全部维度 PASS 才允许 `QUALIFIED`。
`INCONCLUSIVE` 要求补充证据后重新评估，而非当作通过记录。

## 6. 报告呈现 (Report Presentation)

SP 3.68 OOS HTML 研究报告以**显著研究横幅**（`.research` banner）开头：

> 本报告仅用于研究，不构成投资建议，也不表示未来收益或回撤 (research only;
> not investment advice; no promise of future returns).

报告包含：切分图（训练 / 验证 / 测试）、OOS 净值、折叠离散度、环境 / 压力表现、
覆盖评分、限制与结论；结论小节展示 `overall`（`QUALIFIED` / `NOT_QUALIFIED` /
`INCONCLUSIVE`）、测试集版本、数据集指纹、代码版本与结论指纹，并再次注明
“no promise of future returns”。所有输出均不含收益或回撤保证。

---

> 本说明与《回测默认参数说明》（SP 2.73）、《回测限制说明》（SP 2.74）配套：
> 前者说明默认参数“默认值是什么”，本文档说明样本外方法“如何运行、口径是什么、
> 受哪些限制、系统如何如实呈现”。所有验证输出统一遵循同一原则——**仅用于研究，
> 不构成投资建议，也不表示未来收益或回撤**。
