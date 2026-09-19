import { useState } from "react";

import { useBacktestRun } from "../../api/hooks";
import { ErrorState, LoadingState } from "../../components/States";
import { StatusBadge } from "../../components/StatusBadge";
import { RUN_TABS, type RunTab } from "../../app/route";
import { AttributionPanel } from "./AttributionPanel";
import { DrawdownTable } from "./DrawdownTable";
import { FillsPanel } from "./FillsPanel";
import { MetricCards } from "./MetricCards";
import { NetValueChart } from "./NetValueChart";
import { RejectionsPanel } from "./RejectionsPanel";
import { ReplayPanel } from "./ReplayPanel";
import { ReportExport } from "./ReportExport";
import { RunOverview } from "./RunOverview";

const TAB_LABELS: Record<RunTab, string> = {
  overview: "概览",
  performance: "绩效与回撤",
  trades: "成交与拒单",
  attribution: "持仓与归因",
  replay: "重放与导出",
};

export interface RunDetailPageProps {
  runId: string;
  tab: RunTab;
  onTabChange: (tab: RunTab) => void;
  onBack: () => void;
  onOpenRun?: (runId: string) => void;
}

/**
 * One run's detail view (MVP 5 / SP 5.14–5.20).
 *
 * The tab lives in the URL, so a link can point straight at the drawdown table
 * or the trade list (SP 5.24). Every panel fetches independently: a failure in
 * the trade list does not blank the equity curve, and each panel reports its own
 * error with its own retry.
 */
export function RunDetailPage({ runId, tab, onTabChange, onBack, onOpenRun }: RunDetailPageProps) {
  const run = useBacktestRun(runId);
  // The annotated drawdown threshold lives here so the curve and the drawdown
  // table describe the same intervals (SP 5.24).
  const [bandThreshold, setBandThreshold] = useState<number | null>(null);

  return (
    <>
      <section className="section-heading">
        <nav className="crumbs" aria-label="面包屑">
          <button type="button" className="link-button" onClick={onBack}>
            回测运行
          </button>
          <span aria-hidden="true">/</span>
          <span className="mono">{runId}</span>
          {/* A missing status renders as nothing rather than crashing the page;
              an unknown value must not be shown as a neutral verdict either. */}
          {typeof run.data?.status === "string" && run.data.status !== "" ? (
            <StatusBadge status={run.data.status} />
          ) : null}
        </nav>
        <h1 className="section-heading__title">
          运行详情 <span className="mono">{runId}</span>
        </h1>
        <p className="section-heading__subtitle">
          只读展示该次运行的落库数据。指标与回撤由服务端按已落库净值序列、复用 CLI
          同一套核心函数计算，前端不做重算或近似。
        </p>
      </section>

      <div className="tabs" role="tablist" aria-label="运行详情分区">
        {RUN_TABS.map((name) => (
          <button
            key={name}
            type="button"
            role="tab"
            id={`harbor-tab-${name}`}
            aria-selected={name === tab}
            aria-controls={`harbor-panel-${name}`}
            className={name === tab ? "tab tab--active" : "tab"}
            onClick={() => {
              onTabChange(name);
            }}
          >
            {TAB_LABELS[name]}
          </button>
        ))}
      </div>

      <div id={`harbor-panel-${tab}`} role="tabpanel" aria-labelledby={`harbor-tab-${tab}`}>
        {tab === "performance" ? (
          <>
            <div className="card">
              <div className="card__header">
                <span className="card__title">绩效指标</span>
                <span className="card__hint">SP 5.16</span>
              </div>
              <div className="card__body">
                <MetricCards runId={runId} />
              </div>
            </div>

            <div className="card">
              <div className="card__header">
                <span className="card__title">净值曲线</span>
                <span className="card__hint">SP 5.15 · 可缩放时间范围</span>
              </div>
              <div className="card__body">
                <NetValueChart
                  runId={runId}
                  bandThreshold={bandThreshold}
                  onThresholdChange={setBandThreshold}
                />
              </div>
            </div>

            <div className="card">
              <div className="card__header">
                <span className="card__title">回撤事件</span>
                <span className="card__hint">SP 5.17 · 按阈值触发，可与曲线联动</span>
              </div>
              <div className="card__body">
                <DrawdownTable
                  runId={runId}
                  selectedThreshold={bandThreshold}
                  onSelectThreshold={setBandThreshold}
                />
              </div>
            </div>
          </>
        ) : null}

        {tab === "trades" ? (
          <>
            <div className="card">
              <div className="card__header">
                <span className="card__title">成交明细</span>
                <span className="card__hint">SP 5.18</span>
              </div>
              <div className="card__body">
                <FillsPanel runId={runId} />
              </div>
            </div>

            <div className="card">
              <div className="card__header">
                <span className="card__title">拒单与原因分布</span>
                <span className="card__hint">SP 5.18</span>
              </div>
              <div className="card__body">
                <RejectionsPanel runId={runId} />
              </div>
            </div>
          </>
        ) : null}

        {tab === "overview" ? (
          <>
            {run.isPending ? <LoadingState label="正在读取运行记录…" /> : null}
            {run.isError ? (
              <ErrorState
                error={run.error}
                onRetry={() => {
                  void run.refetch();
                }}
              />
            ) : null}
            {run.data !== undefined ? <RunOverview run={run.data} /> : null}
          </>
        ) : null}

        {tab === "attribution" ? (
          <>
            {run.isPending ? <LoadingState label="正在读取运行记录…" /> : null}
            {run.isError ? (
              <ErrorState
                error={run.error}
                onRetry={() => {
                  void run.refetch();
                }}
              />
            ) : null}
            {run.data !== undefined ? <AttributionPanel run={run.data} /> : null}
          </>
        ) : null}

        {tab === "replay" ? (
          <>
            <div className="card">
              <div className="card__header">
                <span className="card__title">报告导出</span>
                <span className="card__hint">SP 5.22 · 服务端渲染</span>
              </div>
              <div className="card__body">
                <ReportExport runId={runId} />
              </div>
            </div>

            <div className="card">
              <div className="card__header">
                <span className="card__title">重放清单与一致性</span>
                <span className="card__hint">SP 5.21 · 相同输入指纹是否一致</span>
              </div>
              <div className="card__body">
                <ReplayPanel
                  runId={runId}
                  runStatus={run.data?.status ?? ""}
                  onOpenRun={onOpenRun ?? (() => undefined)}
                />
              </div>
            </div>
          </>
        ) : null}
      </div>
    </>
  );
}
