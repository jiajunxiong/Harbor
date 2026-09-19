/**
 * Typed endpoint functions (MVP 5 / SP 5.9).
 *
 * Every function here is a GET against a read-only route; nothing in the
 * frontend can express a write. The pagination bounds mirror the server's
 * (`default_page_limit` 50, `max_page_limit` 200 in `ApiSettings`).
 */

import { buildQuery, getJson } from "./client";
import type {
  ApiInfo,
  BacktestRunDetail,
  BacktestRunSummary,
  HealthStatus,
  Page,
  PaperRunDetail,
  PaperRunSummary,
  QualitySummary,
  ValidationRunDetail,
  ValidationRunSummary,
} from "./types";

export interface PageQuery {
  limit?: number;
  offset?: number;
}

export function fetchApiInfo(options?: { signal?: AbortSignal }): Promise<ApiInfo> {
  return getJson<ApiInfo>("/version", options);
}

export function fetchHealth(options?: { signal?: AbortSignal }): Promise<HealthStatus> {
  // The liveness probe is deliberately unversioned and unauthenticated (SP 5.2).
  return getJson<HealthStatus>("/health", { ...options, absolutePath: true });
}

export function fetchBacktestRuns(
  query: PageQuery = {},
  options?: { signal?: AbortSignal },
): Promise<Page<BacktestRunSummary>> {
  const search = buildQuery({ limit: query.limit, offset: query.offset });
  return getJson<Page<BacktestRunSummary>>(`/backtests${search}`, options);
}

export function fetchBacktestRun(
  runId: string,
  options?: { signal?: AbortSignal },
): Promise<BacktestRunDetail> {
  return getJson<BacktestRunDetail>(`/backtests/${encodeURIComponent(runId)}`, options);
}

export function fetchValidationRuns(
  query: PageQuery = {},
  options?: { signal?: AbortSignal },
): Promise<Page<ValidationRunSummary>> {
  const search = buildQuery({ limit: query.limit, offset: query.offset });
  return getJson<Page<ValidationRunSummary>>(`/validations${search}`, options);
}

export function fetchValidationRun(
  runId: string,
  options?: { signal?: AbortSignal },
): Promise<ValidationRunDetail> {
  return getJson<ValidationRunDetail>(`/validations/${encodeURIComponent(runId)}`, options);
}

export function fetchPaperRuns(
  query: PageQuery = {},
  options?: { signal?: AbortSignal },
): Promise<Page<PaperRunSummary>> {
  const search = buildQuery({ limit: query.limit, offset: query.offset });
  return getJson<Page<PaperRunSummary>>(`/paper-runs${search}`, options);
}

export function fetchPaperRun(
  runId: string,
  options?: { signal?: AbortSignal },
): Promise<PaperRunDetail> {
  return getJson<PaperRunDetail>(`/paper-runs/${encodeURIComponent(runId)}`, options);
}

export function fetchQualitySummary(
  market: "HK" | "US",
  options?: { signal?: AbortSignal },
): Promise<QualitySummary> {
  return getJson<QualitySummary>(`/quality/${market}`, options);
}
