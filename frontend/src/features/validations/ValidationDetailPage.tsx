import { useValidationRun } from "../../api/hooks";
import { VALIDATION_TABS, type ValidationTab } from "../../app/route";
import { ErrorState, LoadingState } from "../../components/States";
import { StatusBadge } from "../../components/StatusBadge";
import { ValidationCoveragePanel } from "./ValidationCoveragePanel";
import { ValidationOverviewPanel } from "./ValidationOverviewPanel";
import { ValidationPipelinePanel } from "./ValidationPipelinePanel";
import { ValidationReportExport } from "./ValidationReportExport";
import { ValidationSplitPanel } from "./ValidationSplitPanel";

const TAB_LABELS: Record<ValidationTab, string> = {
  overview: "概览与结论",
  split: "冻结切分",
  coverage: "覆盖与门槛",
  pipeline: "试验与情景",
  report: "报告导出",
};

export interface ValidationDetailPageProps {
  runId: string;
  tab: ValidationTab;
  onTabChange: (tab: ValidationTab) => void;
  onBack: () => void;
}

/**
 * One validation run's detail view (MVP 5 / SP 5.26–SP 5.35).
 *
 * The tab lives in the URL so a reviewer can link straight at the coverage table
 * or the frozen split. Each panel fetches independently: a missing coverage
 * measurement does not blank the split, and every panel reports its own failure
 * with its own retry.
 */
export function ValidationDetailPage({
  runId,
  tab,
  onTabChange,
  onBack,
}: ValidationDetailPageProps) {
  const run = useValidationRun(runId);

  return (
    <>
      <section className="section-heading">
        <nav className="crumbs" aria-label="面包屑">
          <button type="button" className="link-button" onClick={onBack}>
            样本外验证
          </button>
          <span aria-hidden="true">/</span>
          <span className="mono">{runId}</span>
          {typeof run.data?.status === "string" && run.data.status !== "" ? (
            <StatusBadge status={run.data.status} />
          ) : null}
        </nav>
        <h1 className="section-heading__title">
          验证运行详情 <span className="mono">{runId}</span>
        </h1>
        <p className="section-heading__subtitle">
          只读展示该次验证运行的冻结切分、实测数据覆盖与门槛判定、审计事件与报告导出。
          覆盖百分比由服务端实时测量；参数试验、折叠与压力情景在流水线写入之前保持为空，并写明原因。
        </p>
      </section>

      <div className="tabs" role="tablist" aria-label="验证运行详情分区">
        {VALIDATION_TABS.map((name) => (
          <button
            key={name}
            type="button"
            role="tab"
            id={`harbor-validation-tab-${name}`}
            aria-selected={name === tab}
            aria-controls={`harbor-validation-panel-${name}`}
            className={name === tab ? "tab tab--active" : "tab"}
            onClick={() => {
              onTabChange(name);
            }}
          >
            {TAB_LABELS[name]}
          </button>
        ))}
      </div>

      {run.isPending ? <LoadingState label="正在读取验证运行记录…" /> : null}
      {run.isError ? (
        <ErrorState
          error={run.error}
          onRetry={() => {
            void run.refetch();
          }}
        />
      ) : null}

      <div
        id={`harbor-validation-panel-${tab}`}
        role="tabpanel"
        aria-labelledby={`harbor-validation-tab-${tab}`}
      >
        {run.isSuccess && tab === "overview" ? <ValidationOverviewPanel runId={runId} /> : null}
        {run.isSuccess && tab === "split" ? <ValidationSplitPanel runId={runId} /> : null}
        {run.isSuccess && tab === "coverage" ? <ValidationCoveragePanel runId={runId} /> : null}
        {run.isSuccess && tab === "pipeline" ? <ValidationPipelinePanel runId={runId} /> : null}
        {run.isSuccess && tab === "report" ? (
          <div className="card">
            <div className="card__header">
              <span className="card__title">报告导出</span>
              <span className="card__hint">SP 5.34 · 服务端渲染</span>
            </div>
            <div className="card__body">
              <ValidationReportExport runId={runId} />
            </div>
          </div>
        ) : null}
      </div>
    </>
  );
}
