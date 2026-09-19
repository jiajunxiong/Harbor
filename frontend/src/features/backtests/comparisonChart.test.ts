/**
 * Comparison chart and highlighting (MVP 5 / SP 5.23).
 *
 * What is asserted here is the *meaning* of the comparison: that the curve is a
 * cumulative return rather than an amount (so two currencies can share an axis),
 * that each run keeps its own dates, and that highlighting stays descriptive.
 */

import { describe, expect, it } from "vitest";

import type { BacktestComparisonResponse, RunComparisonView } from "../../api/types";
import { LIGHT_CHART_PALETTE } from "../../theme/chartPalette";
import { comparisonResponse } from "../../testing";
import {
  COMPARISON_METRICS,
  buildComparisonChartOption,
  chartedRuns,
  extremeLabel,
  metricExtremes,
  metricValue,
} from "./comparisonChart";

const PAYLOAD = comparisonResponse() as unknown as BacktestComparisonResponse;

function run(overrides: Partial<RunComparisonView> = {}): RunComparisonView {
  const base = PAYLOAD.runs[0] as RunComparisonView;
  return { ...base, ...overrides };
}

describe("buildComparisonChartOption", () => {
  it("plots cumulative returns rather than net values", () => {
    const option = buildComparisonChartOption(PAYLOAD, LIGHT_CHART_PALETTE);
    const series = option.series as { data: [string, number][] }[];

    // 0.26 as a fraction must appear as 26 percent, because the axis is percent.
    expect(series[0]?.data[1]?.[1]).toBeCloseTo(26, 6);
    expect(series[0]?.data[0]?.[1]).toBe(0);
  });

  it("keeps each run's own dates instead of aligning onto a shared category axis", () => {
    const option = buildComparisonChartOption(PAYLOAD, LIGHT_CHART_PALETTE);

    expect((option.xAxis as { type: string }).type).toBe("time");
    const series = option.series as { data: [string, number][] }[];
    expect(series[1]?.data[0]?.[0]).toBe("2026-02-02");
    expect(series[1]?.data[2]?.[0]).toBe("2026-02-04");
  });

  it("leaves a run with no valuations out of the chart entirely", () => {
    const option = buildComparisonChartOption(PAYLOAD, LIGHT_CHART_PALETTE);
    const series = option.series as { name: string }[];

    expect(series).toHaveLength(2);
    expect(series.map((entry) => entry.name)).not.toContain("bt-0003");
  });

  it("labels the axis so the curve cannot be read as an amount", () => {
    const option = buildComparisonChartOption(PAYLOAD, LIGHT_CHART_PALETTE);

    expect((option.yAxis as { name: string }).name).toContain("累计收益");
    expect((option.yAxis as { name: string }).name).not.toContain("净值");
  });

  it("offers zooming over the time range", () => {
    const option = buildComparisonChartOption(PAYLOAD, LIGHT_CHART_PALETTE);

    expect(Array.isArray(option.dataZoom)).toBe(true);
  });
});

describe("chartedRuns", () => {
  it("returns only the runs with a curve", () => {
    expect(chartedRuns(PAYLOAD).map((entry) => entry.run_id)).toEqual(["bt-0001", "bt-0002"]);
  });
});

describe("metricValue", () => {
  it("reads a metric from a run that has them", () => {
    expect(metricValue(run(), "sharpe_ratio")).toBeCloseTo(1.2, 6);
  });

  it("returns null rather than zero when the run has no metrics", () => {
    const failed = PAYLOAD.runs[2] as RunComparisonView;

    // A zero here would be a measurement nobody made.
    expect(failed.metrics).toBeNull();
    expect(metricValue(failed, "sharpe_ratio")).toBeNull();
  });
});

describe("metricExtremes", () => {
  it("reports the lowest and highest of the runs that have the metric", () => {
    const extremes = metricExtremes(PAYLOAD.runs, "cumulative_return");

    expect(extremes).not.toBeNull();
    expect(extremes?.lowest).toBeCloseTo(0.26, 6);
    expect(extremes?.highest).toBeCloseTo(0.3, 6);
  });

  it("reports nothing when fewer than two runs have the metric", () => {
    expect(metricExtremes([run()], "sharpe_ratio")).toBeNull();
  });

  it("reports nothing when every run agrees, because there is nothing to highlight", () => {
    const extremes = metricExtremes(
      [run(), run({ run_id: "bt-9" })],
      "annualized_volatility",
    );

    expect(extremes).toBeNull();
  });

  it("ignores runs without metrics instead of treating them as zero", () => {
    const extremes = metricExtremes([run(), PAYLOAD.runs[2] as RunComparisonView], "sharpe_ratio");

    expect(extremes).toBeNull();
  });
});

describe("extremeLabel", () => {
  const extremes = { lowest: 0.26, highest: 0.3 };

  it("names the extreme a value sits at", () => {
    expect(extremeLabel(0.3, extremes)).toBe("最高");
    expect(extremeLabel(0.26, extremes)).toBe("最低");
    expect(extremeLabel(0.28, extremes)).toBeNull();
    expect(extremeLabel(null, extremes)).toBeNull();
    expect(extremeLabel(0.3, null)).toBeNull();
  });

  it("never claims a value is better, only where it sits", () => {
    for (const label of [extremeLabel(0.3, extremes), extremeLabel(0.26, extremes)]) {
      expect(label).not.toContain("优");
      expect(label).not.toContain("最佳");
    }
  });
});

describe("COMPARISON_METRICS", () => {
  it("marks only the metrics whose extreme is a stated convention", () => {
    const ranked = COMPARISON_METRICS.filter((row) => row.comparable).map((row) => row.key);

    // Volatility and downside deviation are deliberately absent: calling less
    // risk "better" is a research decision, not a display decision.
    expect(ranked.sort()).toEqual(["calmar_ratio", "max_drawdown", "sharpe_ratio"]);
  });

  it("covers every metric the API publishes", () => {
    const keys = COMPARISON_METRICS.map((row) => row.key);

    expect(keys).toContain("cumulative_return");
    expect(keys).toContain("periods");
    expect(new Set(keys).size).toBe(keys.length);
  });
});
