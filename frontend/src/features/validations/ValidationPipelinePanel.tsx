import {
  useValidationFolds,
  useValidationStress,
  useValidationTrials,
} from "../../api/hooks";
import type {
  ValidationFoldView,
  ValidationStressView,
  ValidationTrialView,
} from "../../api/types";
import { DataTable, type Column } from "../../components/DataTable";
import { EmptyState, ErrorState, LoadingState } from "../../components/States";
import { formatDateOnly, formatPercentValue } from "../../format";

export interface ValidationPipelinePanelProps {
  runId: string;
}

const trialColumns: readonly Column<ValidationTrialView>[] = [
  {
    key: "trial_id",
    header: "试验 ID",
    render: (trial) => <span className="mono">{trial.trial_id}</span>,
  },
  {
    key: "parameters",
    header: "参数",
    render: (trial) =>
      trial.parameters.length === 0 ? (
        <span className="muted">未记录参数</span>
      ) : (
        <span className="mono">
          {trial.parameters
            .map((entry) => `${String(entry.name ?? "?")}=${String(entry.value ?? "?")}`)
            .join(", ")}
        </span>
      ),
  },
  {
    key: "metric",
    header: "指标",
    numeric: true,
    render: (trial) =>
      trial.metric === null ? <span className="muted">—</span> : String(trial.metric),
  },
  {
    key: "window",
    header: "验证区间",
    render: (trial) =>
      `${formatDateOnly(trial.validation_start)} → ${formatDateOnly(trial.validation_end)}`,
  },
  { key: "seed", header: "随机种子", numeric: true, render: (trial) => String(trial.seed) },
  {
    key: "failed_reason",
    header: "失败原因",
    render: (trial) =>
      trial.failed_reason === null ? (
        <span className="muted">—</span>
      ) : (
        <span className="muted">{trial.failed_reason}</span>
      ),
  },
];

const foldColumns: readonly Column<ValidationFoldView>[] = [
  { key: "fold_index", header: "折叠", numeric: true, render: (fold) => String(fold.fold_index) },
  {
    key: "train",
    header: "训练区间",
    render: (fold) => `${formatDateOnly(fold.train_start)} → ${formatDateOnly(fold.train_end)}`,
  },
  {
    key: "validation",
    header: "验证区间",
    render: (fold) =>
      `${formatDateOnly(fold.validation_start)} → ${formatDateOnly(fold.validation_end)}`,
  },
  {
    key: "test",
    header: "测试区间",
    render: (fold) => `${formatDateOnly(fold.test_start)} → ${formatDateOnly(fold.test_end)}`,
  },
  {
    key: "retrain_date",
    header: "重训练日期",
    render: (fold) =>
      fold.retrain_date === null ? (
        <span className="muted">—</span>
      ) : (
        <span className="mono">{formatDateOnly(fold.retrain_date)}</span>
      ),
  },
  {
    key: "backtest_run_id",
    header: "回测运行",
    render: (fold) =>
      fold.backtest_run_id === null ? (
        <span className="muted">未关联</span>
      ) : (
        <span className="mono">{fold.backtest_run_id}</span>
      ),
  },
];

const stressColumns: readonly Column<ValidationStressView>[] = [
  {
    key: "scenario_name",
    header: "情景",
    render: (result) => <span className="mono">{result.scenario_name}</span>,
  },
  { key: "scenario_type", header: "类型", render: (result) => result.scenario_type },
  {
    key: "applicable_markets",
    header: "适用市场",
    render: (result) => result.applicable_markets.join("、") || "—",
  },
  {
    key: "assumptions",
    header: "假设",
    render: (result) => (
      <span className="mono">{JSON.stringify(result.assumptions)}</span>
    ),
  },
  {
    key: "delta",
    header: "差异",
    render: (result) => {
      const impact = result.delta.net_value_impact_pct;
      return typeof impact === "number" ? (
        <span className="mono" data-testid={`stress-delta-${result.scenario_name}`}>
          {formatPercentValue(impact)}
        </span>
      ) : (
        <span className="mono">{JSON.stringify(result.delta)}</span>
      );
    },
  },
];

/**
 * The pipeline artifacts: parameter trials, walk-forward folds and stress
 * scenarios (MVP 5 / SP 5.29, SP 5.31, SP 5.32).
 *
 * All three share one panel because they share one state: the validation
 * pipeline that would write them has no caller in this code base yet, so each
 * section reports *why* it is empty and what would fill it. Three tabs holding
 * three identical "not yet produced" notices would hide how much of the
 * pipeline is missing; one panel shows it at once.
 */
export function ValidationPipelinePanel({ runId }: ValidationPipelinePanelProps) {
  const trials = useValidationTrials(runId);
  const folds = useValidationFolds(runId);
  const stress = useValidationStress(runId);

  return (
    <>
      <div className="card">
        <div className="card__header">
          <span className="card__title">参数试验</span>
          <span className="card__hint">SP 5.29</span>
        </div>
        <div className="card__body">
          {trials.isPending ? <LoadingState label="正在读取参数试验…" /> : null}
          {trials.isError ? (
            <ErrorState
              error={trials.error}
              onRetry={() => {
                void trials.refetch();
              }}
            />
          ) : null}
          {trials.data !== undefined && !trials.data.available ? (
            <EmptyState
              title="该运行没有参数试验记录"
              hint={
                <>
                  {trials.data.unavailable_reason}
                  <br />
                  这是「未产生」而不是「产生后为空」，因此不显示为 0 条试验。
                </>
              }
            />
          ) : null}
          {trials.data !== undefined && trials.data.available ? (
            <DataTable
              columns={trialColumns}
              rows={trials.data.trials}
              rowKey={(trial) => trial.trial_id}
              caption={`共 ${trials.data.trial_count} 条参数试验`}
            />
          ) : null}
        </div>
      </div>

      <div className="card">
        <div className="card__header">
          <span className="card__title">折叠（样本外）</span>
          <span className="card__hint">SP 5.32</span>
        </div>
        <div className="card__body">
          {folds.isPending ? <LoadingState label="正在读取折叠记录…" /> : null}
          {folds.isError ? (
            <ErrorState
              error={folds.error}
              onRetry={() => {
                void folds.refetch();
              }}
            />
          ) : null}
          {folds.data !== undefined && !folds.data.available ? (
            <EmptyState
              title="该运行没有折叠记录"
              hint={folds.data.unavailable_reason ?? "服务端未给出原因。"}
            />
          ) : null}
          {folds.data !== undefined && folds.data.available ? (
            <DataTable
              columns={foldColumns}
              rows={folds.data.folds}
              rowKey={(fold) => String(fold.fold_index)}
              caption={`共 ${folds.data.fold_count} 个折叠`}
            />
          ) : null}
        </div>
      </div>

      <div className="card">
        <div className="card__header">
          <span className="card__title">压力情景</span>
          <span className="card__hint">SP 5.31 · 假设与差异同时展示</span>
        </div>
        <div className="card__body">
          {stress.isPending ? <LoadingState label="正在读取压力情景…" /> : null}
          {stress.isError ? (
            <ErrorState
              error={stress.error}
              onRetry={() => {
                void stress.refetch();
              }}
            />
          ) : null}
          {stress.data !== undefined && !stress.data.available ? (
            <EmptyState
              title="该运行没有压力情景记录"
              hint={stress.data.unavailable_reason ?? "服务端未给出原因。"}
            />
          ) : null}
          {stress.data !== undefined && stress.data.available ? (
            <>
              <DataTable
                columns={stressColumns}
                rows={stress.data.results}
                rowKey={(result) => result.scenario_name}
                caption={`共 ${stress.data.stress_count} 个压力情景`}
              />
              <ul className="note-list" data-testid="validation-stress-notes">
                {stress.data.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
      </div>
    </>
  );
}
