/**
 * Typed endpoint functions (MVP 5 / SP 5.9).
 *
 * Every function here is a GET against a read-only route; nothing in the
 * frontend can express a write. The pagination bounds mirror the server's
 * (`default_page_limit` 50, `max_page_limit` 200 in `ApiSettings`).
 */

import { buildQuery, getFile, getJson, type DownloadedFile } from "./client";
import type {
  ApiInfo,
  BacktestComparisonResponse,
  BacktestDrawdownResponse,
  BacktestMetricsResponse,
  BacktestReplayResponse,
  BacktestRunDetail,
  BacktestRunFilters,
  BacktestRunSummary,
  FillPage,
  HealthStatus,
  NetValueSeries,
  Page,
  PageQuery,
  PaperRunDetail,
  PaperRunSummary,
  QualitySummary,
  RejectedTradeResponse,
  RunQuery,
  ValidationRunDetail,
  ValidationRunSummary,
} from "./types";

/** Re-exported so callers can keep importing the paging shape from here. */
export type { PageQuery };

export function fetchApiInfo(options?: { signal?: AbortSignal }): Promise<ApiInfo> {
  return getJson<ApiInfo>("/version", options);
}

export function fetchHealth(options?: { signal?: AbortSignal }): Promise<HealthStatus> {
  // The liveness probe is deliberately unversioned and unauthenticated (SP 5.2).
  return getJson<HealthStatus>("/health", { ...options, absolutePath: true });
}

/**
 * List backtest runs (SP 5.13).
 *
 * Every filter is forwarded explicitly. An earlier version passed only the
 * page bounds, which failed silently — the table simply showed unfiltered
 * history — so each Stage 2 parameter is named here rather than spread.
 */
export function fetchBacktestRuns(
  query: RunQuery = {},
  options?: { signal?: AbortSignal },
): Promise<Page<BacktestRunSummary>> {
  const search = buildQuery({
    limit: query.limit,
    offset: query.offset,
    status: query.status,
    strategy: query.strategy,
    data_cutoff_from: query.data_cutoff_from,
    data_cutoff_to: query.data_cutoff_to,
    sort: query.sort,
    order: query.order,
  });
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

/* -- Stage 2: the backtest dashboard (SP 5.13-5.18) --------------------- */

export function fetchBacktestRunFilters(options?: {
  signal?: AbortSignal;
}): Promise<BacktestRunFilters> {
  return getJson<BacktestRunFilters>("/backtests/filters", options);
}

export function fetchNetValues(
  runId: string,
  query: { max_points?: number } = {},
  options?: { signal?: AbortSignal },
): Promise<NetValueSeries> {
  const search = buildQuery({ max_points: query.max_points });
  return getJson<NetValueSeries>(
    `/backtests/${encodeURIComponent(runId)}/net-values${search}`,
    options,
  );
}

export function fetchBacktestMetrics(
  runId: string,
  options?: { signal?: AbortSignal },
): Promise<BacktestMetricsResponse> {
  return getJson<BacktestMetricsResponse>(
    `/backtests/${encodeURIComponent(runId)}/metrics`,
    options,
  );
}

export function fetchDrawdowns(
  runId: string,
  options?: { signal?: AbortSignal },
): Promise<BacktestDrawdownResponse> {
  return getJson<BacktestDrawdownResponse>(
    `/backtests/${encodeURIComponent(runId)}/drawdowns`,
    options,
  );
}

export interface TradeQuery extends PageQuery {
  market?: string;
  symbol?: string;
}

export function fetchFills(
  runId: string,
  query: TradeQuery = {},
  options?: { signal?: AbortSignal },
): Promise<FillPage> {
  const search = buildQuery({
    market: query.market,
    symbol: query.symbol,
    limit: query.limit,
    offset: query.offset,
  });
  return getJson<FillPage>(`/backtests/${encodeURIComponent(runId)}/fills${search}`, options);
}

export function fetchRejectedTrades(
  runId: string,
  query: TradeQuery = {},
  options?: { signal?: AbortSignal },
): Promise<RejectedTradeResponse> {
  const search = buildQuery({
    market: query.market,
    symbol: query.symbol,
    limit: query.limit,
    offset: query.offset,
  });
  return getJson<RejectedTradeResponse>(
    `/backtests/${encodeURIComponent(runId)}/rejected-trades${search}`,
    options,
  );
}

/* -- replay, export and comparison (SP 5.21-5.23) ----------------------- */

/** A run's replay manifest and its agreement with runs sharing its inputs. */
export function fetchReplay(
  runId: string,
  options?: { signal?: AbortSignal },
): Promise<BacktestReplayResponse> {
  return getJson<BacktestReplayResponse>(
    `/backtests/${encodeURIComponent(runId)}/replay`,
    options,
  );
}

/**
 * Download a run's server-rendered report (SP 5.22).
 *
 * Returned as a file rather than a URL so the request carries the Authorization
 * header: the report is rendered on the server, and the token never appears in a
 * link.
 */
export function downloadReport(
  runId: string,
  reportFormat: string,
  options?: { signal?: AbortSignal },
): Promise<DownloadedFile> {
  const search = buildQuery({ format: reportFormat });
  return getFile(`/backtests/${encodeURIComponent(runId)}/report${search}`, options);
}

/** Several runs' rebased curves and metrics, with the caveats (SP 5.23). */
export function fetchComparison(
  runIds: readonly string[],
  options?: { signal?: AbortSignal },
): Promise<BacktestComparisonResponse> {
  const search = buildQuery({ run_ids: runIds.join(",") });
  return getJson<BacktestComparisonResponse>(`/backtests/compare${search}`, options);
}
