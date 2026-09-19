/**
 * End-to-end smoke test for the minimal dashboard (MVP 5 / SP 5.12).
 *
 * The whole application is rendered against a stubbed `fetch`, so this
 * exercises the real path: HTTP client → TanStack Query → page → table and
 * chart. It also asserts the client-side half of the read-only guarantee
 * (SP 5.3): the dashboard only ever issues bodiless GETs.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { backtestRun, fetchCalls, jsonResponse, pageOf, problemResponse } from "./testing";

const HEALTH_OK = { status: "ok", database: "ok", read_only: true, version: "0.1.0" };

interface StubOptions {
  runs?: Record<string, unknown>[];
  failure?: { code: string; status: number };
}

/** Stub `fetch`, routing `/health` separately from the data routes. */
function stubApi(options: StubOptions = {}) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/health")) {
      return Promise.resolve(jsonResponse(HEALTH_OK));
    }
    if (options.failure !== undefined) {
      return Promise.resolve(problemResponse(options.failure.code, options.failure.status));
    }
    return Promise.resolve(jsonResponse(pageOf(options.runs ?? [])));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

const TWO_RUNS = [
  backtestRun({ run_id: "bt-0001", status: "completed" }),
  backtestRun({ run_id: "bt-0002", status: "failed", error_summary: "no bars for HK" }),
];

describe("dashboard smoke test", () => {
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
    expect(summary.textContent).toContain("completed");
    expect(summary.textContent).toContain("failed");
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
    let dataAttempts = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith("/health")) {
        return Promise.resolve(jsonResponse(HEALTH_OK));
      }
      dataAttempts += 1;
      if (dataAttempts === 1) {
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
    expect(dataAttempts).toBe(2);
  });
});
