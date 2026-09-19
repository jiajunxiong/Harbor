import { useCallback, useMemo } from "react";

import { useValidationRuns } from "../../api/hooks";
import type { ValidationRunSummary } from "../../api/types";
import { DEFAULT_PAGE_SIZE } from "../../app/route";
import { DataTable, type Column } from "../../components/DataTable";
import { EmptyState, ErrorState, LoadingState } from "../../components/States";
import { StatusBadge } from "../../components/StatusBadge";
import { paginationSummary } from "../backtests/paginationSummary";

export interface ValidationRunsPageProps {
  limit: number;
  offset: number;
  onSelectionChange: (limit: number, offset: number) => void;
  onOpenRun: (runId: string) => void;
}

const PAGE_SIZES = [10, 25, 50] as const;

/**
 * The validation run list (MVP 5 / SP 5.26).
 *
 * Deliberately thinner than the backtest list: a validation run has no strategy
 * or data-cutoff filters that would mean anything to a reader deciding which
 * out-of-sample run to inspect, and inventing them here would suggest a
 * filtering capability the API does not offer. What it does show is the status,
 * the frozen fingerprint and the artifact counts, because those are what tell a
 * reader whether a run is worth opening.
 */
export function ValidationRunsPage({
  limit,
  offset,
  onSelectionChange,
  onOpenRun,
}: ValidationRunsPageProps) {
  const query = useValidationRuns({ limit, offset });
  const runs = useMemo(() => query.data?.items ?? [], [query.data]);
  const total = query.data?.total ?? 0;
  const nextOffset = query.data?.next_offset ?? null;

  const changeSelection = useCallback(
    (nextLimit: number, nextOffset: number) => {
      onSelectionChange(nextLimit, nextOffset);
    },
    [onSelectionChange],
  );

  const columns: readonly Column<ValidationRunSummary>[] = useMemo(
    () => [
      {
        key: "run_id",
        header: "运行 ID",
        render: (run) => <span className="mono">{run.run_id}</span>,
      },
      {
        key: "status",
        header: "状态",
        render: (run) => <StatusBadge status={run.status} />,
      },
      {
        key: "code_version",
        header: "代码版本",
        render: (run) => <span className="mono">{run.code_version}</span>,
      },
      {
        key: "test_set_id",
        header: "测试集",
        render: (run) =>
          run.test_set_id === null ? (
            <span className="muted">未解锁</span>
          ) : (
            <span className="mono">{run.test_set_id}</span>
          ),
      },
      {
        key: "created_at",
        header: "创建时间",
        render: (run) => <time dateTime={run.created_at}>{run.created_at}</time>,
      },
      {
        key: "updated_at",
        header: "最后更新",
        render: (run) =>
          run.updated_at === null ? (
            <span className="muted">—</span>
          ) : (
            <time dateTime={run.updated_at}>{run.updated_at}</time>
          ),
      },
      {
        key: "actions",
        header: "操作",
        render: (run) => (
          <button
            type="button"
            className="link-button"
            data-testid={`open-validation-${run.run_id}`}
            onClick={() => {
              onOpenRun(run.run_id);
            }}
          >
            查看详情
          </button>
        ),
      },
    ],
    [onOpenRun],
  );

  const summary = paginationSummary(total, offset, runs.length);

  return (
    <>
      <section className="section-heading">
        <h1 className="section-heading__title">样本外验证</h1>
        <p className="section-heading__subtitle">
          只读展示验证运行的冻结切分、数据覆盖与门槛、审计事件与报告导出。验证运行由{" "}
          <span className="mono">harbor-cli validation run/freeze</span> 等命令创建；此处不提供任何写入入口。
        </p>
      </section>

      <div className="card">
        <div className="card__header">
          <span className="card__title">验证运行列表</span>
          <span className="card__hint">SP 5.26 · 按创建时间倒序</span>
        </div>
        <div className="card__body">
          {query.isPending ? <LoadingState label="正在读取验证运行列表…" /> : null}
          {query.isError ? (
            <ErrorState
              error={query.error}
              onRetry={() => {
                void query.refetch();
              }}
            />
          ) : null}
          {query.isSuccess && runs.length === 0 ? (
            <EmptyState
              title="暂无验证运行记录"
              hint={
                offset > 0 ? (
                  <>
                    当前页没有数据；请返回上一页或减小偏移量。这不代表历史为空。
                  </>
                ) : (
                  <>
                    先通过 CLI 创建并冻结一次验证运行（
                    <span className="mono">harbor-cli validation run --config …</span>
                    ），再回到本页面；此处不提供任何写入入口。
                  </>
                )
              }
            />
          ) : null}
          {runs.length > 0 ? (
            <DataTable
              columns={columns}
              rows={runs}
              rowKey={(run) => run.run_id}
              caption={`共 ${total} 次验证运行`}
            />
          ) : null}

          <div className="pagination">
            <span className="pagination__summary">{summary}</span>
            <label className="theme-toggle__label" htmlFor="harbor-validation-page-size">
              每页
            </label>
            <select
              id="harbor-validation-page-size"
              className="theme-toggle__select"
              value={limit}
              onChange={(event) => {
                changeSelection(Number(event.target.value), 0);
              }}
            >
              {PAGE_SIZES.map((size) => (
                <option key={size} value={size}>
                  {size}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="button"
              onClick={() => {
                changeSelection(limit, Math.max(0, offset - limit));
              }}
              disabled={offset === 0}
            >
              上一页
            </button>
            <button
              type="button"
              className="button"
              onClick={() => {
                changeSelection(limit, nextOffset ?? offset);
              }}
              disabled={nextOffset === null}
            >
              下一页
            </button>
          </div>
        </div>
      </div>

      <p className="card__hint" data-testid="validation-list-note">
        默认每页 {DEFAULT_PAGE_SIZE} 条。列表只显示运行主记录；覆盖情况、参数试验与结论在详情页按分区展示，
        缺失的分区会写明原因，而不是显示为 0。
      </p>
    </>
  );
}
