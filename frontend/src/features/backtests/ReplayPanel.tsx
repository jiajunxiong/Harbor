import { useReplay } from "../../api/hooks";
import { DataTable, type Column } from "../../components/DataTable";
import { EmptyState, ErrorState, LoadingState } from "../../components/States";
import { StatusBadge } from "../../components/StatusBadge";
import { formatDateOnly, shortenId } from "../../format";
import type { ConsistencyIssueView, SiblingConsistencyView } from "../../api/types";

export interface ReplayPanelProps {
  runId: string;
  /** The subject run's status, so a sibling difference can be stated concretely. */
  runStatus: string;
  onOpenRun: (runId: string) => void;
}

const UNPERSISTED = "未持久化";

const ISSUE_COLUMNS: readonly Column<ConsistencyIssueView>[] = [
  { key: "section", header: "区段", render: (issue) => <span className="mono">{issue.section}</span> },
  {
    key: "location",
    header: "位置",
    render: (issue) => <span className="mono">{issue.location}</span>,
  },
  { key: "expected", header: "本运行", render: (issue) => <span className="mono">{issue.expected}</span> },
  { key: "actual", header: "该运行", render: (issue) => <span className="mono">{issue.actual}</span> },
];

/**
 * Replay manifest and sibling consistency (MVP 5 / SP 5.21).
 *
 * The panel's job is to keep a fingerprint from over-claiming. A fingerprint is a
 * composite key over the configuration and the data query boundaries — it says
 * nothing about the data the runs actually read — so the view reports the
 * fingerprint *and* whether runs sharing it produced the same results, and names
 * that limitation in its own notes instead of leaving it to be inferred.
 *
 * The three outcomes are distinguished on purpose:
 *
 * * results and status both agree — the companion run reproduced this one;
 * * results agree but the status does not — two runs that produced nothing agree
 *   vacuously, and calling that "consistent" would hide a real difference;
 * * results differ — located, section by section.
 */
export function ReplayPanel({ runId, runStatus, onOpenRun }: ReplayPanelProps) {
  const query = useReplay(runId);

  if (query.isPending) {
    return <LoadingState label="正在推导重放清单…" />;
  }
  if (query.isError) {
    return (
      <ErrorState
        error={query.error}
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }

  const payload = query.data;
  const manifest = payload.manifest;

  return (
    <>
      <dl className="kv">
        <dt>输入指纹</dt>
        <dd className="mono" data-testid="replay-fingerprint">
          {manifest.fingerprint}
        </dd>
        <dt>配置哈希</dt>
        <dd className="mono">{manifest.config_hash}</dd>
        <dt>代码版本</dt>
        <dd className="mono">{manifest.code_version}</dd>
        <dt>数据区间</dt>
        <dd className="mono">
          {formatDateOnly(manifest.start_date)} ~ {formatDateOnly(manifest.end_date)}
        </dd>
        <dt>数据截点</dt>
        <dd className="mono">{formatDateOnly(manifest.data_cutoff)}</dd>
        <dt>汇率来源</dt>
        <dd>{manifest.fx_source ?? UNPERSISTED}</dd>
        <dt>日历版本</dt>
        <dd>{manifest.calendar_version ?? UNPERSISTED}</dd>
        <dt>随机种子</dt>
        <dd>{manifest.random_seed ?? UNPERSISTED}</dd>
      </dl>

      <ul className="summary-list" data-testid="replay-notes">
        {payload.notes.map((note) => (
          <li key={note}>{note}</li>
        ))}
      </ul>

      <h3 className="card__title">相同输入指纹的其它运行</h3>
      {payload.siblings.length === 0 ? (
        <EmptyState
          title="没有其它运行记录相同的输入指纹"
          hint="指纹相同意味着配置哈希、代码版本与数据截点都相同，也就是「同一次实验」。当前库中只有本运行符合，因此没有可对比的兄弟运行；这不代表本运行已验证过可复现。"
        />
      ) : (
        <>
          {payload.truncated ? (
            <p className="notice" data-testid="replay-truncated">
              共有 {payload.sibling_total} 个运行共享该指纹，此处只对比了前{" "}
              {payload.siblings.length} 个；未对比的不会在下方出现。
            </p>
          ) : null}
          {payload.siblings.map((sibling) => (
            <SiblingCard
              key={sibling.run_id}
              sibling={sibling}
              subjectStatus={runStatus}
              onOpenRun={onOpenRun}
            />
          ))}
        </>
      )}
    </>
  );
}

function SiblingCard({
  sibling,
  subjectStatus,
  onOpenRun,
}: {
  sibling: SiblingConsistencyView;
  subjectStatus: string;
  onOpenRun: (runId: string) => void;
}) {
  const verdict = sibling.outcome_agrees ? (
    <span className="badge badge--ok" data-testid={`sibling-verdict-${sibling.run_id}`}>
      结果与状态均一致
    </span>
  ) : sibling.consistent ? (
    <span className="badge badge--warn" data-testid={`sibling-verdict-${sibling.run_id}`}>
      结果区一致，但状态不同
    </span>
  ) : (
    <span className="badge badge--danger" data-testid={`sibling-verdict-${sibling.run_id}`}>
      结果不一致
    </span>
  );

  return (
    <div className="sibling" data-testid={`sibling-${sibling.run_id}`}>
      <div className="sibling__header">
        <button
          type="button"
          className="link-button mono"
          onClick={() => {
            onOpenRun(sibling.run_id);
          }}
        >
          {shortenId(sibling.run_id, 16)}
        </button>
        <StatusBadge status={sibling.status} />
        {verdict}
        <span className="card__hint">
          {sibling.difference_count === 0
            ? "无差异"
            : `${sibling.difference_count} 处差异`}
        </span>
      </div>

      {!sibling.consistent && sibling.differences.length > 0 ? (
        <DataTable
          columns={ISSUE_COLUMNS}
          rows={sibling.differences}
          rowKey={(issue) => `${issue.section}-${issue.location}`}
          caption={`前 ${sibling.differences.length} 处差异（共 ${sibling.difference_count} 处）`}
        />
      ) : null}

      {sibling.consistent && !sibling.same_status ? (
        <p className="notice" data-testid={`sibling-status-note-${sibling.run_id}`}>
          两次运行的净值 / 成交 / 持仓 / 指标四个结果区段一致，但运行状态不同：本运行为{" "}
          <span className="mono">{subjectStatus}</span>，该运行为{" "}
          <span className="mono">{sibling.status}</span>。
          结果区一致只说明两者产生了相同的内容——两次都未产生结果时也会一致——因此不能据此认定两次实验等价；
          请先核对失败原因与数据覆盖。
        </p>
      ) : null}
    </div>
  );
}
