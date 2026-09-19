/**
 * Validation list behaviour (MVP 5 / SP 5.26), including the state the page is in
 * after the development database was cleaned.
 *
 * With no test runs left in the database the empty state *is* what a reader sees
 * first, so it has to say what is missing and how to produce it — and it must not
 * blame pagination for an empty history, which is the failure mode that makes a
 * reader think runs exist but the table is broken.
 */

import { QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";

import { createQueryClient } from "../../api/queryClient";
import { jsonResponse, pageOf, validationRun } from "../../testing";
import { ThemeProvider } from "../../theme/ThemeProvider";
import { ValidationRunsPage } from "./ValidationRunsPage";

function stubRuns(payload: unknown) {
  const fetchMock = vi.fn(() => Promise.resolve(jsonResponse(payload)));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function renderList(limit = 25, offset = 0, onSelectionChange = vi.fn()): void {
  const page: ReactElement = (
    <QueryClientProvider client={createQueryClient()}>
      <ThemeProvider>
        <ValidationRunsPage
          limit={limit}
          offset={offset}
          onSelectionChange={onSelectionChange}
          onOpenRun={() => undefined}
        />
      </ThemeProvider>
    </QueryClientProvider>
  );
  render(page);
}

describe("validation list (SP 5.26)", () => {
  it("blames the empty history, not the offset, when there are no runs at all", async () => {
    stubRuns(pageOf([]));
    renderList();

    const state = await screen.findByTestId("empty-state");
    expect(state.textContent).toContain("暂无验证运行记录");
    expect(state.textContent).toContain("harbor-cli validation run");
    // Nothing was skipped over: there is simply nothing recorded yet.
    expect(state.textContent).not.toContain("越过");
    expect(state.textContent).not.toContain("上一页");
  });

  it("distinguishes an empty page past the end from an empty history", async () => {
    stubRuns(pageOf([], { total: 6, offset: 25 }));
    renderList(25, 25);

    const state = await screen.findByTestId("empty-state");
    expect(state.textContent).toContain("当前页没有数据");
    expect(state.textContent).toContain("这不代表历史为空");
  });

  it("shows the run's identity and an entry point into the detail page", async () => {
    stubRuns(pageOf([validationRun()]));
    renderList();

    expect(await screen.findByText("val-1")).toBeInTheDocument();
    expect(screen.getByText("DATA_FROZEN")).toBeInTheDocument();
    // No test set is unlocked yet, and that reads differently from "no column".
    expect(screen.getByText("未解锁")).toBeInTheDocument();
    expect(screen.getByTestId("open-validation-val-1")).toBeInTheDocument();
  });

  it("resets to the first page when the page size changes", async () => {
    stubRuns(pageOf([validationRun()], { total: 3, offset: 25 }));
    const onSelectionChange = vi.fn();
    renderList(25, 25, onSelectionChange);

    await screen.findByText("val-1");
    fireEvent.change(document.getElementById("harbor-validation-page-size") as HTMLElement, {
      target: { value: "10" },
    });

    // Keeping offset 25 would land on an empty page that looks like "no runs".
    expect(onSelectionChange).toHaveBeenCalledWith(10, 0);
  });
});
