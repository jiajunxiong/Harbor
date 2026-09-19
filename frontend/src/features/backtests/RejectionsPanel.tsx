import { useCallback, useState } from "react";

import { useRejectedTrades } from "../../api/hooks";
import { DataTable, type Column } from "../../components/DataTable";
import { EmptyState, ErrorState, LoadingState } from "../../components/States";
import { EMPTY_VALUE, formatCount, formatQuantity } from "../../format";
import type { RejectedTradeRow } from "../../api/types";

const COLUMNS: readonly Column<RejectedTradeRow>[] = [
  { key: "market", header: "市场", render: (row) => row.market },
  {
    key: "symbol",
    header: "代码",
    render: (row) => <span className="mono">{row.symbol}</span>,
  },
  { key: "side", header: "方向", render: (row) => row.side ?? EMPTY_VALUE },
  {
    key: "quantity",
    header: "数量",
    numeric: true,
    render: (row) => formatQuantity(row.quantity),
  },
  { key: "reason", header: "拒单原因", render: (row) => row.reason },
  {
    key: "order_ref",
    header: "委托号",
    render: (row) => <span className="mono">{row.order_ref ?? EMPTY_VALUE}</span>,
  },
];

const REASON_COLUMNS: readonly Column<{ reason: string; count: number }>[] = [
  {
    key: "reason",
    header: "原因",
    render: (entry) => <span className="mono">{entry.reason}</span>,
  },
  {
    key: "count",
    header: "笔数",
    numeric: true,
    render: (entry) => formatCount(entry.count),
  },
];

const PAGE_SIZE = 25;
const MARKET_CHOICES = ["", "HK", "US"] as const;

export interface RejectionsPanelProps {
  runId: string;
}

/**
 * Refused trades and why they were refused (MVP 5 / SP 5.18).
 *
 * Three deliberate choices:
 *
 * * The distribution comes from `reasons`, which the server computes over the
 *   **whole** filtered set. Deriving it from the visible page would make the
 *   bars depend on which page you were on.
 * * The bars are drawn as proportional fills rather than a chart axis, because
 *   the reason strings are long enough to be unreadable as tick labels.
 * * Refusals are presented as recorded facts, not failures: a strategy that
 *   never breaches a limit is not proven better, and a run with refusals is not
 *   broken.
 */
export function RejectionsPanel({ runId }: RejectionsPanelProps) {
  const [market, setMarket] = useState<string>("");
  const [offset, setOffset] = useState(0);

  const query = useRejectedTrades(runId, {
    limit: PAGE_SIZE,
    offset,
    market: market === "" ? undefined : market,
  });

  // See `FillsPanel`: a filter change resets paging, and it is done in the
  // handler rather than an effect so the reason stays visible.
  const changeMarket = useCallback((next: string) => {
    setMarket(next);
    setOffset(0);
  }, []);

  const goPrevious = useCallback(() => {
    setOffset((current) => Math.max(0, current - PAGE_SIZE));
  }, []);

  const nextOffset = query.data?.next_offset ?? null;
  const goNext = useCallback(() => {
    if (nextOffset !== null) {
      setOffset(nextOffset);
    }
  }, [nextOffset]);

  const rows = query.data?.items ?? [];
  const reasons = query.data?.reasons ?? [];
  const total = query.data?.total ?? 0;
  const largest = reasons.reduce((peak, entry) => Math.max(peak, entry.count), 0);

  return (
    <>
      <div className="toolbar" role="group" aria-label="拒单筛选">
        <div className="field">
          <label className="field__label" htmlFor="harbor-reject-market">
            市场
          </label>
          <select
            id="harbor-reject-market"
            className="field__control"
            value={market}
            onChange={(event) => {
              changeMarket(event.target.value);
            }}
          >
            {MARKET_CHOICES.map((choice) => (
              <option key={choice} value={choice}>
                {choice === "" ? "全部市场" : choice}
              </option>
            ))}
          </select>
        </div>
        <span className="card__hint" data-testid="rejection-summary">
          {query.data === undefined
            ? "读取中…"
            : `${formatCount(total)} 笔被拒（占总体的拒绝原因分布见下表）`}
        </span>
      </div>

      {query.isPending ? <LoadingState label="正在读取拒单记录…" /> : null}

      {query.isError ? (
        <ErrorState
          error={query.error}
          onRetry={() => {
            void query.refetch();
          }}
        />
      ) : null}

      {query.isSuccess && total === 0 ? (
        <EmptyState
          title="没有拒单记录"
          hint={
            market === ""
              ? "本运行全程没有触发拒单；这不代表风险控制被验证有效，只代表本次样本未触发。"
              : `市场 ${market} 没有拒单记录；其他市场可能有，请切换「全部市场」查看。`
          }
        />
      ) : null}

      {reasons.length > 0 ? (
        <>
          <h3 className="card__title">拒绝原因分布（全量筛选集）</h3>
          <DataTable
            columns={REASON_COLUMNS}
            rows={reasons}
            rowKey={(entry) => entry.reason}
            caption={`共 ${reasons.length} 类拒绝原因 · 合计 ${formatCount(
              reasons.reduce((sum, entry) => sum + entry.count, 0),
            )} 笔`}
          />
          <p className="card__hint" data-testid="rejection-distribution-note">
            占比以当前筛选条件下的全部 {formatCount(total)} 笔为分母，不限于本页；最长条为{" "}
            {formatCount(largest)} 笔。
          </p>
        </>
      ) : null}

      {rows.length > 0 ? (
        <>
          <h3 className="card__title">拒单明细</h3>
          <DataTable
            columns={COLUMNS}
            rows={rows}
            rowKey={(row) => `${row.order_ref ?? "no-ref"}-${row.symbol}-${row.reason}`}
            caption={`拒单明细，本页 ${rows.length} 条`}
          />
          <div className="pagination">
            <span className="pagination__summary">
              共 {formatCount(total)} 笔 · 当前显示第 {offset + 1}–{offset + rows.length} 笔
            </span>
            <button
              type="button"
              className="button"
              onClick={goPrevious}
              disabled={offset === 0 || query.isFetching}
            >
              上一页
            </button>
            <button
              type="button"
              className="button"
              onClick={goNext}
              disabled={nextOffset === null || query.isFetching}
            >
              下一页
            </button>
          </div>
        </>
      ) : null}
    </>
  );
}
