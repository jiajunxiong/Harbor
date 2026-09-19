/**
 * Chart data for the backtest run list (MVP 5 / SP 5.12).
 *
 * Kept as pure functions so the chart's meaning is unit-testable without a
 * canvas: the component supplies data and a palette, nothing else.
 *
 * A deliberate honesty constraint: these counts describe **one page** of runs,
 * never the whole history. Pagination means the page cannot know the global
 * distribution, so the surrounding card says so rather than implying the chart
 * is a complete picture.
 */

import type { BacktestRunSummary } from "../../api/types";
import type { ChartOption } from "../../components/chartTypes";
import { seriesColor, type ChartPalette } from "../../theme/chartPalette";

export interface StatusCount {
  status: string;
  count: number;
}

/** Count runs per status, most frequent first. */
export function countByStatus(runs: readonly BacktestRunSummary[]): StatusCount[] {
  const counts = new Map<string, number>();
  for (const run of runs) {
    counts.set(run.status, (counts.get(run.status) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([status, count]) => ({ status, count }))
    .sort((left, right) => right.count - left.count || left.status.localeCompare(right.status));
}

/** Build the bar chart of run counts per status. */
export function buildStatusChartOption(
  counts: readonly StatusCount[],
  palette: ChartPalette,
): ChartOption {
  return {
    grid: { left: 4, right: 16, top: 16, bottom: 4, containLabel: true },
    tooltip: {
      trigger: "item",
      backgroundColor: palette.surface,
      borderColor: palette.border,
      textStyle: { color: palette.text },
    },
    xAxis: {
      type: "category",
      data: counts.map((entry) => entry.status),
      axisLine: { lineStyle: { color: palette.grid } },
      axisTick: { show: false },
      axisLabel: { color: palette.axisLabel },
    },
    yAxis: {
      type: "value",
      minInterval: 1,
      splitLine: { lineStyle: { color: palette.grid } },
      axisLabel: { color: palette.axisLabel },
    },
    series: [
      {
        type: "bar",
        name: "运行数",
        barMaxWidth: 56,
        data: counts.map((entry, index) => ({
          value: entry.count,
          itemStyle: { color: seriesColor(palette, index) },
        })),
      },
    ],
  };
}
