/**
 * Honesty tests for the detail page's chart data (MVP 5 / SP 5.15, SP 5.17).
 *
 * Pure functions, so what is asserted here is the chart's *meaning*: which
 * points get drawn, which intervals get shaded, and whether the caption admits
 * that the curve is a subsample.
 */

import { describe, expect, it } from "vitest";

import type { DrawdownEventView, NetValueSeries } from "../../api/types";
import { LIGHT_CHART_PALETTE } from "../../theme/chartPalette";
import {
  buildNetValueChartOption,
  subsampleNote,
  thresholdLabel,
  toDrawdownBands,
} from "./netValueChart";

const SERIES: NetValueSeries = {
  run_id: "bt-0001",
  currency: "HKD",
  point_count: 3,
  returned_count: 3,
  downsampled: false,
  first_date: "2026-01-02",
  last_date: "2026-01-06",
  points: [
    { as_of_date: "2026-01-02", currency: "HKD", cash: 10000, securities_value: 0, fees_paid: 0 },
    { as_of_date: "2026-01-05", currency: "HKD", cash: 0, securities_value: 11500, fees_paid: 0 },
    { as_of_date: "2026-01-06", currency: "HKD", cash: 0, securities_value: 10200, fees_paid: 0 },
  ],
};

function event(overrides: Partial<DrawdownEventView> = {}): DrawdownEventView {
  return {
    threshold: 0.05,
    start_date: "2026-01-05",
    peak_date: "2026-01-05",
    peak_value: 11500,
    trough_date: "2026-01-06",
    trough_value: 10200,
    depth: 0.113,
    recovered_date: null,
    position_detail_available: false,
    ...overrides,
  };
}

describe("thresholdLabel", () => {
  it("renders a fraction as a whole-percent label", () => {
    expect(thresholdLabel(0.05)).toBe("5%");
    expect(thresholdLabel(0.1)).toBe("10%");
  });
});

describe("toDrawdownBands", () => {
  it("marks an unrecovered interval as open", () => {
    const [band] = toDrawdownBands([event(), event({ recovered_date: "2026-01-09" })]);
    expect(band?.open).toBe(true);
    expect(band?.recoveredDate).toBeNull();
  });

  it("carries the depth through unchanged", () => {
    expect(toDrawdownBands([event()])[0]?.depth).toBe(0.113);
  });
});

describe("buildNetValueChartOption", () => {
  it("plots cash plus securities value, not cash alone", () => {
    const option = buildNetValueChartOption({
      series: SERIES,
      bands: [],
      palette: LIGHT_CHART_PALETTE,
    });
    const series = option.series as { data: number[] }[];
    expect(series[0]?.data).toEqual([10000, 11500, 10200]);
  });

  it("starts the y-axis at zero so a small move cannot look like a collapse", () => {
    const option = buildNetValueChartOption({
      series: SERIES,
      bands: [],
      palette: LIGHT_CHART_PALETTE,
    });
    expect((option.yAxis as { min: number }).min).toBe(0);
  });

  it("offers zooming over the time range", () => {
    const option = buildNetValueChartOption({
      series: SERIES,
      bands: [],
      palette: LIGHT_CHART_PALETTE,
    });
    expect(Array.isArray(option.dataZoom)).toBe(true);
    expect((option.dataZoom as unknown[]).length).toBeGreaterThan(0);
  });

  it("shades only the requested threshold", () => {
    const bands = toDrawdownBands([
      event({ threshold: 0.05 }),
      event({ threshold: 0.1 }),
      event({ threshold: 0.1, start_date: "2026-02-01", trough_date: "2026-02-02" }),
    ]);
    const option = buildNetValueChartOption({
      series: SERIES,
      bands,
      palette: LIGHT_CHART_PALETTE,
      bandThreshold: 0.1,
    });
    const markArea = (option.series as { markArea?: { data: unknown[] } }[])[0]?.markArea;
    expect(markArea?.data).toHaveLength(2);
  });

  it("shades an open interval down to its trough, since there is no recovery date", () => {
    const option = buildNetValueChartOption({
      series: SERIES,
      bands: toDrawdownBands([event({ recovered_date: null })]),
      palette: LIGHT_CHART_PALETTE,
      bandThreshold: 0.05,
    });
    const data = (option.series as { markArea?: { data: { xAxis: string }[][] } }[])[0]?.markArea
      ?.data;
    expect(data?.[0]?.[0]?.xAxis).toBe("2026-01-05");
    expect(data?.[0]?.[1]?.xAxis).toBe("2026-01-06");
  });

  it("omits the shaded area when there is nothing to shade", () => {
    const option = buildNetValueChartOption({
      series: SERIES,
      bands: [],
      palette: LIGHT_CHART_PALETTE,
      bandThreshold: 0.05,
    });
    expect((option.series as { markArea?: unknown }[])[0]?.markArea).toBeUndefined();
  });
});

describe("subsampleNote", () => {
  it("states plainly when the curve is only part of the series", () => {
    const note = subsampleNote({ ...SERIES, point_count: 1642, returned_count: 200, downsampled: true });
    expect(note).toContain("200");
    expect(note).toContain("1642");
    expect(note).toContain("降采样");
    // The reader must not think the metrics were computed on the drawn subset.
    expect(note).toContain("全量");
  });

  it("says so when the whole series is drawn", () => {
    expect(subsampleNote(SERIES)).toContain("全量净值序列");
    expect(subsampleNote(SERIES)).not.toContain("降采样");
  });
});
