/**
 * Validation detail-page behaviour tests (MVP 5 / SP 5.26–SP 5.35).
 *
 * These assert the page's *claims*, which is where a research dashboard can lie
 * without looking broken: that an artifact the pipeline never wrote is stated as
 * missing rather than rendered as an empty table, that a passing coverage item
 * is not shown as failing its neighbour's threshold, and that a verdict cannot
 * be read without the notices about test-set reuse and re-tuning.
 */

import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";

import { createQueryClient } from "../../api/queryClient";
import type { ValidationTab } from "../../app/route";
import {
  jsonResponse,
  validationArtifactResponse,
  validationConclusion,
  validationCoverageResponse,
  validationEventsResponse,
  validationRunDetail,
  validationSplitResponse,
  validationWarningsResponse,
} from "../../testing";
import { ThemeProvider } from "../../theme/ThemeProvider";
import { ValidationDetailPage } from "./ValidationDetailPage";

interface StubPayloads {
  detail?: unknown;
  split?: unknown;
  coverage?: unknown;
  warnings?: unknown;
  events?: unknown;
  trials?: unknown;
  folds?: unknown;
  stress?: unknown;
}

/**
 * Stub the validation endpoints by URL.
 *
 * `/validations/val-1` is a prefix of every endpoint below it, so the stub
 * answers by suffix: handing the detail payload to the coverage panel would make
 * a missing section look like a rendering bug instead of missing data.
 */
function stubValidationApi(payloads: StubPayloads = {}) {
  const fetchMock = vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/split")) {
      return Promise.resolve(jsonResponse(payloads.split ?? validationSplitResponse()));
    }
    if (url.includes("/coverage")) {
      return Promise.resolve(jsonResponse(payloads.coverage ?? validationCoverageResponse()));
    }
    if (url.includes("/warnings")) {
      return Promise.resolve(jsonResponse(payloads.warnings ?? validationWarningsResponse()));
    }
    if (url.includes("/events")) {
      return Promise.resolve(jsonResponse(payloads.events ?? validationEventsResponse()));
    }
    if (url.includes("/trials")) {
      return Promise.resolve(jsonResponse(payloads.trials ?? validationArtifactResponse("trials")));
    }
    if (url.includes("/folds")) {
      return Promise.resolve(jsonResponse(payloads.folds ?? validationArtifactResponse("folds")));
    }
    if (url.includes("/stress")) {
      return Promise.resolve(jsonResponse(payloads.stress ?? validationArtifactResponse("results")));
    }
    if (url.includes("/version")) {
      return Promise.resolve(
        jsonResponse({
          name: "harbor-api",
          version: "0.1.0",
          api_version: "v1",
          read_only: true,
          auth_required: true,
          report_formats: ["json", "csv", "html"],
          validation_report_formats: ["json", "csv", "html"],
        }),
      );
    }
    return Promise.resolve(jsonResponse(payloads.detail ?? validationRunDetail()));
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderDetail(tab: ValidationTab, payloads: StubPayloads = {}) {
  const client = createQueryClient();
  stubValidationApi(payloads);
  const page: ReactElement = (
    <QueryClientProvider client={client}>
      <ThemeProvider>
        <ValidationDetailPage
          runId="val-1"
          tab={tab}
          onTabChange={() => undefined}
          onBack={() => undefined}
        />
      </ThemeProvider>
    </QueryClientProvider>
  );
  return render(page);
}

describe("overview tab (SP 5.26, SP 5.27, SP 5.33, SP 5.35)", () => {
  it("shows the frozen fingerprint and when it was frozen", async () => {
    renderDetail("overview");

    expect(await screen.findByTestId("dataset-fingerprint")).toHaveTextContent("fp-oos-1");
    // The freeze time comes from the event log, not from a timestamp column.
    expect(screen.getByTestId("frozen-at")).toHaveTextContent("2026-02-01");
  });

  it("says a run has no verdict instead of implying it passed", async () => {
    renderDetail("overview");

    expect(await screen.findByText("该运行暂无结论记录")).toBeInTheDocument();
    expect(screen.queryByTestId("validation-verdict")).not.toBeInTheDocument();
  });

  it("renders the verdict with its limitations and the anti-misreading notices", async () => {
    renderDetail("overview", {
      detail: validationRunDetail({ conclusion: validationConclusion() }),
    });

    const verdict = await screen.findByTestId("validation-verdict");
    expect(verdict.textContent).toContain("待定");
    // INCONCLUSIVE must never read as a soft pass.
    expect(verdict.textContent).toContain("不等于通过");
    expect(await screen.findByTestId("validation-limitations")).toHaveTextContent("single_regime");
    const notices = await screen.findByTestId("validation-notices");
    expect(notices.textContent).toContain("测试集只解锁一次");
    expect(notices.textContent).toContain("不算通过");
  });

  it("lists the audit trail with the creation event marked as having no predecessor", async () => {
    renderDetail("overview");

    const events = await screen.findByTestId("validation-events");
    expect(events.textContent).toContain("（创建） → DRAFT");
    expect(events.textContent).toContain("DRAFT → DATA_FROZEN");
    expect(events.textContent).toContain("validation freeze");
  });

  it("says the freeze time is unknowable when the event log is empty", async () => {
    renderDetail("overview", {
      detail: validationRunDetail({
        frozen_at: null,
        counts: { trials: 0, folds: 0, stress_results: 0, warnings: 5, events: 0 },
      }),
      events: validationEventsResponse({ event_count: 0, frozen_at: null, events: [] }),
    });

    expect(await screen.findByTestId("frozen-at")).toHaveTextContent("尚未冻结");
    expect(await screen.findByText("没有审计事件记录")).toBeInTheDocument();
  });
});

describe("split tab (SP 5.28)", () => {
  it("draws the three segments with their dates and the split hash", async () => {
    renderDetail("split");

    const timeline = await screen.findByTestId("validation-split-timeline");
    expect(timeline.textContent).toContain("训练");
    expect(timeline.textContent).toContain("2019-01-01");
    expect(timeline.textContent).toContain("2022-12-31");
    expect(await screen.findByTestId("split-hash")).toHaveTextContent("split-hash-1");
  });

  it("explains a missing split rather than showing empty dates", async () => {
    renderDetail("split", {
      split: validationSplitResponse({
        available: false,
        unavailable_reason: "该运行没有冻结切分记录（SP 3.12）。",
        split: null,
      }),
    });

    expect(await screen.findByText("该运行没有冻结切分记录")).toBeInTheDocument();
    expect(screen.queryByTestId("validation-split-timeline")).not.toBeInTheDocument();
  });
});

describe("coverage tab (SP 5.30, SP 5.33)", () => {
  it("keeps the raw counts next to the percentage and marks the failing item", async () => {
    renderDetail("coverage");

    // 741 / 989 is the measurement; 74.92% alone could not be checked.
    expect(await screen.findByText("741 / 989")).toBeInTheDocument();
    expect(screen.getByText("74.92%")).toBeInTheDocument();
    expect(screen.getByTestId("gap-HK-prices")).toHaveTextContent("248 个交易日缺少行情数据");
  });

  it("does not let one market's failure make a passing item look failed", async () => {
    renderDetail("coverage");

    // The stock pool is fully covered and carries no gap text.
    expect(await screen.findByText("89 / 89")).toBeInTheDocument();
    expect(screen.getByText("100.00%")).toBeInTheDocument();
    expect(screen.getAllByText("通过门槛").length).toBe(1);
  });

  it("reports a fingerprint mismatch as drift rather than as a rendering detail", async () => {
    renderDetail("coverage", {
      coverage: validationCoverageResponse({
        current_fingerprint: "fp-oos-2",
        fingerprint_matches: false,
      }),
    });

    const drift = await screen.findByTestId("fingerprint-matches");
    expect(drift.textContent).toContain("不一致");
    expect(drift.textContent).toContain("冻结后数据已变化");
  });

  it("states the reason when the coverage cannot be measured", async () => {
    renderDetail("coverage", {
      coverage: validationCoverageResponse({
        available: false,
        unavailable_reason: "无法测量覆盖：该运行的 config_snapshot 无法重建为验证配置（SP 3.69）。",
        items: [],
      }),
    });

    expect(await screen.findByText("无法测量数据覆盖")).toBeInTheDocument();
    expect(screen.getByText(/无法重建为验证配置/)).toBeInTheDocument();
  });

  it("warns that an empty warning list is not evidence of good coverage", async () => {
    renderDetail("coverage", {
      warnings: validationWarningsResponse({ warning_count: 0, items: [] }),
    });

    const empty = (await screen.findAllByTestId("empty-state")).map((node) => node.textContent);
    expect(empty.join(" ")).toContain("警告只记录未通过门槛的项");
  });

  it("renders the recorded warning with its severity and code", async () => {
    renderDetail("coverage");

    const list = await screen.findByTestId("validation-warning-list");
    expect(list.textContent).toContain("coverage.prices");
    expect(list.textContent).toContain("248 个交易日缺少行情数据");
    expect(list.textContent).toContain("error");
  });

  it("shows the gate's third severity level and explains the stored two", async () => {
    renderDetail("coverage", {
      warnings: validationWarningsResponse({
        items: [
          {
            warning_code: "coverage.corporate_actions",
            // The stored level is the two-value vocabulary the table allows...
            severity: "warning",
            message: "该市场窗口内没有企业行动记录",
            // ...while the gate's own verdict survives only in the context.
            context: { market: "HK", item: "corporate_actions", severity: "not_qualified" },
            created_at: "2026-02-01T01:00:00+00:00",
          },
        ],
      }),
    });

    const list = await screen.findByTestId("validation-warning-list");
    // The two panels must not look like they disagree about the same item.
    expect(list.textContent).toContain("不合格（not_qualified）");
    expect(list.textContent).toContain("落库档位：warning");
    const notes = await screen.findByTestId("validation-warning-notes");
    expect(notes.textContent).toContain("CHECK 约束");
  });
});

describe("pipeline tab (SP 5.29, SP 5.31, SP 5.32)", () => {
  it("names why each unwritten artifact is missing instead of showing zero rows", async () => {
    renderDetail("pipeline");

    const states = (await screen.findAllByTestId("empty-state")).map((node) => node.textContent);
    expect(states).toHaveLength(3);
    const joined = states.join(" ");
    expect(joined).toContain("该运行没有参数试验记录");
    expect(joined).toContain("该运行没有折叠记录");
    expect(joined).toContain("该运行没有压力情景记录");
    expect(joined).toContain("尚无调用方");
    // A table with a zero row count would read as "measured and empty".
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("states that trials were not produced, not that they came out empty", async () => {
    renderDetail("pipeline");

    const states = (await screen.findAllByTestId("empty-state")).map((node) => node.textContent);
    expect(states.join(" ")).toContain("未产生");
  });
});

describe("report tab (SP 5.34)", () => {
  it("offers exactly the formats the server declares", async () => {
    renderDetail("report");

    expect(await screen.findByTestId("validation-export-json")).toBeInTheDocument();
    expect(screen.getByTestId("validation-export-csv")).toBeInTheDocument();
    expect(screen.getByTestId("validation-export-html")).toBeInTheDocument();
    const note = await screen.findByTestId("validation-report-export-note");
    expect(note.textContent).toContain("harbor-cli validation report");
  });

  it("offers no download button when the server declares no formats", async () => {
    stubValidationApi({});
    const client = createQueryClient();
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/version")) {
          return Promise.resolve(
            jsonResponse({
              name: "harbor-api",
              version: "0.1.0",
              api_version: "v1",
              read_only: true,
              auth_required: true,
              report_formats: ["json"],
              validation_report_formats: [],
            }),
          );
        }
        return Promise.resolve(jsonResponse(validationRunDetail()));
      }),
    );

    render(
      <QueryClientProvider client={client}>
        <ThemeProvider>
          <ValidationDetailPage
            runId="val-1"
            tab="report"
            onTabChange={() => undefined}
            onBack={() => undefined}
          />
        </ThemeProvider>
      </QueryClientProvider>,
    );

    expect(await screen.findByText("服务端未声明可导出的格式，因此不提供导出按钮。")).toBeInTheDocument();
    expect(screen.queryByTestId("validation-export-json")).not.toBeInTheDocument();
  });
});
