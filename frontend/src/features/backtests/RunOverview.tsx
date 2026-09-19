import { StatusBadge } from "../../components/StatusBadge";
import { EMPTY_VALUE, formatCount, formatDateOnly, formatTimestamp } from "../../format";
import type { BacktestRunDetail } from "../../api/types";

export interface RunOverviewProps {
  run: BacktestRunDetail;
}

interface CountSpec {
  key: string;
  label: string;
  value: number;
  /** True when the count is expected to be zero by design, not by coincidence. */
  alwaysEmpty?: boolean;
  hint?: string;
}

/**
 * A run's identity and persisted footprint (MVP 5 / SP 5.14).
 *
 * The counts are labelled with what they mean. `持仓快照 0` and `成交 13274`
 * look equally authoritative as bare numbers, but the zero is a statement about
 * the persistence layer rather than about the strategy, so it is marked.
 */
export function RunOverview({ run }: RunOverviewProps) {
  const specs: CountSpec[] = [
    { key: "net_value_points", label: "净值点位", value: run.counts.net_value_points },
    {
      key: "fills",
      label: "成交",
      value: run.counts.fills,
    },
    { key: "rejected_trades", label: "拒单", value: run.counts.rejected_trades },
    {
      key: "positions",
      label: "持仓快照",
      value: run.counts.positions,
      alwaysEmpty: true,
      hint: "当前落库不写入持仓行",
    },
    {
      key: "metrics",
      label: "绩效行",
      value: run.counts.metrics,
      alwaysEmpty: true,
      hint: "指标改为按净值实时计算",
    },
  ];

  return (
    <>
      <div className="card">
        <div className="card__header">
          <span className="card__title">运行标识</span>
          <StatusBadge status={run.status} />
        </div>
        <div className="card__body">
          <dl className="kv">
            <dt>运行 ID</dt>
            <dd className="mono" data-testid="run-id">
              {run.run_id}
            </dd>
            <dt>策略</dt>
            <dd>
              {run.strategy} · {run.strategy_version}
            </dd>
            <dt>代码版本</dt>
            <dd className="mono">{run.code_version}</dd>
            <dt>配置指纹</dt>
            <dd className="mono">{run.config_hash}</dd>
            <dt>数据截点</dt>
            <dd className="mono">{formatDateOnly(run.data_cutoff)}</dd>
            <dt>开始时间</dt>
            <dd>{formatTimestamp(run.started_at)}</dd>
            <dt>结束时间</dt>
            <dd>{formatTimestamp(run.finished_at)}</dd>
            <dt>续跑自</dt>
            <dd className="mono">{run.resume_of ?? EMPTY_VALUE}</dd>
            <dt>失败原因</dt>
            <dd>{run.error_summary ?? EMPTY_VALUE}</dd>
          </dl>
          {run.error_summary !== null ? (
            <p className="notice" data-testid="run-failure-note">
              该运行以失败结束，失败原因如上。失败运行同样如实呈现，且不提供任何重跑入口；
              复现与排查请使用 CLI。
            </p>
          ) : null}
        </div>
      </div>

      <div className="card">
        <div className="card__header">
          <span className="card__title">已落库数据量</span>
          <span className="card__hint">数字取自运行记录本身，非前端统计</span>
        </div>
        <div className="card__body">
          <div className="metric-strip">
            {specs.map((spec) => (
              <div className="metric" key={spec.key} data-testid={`count-${spec.key}`}>
                <span className="field__label">{spec.label}</span>
                <span className="metric__value">{formatCount(spec.value)}</span>
                {spec.hint !== undefined ? (
                  <span className="card__hint">{spec.hint}</span>
                ) : null}
                {spec.alwaysEmpty === true && spec.value === 0 ? (
                  <span className="badge badge--neutral">按设计为空</span>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card__header">
          <span className="card__title">配置快照</span>
          <span className="card__hint">敏感字段已由服务端脱敏（SP 5.5）</span>
        </div>
        <div className="card__body">
          <details data-testid="config-snapshot">
            <summary>展开原始配置</summary>
            <pre className="mono code-block">{JSON.stringify(run.config_snapshot, null, 2)}</pre>
          </details>
        </div>
      </div>
    </>
  );
}
