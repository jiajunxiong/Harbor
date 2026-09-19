import { describe, expect, it } from "vitest";

import { LIGHT_CHART_PALETTE } from "../../theme/chartPalette";
import type { BacktestRunSummary } from "../../api/types";
import { buildStatusChartOption, countByStatus } from "./statusChart";
import { backtestRun } from "../../testing";

function runs(...statuses: string[]): BacktestRunSummary[] {
  return statuses.map((status, index) =>
    backtestRun({ run_id: `bt-${index}`, status }),
  ) as unknown as BacktestRunSummary[];
}

describe("countByStatus", () => {
  it("counts each status, most frequent first", () => {
    const counts = countByStatus(runs("completed", "completed", "failed"));
    expect(counts).toEqual([
      { status: "completed", count: 2 },
      { status: "failed", count: 1 },
    ]);
  });

  it("orders ties deterministically", () => {
    const counts = countByStatus(runs("running", "failed"));
    expect(counts.map((entry) => entry.status)).toEqual(["failed", "running"]);
  });

  it("returns nothing for an empty page", () => {
    expect(countByStatus([])).toEqual([]);
  });
});

describe("buildStatusChartOption", () => {
  it("maps statuses to the category axis and counts to the bars", () => {
    const option = buildStatusChartOption(countByStatus(runs("completed", "failed", "failed")), LIGHT_CHART_PALETTE);

    expect(option.xAxis).toMatchObject({ type: "category", data: ["failed", "completed"] });
    expect(option.yAxis).toMatchObject({ type: "value", minInterval: 1 });
    const series = option.series as { type: string; data: { value: number }[] }[];
    expect(series[0]?.type).toBe("bar");
    expect(series[0]?.data.map((item) => item.value)).toEqual([2, 1]);
  });

  it("paints the bars from the supplied palette", () => {
    const option = buildStatusChartOption(countByStatus(runs("completed", "failed")), LIGHT_CHART_PALETTE);
    const series = option.series as { data: { itemStyle: { color: string } }[] }[];
    expect(series[0]?.data[0]?.itemStyle.color).toBe(LIGHT_CHART_PALETTE.series[0]);
    expect(series[0]?.data[1]?.itemStyle.color).toBe(LIGHT_CHART_PALETTE.series[1]);
  });

  it("produces a valid option with no data", () => {
    const option = buildStatusChartOption([], LIGHT_CHART_PALETTE);
    expect(option.xAxis).toMatchObject({ data: [] });
    const series = option.series as { data: unknown[] }[];
    expect(series[0]?.data).toEqual([]);
  });
});
