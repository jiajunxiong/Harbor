/**
 * React data hooks over the read-only endpoints (MVP 5 / SP 5.9).
 *
 * Query keys are centralised so a page can invalidate exactly what it changed
 * (for example after the user clicks "刷新") without guessing at key shapes.
 */

import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import {
  fetchApiInfo,
  fetchBacktestRun,
  fetchBacktestRuns,
  fetchHealth,
  type PageQuery,
} from "./endpoints";
import type {
  ApiInfo,
  BacktestRunDetail,
  BacktestRunSummary,
  HealthStatus,
  Page,
} from "./types";

export const queryKeys = {
  info: ["api", "info"] as const,
  health: ["api", "health"] as const,
  backtestRuns: (query: PageQuery) => ["backtests", "list", query.limit ?? null, query.offset ?? 0] as const,
  backtestRun: (runId: string) => ["backtests", "detail", runId] as const,
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

export function useBacktestRuns(query: PageQuery = {}): UseQueryResult<Page<BacktestRunSummary>, Error> {
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
