/**
 * Status badge with a deliberately honest colour vocabulary (MVP 5 / SP 5.10).
 *
 * Research verdicts are not "win/lose": `NOT_QUALIFIED` is a recorded fact and
 * is rendered neutrally, while `INCONCLUSIVE` is rendered as a warning with an
 * explicit "evidence is not a pass" label, so an inconclusive outcome can
 * never be mistaken for approval. Red is reserved for incidents (failed runs,
 * breached limits, unresolved reconciliation differences).
 */

type Tone = "ok" | "warn" | "danger" | "info" | "neutral";

const TONE_BY_STATUS: Record<string, Tone> = {
  completed: "ok",
  qualified: "ok",
  running: "info",
  started: "info",
  degraded: "warn",
  inconclusive: "warn",
  not_qualified: "neutral",
  failed: "danger",
  breached: "danger",
  frozen: "danger",
};

const INCONCLUSIVE_NOTE = "证据不足 ≠ 通过";

function toneFor(status: string): Tone {
  return TONE_BY_STATUS[status.trim().toLowerCase()] ?? "neutral";
}

export interface StatusBadgeProps {
  status: string;
  /** Human-readable label; defaults to the raw status. */
  label?: string;
  /** Extra context appended in a tooltip. */
  hint?: string;
  /** Force a tone instead of deriving it from the status vocabulary. */
  tone?: Tone;
}

export function StatusBadge({ status, label, hint, tone }: StatusBadgeProps) {
  const resolvedTone = tone ?? toneFor(status);
  const isInconclusive = status.trim().toLowerCase() === "inconclusive";
  const text = label ?? status;
  const title = hint ?? (isInconclusive ? INCONCLUSIVE_NOTE : undefined);

  return (
    <span className={`badge badge--${resolvedTone}`} title={title}>
      {text}
      {isInconclusive ? <span className="visually-hidden">（{INCONCLUSIVE_NOTE}）</span> : null}
    </span>
  );
}
