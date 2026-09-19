import { useMemo } from "react";

import { useComparison } from "../../api/hooks";
import { EChart } from "../../components/EChart";
import { EmptyState, ErrorState, LoadingState } from "../../components/States";
import { StatusBadge } from "../../components/StatusBadge";
import { MAX_COMPARISON_RUNS } from "../../app/route";
import { EMPTY_VALUE, formatCount, formatDateOnly, formatPercent, formatRatio, shortenId } from "../../format";
import { LIGHT_CHART_PALETTE } from "../../theme/chartPalette";
import { useTheme } from "../../theme/ThemeContext";
import {
  COMPARISON_METRICS,
  COMPARISON_NOTES,
  buildComparisonChartOption,
  chartedRuns,
  extremeLabel,
  metricExtremes,
  metricValue,
  type ComparisonMetricRow,
} from "./comparisonChart";

export interface RunComparisonPageProps {
  runIds: readonly string[];
  onOpenRun: (runId: string) => void;
  onBack: () => void;
}

function renderMetric(value: number | null, row: ComparisonMetricRow): string {
  if (value === null) {
    return EMPTY_VALUE;
  }
  if (row.kind === "percent") {
    return formatPercent(value);
  }
  if (row.kind === "ratio") {
    return formatRatio(value);
  }
  return formatCount(value);
}

/**
 * Several runs side by side (MVP 5 / SP 5.23).
 *
 * Two decisions carry the honesty of this page:
 *
 * 1. The curves are cumulative *returns*, not net values, because a net value is
 *    only meaningful together with its currency and capital. The axis says so.
 * 2. Highlighting is descriptive — "最高 / 最低" within a row — and only where the
 *    convention makes the extreme meaningful. No cell is called "better", because
 *    that judgement depends on the research question and not on the data.
 */
export function RunComparisonPage({ runIds, onOpenRun, onBack }: RunComparisonPageProps) {
  const theme = useTheme();
  const palette = theme?.palette ?? LIGHT_CHART_PALETTE;
  const query = useComparison(runIds);

  const payload = query.data ?? null;
  const option = useMemo(
    () => (payload === null ? null : buildComparisonChartOption(payload, palette)),
    [payload, palette],
  );

  const runs = payload?.runs ?? [];
  const chartable = payload === null ? [] : chartedRuns(payload);

  return (
    <>
      <section className="section-heading">
        <nav className="crumbs" aria-label="面包屑">
          <button type="button" className="link-button" onClick={onBack}>
            回测运行
          </button>
          <span aria-hidden="true">/</span>
          <span>多运行对比</span>
        </nav>
        <h1 className="section-heading__title">多运行对比</h1>
        <p className="section-heading__subtitle">
          对比 {runIds.length} 次运行的累计收益曲线与绩效指标。曲线由服务端按各运行自身首个净值归一，
          前端不做重算；任何不可直接比较之处都会明确列出。
        </p>
      </section>

      {runIds.length < 2 ? (
        <EmptyState
          title="至少需要 2 次运行才能对比"
          hint={
            <>
              对比请求至少需要两个运行 ID（最多 {MAX_COMPARISON_RUNS} 个）。当前链接只带了{" "}
              {runIds.length} 个，因此没有发起请求，也没有可展示的曲线。
              请回到列表页勾选运行后点击「对比所选运行」。
            </>
          }
        />
      ) : null}

      {runIds.length >= 2 && query.isPending ? (
        <LoadingState label="正在读取对比数据…" />
      ) : null}
      {runIds.length >= 2 && query.isError ? (
        <ErrorState
          error={query.error}
          onRetry={() => {
            void query.refetch();
          }}
        />
      ) : null}

      {payload !== null && payload.warnings.length > 0 ? (
        <div className="notice" data-testid="comparison-warnings">
          <strong>可比性提示</strong>
          <ul className="summary-list">
            {payload.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {payload !== null ? (
        <div className="card">
          <div className="card__header">
            <span className="card__title">累计收益曲线</span>
            <span className="card__hint">纵轴为收益率，非金额；可缩放时间范围</span>
          </div>
          <div className="card__body">
            {chartable.length === 0 ? (
              <EmptyState
                title="所选运行都没有可绘制的净值序列"
                hint="失败或中断的运行不会有净值点位；下方指标与列表仍如实呈现它们存在与否。"
              />
            ) : (
              option !== null && <EChart option={option} height={380} ariaLabel="多运行累计收益对比" />
            )}
          </div>
        </div>
      ) : null}

      {payload !== null ? (
        <div className="card">
          <div className="card__header">
            <span className="card__title">运行的落库事实</span>
            <span className="card__hint">{runs.length} 次运行</span>
          </div>
          <div className="card__body">
            <div className="table-scroll">
              <table className="table">
                <caption>每次运行的来源与可对比性</caption>
                <thead>
                  <tr>
                    <th scope="col">运行</th>
                    <th scope="col">状态</th>
                    <th scope="col">策略</th>
                    <th scope="col">币种</th>
                    <th scope="col">区间</th>
                    <th scope="col" className="table__numeric">
                      净值点
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {runs.map((run) => (
                    <tr key={run.run_id}>
                      <td>
                        <button
                          type="button"
                          className="link-button mono"
                          onClick={() => {
                            onOpenRun(run.run_id);
                          }}
                        >
                          {shortenId(run.run_id, 14)}
                        </button>
                      </td>
                      <td>
                        <StatusBadge status={run.status} />
                      </td>
                      <td>
                        {run.strategy} · <span className="mono">{run.strategy_version}</span>
                      </td>
                      <td>{run.currency ?? "未记录"}</td>
                      <td className="mono">
                        {run.start_date === null || run.end_date === null
                          ? EMPTY_VALUE
                          : `${formatDateOnly(run.start_date)} ~ ${formatDateOnly(run.end_date)}`}
                      </td>
                      <td className="table__numeric">{formatCount(run.point_count)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {runs.some((run) => !run.available) ? (
              <p className="notice" data-testid="comparison-unavailable">
                部分运行没有可计算指标：
                <ul className="summary-list">
                  {runs
                    .filter((run) => !run.available)
                    .map((run) => (
                      <li key={run.run_id}>
                        <span className="mono">{run.run_id}</span>：
                        {run.unavailable_reason ?? "服务端未说明原因。"}
                      </li>
                    ))}
                </ul>
              </p>
            ) : null}
          </div>
        </div>
      ) : null}

      {payload !== null ? (
        <div className="card">
          <div className="card__header">
            <span className="card__title">指标对照</span>
            <span className="card__hint">标注仅为「最高 / 最低」，不代表策略更优</span>
          </div>
          <div className="card__body">
            <div className="table-scroll">
              <table className="table">
                <caption>绩效指标对照（同口径来源于各运行已落库净值）</caption>
                <thead>
                  <tr>
                    <th scope="col">指标</th>
                    {runs.map((run) => (
                      <th key={run.run_id} scope="col" className="table__numeric">
                        {shortenId(run.run_id, 14)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {COMPARISON_METRICS.map((row) => {
                    const extremes = metricExtremes(runs, row.key);
                    return (
                      <tr key={row.key}>
                        <th scope="row">{row.label}</th>
                        {runs.map((run) => {
                          const value = metricValue(run, row.key);
                          const label = row.comparable ? extremeLabel(value, extremes) : null;
                          return (
                            <td key={run.run_id} className="table__numeric">
                              {renderMetric(value, row)}
                              {label !== null ? (
                                <span className="badge badge--neutral">{label}</span>
                              ) : null}
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <ul className="summary-list" data-testid="comparison-notes">
              {/* The server's notes are the canonical wording of the caveats
                  (they ship with the payload); the local list is only a
                  fallback for a response that carries none. Rendering both
                  would repeat the same two caveats in two voices. */}
              {(payload.notes.length > 0 ? payload.notes : COMPARISON_NOTES).map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}
    </>
  );
}
