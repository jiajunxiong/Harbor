import { useHealth } from "../api/hooks";
import { StatusBadge } from "./StatusBadge";

/**
 * Live liveness and database-readiness indicator (MVP 5 / SP 5.12).
 *
 * A dashboard that shows research numbers must also show whether the data
 * behind them is reachable right now, so a stale screen is never mistaken for
 * a fresh one.
 */
export function ApiStatus() {
  const health = useHealth();

  if (health.isPending) {
    return <StatusBadge status="unknown" tone="neutral" label="API 状态检查中…" />;
  }
  if (health.isError) {
    return <StatusBadge status="failed" tone="danger" label="API 不可达" />;
  }

  const { database, version } = health.data;
  if (database !== "ok") {
    return (
      <StatusBadge
        status="degraded"
        tone="warn"
        label={`API ${version} · 数据库不可用`}
        hint="只读视图可能无法读取数据。"
      />
    );
  }
  return <StatusBadge status="ok" tone="ok" label={`API ${version} · 数据库正常`} />;
}
