import { DataTable, type Column } from "../../components/DataTable";
import { StatusBadge } from "../../components/StatusBadge";
import { EMPTY_VALUE, formatDateOnly, formatTimestamp } from "../../format";
import type { BacktestRunSummary } from "../../api/types";

const COLUMNS: readonly Column<BacktestRunSummary>[] = [
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
    key: "strategy",
    header: "策略",
    render: (run) => run.strategy,
  },
  {
    key: "strategy_version",
    header: "策略版本",
    render: (run) => <span className="mono">{run.strategy_version}</span>,
  },
  {
    key: "code_version",
    header: "代码版本",
    render: (run) => <span className="mono">{run.code_version}</span>,
  },
  {
    key: "data_cutoff",
    header: "数据截点",
    render: (run) => <span className="mono">{formatDateOnly(run.data_cutoff)}</span>,
  },
  {
    key: "started_at",
    header: "开始时间",
    render: (run) => formatTimestamp(run.started_at),
  },
  {
    key: "finished_at",
    header: "结束时间",
    render: (run) => formatTimestamp(run.finished_at),
  },
  {
    key: "error_summary",
    header: "失败原因",
    render: (run) => run.error_summary ?? EMPTY_VALUE,
  },
];

export interface BacktestRunsTableProps {
  runs: readonly BacktestRunSummary[];
  caption?: string;
}

/** The run list table (MVP 5 / SP 5.12). */
export function BacktestRunsTable({ runs, caption }: BacktestRunsTableProps) {
  return (
    <DataTable
      columns={COLUMNS}
      rows={runs}
      rowKey={(run) => run.run_id}
      caption={caption ?? `共 ${runs.length} 次回测运行`}
    />
  );
}
