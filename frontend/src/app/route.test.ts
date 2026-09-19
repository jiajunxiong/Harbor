/**
 * URL contract tests (MVP 5 / SP 5.24).
 *
 * Deep links are a promise: a link a reviewer copies must reproduce the view
 * they were looking at. That makes the parse/serialise pair worth testing
 * directly — a round trip that silently drops a filter produces a link that
 * looks right and shows the wrong data.
 */

import { describe, expect, it } from "vitest";

import {
  buildHash,
  buildRunListSearch,
  DEFAULT_PAGE_SIZE,
  hasActiveFilters,
  MAX_COMPARISON_RUNS,
  parseHash,
  parseRunListSelection,
  selectionToQuery,
  withSelection,
  type RunListSelection,
} from "./route";

describe("run list selection", () => {
  it("omits defaults from the URL so the common case stays short", () => {
    expect(buildRunListSearch({ limit: DEFAULT_PAGE_SIZE, offset: 0 })).toBe("");
    expect(buildHash({ kind: "runs", selection: { limit: DEFAULT_PAGE_SIZE, offset: 0 } })).toBe(
      "#/runs",
    );
  });

  it("round-trips every filter", () => {
    const selection: RunListSelection = {
      limit: 50,
      offset: 25,
      status: "FAILED",
      strategy: "momentum",
      dataCutoffFrom: "2026-01-01",
      dataCutoffTo: "2026-02-01",
      sort: "finished_at",
      order: "asc",
    };
    const parsed = parseRunListSelection(new URLSearchParams(buildRunListSearch(selection)));
    expect(parsed).toEqual(selection);
  });

  it("falls back to defaults for a malformed URL instead of throwing", () => {
    const parsed = parseRunListSelection(new URLSearchParams("limit=abc&offset=-5&order=sideways"));
    expect(parsed.limit).toBe(DEFAULT_PAGE_SIZE);
    expect(parsed.offset).toBe(0);
    expect(parsed.order).toBeUndefined();
  });

  it("maps the shareable selection onto the API query", () => {
    const query = selectionToQuery({
      limit: 10,
      offset: 0,
      status: "COMPLETED",
      sort: "run_id",
      order: "asc",
    });
    expect(query).toEqual({
      limit: 10,
      offset: 0,
      status: "COMPLETED",
      sort: "run_id",
      order: "asc",
    });
    // Nothing the server has no parameter for is sent.
    expect(Object.keys(query).sort()).toEqual(["limit", "offset", "order", "sort", "status"]);
  });

  it("treats only narrowing filters as active", () => {
    expect(hasActiveFilters({ limit: 25, offset: 0 })).toBe(false);
    expect(hasActiveFilters({ limit: 25, offset: 100 })).toBe(false);
    expect(hasActiveFilters({ limit: 25, offset: 0, sort: "run_id" })).toBe(false);
    expect(hasActiveFilters({ limit: 25, offset: 0, status: "FAILED" })).toBe(true);
  });
});

describe("withSelection", () => {
  it("clears a filter given an empty string rather than sending an empty parameter", () => {
    const next = withSelection({ limit: 25, offset: 0, status: "FAILED" }, { status: "" });
    expect(next.status).toBeUndefined();
    expect(buildRunListSearch(next)).toBe("");
  });

  it("returns to the first page when a filter changes", () => {
    const next = withSelection({ limit: 25, offset: 200, status: "FAILED" }, { status: "COMPLETED" });
    expect(next.offset).toBe(0);
    expect(next.status).toBe("COMPLETED");
  });

  it("keeps the offset when the caller is paging", () => {
    const next = withSelection({ limit: 25, offset: 0, status: "FAILED" }, { offset: 25 });
    expect(next).toEqual({ limit: 25, offset: 25, status: "FAILED" });
  });

  it("keeps an unset filter unset", () => {
    const next = withSelection({ limit: 25, offset: 0 }, { strategy: "momentum" });
    expect(next).toEqual({ limit: 25, offset: 0, strategy: "momentum" });
    expect(next.status).toBeUndefined();
  });
});

describe("hash routes", () => {
  it("sends anything unrecognised to the run list", () => {
    expect(parseHash("").kind).toBe("runs");
    expect(parseHash("#").kind).toBe("runs");
    expect(parseHash("#/nope").kind).toBe("runs");
    expect(parseHash("#looks/like/a/path").kind).toBe("runs");
  });

  it("parses a run detail link with its tab", () => {
    expect(parseHash("#/runs/bt-0001")).toEqual({
      kind: "run",
      runId: "bt-0001",
      tab: "overview",
    });
    expect(parseHash("#/runs/bt-0001?tab=trades")).toEqual({
      kind: "run",
      runId: "bt-0001",
      tab: "trades",
    });
  });

  it("falls back to the overview tab for an unknown tab", () => {
    expect(parseHash("#/runs/bt-0001?tab=banana")).toEqual({
      kind: "run",
      runId: "bt-0001",
      tab: "overview",
    });
  });

  it("round-trips a run detail link without the default tab", () => {
    expect(buildHash({ kind: "run", runId: "bt-0001", tab: "overview" })).toBe("#/runs/bt-0001");
    expect(buildHash({ kind: "run", runId: "bt-0001", tab: "attribution" })).toBe(
      "#/runs/bt-0001?tab=attribution",
    );
  });

  it("escapes a run id so one identifier cannot forge another route", () => {
    const hash = buildHash({ kind: "run", runId: "a/b?tab=x", tab: "overview" });
    expect(hash).toBe("#/runs/a%2Fb%3Ftab%3Dx");
    expect(parseHash(hash)).toEqual({ kind: "run", runId: "a/b?tab=x", tab: "overview" });
  });

  it("carries the list filters through a hash", () => {
    const route = parseHash("#/runs?status=FAILED&offset=25");
    expect(route.kind).toBe("runs");
    if (route.kind === "runs") {
      expect(route.selection.status).toBe("FAILED");
      expect(route.selection.offset).toBe(25);
    }
  });

  it("still resolves a deep link whose fragment a proxy encoded wholesale", () => {
    // A forwarded port rewrites `#/runs/bt-0001?tab=trades` this way.
    expect(parseHash("#%2Fruns%2Fbt-0001%3Ftab%3Dtrades")).toEqual({
      kind: "run",
      runId: "bt-0001",
      tab: "trades",
    });
    const list = parseHash("#%2Fruns%3Fstatus%3DFAILED");
    expect(list.kind).toBe("runs");
    if (list.kind === "runs") {
      expect(list.selection.status).toBe("FAILED");
    }
  });
});

describe("comparison routes", () => {
  it("parses a comparison link into its run ids", () => {
    expect(parseHash("#/runs/compare?ids=bt-1,bt-2")).toEqual({
      kind: "comparison",
      runIds: ["bt-1", "bt-2"],
    });
  });

  it("round-trips a comparison link", () => {
    const route = { kind: "comparison", runIds: ["bt-1", "bt-2"] } as const;

    expect(parseHash(buildHash(route))).toEqual(route);
  });

  it("drops repeats, because the server refuses a duplicated run", () => {
    const route = parseHash("#/runs/compare?ids=bt-1,bt-1,bt-2");

    expect(route).toEqual({ kind: "comparison", runIds: ["bt-1", "bt-2"] });
  });

  it("caps the selection at the bound the server accepts", () => {
    const route = parseHash("#/runs/compare?ids=a,b,c,d,e,f,g");

    expect(route.kind).toBe("comparison");
    if (route.kind === "comparison") {
      expect(route.runIds).toHaveLength(MAX_COMPARISON_RUNS);
    }
  });

  it("keeps an empty selection as a comparison, so the page can explain it", () => {
    expect(parseHash("#/runs/compare")).toEqual({ kind: "comparison", runIds: [] });
  });

  it("treats the literal compare segment as the comparison, not as a run id", () => {
    // The same precedence rule the API applies to `/backtests/compare`.
    const route = parseHash("#/runs/compare?ids=bt-1,bt-2");

    expect(route.kind).toBe("comparison");
  });

  it("still parses a run whose id merely starts with compare", () => {
    expect(parseHash("#/runs/compare-two")).toEqual({
      kind: "run",
      runId: "compare-two",
      tab: "overview",
    });
  });
});
