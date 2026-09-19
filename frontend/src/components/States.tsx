import { useCallback, type ReactNode } from "react";

import { describeApiError } from "../api/problem";

/** Shown while a collection is loading for the first time. */
export function LoadingState({ label }: { label: string }) {
  return (
    <div className="state" role="status" aria-live="polite">
      <span className="state__title">加载中…</span>
      <span>{label}</span>
    </div>
  );
}

/** Shown when a request succeeded but there is nothing to display. */
export function EmptyState({ title, hint }: { title: string; hint?: ReactNode }) {
  return (
    <div className="state" data-testid="empty-state">
      <span className="state__title">{title}</span>
      {hint !== undefined ? <span className="state__detail">{hint}</span> : null}
    </div>
  );
}

export interface ErrorStateProps {
  error: unknown;
  /** Called when the user asks to try again. */
  onRetry?: () => void;
  /** Replaces the generic title derived from the HTTP status. */
  title?: string;
}

/**
 * Renders a failure with its stable error code and request id (SP 5.7), so a
 * screenshot of the page can be correlated with a server log line.
 */
export function ErrorState({ error, onRetry, title }: ErrorStateProps) {
  const description = describeApiError(error);
  const handleRetry = useCallback(() => {
    onRetry?.();
  }, [onRetry]);

  return (
    <div className="state state--error" role="alert" data-testid="error-state">
      <span className="state__title">{title ?? description.title}</span>
      <span className="state__detail">{description.detail}</span>
      <span className="state__meta">
        {description.code !== null ? `错误码：${description.code}` : "无错误码"}
        {description.status !== null ? ` · HTTP ${description.status}` : ""}
        {description.requestId !== null ? ` · 请求 ID：${description.requestId}` : ""}
      </span>
      {onRetry !== undefined ? (
        <button type="button" className="button" onClick={handleRetry}>
          重试
        </button>
      ) : null}
    </div>
  );
}
