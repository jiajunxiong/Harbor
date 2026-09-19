/**
 * Multi-run comparison data (MVP 5 / SP 5.23).
 *
 * Pure functions, so the *meaning* of the comparison is testable without a
 * canvas or a network: the component supplies the payload and a palette.
 *
 * The whole reason this module exists is that net values are not comparable
 * across runs. A net value carries a currency and an initial capital, so an HKD
 * run and a USD run on one axis would compare amounts that mean different things
 * — and converting them would need an FX rate the backtest tables do not carry.
 * The server therefore sends *cumulative returns* rebased on each run's own
 * first valuation, and this module plots those; every axis label says "收益" so no
 * reader can mistake the chart for amounts.
 */

import type { BacktestComparisonResponse, PerformanceMetricsView, RunComparisonView } from "../../api/types";
import type { ChartOption } from "../../components/chartTypes";
import { seriesColor, type ChartPalette } from "../../theme/chartPalette";

/** A metric row in the comparison table. */
export interface ComparisonMetricRow {
  key: keyof PerformanceMetricsView;
  label: string;
  /** How to render the value. */
  kind: "percent" | "ratio" | "count";
  /** Why this row is or is not ranked: a claim about "better" needs a convention. */
  comparable: boolean;
}

/**
 * The metric rows shown side by side.
 *
 * ``comparable`` marks the metrics where "highest" or "lowest" is a *stated
 * convention* rather than a preference. Sharpe and Calmar are risk-adjusted
 * returns and a maximum drawdown is a shortfall, so the extreme is meaningful;
 * ranking volatility or downside deviation would be asserting that less risk is
 * always better, which is a research decision, not a display decision.
 */
export const COMPARISON_METRICS: readonly ComparisonMetricRow[] = [
  { key: "cumulative_return", label: "累计收益", kind: "percent", comparable: false },
  { key: "annualized_return", label: "年化收益", kind: "percent", comparable: false },
  { key: "annualized_volatility", label: "年化波动率", kind: "percent", comparable: false },
  { key: "max_drawdown", label: "最大回撤", kind: "percent", comparable: true },
  { key: "sharpe_ratio", label: "Sharpe", kind: "ratio", comparable: true },
  { key: "calmar_ratio", label: "Calmar", kind: "ratio", comparable: true },
  { key: "downside_deviation", label: "下行波动", kind: "percent", comparable: false },
  { key: "periods", label: "收益期数", kind: "count", comparable: false },
];

export const COMPARISON_NOTES: readonly string[] = [
  "曲线为各运行自身的累计收益（以各自首个净值为基准），因此本金规模与币种不同的运行可以比较曲线形状与幅度。",
  "不提供跨币种金额比较：本项目数据没有汇率表，任何隐式 1:1 换算都会被拒绝，所以纵轴是收益而不是净值金额。",
  "单指标的高低不代表策略更优；样本期、市场与标的不同时，指标之间不具备可比性。",
];

/** Build the multi-run line chart of rebased cumulative returns. */
export function buildComparisonChartOption(
  payload: BacktestComparisonResponse,
  palette: ChartPalette,
): ChartOption {
  const series = payload.runs
    .filter((run) => run.points.length > 0)
    .map((run, index) => ({
      type: "line" as const,
      name: run.run_id,
      showSymbol: false,
      smooth: false,
      connectNulls: false,
      lineStyle: { width: 1.5, color: seriesColor(palette, index) },
      itemStyle: { color: seriesColor(palette, index) },
      // A time axis accepts [timestamp, value] pairs, so runs whose trading days
      // differ (HK and US close on different holidays) do not need aligning onto
      // a shared category axis — each point keeps its own date.
      data: run.points.map((point) => [point.as_of_date, point.cumulative_return * 100]),
    }));

  return {
    grid: { left: 8, right: 16, top: 32, bottom: 48, containLabel: true },
    tooltip: {
      trigger: "axis",
      backgroundColor: palette.surface,
      borderColor: palette.border,
      textStyle: { color: palette.text },
      valueFormatter: (value: unknown) =>
        typeof value === "number" ? `${value.toFixed(2)}%` : String(value),
    },
    legend: {
      top: 0,
      type: "scroll",
      textStyle: { color: palette.axisLabel },
    },
    dataZoom: [
      { type: "inside", throttle: 50 },
      { type: "slider", height: 18, bottom: 8, borderColor: palette.border },
    ],
    xAxis: {
      type: "time",
      axisLine: { lineStyle: { color: palette.grid } },
      axisLabel: { color: palette.axisLabel },
    },
    yAxis: {
      type: "value",
      name: "累计收益 %",
      nameTextStyle: { color: palette.axisLabel },
      splitLine: { lineStyle: { color: palette.grid } },
      axisLabel: { color: palette.axisLabel, formatter: "{value}%" },
    },
    series,
  };
}

/** Runs with a curve, in payload order; the chart and the legend agree on this. */
export function chartedRuns(payload: BacktestComparisonResponse): RunComparisonView[] {
  return payload.runs.filter((run) => run.points.length > 0);
}

/** The value of one metric for one run, or `null` when it is not available. */
export function metricValue(
  run: RunComparisonView,
  key: ComparisonMetricRow["key"],
): number | null {
  if (run.metrics === null) {
    return null;
  }
  const value = run.metrics[key];
  return typeof value === "number" ? value : null;
}

/** The lowest and highest value present, for descriptive highlighting. */
export interface MetricExtremes {
  lowest: number;
  highest: number;
}

/**
 * The extremes of one metric across the runs that have it.
 *
 * Returns `null` when fewer than two runs report the metric or they all agree —
 * there is nothing to highlight, and marking every cell would say nothing.
 */
export function metricExtremes(
  runs: readonly RunComparisonView[],
  key: ComparisonMetricRow["key"],
): MetricExtremes | null {
  const values = runs
    .map((run) => metricValue(run, key))
    .filter((value): value is number => value !== null);
  if (values.length < 2) {
    return null;
  }
  const lowest = Math.min(...values);
  const highest = Math.max(...values);
  return lowest === highest ? null : { lowest, highest };
}

/** Which extreme a cell sits at, or `null` when it is neither. */
export function extremeLabel(
  value: number | null,
  extremes: MetricExtremes | null,
): "最高" | "最低" | null {
  if (value === null || extremes === null) {
    return null;
  }
  if (value === extremes.highest) {
    return "最高";
  }
  if (value === extremes.lowest) {
    return "最低";
  }
  return null;
}
