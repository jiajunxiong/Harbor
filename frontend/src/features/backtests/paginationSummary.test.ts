import { describe, expect, it } from "vitest";

import { paginationSummary } from "./paginationSummary";

describe("paginationSummary", () => {
  it("describes a full page within a larger history", () => {
    expect(paginationSummary(120, 0, 25)).toBe("共 120 次运行 · 当前显示第 1–25 条");
  });

  it("describes a middle page using the offset", () => {
    expect(paginationSummary(120, 25, 25)).toBe("共 120 次运行 · 当前显示第 26–50 条");
  });

  it("describes a short final page", () => {
    expect(paginationSummary(120, 100, 20)).toBe("共 120 次运行 · 当前显示第 101–120 条");
  });

  it("never claims to show rows when the history is empty", () => {
    expect(paginationSummary(0, 0, 0)).toBe("共 0 次运行");
  });

  it("says so when the requested page has no rows", () => {
    expect(paginationSummary(120, 200, 0)).toBe("共 120 次运行 · 本页无数据");
  });
});
