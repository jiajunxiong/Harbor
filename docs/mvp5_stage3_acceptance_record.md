# MVP 5 阶段 3 验收记录：样本外验证看板（SP 5.26–5.36）

> 本文件固化 MVP 5 阶段 3（样本外验证看板，11 SP）的交付范围、验收命令、实测数据与
> **部分交付清单**，与 [`mvp3_acceptance_record.md`](mvp3_acceptance_record.md) 配套阅读。
>
> 看板只呈现已落库的事实：不新增交易能力、不写入任何表、不重算任何数字。样本外验证
> 输出仅用于研究，不构成投资建议，也不表示未来收益或回撤。

## 交付方式（本轮范围决策）

阶段 3 的 11 个 SP 中，有 5 个依赖 `validation_trials` / `validation_folds` /
`validation_stress_results` / `validation_conclusions` 四张表的数据，而**这四张表的写入者
在代码库中没有任何调用方**（详见「已知限制」）。因此本轮采用**折中方案**：

1. **补齐可验证的一半**：把冻结时的数据清单、覆盖测量、门槛判定与生命周期事件**真正落库**，
   让看板展示的是实测结果而不是推断；
2. **如实记录另一半**：参数试验、压力情景、折叠 OOS 与结论当前**没有数据源**，看板按
   「未产生 + 原因 + 缺失的写入者」呈现，**不显示为 0**，并在本文件中记为部分交付；
3. 不为了让看板"看起来完整"而合成数据、不修改状态机、不越过 CLI 的审计路径。

本轮新增了数据库表 `validation_events`（迁移 `0025_create_validation_events`）：阶段 3 的
「冻结时间」与「审计事件」在落库层面**原本无表可存**，运行行只保留最后一次 `updated_at`，
冻结时刻无从追溯。创建事件与冻结事件因此各写一行，`from_status` 对创建事件为 `NULL`
（该运行没有前置状态，写成 `DRAFT → DRAFT` 会是一条编造的状态迁移）。

## 验收命令（Commands）

```bash
# 迁移到最新 schema（含验证运行/清单/切分/警告/事件表，SP 3.12）
alembic upgrade head

# 创建 DRAFT 验证运行并冻结数据集：冻结会写入 SP 3.6 清单、SP 3.10 门槛警告与事件
harbor-cli validation run --config examples/configs/validation/hk_validation.yaml
harbor-cli validation freeze <run-id>
harbor-cli validation show <run-id>          # 状态、切分、指纹、各表计数
harbor-cli validation report <run-id> --format json

# 只读 API（SP 5.26–5.34）：与 CLI 同源，导出用同一渲染器
harbor-cli api serve --port 8010
curl -s -H "Authorization: Bearer $HARBOR_API_TOKEN" http://127.0.0.1:8010/api/v1/validations
curl -s -H "Authorization: Bearer $HARBOR_API_TOKEN" http://127.0.0.1:8010/api/v1/validations/<run-id>/split
curl -s -H "Authorization: Bearer $HARBOR_API_TOKEN" http://127.0.0.1:8010/api/v1/validations/<run-id>/coverage
curl -s -H "Authorization: Bearer $HARBOR_API_TOKEN" http://127.0.0.1:8010/api/v1/validations/<run-id>/warnings
curl -s -H "Authorization: Bearer $HARBOR_API_TOKEN" http://127.0.0.1:8010/api/v1/validations/<run-id>/events
curl -s -H "Authorization: Bearer $HARBOR_API_TOKEN" http://127.0.0.1:8010/api/v1/validations/<run-id>/report?format=csv

# 前端（Vite + React，只读）
cd frontend && npm ci && npm run dev      # 深链接：#/validations 与 #/validations/<run-id>?tab=<tab>
```

## 实测数据（验收运行）

以 `hk_validation.yaml` 冻结的运行 `3c1e855a71c44cd2a8274046acc232dd` 为例（运行 ID 每次
新建都会变化，其余数字由数据决定）：

| 项目 | 实测值 |
| :--- | :--- |
| 数据集指纹（SP 3.7） | `3cf9bf2ac678e92b9b9671c47a5de2cecc632f3c1a56c2cebe8036d97eb7a6a8` |
| 冻结切分哈希（SP 5.28） | `f2ff8cf32b42a436f260a892d9e88218af6103468e95bc68eadc86155d61fbaa` |
| 切分边界 | 训练 2019-01-01→2020-12-31 · 验证 2021-01-01→2021-12-31 · 测试 2022-01-01→2022-12-31 |
| 审计事件（SP 5.26/5.33） | `NULL → DRAFT`（创建）· `DRAFT → DATA_FROZEN`（冻结时间取此事件） |
| 市场 | HK（基准币种 HKD，窗口内无需 FX 换算） |
| 覆盖：行情（SP 5.30） | **74.92%**（741 / 989 交易日，缺口 248 天）→ 门槛 **error**（阻断） |
| 覆盖：历史股票池 | **100.00%**（89 / 89）→ 通过，无缺口文本 |
| 覆盖：财报 | **0.00%**（0 / 89）→ 门槛 **warning** |
| 覆盖：企业行动 | **0.00%**（0 / 1，窗口内无记录）→ 门槛 **not_qualified** |
| 覆盖：交易日历 | 1 / 1（携带示例性节假日清单的 caveat）→ **warning** |
| 覆盖：汇率 | 1 / 1（本市场无需换算）→ 通过 |
| 覆盖：基准 | 0 / 1 → **warning**（无基准数据表，超额表现不可计算） |
| 覆盖警告（SP 5.33） | 5 条：1 error + 4 warning（含 1 条门槛档位为 not_qualified） |
| 数据漂移校验 | 冻结指纹 == 当前实测指纹，`fingerprint_matches: true` |

覆盖率的分子/分母是**实测计数**而非配置声明：港股证券 93 个、窗口内股票池 89 个、其中 89 个
窗口内有行情、行情覆盖 741 个交易日；`financials` 表在该窗口内 0 行，因此财报覆盖为 0.00%
是事实而非渲染问题。以上数字已用 SQL 独立复核。

## 逐条验收（SP 5.26–5.36）

| SP | 任务 | 状态 | 交付内容与证据 |
| :---: | :--- | :---: | :--- |
| 5.26 | 验证运行列表/详情 | 完成 | 列表 `GET /validations` + 详情 `GET /validations/{id}`；状态机阶段、配置哈希、数据集指纹与**冻结时间**（取自事件日志，而非 `updated_at`）；前端 `#/validations` 与 `#/validations/<id>` |
| 5.27 | 结论展示 | **部分交付** | 结论（`QUALIFIED`/`NOT_QUALIFIED`/`INCONCLUSIVE`）在概览分区显著展示，**不与限制分离**，并强制携带反误读提示；但 `validation_conclusions` 无写入者，实测运行结论为空，页面显示「该运行暂无结论记录 + 原因」，**不显示为通过** |
| 5.28 | 冻结切分 | 完成 | `GET /validations/{id}/split`；训练/验证/测试三段区间与边界日期以时间轴可视化，并展示 `split_hash`；测试集已解锁时额外标注 |
| 5.29 | 参数试验 | **部分交付** | `GET /validations/{id}/trials` 返回试验表（参数/指标/失败原因/随机种子），但 `validation_trials` 无写入者 → `available:false` + 缺失写入者说明 |
| 5.30 | 覆盖评分与门槛 | 完成 | `GET /validations/{id}/coverage`：**分市场**的覆盖评分（分子/分母实测计数）、缺口文本与门槛判定；额外提供冻结指纹 vs 当前指纹的**漂移校验** |
| 5.31 | 压力情景 | **部分交付** | `GET /validations/{id}/stress` 返回预注册情景、假设与 `delta`，假设与差异**同表展示**；但 `validation_stress_results` 无写入者 → `available:false` + 说明 |
| 5.32 | 折叠 OOS | **部分交付** | `GET /validations/{id}/folds` 返回折叠区间与关联回测运行；但 `validation_folds` 无写入者 → `available:false` + 说明。滚动净值与最差折叠高亮因此无数据可画 |
| 5.33 | 结论证据与限制 | 完成 | 限制清单与证据字典随结论展示（无结论时说明原因）；`GET /validations/{id}/warnings`（门槛警告）与 `GET /validations/{id}/events`（审计事件，最早在前） |
| 5.34 | 报告导出 | 完成 | `GET /validations/{id}/report?format=json\|csv\|html`，由服务端复用 CLI 的 `report_validation` 渲染器，文件名经 `Content-Disposition` 安全化；格式清单由 `/api/v1/version` 声明 |
| 5.35 | 反误读提示 | 完成 | 由**服务端**随运行详情下发：①`INCONCLUSIVE` 不等于通过；②不得调参后重测即通过（测试集不参与调参）；③测试集只解锁一次（状态机不可回退）。另按状态追加「尚未产生结论」提示 |
| 5.36 | 验证看板测试 | 完成 | 后端：`tests/test_api_contract.py` 的 `Stage3ValidationTests`（11 项契约测试）、`tests/test_validation_dataset.py`（14 项免数据库单测）、`tests/test_validation_freeze.py`（7 项只写测试）、`tests/test_validation_repositories.py`（事件仓储）；前端：`validationDetail.test.tsx`（18 项）、`route.test.ts`（新增 7 项路由测试）、`format.test.ts`（3 项） |

## 已知限制与限制来源

1. **六张验证结果表没有写入者**。`upsert_manifest`、`upsert_conclusion`、`insert_trials`、
   `insert_folds`、`insert_stress_results`、`insert_warnings` 在 `src/` 中**只被定义、从未被调用**
   （只有各自的单元测试引用）。`advance_validation` 只推进状态机、不产生结果行。
   因此 5.29 / 5.31 / 5.32 与 5.27 的结论展示在数据层面没有来源。本轮为其中三张补齐了
   **清单 + 警告 + 事件**的写入（5.26/5.30/5.33 与 5.28 因此可验收），其余保持部分交付。
2. **覆盖百分比在请求时实测**（`source: "live_measurement"`）。冻结时落库的是成分区间与门槛
   警告，百分比本身没有持久化字段；看板因此实时调用 `build_profile`（与 `validation freeze`
   完全同一个函数）。代价是数字可能随数据变化，收益是**漂移可见**：两个指纹不一致即表示冻结
   之后数据被改动过。
3. **警告表只有两档 severity**（`CHECK (severity IN ('warning','error'))`），而门槛判定有三档。
   第三档 `not_qualified` 记录在 `context.severity` 中，页面按门槛原值展示并标注「落库档位」，
   避免两张表看起来互相矛盾。若需按三档检索，需要迁移并同步 `ValidationWarning` 的约束。
4. **基准超额表现不可计算**：没有基准数据表，`benchmark` 固定为 0 / 1 并说明原因。
5. **交易日历为示例性节假日清单**（SP 2.74），非交易所官方日历；该 caveat 作为覆盖缺口文本
   出现在每一次运行上，而不是只写在文档里。
6. **质量检查清单未纳入冻结清单**：`quality_issues` 没有时间戳，无法界定清单区间。
7. **验证运行会累积**：`validation run` / `freeze` 与只写型测试都会在开发库中追加运行行
   （append-only，不覆盖）。开发库中出现多行 `DATA_FROZEN` 属预期；清理需手工删除，
   看板不提供任何删除入口。
8. **测试集尚未解锁**：实测运行为 `DATA_FROZEN`，`test_set_id` 为空，因此所有相关提示都处于
   「尚未评测」状态；`TEST_LOCKED`/`EVALUATED` 路径仍由 CLI 与 API 契约测试覆盖。

## 测试与静态检查证据

| 检查 | 结果 |
| :--- | :--- |
| `ruff format --check .` | 515 文件已格式化 |
| `ruff check .` | 全部通过 |
| `mypy` | 213 个源文件无问题 |
| `pytest tests -q` | **5007 passed / 51 skipped**（跳过项需 `HARBOR_TEST_DATABASE_URL`） |
| 只写集成测试（`HARBOR_TEST_DATABASE_URL=$DATABASE_URL`） | `test_validation_freeze.py` 7 项通过 |
| Docker 冒烟（`test_docker_validation_smoke.py`） | 通过：新迁移在空库上从零建表并完成 freeze |
| 前端 `eslint` / `tsc --noEmit` | 通过 |
| 前端 `vitest run` | **176 passed**（14 文件） |
| 前端 `npm run build` | 通过（应用壳 133.72 kB / gzip 35.37 kB） |

> 注意：`tests/test_api_contract.py::AuthenticationTests::test_settings_refuse_to_default_to_unauthenticated`
> 依赖干净环境。若当前 shell 曾 `source .env`（导出了 `HARBOR_API_TOKEN`），该测试会失败；
> 用 `env -u HARBOR_API_TOKEN -u HARBOR_OPS_TOKEN pytest …` 复跑即可。

## 本轮修复的真实缺陷

1. **股票池区间反转**：分别夹逼区间两端，会把"全部上市日都在窗口之前"的数据集算成
   `2024-01-01 > 2000-01-03` 的反向区间（Docker 冒烟种子数据即如此），清单模型拒绝且
   `freeze` 失败。改为由**窗口内的股票池成员**推导区间：`[max(最早上市日, 窗口起点), 窗口终点]`；
   池为空则整个成分不记录，由 SP 3.9 记为完整缺口。
2. **空缺口文本被当成缺口**：`_gap(prefix, ())` 返回裸前缀，使 100% 覆盖的项自带一条缺口说明，
   进而产生幽灵警告（实测运行中 89/89 覆盖的股票池曾带「无行情数据」警告）。修正为空列表返回空串。
3. **覆盖率被放大 100 倍**：前端 `formatPercent` 接收的是**分数**（内部 ×100），而
   `coverage_pct` 已是百分数，直接复用会把 74.92% 渲染成 **7492.42%**。新增
   `formatPercentValue` 并加回归测试；`net_value_impact_pct` 同样改用该函数。
4. **证据字典被擅自加单位**：结论 `evidence` 是异构字段，把数字一律按百分比渲染会把
   Sharpe 0.9 显示为 90%。改为原值呈现，不猜测单位。
5. **冻结前失败会污染状态**：`freeze` 原本先写状态再落库清单，清单写入失败会让运行停在
   `DATA_FROZEN` 却没有任何数据。改为**先落库、后写状态**，失败时运行仍是 `DRAFT`，可重试。

## 反误读提示（前端显著呈现，服务端下发原文）

- 「测试集只解锁一次：评测后不得为调参再次解锁（状态机 DRAFT → DATA_FROZEN → TUNING →
  TEST_LOCKED → EVALUATED 不可回退）」
- 「调参后用同一切分重测得到的通过不算通过：参数试验必须在冻结切分上完成，超过预算的调参会使
  结论失效」
- 「`INCONCLUSIVE` 不等于通过：证据不足时结论为待定，需补足证据后重新评测」
- 「警告只记录未通过门槛的覆盖项；通过项不会写入警告行，因此『没有警告』不等于『覆盖良好』」
