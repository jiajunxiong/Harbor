/**
 * TanStack Query configuration: cache, retry and invalidation policy
 * (MVP 5 / SP 5.9).
 *
 * The policy is deliberately conservative for a research audit surface:
 *
 * * a 4xx is never retried — the request is wrong, not unlucky;
 * * a 5xx or a transport failure is retried twice with backoff, because the
 *   API may be restarting behind a proxy;
 * * refetching on window focus is off, so a number on screen does not silently
 *   change while it is being read;
 * * data stays fresh for 30s and is cached for 5 minutes.
 */

import { QueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";

export const STALE_TIME_MS = 30_000;
export const CACHE_TIME_MS = 5 * 60_000;
export const MAX_RETRIES = 2;

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: STALE_TIME_MS,
        gcTime: CACHE_TIME_MS,
        refetchOnWindowFocus: false,
        refetchOnReconnect: true,
        retry: (failureCount, error) => {
          if (error instanceof ApiError) {
            return error.retryable && failureCount < MAX_RETRIES;
          }
          return failureCount < MAX_RETRIES;
        },
        retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
      },
    },
  });
}
