/**
 * React data hooks over the read-only endpoints (MVP 5 / SP 5.9, SP 5.13-5.18).
 *
 * Query keys are centralised so a page can invalidate exactly what it changed
 * without guessing at key shapes. Filter objects are part of the key, so two
 * different filter combinations never share a cache entry.
 */

import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import {
  fetchApiInfo,
  fetchBacktestMetrics,
  fetchBacktestRun,
  fetchBacktestRunFilters,
  fetchBacktestRuns,
  fetchComparison,
  fetchDrawdowns,
  fetchFills,
  fetchHealth,
  fetchNetValues,
  fetchRejectedTrades,
  fetchReplay,
  fetchValidationCoverage,
  fetchValidationEvents,
  fetchValidationFolds,
  fetchValidationRun,
  fetchValidationRuns,
  fetchValidationSplit,
  fetchValidationStress,
  fetchValidationTrials,
  fetchValidationWarnings,
  type TradeQuery,
} from "./endpoints";
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
  RejectedTradeResponse,
  RunQuery,
  ValidationCoverageResponse,
  ValidationEventsResponse,
  ValidationFoldsResponse,
  ValidationRunDetail,
  ValidationRunSummary,
  ValidationSplitResponse,
  ValidationStressResponse,
  ValidationTrialsResponse,
  ValidationWarningsResponse,
} from "./types";

export const queryKeys = {
  info: ["api", "info"] as const,
  health: ["api", "health"] as const,
  runFilters: ["backtests", "filters"] as const,
  // The whole query object is part of the key: every filter and sort value that
  // changes the response also changes the cache entry.
  backtestRuns: (query: RunQuery) => ["backtests", "list", query] as const,
  backtestRun: (runId: string) => ["backtests", "detail", runId] as const,
  netValues: (runId: string, maxPoints: number | undefined) =>
    ["backtests", "net-values", runId, maxPoints ?? null] as const,
  metrics: (runId: string) => ["backtests", "metrics", runId] as const,
  drawdowns: (runId: string) => ["backtests", "drawdowns", runId] as const,
  fills: (runId: string, query: TradeQuery) => ["backtests", "fills", runId, query] as const,
  rejectedTrades: (runId: string, query: TradeQuery) =>
    ["backtests", "rejected-trades", runId, query] as const,
  replay: (runId: string) => ["backtests", "replay", runId] as const,
  comparison: (runIds: readonly string[]) => ["backtests", "comparison", runIds] as const,
  validationRuns: (query: PageQuery) => ["validations", "list", query] as const,
  validationRun: (runId: string) => ["validations", "detail", runId] as const,
  validationSplit: (runId: string) => ["validations", "split", runId] as const,
  validationCoverage: (runId: string) => ["validations", "coverage", runId] as const,
  validationWarnings: (runId: string) => ["validations", "warnings", runId] as const,
  validationEvents: (runId: string) => ["validations", "events", runId] as const,
  validationTrials: (runId: string) => ["validations", "trials", runId] as const,
  validationFolds: (runId: string) => ["validations", "folds", runId] as const,
  validationStress: (runId: string) => ["validations", "stress", runId] as const,
};

export function useApiInfo(): UseQueryResult<ApiInfo, Error> {
  return useQuery({
    queryKey: queryKeys.info,
    queryFn: ({ signal }) => fetchApiInfo({ signal }),
  });
}

export function useHealth(): UseQueryResult<HealthStatus, Error> {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: ({ signal }) => fetchHealth({ signal }),
  });
}

export function useBacktestRuns(query: RunQuery = {}): UseQueryResult<Page<BacktestRunSummary>, Error> {
  return useQuery({
    queryKey: queryKeys.backtestRuns(query),
    queryFn: ({ signal }) => fetchBacktestRuns(query, { signal }),
    placeholderData: (previous) => previous,
  });
}

export function useBacktestRun(runId: string | null): UseQueryResult<BacktestRunDetail, Error> {
  return useQuery({
    queryKey: queryKeys.backtestRun(runId ?? ""),
    queryFn: ({ signal }) => fetchBacktestRun(runId as string, { signal }),
    enabled: runId !== null && runId !== "",
  });
}

export function useBacktestRunFilters(): UseQueryResult<BacktestRunFilters, Error> {
  return useQuery({
    queryKey: queryKeys.runFilters,
    queryFn: ({ signal }) => fetchBacktestRunFilters({ signal }),
    // The list of statuses and strategies changes only when a run is created.
    staleTime: 5 * 60_000,
  });
}

export function useNetValues(
  runId: string | null,
  maxPoints?: number,
): UseQueryResult<NetValueSeries, Error> {
  return useQuery({
    queryKey: queryKeys.netValues(runId ?? "", maxPoints),
    queryFn: ({ signal }) => fetchNetValues(runId as string, { max_points: maxPoints }, { signal }),
    enabled: runId !== null && runId !== "",
  });
}

export function useBacktestMetrics(
  runId: string | null,
): UseQueryResult<BacktestMetricsResponse, Error> {
  return useQuery({
    queryKey: queryKeys.metrics(runId ?? ""),
    queryFn: ({ signal }) => fetchBacktestMetrics(runId as string, { signal }),
    enabled: runId !== null && runId !== "",
  });
}

export function useDrawdowns(
  runId: string | null,
): UseQueryResult<BacktestDrawdownResponse, Error> {
  return useQuery({
    queryKey: queryKeys.drawdowns(runId ?? ""),
    queryFn: ({ signal }) => fetchDrawdowns(runId as string, { signal }),
    enabled: runId !== null && runId !== "",
  });
}

export function useFills(runId: string | null, query: TradeQuery = {}): UseQueryResult<FillPage, Error> {
  return useQuery({
    queryKey: queryKeys.fills(runId ?? "", query),
    queryFn: ({ signal }) => fetchFills(runId as string, query, { signal }),
    enabled: runId !== null && runId !== "",
    placeholderData: (previous) => previous,
  });
}

export function useRejectedTrades(
  runId: string | null,
  query: TradeQuery = {},
): UseQueryResult<RejectedTradeResponse, Error> {
  return useQuery({
    queryKey: queryKeys.rejectedTrades(runId ?? "", query),
    queryFn: ({ signal }) => fetchRejectedTrades(runId as string, query, { signal }),
    enabled: runId !== null && runId !== "",
    placeholderData: (previous) => previous,
  });
}

export function useReplay(runId: string | null): UseQueryResult<BacktestReplayResponse, Error> {
  return useQuery({
    queryKey: queryKeys.replay(runId ?? ""),
    queryFn: ({ signal }) => fetchReplay(runId as string, { signal }),
    enabled: runId !== null && runId !== "",
  });
}

/**
 * Several runs side by side (SP 5.23).
 *
 * The run ids are sorted into the key so the same selection always hits the same
 * cache entry regardless of the order they were picked in.
 */
export function useComparison(runIds: readonly string[]): UseQueryResult<
  BacktestComparisonResponse,
  Error
> {
  const ordered = [...runIds].sort();
  return useQuery({
    queryKey: queryKeys.comparison(ordered),
    queryFn: ({ signal }) => fetchComparison(ordered, { signal }),
    enabled: ordered.length >= 2,
  });
}

/* -- Stage 3: the out-of-sample validation dashboard (SP 5.26-5.34) ----- */

/** One page of validation runs (SP 5.26). */
export function useValidationRuns(
  query: PageQuery = {},
): UseQueryResult<Page<ValidationRunSummary>, Error> {
  return useQuery({
    queryKey: queryKeys.validationRuns(query),
    queryFn: ({ signal }) => fetchValidationRuns(query, { signal }),
    placeholderData: (previous) => previous,
  });
}
export function useValidationRun(runId: string | null): UseQueryResult<ValidationRunDetail, Error> {
  return useQuery({
    queryKey: queryKeys.validationRun(runId ?? ""),
    queryFn: ({ signal }) => fetchValidationRun(runId as string, { signal }),
    enabled: runId !== null && runId !== "",
  });
}

/** A run's frozen split (SP 5.28). */
export function useValidationSplit(
  runId: string | null,
): UseQueryResult<ValidationSplitResponse, Error> {
  return useQuery({
    queryKey: queryKeys.validationSplit(runId ?? ""),
    queryFn: ({ signal }) => fetchValidationSplit(runId as string, { signal }),
    enabled: runId !== null && runId !== "",
  });
}

/**
 * A run's measured coverage (SP 5.30).
 *
 * Not cached beyond the default, because the numbers are measured at request
 * time: a stale percentage would be a claim about data that may have changed.
 */
export function useValidationCoverage(
  runId: string | null,
): UseQueryResult<ValidationCoverageResponse, Error> {
  return useQuery({
    queryKey: queryKeys.validationCoverage(runId ?? ""),
    queryFn: ({ signal }) => fetchValidationCoverage(runId as string, { signal }),
    enabled: runId !== null && runId !== "",
    staleTime: 0,
  });
}

/** A run's recorded coverage warnings (SP 5.33). */
export function useValidationWarnings(
  runId: string | null,
): UseQueryResult<ValidationWarningsResponse, Error> {
  return useQuery({
    queryKey: queryKeys.validationWarnings(runId ?? ""),
    queryFn: ({ signal }) => fetchValidationWarnings(runId as string, { signal }),
    enabled: runId !== null && runId !== "",
  });
}

/** A run's lifecycle events (SP 5.26 / SP 5.33). */
export function useValidationEvents(
  runId: string | null,
): UseQueryResult<ValidationEventsResponse, Error> {
  return useQuery({
    queryKey: queryKeys.validationEvents(runId ?? ""),
    queryFn: ({ signal }) => fetchValidationEvents(runId as string, { signal }),
    enabled: runId !== null && runId !== "",
  });
}

/** A run's parameter trials (SP 5.27). */
export function useValidationTrials(
  runId: string | null,
): UseQueryResult<ValidationTrialsResponse, Error> {
  return useQuery({
    queryKey: queryKeys.validationTrials(runId ?? ""),
    queryFn: ({ signal }) => fetchValidationTrials(runId as string, { signal }),
    enabled: runId !== null && runId !== "",
  });
}

/** A run's walk-forward folds (SP 5.29). */
export function useValidationFolds(
  runId: string | null,
): UseQueryResult<ValidationFoldsResponse, Error> {
  return useQuery({
    queryKey: queryKeys.validationFolds(runId ?? ""),
    queryFn: ({ signal }) => fetchValidationFolds(runId as string, { signal }),
    enabled: runId !== null && runId !== "",
  });
}

/** A run's stress-scenario results (SP 5.31). */
export function useValidationStress(
  runId: string | null,
): UseQueryResult<ValidationStressResponse, Error> {
  return useQuery({
    queryKey: queryKeys.validationStress(runId ?? ""),
    queryFn: ({ signal }) => fetchValidationStress(runId as string, { signal }),
    enabled: runId !== null && runId !== "",
  });
}
