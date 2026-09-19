import { useValidationCoverage, useValidationWarnings } from "../../api/hooks";
import type { CoverageItemView } from "../../api/types";
import { DataTable, type Column } from "../../components/DataTable";
import { EmptyState, ErrorState, LoadingState } from "../../components/States";
import { formatCount, formatPercentValue } from "../../format";

export interface ValidationCoveragePanelProps {
  runId: string;
}

/** Readable names for the coverage items; the raw key is shown alongside. */
const ITEM_LABELS: Record<string, string> = {
  prices: "行情",
  stock_pool: "历史股票池",
  fundamentals: "财报",
  corporate_actions: "企业行动",
  dividends: "分红",
  calendar: "交易日历",
  fx: "汇率",
  benchmark: "基准",
  quality: "数据质量",
};

const SEVERITY_LABELS: Record<string, string> = {
  error: "阻断（error）",
  warning: "警告（warning）",
  not_qualified: "不合格（not_qualified）",
};

const SEVERITY_CLASSES: Record<string, string> = {
  error: "badge badge--danger",
  warning: "badge badge--warn",
  not_qualified: "badge badge--info",
};

function renderSeverity(item: CoverageItemView) {
  if (item.severity === null) {
    // A passing item is stated as passing rather than left blank: an empty cell
    // next to a failing one reads as "not measured".
    return <span className="badge badge--ok">通过门槛</span>;
  }
  return (
    <span className={SEVERITY_CLASSES[item.severity] ?? "badge badge--neutral"}>
      {SEVERITY_LABELS[item.severity] ?? item.severity}
    </span>
  );
}

/**
 * Measured coverage, the gate verdicts and the recorded warnings
 * (MVP 5 / SP 5.30, SP 5.33).
 *
 * Warnings live here rather than in their own tab because they *are* the gate
 * outcomes: separating them would let a reader look at a coverage table without
 * ever seeing which items failed. The drift line is here for the same reason —
 * coverage measured now can disagree with the frozen fingerprint, and that
 * disagreement is a finding rather than a rendering detail.
 */
export function ValidationCoveragePanel({ runId }: ValidationCoveragePanelProps) {
  const coverage = useValidationCoverage(runId);
  const warnings = useValidationWarnings(runId);

  const columns: readonly Column<CoverageItemView>[] = [
    { key: "market", header: "市场", render: (item) => <span className="mono">{item.market}</span> },
    {
      key: "item",
      header: "覆盖项",
      render: (item) => (
        <>
          {ITEM_LABELS[item.item] ?? item.item} <span className="muted mono">{item.item}</span>
        </>
      ),
    },
    {
      key: "covered",
      header: "实测 / 应有",
      numeric: true,
      render: (item) => `${formatCount(item.covered)} / ${formatCount(item.denominator)}`,
    },
    {
      key: "pct",
      header: "覆盖率",
      numeric: true,
      render: (item) => formatPercentValue(item.coverage_pct),
    },
    { key: "severity", header: "门槛判定", render: (item) => renderSeverity(item) },
    {
      key: "gap",
      header: "缺口说明",
      render: (item) =>
        item.gap === "" ? (
          <span className="muted">无缺失</span>
        ) : (
          <span data-testid={`gap-${item.market}-${item.item}`}>{item.gap}</span>
        ),
    },
  ];

  return (
    <>
      <div className="card">
        <div className="card__header">
          <span className="card__title">数据覆盖与门槛</span>
          <span className="card__hint">SP 5.30 · 按市场分别判定，不做合并</span>
        </div>
        <div className="card__body">
          {coverage.isPending ? <LoadingState label="正在测量数据覆盖…" /> : null}
          {coverage.isError ? (
            <ErrorState
              error={coverage.error}
              onRetry={() => {
                void coverage.refetch();
              }}
            />
          ) : null}

          {coverage.data !== undefined && !coverage.data.available ? (
            <EmptyState
              title="无法测量数据覆盖"
              hint={coverage.data.unavailable_reason ?? "服务端未给出原因。"}
            />
          ) : null}

          {coverage.data !== undefined && coverage.data.available ? (
            <>
              <dl className="kv">
                <dt>测量时间</dt>
                <dd>
                  <time dateTime={coverage.data.measured_at}>{coverage.data.measured_at}</time>
                </dd>
                <dt>市场</dt>
                <dd className="mono">{coverage.data.markets.join("、") || "—"}</dd>
                <dt>冻结指纹</dt>
                <dd className="mono">{coverage.data.frozen_fingerprint ?? "未冻结"}</dd>
                <dt>当前指纹</dt>
                <dd className="mono" data-testid="current-fingerprint">
                  {coverage.data.current_fingerprint ?? "—"}
                </dd>
                <dt>数据漂移</dt>
                <dd data-testid="fingerprint-matches">
                  {coverage.data.fingerprint_matches === true ? (
                    <span className="badge badge--ok">一致（冻结后数据未变）</span>
                  ) : coverage.data.fingerprint_matches === false ? (
                    <span className="badge badge--danger">不一致：冻结后数据已变化</span>
                  ) : (
                    <span className="muted">无法比较（该运行尚未冻结）</span>
                  )}
                </dd>
              </dl>

              <DataTable
                columns={columns}
                rows={coverage.data.items}
                rowKey={(item) => `${item.market}-${item.item}`}
                caption="实测覆盖明细：分子是库内确实存在的数据，分母是配置要求的数据范围"
              />

              <ul className="note-list" data-testid="validation-coverage-notes">
                {coverage.data.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
      </div>

      <div className="card">
        <div className="card__header">
          <span className="card__title">冻结时的覆盖警告</span>
          <span className="card__hint">SP 5.33 · 由冻结命令写入 warning 表</span>
        </div>
        <div className="card__body">
          {warnings.isPending ? <LoadingState label="正在读取覆盖警告…" /> : null}
          {warnings.isError ? (
            <ErrorState
              error={warnings.error}
              onRetry={() => {
                void warnings.refetch();
              }}
            />
          ) : null}
          {warnings.data !== undefined && warnings.data.items.length === 0 ? (
            <EmptyState
              title="没有记录到覆盖警告"
              hint="这不代表覆盖良好：警告只记录未通过门槛的项，通过的项不写警告行。请以上方实测覆盖表为准。"
            />
          ) : null}
          {warnings.data !== undefined && warnings.data.items.length > 0 ? (
            <>
              <ul className="summary-list" data-testid="validation-warning-list">
                {warnings.data.items.map((warning, index) => {
                  // The stored severity vocabulary is two levels (the table has a
                  // CHECK constraint); the gate's three-level verdict survives in
                  // `context.severity`. Showing only the stored value here would
                  // make this list look like it contradicts the coverage table.
                  const gateSeverity =
                    typeof warning.context.severity === "string"
                      ? warning.context.severity
                      : warning.severity;
                  return (
                    <li key={`${warning.warning_code}-${index}`}>
                      <span className={SEVERITY_CLASSES[gateSeverity] ?? "badge badge--neutral"}>
                        {SEVERITY_LABELS[gateSeverity] ?? gateSeverity}
                      </span>
                      <span className="mono">{warning.warning_code}</span>
                      <span>{warning.message}</span>
                      {gateSeverity === warning.severity ? null : (
                        <span className="muted">（落库档位：{warning.severity}）</span>
                      )}
                    </li>
                  );
                })}
              </ul>
              <ul className="note-list" data-testid="validation-warning-notes">
                {warnings.data.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
      </div>
    </>
  );
}
