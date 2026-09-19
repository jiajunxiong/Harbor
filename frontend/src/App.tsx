import { QueryClientProvider } from "@tanstack/react-query";
import { useCallback, useState } from "react";

import { createQueryClient } from "./api/queryClient";
import {
  DEFAULT_PAGE_SIZE,
  DEFAULT_ROUTE,
  type RunListSelection,
  type RunTab,
  type ValidationTab,
} from "./app/route";
import { useHashRoute } from "./app/useHashRoute";
import { ApiStatus } from "./components/ApiStatus";
import { ResearchDisclaimer } from "./components/ResearchDisclaimer";
import { ThemeToggle } from "./components/ThemeToggle";
import { BacktestRunsPage } from "./features/backtests/BacktestRunsPage";
import { RunComparisonPage } from "./features/backtests/RunComparisonPage";
import { RunDetailPage } from "./features/backtests/RunDetailPage";
import { ValidationDetailPage } from "./features/validations/ValidationDetailPage";
import { ValidationRunsPage } from "./features/validations/ValidationRunsPage";
import { ThemeProvider } from "./theme/ThemeProvider";

function AppShell() {
  const [route, navigate] = useHashRoute();

  const openRunList = useCallback(
    (selection: RunListSelection) => {
      navigate({ kind: "runs", selection });
    },
    [navigate],
  );

  const openRun = useCallback(
    (runId: string) => {
      navigate({ kind: "run", runId, tab: "overview" });
    },
    [navigate],
  );

  const openComparison = useCallback(
    (runIds: string[]) => {
      navigate({ kind: "comparison", runIds });
    },
    [navigate],
  );

  const showBacktests = useCallback(() => {
    navigate(DEFAULT_ROUTE);
  }, [navigate]);

  const showValidations = useCallback(() => {
    navigate({ kind: "validations", limit: DEFAULT_PAGE_SIZE, offset: 0 });
  }, [navigate]);

  const openValidation = useCallback(
    (runId: string) => {
      navigate({ kind: "validation", runId, tab: "overview" });
    },
    [navigate],
  );

  const backToValidations = useCallback(() => {
    navigate({ kind: "validations", limit: DEFAULT_PAGE_SIZE, offset: 0 });
  }, [navigate]);

  const inValidations = route.kind === "validations" || route.kind === "validation";

  return (
    <div className="app">
      <header className="app__header">
        <div className="app__header-inner">
          <div className="app__brand">
            <span className="app__brand-name">Harbor 监控看板</span>
            <span className="app__brand-tagline">
              只读展示回测、样本外验证与模拟盘结果 · 无下单与写入入口
            </span>
          </div>
          <nav className="crumbs" aria-label="主视图切换">
            <button
              type="button"
              className={inValidations ? "link-button" : "link-button link-button--active"}
              aria-current={inValidations ? undefined : "page"}
              data-testid="nav-backtests"
              onClick={showBacktests}
            >
              回测运行
            </button>
            <span aria-hidden="true">|</span>
            <button
              type="button"
              className={inValidations ? "link-button link-button--active" : "link-button"}
              aria-current={inValidations ? "page" : undefined}
              data-testid="nav-validations"
              onClick={showValidations}
            >
              样本外验证
            </button>
          </nav>
          <ApiStatus />
          <ThemeToggle />
        </div>
      </header>

      <main className="app__main">
        <ResearchDisclaimer />
        {route.kind === "runs" ? (
          <BacktestRunsPage
            selection={route.selection}
            onSelectionChange={openRunList}
            onOpenRun={openRun}
            onCompare={openComparison}
          />
        ) : null}
        {route.kind === "run" ? (
          <RunDetailPage
            runId={route.runId}
            tab={route.tab}
            onTabChange={(tab: RunTab) => {
              navigate({ kind: "run", runId: route.runId, tab });
            }}
            onBack={() => {
              navigate(DEFAULT_ROUTE);
            }}
            onOpenRun={openRun}
          />
        ) : null}
        {route.kind === "comparison" ? (
          <RunComparisonPage
            runIds={route.runIds}
            onOpenRun={openRun}
            onBack={() => {
              navigate(DEFAULT_ROUTE);
            }}
          />
        ) : null}
        {route.kind === "validations" ? (
          <ValidationRunsPage
            limit={route.limit}
            offset={route.offset}
            onSelectionChange={(limit, offset) => {
              navigate({ kind: "validations", limit, offset });
            }}
            onOpenRun={openValidation}
          />
        ) : null}
        {route.kind === "validation" ? (
          <ValidationDetailPage
            runId={route.runId}
            tab={route.tab}
            onTabChange={(tab: ValidationTab) => {
              navigate({ kind: "validation", runId: route.runId, tab });
            }}
            onBack={backToValidations}
          />
        ) : null}
      </main>

      <footer className="app__footer">
        <div className="app__footer-inner">
          <ResearchDisclaimer variant="compact" />
        </div>
      </footer>
    </div>
  );
}

/** Application root: data layer, theme layer, then the read-only shell (SP 5.9). */
export function App() {
  const [queryClient] = useState(() => createQueryClient());

  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <AppShell />
      </ThemeProvider>
    </QueryClientProvider>
  );
}
