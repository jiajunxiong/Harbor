import { useValidationEvents, useValidationRun } from "../../api/hooks";
import { formatCount } from "../../format";
import { EmptyState, ErrorState, LoadingState } from "../../components/States";
import { StatusBadge } from "../../components/StatusBadge";

export interface ValidationOverviewPanelProps {
  runId: string;
}

const VERDICT_LABELS: Record<string, string> = {
  QUALIFIED: "通过（样本外证据支持）",
  NOT_QUALIFIED: "未通过",
  INCONCLUSIVE: "待定（证据不足）",
};

const VERDICT_HINTS: Record<string, string> = {
  QUALIFIED: "结论表示证据支持，不承诺未来收益；请连同限制与提示语一起阅读。",
  NOT_QUALIFIED: "结论表示证据不支持该配置；仍可查看覆盖与审计记录复核原因。",
  INCONCLUSIVE: "INCONCLUSIVE 不等于通过：证据不足时结论为待定。",
};

/**
 * Render one evidence field.
 *
 * Deliberately unit-free: the evidence dictionary is heterogeneous and its keys
 * carry no unit, so formatting a number as a percentage would invent one (a
 * Sharpe of 0.9 is not 90%).
 */
function renderEvidenceValue(value: unknown): string {
  if (typeof value === "object" && value !== null) {
    return JSON.stringify(value);
  }
  return String(value);
}

/**
 * The run's identity: status, frozen fingerprint, freeze time and verdict
 * (MVP 5 / SP 5.26, SP 5.27, SP 5.33, SP 5.35).
 *
 * The conclusion is rendered here rather than in a tab of its own, and it is
 * always rendered *with* its limitations and the anti-misreading notices: the
 * specification's whole point is that a verdict must not be readable on its own.
 */
export function ValidationOverviewPanel({ runId }: ValidationOverviewPanelProps) {
  const run = useValidationRun(runId);
  const events = useValidationEvents(runId);

  if (run.isPending) {
    return <LoadingState label="正在读取验证运行记录…" />;
  }
  if (run.isError) {
    return (
      <ErrorState
        error={run.error}
        onRetry={() => {
          void run.refetch();
        }}
      />
    );
  }
  if (run.data === undefined) {
    return null;
  }

  const detail = run.data;
  const conclusion = detail.conclusion;
  const limitations = conclusion?.limitations ?? [];
  const evidence = conclusion?.evidence ?? {};

  return (
    <>
      <div className="card">
        <div className="card__header">
          <span className="card__title">运行概览</span>
          <span className="card__hint">SP 5.26 · 状态、指纹与冻结时间</span>
        </div>
        <div className="card__body">
          <dl className="kv">
            <div>
              <dt>运行 ID</dt>
              <dd className="mono">{detail.run_id}</dd>
            </div>
            <div>
              <dt>状态</dt>
              <dd>
                <StatusBadge status={detail.status} />
              </dd>
            </div>
            <div>
              <dt>数据集指纹</dt>
              <dd data-testid="dataset-fingerprint">
                {detail.dataset_fingerprint === null ? (
                  <span className="muted">未冻结（无清单记录）</span>
                ) : (
                  <span className="mono">{detail.dataset_fingerprint}</span>
                )}
              </dd>
            </div>
            <div>
              <dt>冻结时间</dt>
              <dd data-testid="frozen-at">
                {detail.frozen_at === null ? (
                  <span className="muted">尚未冻结</span>
                ) : (
                  <time dateTime={detail.frozen_at}>{detail.frozen_at}</time>
                )}
              </dd>
            </div>
            <div>
              <dt>配置哈希</dt>
              <dd className="mono">{detail.config_hash}</dd>
            </div>
            <div>
              <dt>代码版本</dt>
              <dd className="mono">{detail.code_version}</dd>
            </div>
            <div>
              <dt>测试集登记</dt>
              <dd>
                {detail.test_set_id === null ? (
                  <span className="muted">未解锁（尚未评测）</span>
                ) : (
                  <span className="mono">{detail.test_set_id}</span>
                )}
              </dd>
            </div>
            <div>
              <dt>创建时间</dt>
              <dd>
                <time dateTime={detail.created_at}>{detail.created_at}</time>
              </dd>
            </div>
          </dl>

          {detail.error_summary === null ? null : (
            <p className="notice" role="alert">
              运行记录了错误摘要：{detail.error_summary}
            </p>
          )}

          <div className="metric-strip" data-testid="artifact-counts">
            <div className="metric">
              <span className="metric__label">参数试验</span>
              <span className="metric__value">{formatCount(detail.counts.trials)}</span>
            </div>
            <div className="metric">
              <span className="metric__label">折叠（OOS）</span>
              <span className="metric__value">{formatCount(detail.counts.folds)}</span>
            </div>
            <div className="metric">
              <span className="metric__label">压力情景</span>
              <span className="metric__value">{formatCount(detail.counts.stress_results)}</span>
            </div>
            <div className="metric">
              <span className="metric__label">覆盖警告</span>
              <span className="metric__value">{formatCount(detail.counts.warnings)}</span>
            </div>
            <div className="metric">
              <span className="metric__label">审计事件</span>
              <span className="metric__value">{formatCount(detail.counts.events)}</span>
            </div>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card__header">
          <span className="card__title">结论与限制</span>
          <span className="card__hint">SP 5.27 / SP 5.33 · 结论不与限制分离展示</span>
        </div>
        <div className="card__body">
          {conclusion === null ? (
            <EmptyState
              title="该运行暂无结论记录"
              hint="结论由验证流水线评测后写入 validation_conclusions；未评测就显示为通过会误导读者，因此此处留空。"
            />
          ) : (
            <>
              <p className="verdict" data-testid="validation-verdict">
                <span className="verdict__label">
                  {VERDICT_LABELS[conclusion.conclusion] ?? conclusion.conclusion}
                </span>
                <span className="verdict__hint">
                  {VERDICT_HINTS[conclusion.conclusion] ?? "未知结论取值，请以服务端记录为准。"}
                </span>
              </p>
              <dl className="kv">
                <div>
                  <dt>规则版本</dt>
                  <dd className="mono">{conclusion.rule_version}</dd>
                </div>
                <div>
                  <dt>记录时间</dt>
                  <dd>
                    <time dateTime={conclusion.created_at}>{conclusion.created_at}</time>
                  </dd>
                </div>
              </dl>

              <h3 className="card__subtitle">限制</h3>
              {limitations.length === 0 ? (
                <p className="notice" data-testid="validation-limitations-empty">
                  该结论没有登记限制条目。这不代表结论没有适用范围：数据范围、市场与成本假设的限制见覆盖与报告分区。
                </p>
              ) : (
                <ul className="summary-list" data-testid="validation-limitations">
                  {limitations.map((limitation, index) => (
                    <li key={index}>
                      <span className="mono">
                        {String(limitation.code ?? limitation.kind ?? "limitation")}
                      </span>
                      ：{String(limitation.detail ?? limitation.message ?? JSON.stringify(limitation))}
                    </li>
                  ))}
                </ul>
              )}

              <h3 className="card__subtitle">证据</h3>
              {Object.keys(evidence).length === 0 ? (
                <p className="notice">结论未登记证据字段；请以覆盖与报告分区记录为准。</p>
              ) : (
                <ul className="summary-list" data-testid="validation-evidence">
                  {Object.entries(evidence).map(([key, value]) => (
                    <li key={key}>
                      <span className="mono">{key}</span>：{renderEvidenceValue(value)}
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
      </div>

      <div className="card">
        <div className="card__header">
          <span className="card__title">反误读提示</span>
          <span className="card__hint">SP 5.35 · 由服务端随结论下发</span>
        </div>
        <div className="card__body">
          {detail.notices.length === 0 ? (
            <p className="notice">服务端未下发提示语；请勿在缺少提示的情况下引用结论。</p>
          ) : (
            <ul className="summary-list" data-testid="validation-notices">
              {detail.notices.map((notice) => (
                <li key={notice}>{notice}</li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="card">
        <div className="card__header">
          <span className="card__title">审计事件</span>
          <span className="card__hint">SP 5.33 · 状态迁移记录，最早在前</span>
        </div>
        <div className="card__body">
          {events.isPending ? <LoadingState label="正在读取审计事件…" /> : null}
          {events.isError ? (
            <ErrorState
              error={events.error}
              onRetry={() => {
                void events.refetch();
              }}
            />
          ) : null}
          {events.data !== undefined && events.data.events.length === 0 ? (
            <EmptyState
              title="没有审计事件记录"
              hint="该运行创建于事件表引入之前，或事件未落库；冻结时间因此不可考。"
            />
          ) : null}
          {events.data !== undefined && events.data.events.length > 0 ? (
            <>
              <ol className="timeline" data-testid="validation-events">
                {events.data.events.map((event, index) => (
                  <li key={`${event.to_status}-${event.recorded_at}-${index}`}>
                    <span className="mono">
                      {event.from_status === null ? "（创建）" : event.from_status} → {event.to_status}
                    </span>
                    <time dateTime={event.recorded_at}>{event.recorded_at}</time>
                    <span className="muted">{event.reason ?? "未记录原因"}</span>
                  </li>
                ))}
              </ol>
              {events.data.notes.map((note) => (
                <p key={note} className="card__hint">
                  {note}
                </p>
              ))}
            </>
          ) : null}
        </div>
      </div>
    </>
  );
}
