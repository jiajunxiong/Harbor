/**
 * Replay, export and comparison UI (MVP 5 / SP 5.21-SP 5.23).
 *
 * The assertions are about claims rather than markup: that the three sibling
 * outcomes are told apart, that a download never becomes a plain link (which
 * would drop the Authorization header), that the export formats come from the
 * server, and that the comparison states what makes the runs not like-for-like.
 */

import { QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createQueryClient } from "../../api/queryClient";
import {
  apiInfo,
  comparisonResponse,
  jsonResponse,
  pageOf,
  problemResponse,
  replayResponse,
} from "../../testing";
import { ThemeProvider } from "../../theme/ThemeProvider";
import { downloadFilename, saveBlob } from "../../download";
import { ReplayPanel } from "./ReplayPanel";
import { ReportExport } from "./ReportExport";
import { RunComparisonPage } from "./RunComparisonPage";

interface StubPayloads {
  info?: unknown;
  replay?: unknown;
  comparison?: unknown;
  report?: Response;
}

function stubApi(payloads: StubPayloads = {}) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/version")) {
      return Promise.resolve(jsonResponse(payloads.info ?? apiInfo()));
    }
    if (url.includes("/report")) {
      return Promise.resolve(
        payloads.report ??
          new Response("col_a,col_b\n1,2\n", {
            status: 200,
            headers: {
              "Content-Type": "text/csv",
              "Content-Disposition": 'attachment; filename="harbor-backtest-bt-0001.csv"',
            },
          }),
      );
    }
    if (url.includes("/replay")) {
      return Promise.resolve(jsonResponse(payloads.replay ?? replayResponse()));
    }
    if (url.includes("/compare")) {
      return Promise.resolve(jsonResponse(payloads.comparison ?? comparisonResponse()));
    }
    return Promise.resolve(jsonResponse(pageOf([])));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderWithProviders(page: ReactElement) {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <ThemeProvider>{page}</ThemeProvider>
    </QueryClientProvider>,
  );
}

describe("replay panel (SP 5.21)", () => {
  it("publishes the composite fingerprint and the unpersisted inputs", async () => {
    stubApi();
    renderWithProviders(
      <ReplayPanel runId="bt-0001" runStatus="COMPLETED" onOpenRun={() => undefined} />,
    );

    const fingerprint = await screen.findByTestId("replay-fingerprint");
    // The fingerprint is a composite key, so it must be shown verbatim rather
    // than as a shortened id.
    expect(fingerprint.textContent).toContain("hash-1|abc1234|");
    // Inputs that are not persisted are named as unset, not presented as facts.
    expect(screen.getAllByText("未持久化").length).toBe(3);
  });

  it("serves the server's own caveats about what a fingerprint covers", async () => {
    stubApi();
    renderWithProviders(
      <ReplayPanel runId="bt-0001" runStatus="COMPLETED" onOpenRun={() => undefined} />,
    );

    const notes = await screen.findByTestId("replay-notes");
    expect(notes.textContent).toContain("不覆盖数据内容本身");
  });

  it("tells the three sibling outcomes apart", async () => {
    stubApi();
    renderWithProviders(
      <ReplayPanel runId="bt-0001" runStatus="COMPLETED" onOpenRun={() => undefined} />,
    );

    // same results and status
    expect(await screen.findByTestId("sibling-verdict-bt-0002")).toHaveTextContent(
      "结果与状态均一致",
    );
    // same result sections, different status
    expect(screen.getByTestId("sibling-verdict-bt-0003")).toHaveTextContent("结果区一致，但状态不同");
    // located differences
    expect(screen.getByTestId("sibling-verdict-bt-0004")).toHaveTextContent("结果不一致");
  });

  it("explains why agreeing result sections do not mean the same outcome", async () => {
    stubApi();
    renderWithProviders(
      <ReplayPanel runId="bt-0001" runStatus="COMPLETED" onOpenRun={() => undefined} />,
    );

    const note = await screen.findByTestId("sibling-status-note-bt-0003");
    expect(note.textContent).toContain("COMPLETED");
    expect(note.textContent).toContain("FAILED");
    expect(note.textContent).toContain("两次都未产生结果时也会一致");
  });

  it("locates the differences it found", async () => {
    stubApi();
    renderWithProviders(
      <ReplayPanel runId="bt-0001" runStatus="COMPLETED" onOpenRun={() => undefined} />,
    );

    expect(await screen.findByText("net_values")).toBeInTheDocument();
    // Two rows carry a location, so the query is deliberately plural.
    expect(screen.getAllByText("length").length).toBeGreaterThan(0);
    expect(screen.getByTestId("sibling-bt-0004").textContent).toContain("2 处差异");
  });

  it("admits when there is nothing to compare against", async () => {
    stubApi({ replay: replayResponse({ siblings: [], sibling_total: 0 }) });
    renderWithProviders(
      <ReplayPanel runId="bt-0001" runStatus="COMPLETED" onOpenRun={() => undefined} />,
    );

    expect(await screen.findByText("没有其它运行记录相同的输入指纹")).toBeInTheDocument();
  });

  it("says when the sibling list was capped", async () => {
    stubApi({ replay: replayResponse({ truncated: true, sibling_total: 9 }) });
    renderWithProviders(
      <ReplayPanel runId="bt-0001" runStatus="COMPLETED" onOpenRun={() => undefined} />,
    );

    const notice = await screen.findByTestId("replay-truncated");
    expect(notice.textContent).toContain("9");
    expect(notice.textContent).toContain("3");
  });

  it("reports a failed request with its error code", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(problemResponse("backtest_run_not_found", 404)));
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(
      <ReplayPanel runId="bt-0001" runStatus="COMPLETED" onOpenRun={() => undefined} />,
    );

    const error = await screen.findByTestId("error-state");
    expect(error.textContent).toContain("backtest_run_not_found");
  });
});

describe("report export (SP 5.22)", () => {
  const createObjectURL = vi.fn(() => "blob:mock-report");
  const revokeObjectURL = vi.fn();
  let clickSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    createObjectURL.mockClear();
    revokeObjectURL.mockClear();
    Object.defineProperty(URL, "createObjectURL", { value: createObjectURL, configurable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: revokeObjectURL, configurable: true });
    clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
  });

  afterEach(() => {
    clickSpy.mockRestore();
  });

  it("offers exactly the formats the API publishes", async () => {
    stubApi({ info: apiInfo({ report_formats: ["json", "html"] }) });
    renderWithProviders(<ReportExport runId="bt-0001" />);

    expect(await screen.findByTestId("export-json")).toBeInTheDocument();
    expect(screen.getByTestId("export-html")).toBeInTheDocument();
    // A hardcoded list would offer a format the API would reject.
    expect(screen.queryByTestId("export-csv")).not.toBeInTheDocument();
  });

  it("fetches the document and saves it under the server's filename", async () => {
    const fetchMock = stubApi();
    renderWithProviders(<ReportExport runId="bt-0001" />);

    fireEvent.click(await screen.findByTestId("export-csv"));

    await waitFor(() => {
      expect(createObjectURL).toHaveBeenCalledTimes(1);
    });
    // It is fetched as a file request, not opened as a link: a plain href would
    // not carry the Authorization header.
    const urls = fetchMock.mock.calls.map((call) => String(call[0]));
    expect(urls.some((url) => url.includes("/report?format=csv"))).toBe(true);
    expect(clickSpy).toHaveBeenCalledTimes(1);
    // The object URL is released again, so a blob is not pinned for the session.
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:mock-report");
  });

  it("states that the server renders the report", async () => {
    stubApi();
    renderWithProviders(<ReportExport runId="bt-0001" />);

    expect(await screen.findByTestId("report-export-note")).toHaveTextContent(
      "harbor-cli backtest report",
    );
  });

  it("surfaces an export failure instead of failing silently", async () => {
    stubApi({ report: problemResponse("invalid_report_format", 422) });
    renderWithProviders(<ReportExport runId="bt-0001" />);

    fireEvent.click(await screen.findByTestId("export-json"));

    const error = await screen.findByTestId("report-export-error");
    expect(error.textContent).toContain("invalid_report_format");
    expect(createObjectURL).not.toHaveBeenCalled();
  });

  it("says why the buttons are unavailable when the capability call fails", async () => {
    // A 401 is used rather than a 5xx: the query client retries a server error by
    // design, so a 5xx would still be pending when the assertion ran.
    const fetchMock = vi.fn(() => Promise.resolve(problemResponse("missing_credentials", 401)));
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(<ReportExport runId="bt-0001" />);

    expect(await screen.findByTestId("report-export-format-error")).toHaveTextContent(
      "无法读取可用导出格式",
    );
    expect(screen.queryByTestId("export-json")).not.toBeInTheDocument();
  });
});

describe("download helpers (SP 5.22)", () => {
  it("prefers the server's filename", () => {
    expect(downloadFilename("harbor-backtest-bt-1.csv", "fallback.csv")).toBe(
      "harbor-backtest-bt-1.csv",
    );
  });

  it("falls back when the header could not be read", () => {
    expect(downloadFilename(null, "fallback.csv")).toBe("fallback.csv");
    expect(downloadFilename("  ", "fallback.csv")).toBe("fallback.csv");
  });

  it("revokes the object URL it created", () => {
    const create = vi.fn(() => "blob:x");
    const revoke = vi.fn();
    Object.defineProperty(URL, "createObjectURL", { value: create, configurable: true });
    Object.defineProperty(URL, "revokeObjectURL", { value: revoke, configurable: true });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);

    saveBlob(new Blob(["data"]), "file.csv");

    expect(create).toHaveBeenCalledTimes(1);
    expect(revoke).toHaveBeenCalledWith("blob:x");
    expect(click).toHaveBeenCalledTimes(1);
    click.mockRestore();
  });
});

describe("comparison page (SP 5.23)", () => {
  it("states what makes the runs not like-for-like", async () => {
    stubApi();
    renderWithProviders(
      <RunComparisonPage runIds={["bt-0001", "bt-0002"]} onOpenRun={() => undefined} onBack={() => undefined} />,
    );

    const warnings = await screen.findByTestId("comparison-warnings");
    expect(warnings.textContent).toContain("币种不同");
  });

  it("names the runs that have no metrics instead of scoring them zero", async () => {
    stubApi();
    renderWithProviders(
      <RunComparisonPage
        runIds={["bt-0001", "bt-0002", "bt-0003"]}
        onOpenRun={() => undefined}
        onBack={() => undefined}
      />,
    );

    const unavailable = await screen.findByTestId("comparison-unavailable");
    expect(unavailable.textContent).toContain("bt-0003");
    expect(unavailable.textContent).toContain("no persisted net values");
  });

  it("labels the extremes descriptively, never as better", async () => {
    stubApi();
    renderWithProviders(
      <RunComparisonPage runIds={["bt-0001", "bt-0002"]} onOpenRun={() => undefined} onBack={() => undefined} />,
    );

    await screen.findByTestId("comparison-warnings");
    expect(screen.getAllByText("最高").length).toBeGreaterThan(0);
    expect(screen.getAllByText("最低").length).toBeGreaterThan(0);
    // The only verdicts a cell may carry are the two positional ones.
    expect(screen.queryByText("最佳")).not.toBeInTheDocument();
    expect(screen.queryByText(/^更优$/)).not.toBeInTheDocument();
    const metricsTable = screen.getByText("指标对照").closest(".card");
    expect(metricsTable?.textContent).toContain("不代表策略更优");
  });

  it("restates what the curve is and is not", async () => {
    stubApi();
    renderWithProviders(
      <RunComparisonPage runIds={["bt-0001", "bt-0002"]} onOpenRun={() => undefined} onBack={() => undefined} />,
    );

    const notes = await screen.findByTestId("comparison-notes");
    expect(notes.textContent).toContain("没有汇率表");
    expect(notes.textContent).toContain("单指标最优不等于策略更优");
    // The server's caveats are shown once, not once per voice.
    const items = notes.querySelectorAll("li");
    expect(items).toHaveLength(3);
  });

  it("falls back to its own caveats when the server sends none", async () => {
    stubApi({ comparison: comparisonResponse({ notes: [] }) });
    renderWithProviders(
      <RunComparisonPage runIds={["bt-0001", "bt-0002"]} onOpenRun={() => undefined} onBack={() => undefined} />,
    );

    const notes = await screen.findByTestId("comparison-notes");
    expect(notes.textContent).toContain("不代表策略更优");
  });

  it("opens a run's detail view from the comparison table", async () => {
    stubApi();
    const onOpenRun = vi.fn();
    renderWithProviders(
      <RunComparisonPage runIds={["bt-0001", "bt-0002"]} onOpenRun={onOpenRun} onBack={() => undefined} />,
    );

    await screen.findByTestId("comparison-warnings");
    fireEvent.click(screen.getAllByRole("button", { name: "bt-0001" })[0] as HTMLElement);

    expect(onOpenRun).toHaveBeenCalledWith("bt-0001");
  });

  it("reports a failed comparison with its error code", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(problemResponse("too_few_runs", 422)));
    vi.stubGlobal("fetch", fetchMock);
    renderWithProviders(
      <RunComparisonPage
        runIds={["bt-0001", "bt-0002"]}
        onOpenRun={() => undefined}
        onBack={() => undefined}
      />,
    );

    expect(await screen.findByTestId("error-state")).toHaveTextContent("too_few_runs");
  });

  it("explains a link with too few runs instead of waiting forever", async () => {
    const fetchMock = stubApi();
    renderWithProviders(
      <RunComparisonPage runIds={["bt-0001"]} onOpenRun={() => undefined} onBack={() => undefined} />,
    );

    expect(await screen.findByText("至少需要 2 次运行才能对比")).toBeInTheDocument();
    // Nothing is requested, so the page cannot sit on a loading state.
    expect(fetchMock.mock.calls.map((call) => String(call[0])).some((url) => url.includes("/compare"))).toBe(
      false,
    );
  });

  it("says so when none of the runs has a curve", async () => {
    stubApi({
      comparison: comparisonResponse({
        runs: (comparisonResponse()["runs"] as unknown[]).slice(2),
        warnings: [],
      }),
    });
    renderWithProviders(
      <RunComparisonPage
        runIds={["bt-0003", "bt-0004"]}
        onOpenRun={() => undefined}
        onBack={() => undefined}
      />,
    );

    expect(await screen.findByText("所选运行都没有可绘制的净值序列")).toBeInTheDocument();
  });
});
