import { DataTable, type Column } from "../../components/DataTable";
import { StatusBadge } from "../../components/StatusBadge";
import { EMPTY_VALUE, formatDateOnly, formatTimestamp } from "../../format";
import type { BacktestRunSummary } from "../../api/types";

export interface BacktestRunsTableProps {
  runs: readonly BacktestRunSummary[];
  caption?: string;
  /** Opens a run's detail view; omit to render the list read-only. */
  onOpen?: (runId: string) => void;
  /** Run ids currently ticked for comparison. */
  selected?: ReadonlySet<string>;
  onToggleSelect?: (runId: string) => void;
}

/**
 * The identifier, as a link into the detail view.
 *
 * Only the id is clickable, not the whole row, so a reader can still select and
 * copy an identifier without navigating away from the list.
 */
function runIdColumn(onOpen: (runId: string) => void): Column<BacktestRunSummary> {
  return {
    key: "run_id",
    header: "运行 ID",
    render: (run) => (
      <button
        type="button"
        className="link-button mono"
        data-testid={`open-run-${run.run_id}`}
        onClick={() => {
          onOpen(run.run_id);
        }}
      >
        {run.run_id}
      </button>
    ),
  };
}

/** The tick-box column, present only while selection is wired up. */
function selectionColumn(
  selected: ReadonlySet<string>,
  onToggleSelect: (runId: string) => void,
): Column<BacktestRunSummary> {
  return {
    key: "select",
    header: "对比",
    render: (run) => (
      <input
        type="checkbox"
        className="row-check"
        data-testid={`select-run-${run.run_id}`}
        aria-label={`选择运行 ${run.run_id} 用于对比`}
        checked={selected.has(run.run_id)}
        onChange={() => {
          onToggleSelect(run.run_id);
        }}
      />
    ),
  };
}

/** Every column except the identifier, which depends on the navigation callback. */
const COLUMNS: readonly Column<BacktestRunSummary>[] = [
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

/** The run list table (MVP 5 / SP 5.12, SP 5.13). */
export function BacktestRunsTable({
  runs,
  caption,
  onOpen,
  selected,
  onToggleSelect,
}: BacktestRunsTableProps) {
  const columns: readonly Column<BacktestRunSummary>[] =
    onOpen === undefined ? COLUMNS : [runIdColumn(onOpen), ...COLUMNS];
  const withSelection =
    selected === undefined || onToggleSelect === undefined
      ? columns
      : [selectionColumn(selected, onToggleSelect), ...columns];

  return (
    <DataTable
      columns={withSelection}
      rows={runs}
      rowKey={(run) => run.run_id}
      caption={caption ?? `共 ${runs.length} 次回测运行`}
    />
  );
}
