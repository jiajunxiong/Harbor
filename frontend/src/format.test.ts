import { describe, expect, it } from "vitest";

import {
  EMPTY_VALUE,
  formatCount,
  formatDateOnly,
  formatPercent,
  formatPercentValue,
  formatTimestamp,
  shortenId,
} from "./format";

describe("formatDateOnly", () => {
  it("never timezone-shifts a calendar date", () => {
    // A data cutoff is a calendar date; parsing it as an instant would render
    // 2026-01-01 for a viewer west of UTC.
    expect(formatDateOnly("2026-01-02")).toBe("2026-01-02");
  });

  it("shows the placeholder for a missing date", () => {
    expect(formatDateOnly(null)).toBe(EMPTY_VALUE);
    expect(formatDateOnly(undefined)).toBe(EMPTY_VALUE);
    expect(formatDateOnly("")).toBe(EMPTY_VALUE);
  });
});

describe("formatTimestamp", () => {
  it("renders an instant with a timezone hint", () => {
    const rendered = formatTimestamp("2026-01-02T08:00:00+00:00");
    expect(rendered).toContain("2026");
    expect(rendered).toMatch(/[A-Z]{2,4}|GMT|UTC/);
  });

  it("returns an unparseable value unchanged instead of inventing an instant", () => {
    expect(formatTimestamp("not-a-timestamp")).toBe("not-a-timestamp");
  });

  it("shows the placeholder for a missing timestamp", () => {
    expect(formatTimestamp(null)).toBe(EMPTY_VALUE);
  });
});

describe("formatCount", () => {
  it("groups thousands", () => {
    expect(formatCount(1234567)).toBe("1,234,567");
  });
});

describe("shortenId", () => {
  it("keeps short identifiers intact", () => {
    expect(shortenId("bt-1")).toBe("bt-1");
  });

  it("truncates long identifiers", () => {
    expect(shortenId("a".repeat(40))).toBe(`${"a".repeat(10)}…`);
  });
});

describe("formatPercentValue (SP 5.30)", () => {
  it("renders an already-percentage value without scaling it again", () => {
    // The bug this guards: formatPercent multiplies by 100, so a measured 74.92%
    // coverage rendered as 7492.42% — a number nobody could read as plausible
    // but a number a reader would still trust.
    expect(formatPercentValue(74.92416582406472)).toBe("74.92%");
    expect(formatPercentValue(100)).toBe("100.00%");
    expect(formatPercentValue(0)).toBe("0.00%");
  });

  it("keeps the two renderers distinct for the same input", () => {
    expect(formatPercent(0.7492)).toBe("74.92%");
    expect(formatPercentValue(74.92)).toBe("74.92%");
    // Same displayed value, different inputs: mixing them up is the defect.
    expect(formatPercentValue(0.7492)).toBe("0.75%");
  });

  it("shows the placeholder for a missing measurement", () => {
    expect(formatPercentValue(null)).toBe(EMPTY_VALUE);
    expect(formatPercentValue(undefined)).toBe(EMPTY_VALUE);
    expect(formatPercentValue(Number.NaN)).toBe(EMPTY_VALUE);
  });
});
