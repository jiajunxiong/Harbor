/**
 * Detail-page behaviour tests (MVP 5 / SP 5.14–SP 5.20).
 *
 * These assert the page's *claims* rather than its markup: that a count which is
 * empty by design is labelled as such, that an unavailable metric shows its
 * reason instead of a zero, and that the refusal distribution describes the full
 * filtered set rather than the page on screen. Those are the failure modes that
 * would make a research dashboard lie without ever looking broken.
 */

import { QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";

import { createQueryClient } from "../../api/queryClient";
import {
  backtestRunDetail,
  drawdownResponse,
  fillPage,
  jsonResponse,
  metricsResponse,
  metricsUnavailable,
  netValueSeries,
  rejectedResponse,
  runFilters,
} from "../../testing";
import { ThemeProvider } from "../../theme/ThemeProvider";
import { RunDetailPage } from "./RunDetailPage";
import type { RunTab } from "../../app/route";

interface StubPayloads {
  detail?: unknown;
  metrics?: unknown;
  series?: unknown;
  drawdowns?: unknown;
  fills?: unknown;
  rejected?: unknown;
}

/**
 * Stub the detail endpoints by URL.
 *
 * `/backtests/bt-0001`, `.../metrics`, `.../net-values`, `.../drawdowns`,
 * `.../fills` and `.../rejected-trades` all share a prefix, so answering by
 * suffix is what keeps one endpoint's payload from being handed to another.
 */
function stubDetailApi(payloads: StubPayloads = {}) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/backtests/filters")) {
      return Promise.resolve(jsonResponse(runFilters()));
    }
    if (url.includes("/metrics")) {
      return Promise.resolve(jsonResponse(payloads.metrics ?? metricsResponse()));
    }
    if (url.includes("/net-values")) {
      return Promise.resolve(jsonResponse(payloads.series ?? netValueSeries()));
    }
    if (url.includes("/drawdowns")) {
      return Promise.resolve(jsonResponse(payloads.drawdowns ?? drawdownResponse()));
    }
    if (url.includes("/fills")) {
      // The fixture run trades in US only, so filtering to HK legitimately
      // returns nothing — which is what makes the empty wording testable.
      if (url.includes("market=HK")) {
        return Promise.resolve(jsonResponse(fillPage({ items: [], total: 0 })));
      }
      return Promise.resolve(jsonResponse(payloads.fills ?? fillPage()));
    }
    if (url.includes("/rejected-trades")) {
      return Promise.resolve(jsonResponse(payloads.rejected ?? rejectedResponse()));
    }
    return Promise.resolve(jsonResponse(payloads.detail ?? backtestRunDetail()));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderDetail(tab: RunTab, payloads: StubPayloads = {}) {
  const client = createQueryClient();
  stubDetailApi(payloads);
  const page: ReactElement = (
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <RunDetailPage runId="bt-0001" tab={tab} onTabChange={() => undefined} onBack={() => undefined} />
      </ThemeProvider>
    </QueryClientProvider>
  );
  return render(page);
}

describe("overview tab (SP 5.14)", () => {
  it("shows the persisted counts with the always-empty ones labelled", async () => {
    renderDetail("overview");

    expect(await screen.findByTestId("count-fills")).toHaveTextContent("2");
    // `positions` is zero because nothing writes it, not because the strategy
    // held nothing — so it is marked rather than shown as a bare zero.
    const positions = await screen.findByTestId("count-positions");
    expect(positions.textContent).toContain("0");
    expect(positions.textContent).toContain("按设计为空");
  });

  it("shows the run's identity including its config fingerprint", async () => {
    renderDetail("overview");

    const panel = await screen.findByTestId("run-id");
    expect(panel).toHaveTextContent("bt-0001");
    expect(screen.getByText("hash-1")).toBeInTheDocument();
  });
});

describe("performance tab (SP 5.15-5.17)", () => {
  it("renders the server's metrics with an explicit sign", async () => {
    renderDetail("performance");

    expect(await screen.findByTestId("metric-cumulative_return")).toHaveTextContent("+26.00%");
    expect(screen.getByTestId("metric-max_drawdown")).toHaveTextContent("11.30%");
    expect(screen.getByTestId("metric-sharpe_ratio")).toHaveTextContent("1.20");
  });

  it("states the reason instead of zeros when the server says metrics are unavailable", async () => {
    renderDetail("performance", {
      metrics: metricsUnavailable("This run has no persisted net values."),
    });

    const states = await screen.findAllByTestId("empty-state");
    expect(states.map((node) => node.textContent).join(" ")).toContain(
      "This run has no persisted net values.",
    );
    // A zero would be a measurement nobody made.
    expect(screen.queryByTestId("metric-cumulative_return")).not.toBeInTheDocument();
    expect(screen.queryByTestId("metric-sharpe_ratio")).not.toBeInTheDocument();
  });

  it("shows the curve, the depth of each drawdown and the missing position detail", async () => {
    renderDetail("performance");

    expect(await screen.findByTestId("net-value-note")).toHaveTextContent("全量净值序列");
    // The depth appears on the metric card and on each threshold's table row.
    expect(screen.getAllByText("11.30%").length).toBeGreaterThan(0);
    expect(await screen.findByTestId("drawdown-position-note")).toHaveTextContent("持仓明细");
  });

  it("admits when the curve is a subsample of the series", async () => {
    renderDetail("performance", {
      series: netValueSeries({
        downsampled: true,
        point_count: 1642,
        returned_count: 200,
      }),
    });

    const note = await screen.findByTestId("net-value-note");
    expect(note.textContent).toContain("200");
    expect(note.textContent).toContain("1642");
    expect(note.textContent).toContain("降采样");
  });

  it("says a run has no curve rather than drawing an empty one", async () => {
    renderDetail("performance", {
      series: netValueSeries({
        currency: null,
        point_count: 0,
        returned_count: 0,
        first_date: null,
        last_date: null,
        points: [],
      }),
      drawdowns: drawdownResponse({ available: false, unavailable_reason: "no net values", events: [] }),
    });

    expect(await screen.findByText("本运行没有净值序列")).toBeInTheDocument();
    // Both panels state their own absence; neither draws an empty axis.
    expect(await screen.findByText("无法计算回撤")).toBeInTheDocument();
    expect(screen.queryByTestId("net-value-note")).not.toBeInTheDocument();
    expect(screen.queryByTestId("net-value-range")).not.toBeInTheDocument();
  });
});

describe("trades tab (SP 5.18)", () => {
  it("lists fills with their fee and currency", async () => {
    renderDetail("trades");

    expect(await screen.findByText("700.HK")).toBeInTheDocument();
    expect(screen.getByText("320.50")).toBeInTheDocument();
    // The fee is the persisted value; the page does not recompute it.
    expect(screen.getAllByText("12.00").length).toBeGreaterThan(0);
    expect(screen.getByText("HKD")).toBeInTheDocument();
  });

  it("describes the refusal distribution over the full set, not the visible page", async () => {
    renderDetail("trades");

    // The page holds one row while the distribution covers three refusals.
    expect(await screen.findByText(/3 笔被拒/)).toBeInTheDocument();
    expect(await screen.findByText("position limit")).toBeInTheDocument();
    const note = await screen.findByTestId("rejection-distribution-note");
    expect(note.textContent).toContain("3");
    expect(note.textContent).toContain("不限于本页");
  });

  it("says a run traded nothing rather than blaming pagination", async () => {
    renderDetail("trades", { fills: fillPage({ items: [], total: 0 }) });

    const state = await screen.findByTestId("empty-state");
    expect(state.textContent).toContain("本运行未落库任何成交记录");
    expect(state.textContent).not.toContain("越过");
  });

  it("explains an empty filtered result as a filter with no matches", async () => {
    renderDetail("trades");
    await screen.findByText("700.HK");

    const market = document.getElementById("harbor-fill-market");
    expect(market).not.toBeNull();
    fireEvent.change(market as HTMLElement, { target: { value: "HK" } });

    const state = await screen.findByTestId("empty-state");
    expect(state.textContent).toContain("没有任何成交匹配当前筛选");
    expect(state.textContent).toContain("这不代表本运行没有成交");
    // The offset did not run past anything: there are simply no matches.
    expect(state.textContent).not.toContain("越过");
  });
});

describe("attribution tab (SP 5.19, SP 5.20)", () => {
  it("explains that positions and attribution are not persisted, and names the CLI path", async () => {
    renderDetail("attribution");

    await screen.findByTestId("attribution-unavailable");
    // The claim spans the notice and the table of records; assert on the panel.
    const panel = await screen.findByRole("tabpanel");
    expect(panel.textContent).toContain("未持久化持仓与归因数据");
    expect(panel.textContent).toContain("backtest_positions");
    expect(panel.textContent).toContain("backtest_metrics");
    expect(panel.textContent).toContain("harbor-cli backtest show");
  });

  it("distinguishes 'nothing recorded' from 'no positions held'", async () => {
    renderDetail("attribution");

    // The empty state is about missing data, not about an empty portfolio.
    expect(await screen.findByText("无可展示的持仓明细")).toBeInTheDocument();
    expect(screen.getByText(/未记录持仓/)).toBeInTheDocument();
    expect(screen.queryByText(/^持仓为零/)).not.toBeInTheDocument();
  });
});
