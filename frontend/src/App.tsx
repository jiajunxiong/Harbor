import { QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import { createQueryClient } from "./api/queryClient";
import { ApiStatus } from "./components/ApiStatus";
import { ResearchDisclaimer } from "./components/ResearchDisclaimer";
import { ThemeToggle } from "./components/ThemeToggle";
import { BacktestRunsPage } from "./features/backtests/BacktestRunsPage";
import { ThemeProvider } from "./theme/ThemeProvider";

function AppShell() {
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
          <ApiStatus />
          <ThemeToggle />
        </div>
      </header>

      <main className="app__main">
        <ResearchDisclaimer />
        <BacktestRunsPage />
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
