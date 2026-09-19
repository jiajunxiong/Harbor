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
  RejectedTradeResponse,
  RunQuery,
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
