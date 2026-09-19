import { useValidationSplit } from "../../api/hooks";
import { EmptyState, ErrorState, LoadingState } from "../../components/States";
import { formatDateOnly } from "../../format";

export interface ValidationSplitPanelProps {
  runId: string;
}

const SEGMENTS = [
  { key: "train", label: "训练", start: "train_start", end: "train_end" },
  { key: "validation", label: "验证（调参）", start: "validation_start", end: "validation_end" },
  { key: "test", label: "测试（仅评测一次）", start: "test_start", end: "test_end" },
] as const;

/**
 * The frozen train / validation / test boundaries (MVP 5 / SP 5.28).
 *
 * The three segments are drawn as a timeline rather than as three date pairs:
 * the property that matters is that they do not overlap and that the test window
 * is last, and a reader can see that at a glance here but not in a list of
 * dates. The hash is shown because it is what ties later artifacts to this
 * exact split.
 */
export function ValidationSplitPanel({ runId }: ValidationSplitPanelProps) {
  const query = useValidationSplit(runId);

  if (query.isPending) {
    return <LoadingState label="正在读取冻结切分…" />;
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
  if (query.data === undefined) {
    return null;
  }

  const body = query.data;
  if (!body.available || body.split === null) {
    return (
      <EmptyState
        title="该运行没有冻结切分记录"
        hint={body.unavailable_reason ?? "冻结切分会由验证命令写入 validation_splits；此处不提供任何写入入口。"}
      />
    );
  }

  const split = body.split;

  return (
    <>
      <div className="card">
        <div className="card__header">
          <span className="card__title">冻结切分</span>
          <span className="card__hint">SP 5.28 · 训练 / 验证 / 测试三段不重叠</span>
        </div>
        <div className="card__body">
          <ol className="timeline" data-testid="validation-split-timeline">
            {SEGMENTS.map((segment) => (
              <li key={segment.key}>
                <span className="mono">{segment.label}</span>
                <time dateTime={split[segment.start]}>{formatDateOnly(split[segment.start])}</time>
                <span aria-hidden="true">→</span>
                <time dateTime={split[segment.end]}>{formatDateOnly(split[segment.end])}</time>
              </li>
            ))}
          </ol>

          <dl className="kv">
            <dt>切分哈希</dt>
            <dd className="mono" data-testid="split-hash">
              {split.split_hash}
            </dd>
            <dt>运行状态</dt>
            <dd className="mono">{body.status}</dd>
          </dl>
        </div>
      </div>

      <div className="card">
        <div className="card__header">
          <span className="card__title">口径说明</span>
          <span className="card__hint">与 CLI 一致</span>
        </div>
        <div className="card__body">
          <ul className="note-list" data-testid="validation-split-notes">
            {body.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
            <li>
              测试段只在评测时读取一次；状态机不允许回退，因此同一测试集不能用于再次调参。
            </li>
          </ul>
        </div>
      </div>
    </>
  );
}
