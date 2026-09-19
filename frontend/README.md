# Harbor 监控看板（MVP 5）

在 MVP 1–4 已落库的数据之上，提供**只读**的可视化审计入口：回测运行、样本外验证结论、模拟盘状态与数据质量。

> 本看板是研究工具，只呈现已落库事实；**不构成投资建议，不表示未来收益或回撤**。看板不提供下单、审批、冻结等任何写操作，也不持有券商凭据。

## 当前进度

MVP 5 阶段 1（API 层与前端治理基础，SP 5.1–5.12）已完成：

| SP | 内容 | 位置 |
| :--- | :--- | :--- |
| 5.1–5.8 | 只读 API：骨架、版本化契约、只读边界、认证、脱敏、分页、错误契约、只读查询层 | `../src/harbor/api/` |
| 5.1 | 可运行入口：`harbor-cli api serve`（`../src/harbor/api/server.py`） | 本文件下方「本地启动」 |
| 5.9 | 前端脚手架：Vite + React + TypeScript(strict) + ECharts + TanStack Query | 本目录 |
| 5.10 | 设计系统与主题：设计令牌、组件规范、免责声明组件、明暗主题 | `src/theme/`、`src/components/` |
| 5.11 | API 契约测试 | `../tests/test_api_contract.py` |
| 5.12 | 最小看板冒烟：回测运行列表 → 表格 + 图表 | `src/features/backtests/` |

阶段 2 起（回测看板、样本外验证看板、模拟盘看板、数据质量与告警、交付验收）尚未开始。

## 环境要求

- Node.js ≥ 24
- 一个运行中的 Harbor API（用 `harbor-cli api serve` 启动，见下）
- 已迁移且已落库的数据库（看板只读，不会写入）

## 本地启动

```bash
# 1) 启动只读 API（在仓库根目录）
set -a && source .env && set +a                 # 提供 DATABASE_URL
export HARBOR_API_TOKEN=dev-read-token          # 只读令牌；不要使用 ops 令牌
.venv/bin/harbor-cli api serve --port 8000

# 未配置 HARBOR_API_TOKEN 时命令会直接报错并 exit 2（SP 5.4 不允许静默免认证）。
# 仅本地免认证调试需显式选择：export HARBOR_API_ALLOW_UNAUTHENTICATED=1

# 2) 启动看板（另开一个终端）
cd frontend
cp .env.example .env.local                       # 填入 VITE_HARBOR_API_TOKEN
npm install
npm run dev                                      # http://localhost:5173
```

开发态由 Vite 将 `/api` 与 `/health` 代理到 `HARBOR_API_TARGET`（默认 `http://127.0.0.1:8000`），因此前端使用的请求路径与生产完全一致。

## 配置

| 变量 | 作用 |
| :--- | :--- |
| `VITE_HARBOR_API_TOKEN` | 只读 bearer 令牌，必须与 API 的 `HARBOR_API_TOKEN` 一致 |
| `VITE_HARBOR_API_BASE_URL` | API 源前缀；留空表示同源（开发态走代理） |
| `HARBOR_API_TARGET` | 仅开发态：Vite 代理的后端地址 |

> `VITE_*` 变量会随构建产物下发到浏览器。**只能**使用只读令牌；运维（ops）令牌绝不可前端化。

## 可用脚本

| 命令 | 说明 |
| :--- | :--- |
| `npm run dev` | 开发服务器 |
| `npm run build` | `tsc --noEmit` + 生产构建（输出 `dist/`） |
| `npm run lint` | ESLint |
| `npm run typecheck` | TypeScript 严格模式检查 |
| `npm test` | Vitest（jsdom）单元与端到端冒烟测试 |

以上五项均在 CI 的 `frontend` job 中执行（`.github/workflows/quality.yml`）。

## 目录结构

```text
src/
  api/            # HTTP 客户端、端点函数、TanStack Query 策略、契约类型、错误描述
  components/     # 设计系统组件：表格、徽章、状态、图表封装、免责声明、主题切换
  features/       # 按看板划分的特性模块（backtests/…）
  theme/          # 设计令牌（CSS）、主题上下文、图表调色板
```

## 只读边界（不可放宽）

- `src/api/client.ts` 只提供 GET，且从不发送请求体，因此页面在**类型层面**无法变更服务端状态。
- 契约类型（`src/api/types.ts`）是 `src/harbor/api/schemas.py` 的前端映射；契约变更须同时评审两侧。
- `src/App.test.tsx` 断言所有请求均为无请求体的 GET。

## 已知限制

- 图表包（ECharts）约 500 kB，已拆分为独立 chunk；应用外壳约 50 kB。
- 阶段 1 仅接入回测运行列表；验证、模拟盘与数据质量页在阶段 3–5 接入。
- 未配置 token 时页面会显示 `missing_credentials` 错误态（含服务端请求 ID），这是预期行为而非缺陷。
