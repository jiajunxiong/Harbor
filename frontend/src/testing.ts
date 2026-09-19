/**
 * Test-only helpers (MVP 5 / SP 5.9).
 *
 * Kept deliberately small: a canned API response builder and a canned run row,
 * so every test states its own data instead of sharing a fixture that drifts.
 */

/** A `fetch` call as recorded by a Vitest mock. */
export interface RecordedFetchCall {
  url: string;
  init: RequestInit;
}

/**
 * Read a recorded `fetch` call.
 *
 * Vitest types `mock.calls` from the mock's own (deliberately loose) signature,
 * so the runtime shape is asserted here once rather than cast at every call
 * site in the tests.
 */
export function fetchCall(
  mock: { mock: { calls: readonly unknown[] } },
  index = 0,
): RecordedFetchCall {
  const call = (mock.mock.calls[index] ?? []) as unknown as [unknown, RequestInit?];
  return { url: String(call[0]), init: call[1] ?? {} };
}

/** Read every recorded `fetch` call. */
export function fetchCalls(mock: {
  mock: { calls: readonly unknown[] };
}): RecordedFetchCall[] {
  return mock.mock.calls.map((_call, index) => fetchCall(mock, index));
}

/** Build a JSON `Response` like the API would return. */
export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Build a problem+json `Response` (SP 5.7). */
export function problemResponse(code: string, status: number, requestId = "req-test-1"): Response {
  return new Response(
    JSON.stringify({
      type: `https://harbor.local/problems/${code}`,
      title: "Request failed",
      status,
      detail: `detail for ${code}`,
      code,
      instance: null,
      request_id: requestId,
    }),
    { status, headers: { "Content-Type": "application/problem+json" } },
  );
}

/** A backtest run row shaped like the API schema. */
export function backtestRun(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    run_id: "bt-0001",
    status: "completed",
    strategy: "momentum",
    strategy_version: "1.0.0",
    code_version: "abc1234",
    config_hash: "hash-1",
    data_cutoff: "2026-01-02",
    started_at: "2026-01-02T08:00:00+00:00",
    finished_at: "2026-01-02T09:00:00+00:00",
    error_summary: null,
    resume_of: null,
    ...overrides,
  };
}

/** A paginated envelope shaped like the API schema. */
export function pageOf(
  items: readonly Record<string, unknown>[],
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    items: [...items],
    total: items.length,
    limit: 25,
    offset: 0,
    next_offset: null,
    ...overrides,
  };
}
