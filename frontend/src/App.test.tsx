/**
 * End-to-end smoke test for the minimal dashboard (MVP 5 / SP 5.12).
 *
 * The whole application is rendered against a stubbed `fetch`, so this
 * exercises the real path: HTTP client → TanStack Query → page → table and
 * chart. It also asserts the client-side half of the read-only guarantee
 * (SP 5.3): the dashboard only ever issues bodiless GETs.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import {
  backtestRun,
  backtestRunDetail,
  comparisonResponse,
  fetchCalls,
  jsonResponse,
  pageOf,
  problemResponse,
  runFilters,
} from "./testing";

const HEALTH_OK = { status: "ok", database: "ok", read_only: true, version: "0.1.0" };

const FILTERS_OK = runFilters();

interface StubOptions {
  runs?: Record<string, unknown>[];
  filters?: Record<string, unknown>;
  detail?: Record<string, unknown>;
  comparison?: Record<string, unknown>;
  failure?: { code: string; status: number };
}

/** The run list is `/backtests`; `/backtests/filters` is a different endpoint. */
function isRunListRequest(url: string): boolean {
  return /\/backtests(\?|$)/.test(url);
}

/** `/backtests/<run_id>` — the detail route, not the list and not `/filters`. */
function isRunDetailRequest(url: string): boolean {
  return /\/backtests\/[^/?]+(\?|$)/.test(url) && !url.includes("/backtests/filters");
}

/**
 * Stub `fetch`, routing each endpoint separately.
 *
 * Routing matters: the page now reads the run list, the filter vocabulary and a
 * run's detail, so a stub that answers every data route with the same body
 * cannot tell which request failed.
 */
function stubApi(options: StubOptions = {}) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/health")) {
      return Promise.resolve(jsonResponse(HEALTH_OK));
    }
    if (options.failure !== undefined) {
      return Promise.resolve(problemResponse(options.failure.code, options.failure.status));
    }
    if (url.includes("/backtests/filters")) {
      return Promise.resolve(jsonResponse(options.filters ?? FILTERS_OK));
    }
    if (url.includes("/backtests/compare")) {
      return Promise.resolve(jsonResponse(options.comparison ?? comparisonResponse()));
    }
    if (isRunDetailRequest(url)) {
      return Promise.resolve(jsonResponse(options.detail ?? backtestRunDetail()));
    }
    return Promise.resolve(jsonResponse(pageOf(options.runs ?? [])));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const TWO_RUNS = [
  backtestRun({ run_id: "bt-0001", status: "COMPLETED" }),
  backtestRun({ run_id: "bt-0002", status: "FAILED", error_summary: "no bars for HK" }),
];

describe("dashboard smoke test", () => {
  beforeEach(() => {
    // The route lives in the URL, so a test that navigates would otherwise leak
    // its location into the next render — the next `render(<App />)` would open
    // whatever page the previous test left behind.
    window.location.hash = "";
  });

  it("renders the disclaimer before anything a reader might act on", async () => {
    stubApi({ runs: TWO_RUNS });
    render(<App />);

    const disclaimers = await screen.findAllByTestId("research-disclaimer");
    expect(disclaimers.length).toBeGreaterThan(0);
    expect(disclaimers[0]?.textContent).toContain("不构成投资建议");
    expect(disclaimers[0]?.textContent).toContain("不表示未来收益或回撤");
  });

  it("renders the run list from the read-only endpoint", async () => {
    stubApi({ runs: TWO_RUNS });
    render(<App />);

    expect(await screen.findByText("bt-0001")).toBeInTheDocument();
    expect(screen.getByText("bt-0002")).toBeInTheDocument();
    // A failed run shows its reason rather than hiding it.
    expect(screen.getByText("no bars for HK")).toBeInTheDocument();
  });

  it("renders the chart and the same numbers as text", async () => {
    stubApi({ runs: TWO_RUNS });
    render(<App />);

    expect(await screen.findByTestId("backtest-status-chart")).toBeInTheDocument();
    const summary = await screen.findByTestId("backtest-status-summary");
    expect(summary.textContent).toContain("COMPLETED");
    expect(summary.textContent).toContain("FAILED");
  });

  it("reports how much of the history is on screen", async () => {
    stubApi({ runs: TWO_RUNS });
    render(<App />);

    const summaries = await screen.findAllByText(/共 2 次运行 · 当前显示第 1–2 条/);
    expect(summaries.length).toBeGreaterThan(0);
  });

  it("never issues anything but a GET with no body", async () => {
    const fetchMock = stubApi({ runs: TWO_RUNS });
    render(<App />);
    await screen.findByText("bt-0001");

    const calls = fetchCalls(fetchMock);
    expect(calls.length).toBeGreaterThan(0);
    for (const call of calls) {
      expect(call.init.method).toBe("GET");
      expect(call.init.body).toBeUndefined();
    }
  });

  it("shows an empty state instead of an empty table", async () => {
    stubApi({ runs: [] });
    render(<App />);

    const texts = (await screen.findAllByTestId("empty-state")).map(
      (node) => node.textContent ?? "",
    );
    expect(texts).toContainEqual(expect.stringContaining("暂无回测运行记录"));
    expect(texts).toContainEqual(expect.stringContaining("当前页没有可统计的运行"));
  });

  it("never claims the history is empty while a request is still in flight", async () => {
    // A data request that never settles keeps the page in its loading state.
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/health")) {
        return Promise.resolve(jsonResponse(HEALTH_OK));
      }
      return new Promise<Response>(() => undefined);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    expect((await screen.findAllByText("加载中…")).length).toBeGreaterThan(0);
    expect(screen.queryAllByTestId("empty-state")).toHaveLength(0);
  });

  it("renders the server's error code and request id when the API refuses", async () => {
    stubApi({ failure: { code: "missing_credentials", status: 401 } });
    render(<App />);

    const panels = await screen.findAllByTestId("error-state");
    expect(panels).toHaveLength(1);
    expect(panels[0]?.textContent).toContain("missing_credentials");
    expect(panels[0]?.textContent).toContain("HTTP 401");
    expect(panels[0]?.textContent).toContain("req-test-1");
    // The chart card reports the same failure compactly instead of repeating it.
    expect(screen.getByTestId("chart-unavailable").textContent).toContain("状态分布不可用");
  });

  it("re-issues the request when the reader asks to retry", async () => {
    // Only the run-list request is counted: the page also asks for filter
    // options, and a stub that counted every data request would be measuring
    // the render order of two unrelated hooks.
    let listAttempts = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/health")) {
        return Promise.resolve(jsonResponse(HEALTH_OK));
      }
      if (url.includes("/backtests/filters")) {
        return Promise.resolve(jsonResponse(FILTERS_OK));
      }
      if (!isRunListRequest(url)) {
        return Promise.resolve(jsonResponse(pageOf([])));
      }
      listAttempts += 1;
      if (listAttempts === 1) {
        return Promise.resolve(problemResponse("backtest_run_not_found", 404));
      }
      return Promise.resolve(jsonResponse(pageOf(TWO_RUNS)));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    await screen.findByTestId("error-state");
    expect(screen.getAllByRole("button", { name: "重试" })).toHaveLength(1);

    fireEvent.click(screen.getByRole("button", { name: "重试" }));

    expect(await screen.findByText("bt-0001")).toBeInTheDocument();
    expect(listAttempts).toBe(2);
  });

  it("sends the selected filter to the server and records it in the URL", async () => {
    const fetchMock = stubApi({ runs: TWO_RUNS });
    render(<App />);

    const status = await screen.findByLabelText("状态");
    await screen.findByRole("option", { name: "COMPLETED" });
    fireEvent.change(status, { target: { value: "FAILED" } });

    await screen.findByText("bt-0001");
    const listUrls = fetchCalls(fetchMock)
      .map((call) => call.url)
      .filter(isRunListRequest);
    expect(listUrls.some((url) => url.includes("status=FAILED"))).toBe(true);
    // The view is shareable: the filter is part of the location hash (SP 5.24).
    expect(window.location.hash).toContain("status=FAILED");
  });

  it("opens a run's detail view with the tab in the URL", async () => {
    stubApi({ runs: TWO_RUNS });
    render(<App />);

    fireEvent.click(await screen.findByTestId("open-run-bt-0002"));

    expect(window.location.hash).toBe("#/runs/bt-0002");
    expect(await screen.findByText(/运行详情/)).toBeInTheDocument();
  });
  it("compares the ticked runs and puts the selection in the URL", async () => {
    stubApi({ runs: TWO_RUNS, comparison: comparisonResponse() });
    render(<App />);

    fireEvent.click(await screen.findByTestId("select-run-bt-0001"));
    fireEvent.click(screen.getByTestId("select-run-bt-0002"));
    expect(screen.getByTestId("compare-selection")).toHaveTextContent("已选 2 / 5 次运行");

    fireEvent.click(screen.getByTestId("compare-selected"));

    expect(window.location.hash).toBe("#/runs/compare?ids=bt-0001%2Cbt-0002");
    expect(await screen.findByRole("heading", { name: "多运行对比" })).toBeInTheDocument();
  });

  it("cannot compare a single run", async () => {
    stubApi({ runs: TWO_RUNS });
    render(<App />);

    fireEvent.click(await screen.findByTestId("select-run-bt-0001"));

    // The server refuses fewer than two runs, so the button stays disabled
    // rather than sending a request that would be rejected.
    expect(screen.getByTestId("compare-selected")).toBeDisabled();
  });
});
