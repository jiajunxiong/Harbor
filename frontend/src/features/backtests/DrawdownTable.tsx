import { useDrawdowns } from "../../api/hooks";
import { DataTable, type Column } from "../../components/DataTable";
import { EmptyState, ErrorState, LoadingState } from "../../components/States";
import { formatAmount, formatDateOnly } from "../../format";
import { thresholdLabel } from "./netValueChart";
import type { DrawdownEventView } from "../../api/types";

const COLUMNS: readonly Column<DrawdownEventView>[] = [
  {
    key: "threshold",
    header: "阈值",
    render: (event) => thresholdLabel(event.threshold),
  },
  {
    key: "start_date",
    header: "回撤开始",
    render: (event) => <span className="mono">{formatDateOnly(event.start_date)}</span>,
  },
  {
    key: "peak_date",
    header: "峰值日",
    render: (event) => <span className="mono">{formatDateOnly(event.peak_date)}</span>,
  },
  {
    key: "peak_value",
    header: "峰值",
    numeric: true,
    render: (event) => formatAmount(event.peak_value),
  },
  {
    key: "trough_date",
    header: "谷底日",
    render: (event) => <span className="mono">{formatDateOnly(event.trough_date)}</span>,
  },
  {
    key: "trough_value",
    header: "谷底",
    numeric: true,
    render: (event) => formatAmount(event.trough_value),
  },
  {
    key: "depth",
    header: "深度",
    numeric: true,
    render: (event) => `${(event.depth * 100).toFixed(2)}%`,
  },
  {
    key: "recovered_date",
    header: "恢复日",
    render: (event) =>
      event.recovered_date === null ? (
        <span className="badge badge--warn">尚未恢复</span>
      ) : (
        <span className="mono">{formatDateOnly(event.recovered_date)}</span>
      ),
  },
];

export interface DrawdownTableProps {
  runId: string;
}

/**
 * Threshold-triggered drawdown intervals (MVP 5 / SP 5.17).
 *
 * The events come from the server, which detects them on the full net-value
 * series, so the table and the shaded bands on the curve describe the same
 * intervals. Position-level detail is a separate matter: `position_detail_available`
 * is false because position values and FX P&L are not persisted, and the note
 * below says so rather than leaving the column looking merely empty.
 */
export function DrawdownTable({ runId }: DrawdownTableProps) {
  const query = useDrawdowns(runId);

  if (query.isPending) {
    return <LoadingState label="正在读取回撤事件…" />;
  }
  if (query.isError) {
    return (
      <ErrorState
        error={query.error}
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }

  const payload = query.data;
  if (!payload.available) {
    return (
      <EmptyState
        title="无法计算回撤"
        hint={payload.unavailable_reason ?? "服务端未说明原因。"}
      />
    );
  }
  if (payload.events.length === 0) {
    return (
      <EmptyState
        title="无回撤事件"
        hint={`净值序列未出现达到 ${payload.thresholds.map(thresholdLabel).join(" / ") || "给定"} 阈值
          的回撤。阈值越小事件越多；此处仅呈现服务端已检测出的事件。`}
      />
    );
  }

  const detailAvailable = payload.events.some((event) => event.position_detail_available);

  return (
    <>
      <DataTable
        columns={COLUMNS}
        rows={payload.events}
        rowKey={(event) => `${event.threshold}-${event.start_date}-${event.trough_date}`}
        caption={`共 ${payload.events.length} 个回撤事件`}
      />
      {!detailAvailable ? (
        <p className="notice" data-testid="drawdown-position-note">
          本运行未持久化持仓明细与汇率损益，因此每个回撤事件只能给出组合层面的峰谷区间，无法拆分到个股与费用。
          需要归因时请以 CLI 报告为准（<span className="mono">harbor-cli backtest show</span>）。
        </p>
      ) : null}
    </>
  );
}
