import { useCallback, useMemo, useState } from "react";

import { useBacktestRuns } from "../../api/hooks";
import type { BacktestRunSummary } from "../../api/types";
import { EmptyState, ErrorState, LoadingState } from "../../components/States";
import { BacktestRunsTable } from "./BacktestRunsTable";
import { BacktestStatusChart } from "./BacktestStatusChart";
import { paginationSummary } from "./paginationSummary";
import { countByStatus } from "./statusChart";

/** Page sizes offered to the user; all are within the server's 200-row ceiling. */
const PAGE_SIZES = [10, 25, 50, 100] as const;
const DEFAULT_PAGE_SIZE = 25;

/** A stable empty page so `runs` keeps its identity while data is absent. */
const EMPTY_RUNS: readonly BacktestRunSummary[] = [];

/**
 * The minimal end-to-end dashboard view (MVP 5 / SP 5.12, SP 5.13).
 *
 * One read-only endpoint (`GET /api/v1/backtests`) drives both a table and a
 * chart, with loading, empty and error states wired to the server's
 * problem+json contract (SP 5.7).
 */
export function BacktestRunsPage() {
  const [limit, setLimit] = useState<number>(DEFAULT_PAGE_SIZE);
  const [offset, setOffset] = useState(0);

  const query = useBacktestRuns({ limit, offset });
  const runs = query.data?.items ?? EMPTY_RUNS;
  const total = query.data?.total ?? 0;
  const nextOffset = query.data?.next_offset ?? null;
  const counts = useMemo(() => countByStatus(runs), [runs]);

  const goToPrevious = useCallback(() => {
    setOffset((current) => Math.max(0, current - limit));
  }, [limit]);

  const goToNext = useCallback(() => {
    if (nextOffset !== null) {
      setOffset(nextOffset);
    }
  }, [nextOffset]);

  const changeLimit = useCallback((next: number) => {
    setLimit(next);
    setOffset(0);
  }, []);

  const summary =
    query.data === undefined ? "读取中…" : paginationSummary(total, offset, runs.length);
  const busy = query.isFetching;

  return (
    <>
      <section className="section-heading">
        <h1 className="section-heading__title">回测运行</h1>
        <p className="section-heading__subtitle">
          只读列出已落库的回测运行记录。字段与 CLI 报告同源，不做重算或近似；失败运行的原因同样如实呈现。
        </p>
      </section>

      <div className="card">
        <div className="card__header">
          <span className="card__title">运行列表</span>
          <span className="card__hint">{summary}</span>
        </div>

        {query.isPending ? <LoadingState label="正在读取回测运行列表…" /> : null}

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
            title="暂无回测运行记录"
            hint="先通过 CLI 执行一次回测并落库，再回到本页面；此处不提供任何写入入口。"
          />
        ) : null}

        {runs.length > 0 ? <BacktestRunsTable runs={runs} /> : null}

        <div className="pagination">
          <span className="pagination__summary">{summary}</span>
          <label className="theme-toggle__label" htmlFor="harbor-page-size">
            每页
          </label>
          <select
            id="harbor-page-size"
            className="theme-toggle__select"
            value={limit}
            onChange={(event) => {
              changeLimit(Number(event.target.value));
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
            onClick={goToPrevious}
            disabled={offset === 0 || busy}
          >
            上一页
          </button>
          <button
            type="button"
            className="button"
            onClick={goToNext}
            disabled={nextOffset === null || busy}
          >
            下一页
          </button>
        </div>
      </div>

      <div className="card">
        <div className="card__header">
          <span className="card__title">当前页状态分布</span>
          <span className="card__hint">仅统计当前页 {runs.length} 条，非全量分布</span>
        </div>
        <div className="card__body">
          {query.isPending ? <LoadingState label="正在读取回测运行列表…" /> : null}
          {query.isError ? (
            <p className="state__meta" data-testid="chart-unavailable">
              运行列表读取失败，状态分布不可用；错误详情与重试入口见上方「运行列表」。
            </p>
          ) : null}
          {query.isSuccess && runs.length === 0 ? (
            <EmptyState title="当前页没有可统计的运行" />
          ) : null}
          {runs.length > 0 ? <BacktestStatusChart counts={counts} /> : null}
        </div>
      </div>
    </>
  );
}
