/**
 * Chart data for a run's net-value curve and its drawdown intervals
 * (MVP 5 / SP 5.15, SP 5.17).
 *
 * Pure functions so the chart's *meaning* is testable without a canvas: the
 * component supplies data and a palette, nothing else.
 *
 * One rule matters more than the rest. Drawdown bands are drawn from the
 * server's events, which are computed on the **full** series — never from the
 * (possibly subsampled) curve on screen. Otherwise a subsampled curve could
 * appear to have a shallower trough than the reported one, and a reader would be
 * looking at a chart that contradicts its own annotations.
 */

import type { DrawdownEventView, NetValueSeries } from "../../api/types";
import type { ChartOption } from "../../components/chartTypes";
import { seriesColor, type ChartPalette } from "../../theme/chartPalette";

/** A drawdown interval prepared for display. */
export interface DrawdownBand {
  threshold: number;
  startDate: string;
  troughDate: string;
  depth: number;
  recoveredDate: string | null;
  /** True while the run has not regained the peak (still under water). */
  open: boolean;
}

export function toDrawdownBands(events: readonly DrawdownEventView[]): DrawdownBand[] {
  return events.map((event) => ({
    threshold: event.threshold,
    startDate: event.start_date,
    troughDate: event.trough_date,
    depth: event.depth,
    recoveredDate: event.recovered_date,
    open: event.recovered_date === null,
  }));
}

/** Round a threshold to a label like `5%`. */
export function thresholdLabel(threshold: number): string {
  return `${(threshold * 100).toFixed(0)}%`;
}

export interface NetValueChartInput {
  series: NetValueSeries;
  bands: readonly DrawdownBand[];
  palette: ChartPalette;
  /**
   * The deepest band to shade. Shading every overlapping interval would stack
   * translucent rectangles until the curve disappears.
   */
  bandThreshold?: number;
}

/**
 * Build the net-value line chart.
 *
 * The y-axis starts at zero. For an equity curve that is a deliberate choice:
 * a truncated axis exaggerates every move, which is exactly the impression a
 * research tool must not create.
 */
export function buildNetValueChartOption({
  series,
  bands,
  palette,
  bandThreshold,
}: NetValueChartInput): ChartOption {
  const dates = series.points.map((point) => point.as_of_date);
  const totals = series.points.map((point) => point.cash + point.securities_value);

  const shaded =
    bandThreshold === undefined ? bands : bands.filter((band) => band.threshold === bandThreshold);

  return {
    grid: { left: 8, right: 16, top: 24, bottom: 56, containLabel: true },
    tooltip: {
      trigger: "axis",
      backgroundColor: palette.surface,
      borderColor: palette.border,
      textStyle: { color: palette.text },
    },
    dataZoom: [
      { type: "inside", throttle: 50 },
      { type: "slider", height: 18, bottom: 8, borderColor: palette.border },
    ],
    xAxis: {
      type: "category",
      data: dates,
      boundaryGap: false,
      axisLine: { lineStyle: { color: palette.grid } },
      axisLabel: { color: palette.axisLabel },
    },
    yAxis: {
      type: "value",
      min: 0,
      splitLine: { lineStyle: { color: palette.grid } },
      axisLabel: { color: palette.axisLabel },
    },
    series: [
      {
        type: "line",
        name: series.currency ?? "net value",
        showSymbol: false,
        smooth: false,
        lineStyle: { width: 1.5, color: seriesColor(palette, 0) },
        itemStyle: { color: seriesColor(palette, 0) },
        areaStyle: { opacity: 0.06, color: seriesColor(palette, 0) },
        data: totals,
        markArea:
          shaded.length === 0
            ? undefined
            : {
                silent: true,
                itemStyle: { color: "rgba(161, 34, 34, 0.12)" },
                label: {
                  show: true,
                  position: "insideTop",
                  color: palette.axisLabel,
                  fontSize: 10,
                  formatter: (params: { name?: string }) => params.name ?? "",
                },
                data: shaded.map((band) => [
                  { xAxis: band.startDate, name: `${thresholdLabel(band.threshold)} 回撤` },
                  { xAxis: band.recoveredDate ?? band.troughDate },
                ]),
              },
      },
    ],
  };
}

/** A readable statement of whether the curve shown is the whole series. */
export function subsampleNote(series: NetValueSeries): string {
  if (!series.downsampled) {
    return `全量净值序列（${series.point_count} 个交易日）`;
  }
  return `为绘图降采样：显示 ${series.returned_count} / ${series.point_count} 个交易日（指标与回撤仍按全量计算）`;
}
