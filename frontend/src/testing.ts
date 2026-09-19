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
    // The persisted vocabulary is upper case (`BacktestStatus`); a lower-cased
    // fixture would hide a real mismatch with the API.
    status: "COMPLETED",
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

/* -- Stage 2 fixtures (SP 5.13-5.18) ------------------------------------ */

/** A run detail, with the persisted counts a run actually has. */
export function backtestRunDetail(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    ...backtestRun(),
    config_snapshot: { strategy: "momentum", base_currency: "HKD" },
    // Positions and metrics rows are always zero for a backtest run: the
    // persistence layer never writes them (SP 5.19/SP 5.20).
    counts: { net_value_points: 4, positions: 0, fills: 2, metrics: 0, rejected_trades: 1 },
    ...overrides,
  };
}

/** The filter vocabulary the server offers for the run list. */
export function runFilters(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    statuses: ["COMPLETED", "FAILED"],
    strategies: ["momentum"],
    sort_fields: ["started_at", "finished_at", "run_id"],
    sort_orders: ["asc", "desc"],
    ...overrides,
  };
}

/**
 * A net-value series: 10000 → 11500 → 10200 → 12600 HKD.
 *
 * Chosen so the curve contains a real ~11.3% drawdown with a recovery, which is
 * what makes the drawdown panel meaningful in a test.
 */
export function netValueSeries(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  const points = [
    { as_of_date: "2026-01-02", currency: "HKD", cash: 10000, securities_value: 0, fees_paid: 0 },
    { as_of_date: "2026-01-05", currency: "HKD", cash: 0, securities_value: 11500, fees_paid: 12 },
    { as_of_date: "2026-01-06", currency: "HKD", cash: 0, securities_value: 10200, fees_paid: 24 },
    { as_of_date: "2026-01-09", currency: "HKD", cash: 0, securities_value: 12600, fees_paid: 36 },
  ];
  return {
    run_id: "bt-0001",
    currency: "HKD",
    point_count: points.length,
    returned_count: points.length,
    downsampled: false,
    first_date: "2026-01-02",
    last_date: "2026-01-09",
    points,
    ...overrides,
  };
}

/** Metrics computed from the fixture series, matching the CLI's core functions. */
export function metricsResponse(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    run_id: "bt-0001",
    source: "persisted_net_values",
    available: true,
    unavailable_reason: null,
    currency: "HKD",
    metrics: {
      start_date: "2026-01-02",
      end_date: "2026-01-09",
      periods: 3,
      cumulative_return: 0.26,
      annualized_return: 0.4,
      annualized_volatility: 0.3,
      max_drawdown: 0.113,
      sharpe_ratio: 1.2,
      calmar_ratio: 3.5,
      downside_deviation: 0.2,
    },
    ...overrides,
  };
}

/** The shape a run with no net values returns: a reason, never zeros. */
export function metricsUnavailable(reason = "This run has no persisted net values."): Record<string, unknown> {
  return {
    run_id: "bt-0001",
    source: "persisted_net_values",
    available: false,
    unavailable_reason: reason,
    currency: null,
    metrics: null,
  };
}

/** Drawdown events over the fixture series, at three thresholds. */
export function drawdownResponse(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    run_id: "bt-0001",
    available: true,
    unavailable_reason: null,
    currency: "HKD",
    thresholds: [0.05, 0.08, 0.1],
    events: [
      {
        threshold: 0.05,
        start_date: "2026-01-05",
        peak_date: "2026-01-05",
        peak_value: 11500,
        trough_date: "2026-01-06",
        trough_value: 10200,
        depth: 0.113,
        recovered_date: "2026-01-09",
        position_detail_available: false,
      },
      {
        threshold: 0.1,
        start_date: "2026-01-05",
        peak_date: "2026-01-05",
        peak_value: 11500,
        trough_date: "2026-01-06",
        trough_value: 10200,
        depth: 0.113,
        recovered_date: "2026-01-09",
        position_detail_available: false,
      },
    ],
    ...overrides,
  };
}

/** A page of fills. */
export function fillPage(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    run_id: "bt-0001",
    items: [
      {
        trade_date: "2026-01-05",
        market: "HK",
        symbol: "700.HK",
        side: "BUY",
        quantity: 100,
        price: 320.5,
        fee: 12,
        currency: "HKD",
        order_ref: "ord-1",
      },
    ],
    total: 1,
    limit: 25,
    offset: 0,
    next_offset: null,
    ...overrides,
  };
}

/** A page of refusals plus the full-set reason distribution. */
export function rejectedResponse(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    run_id: "bt-0001",
    items: [
      {
        market: "HK",
        symbol: "0005.HK",
        side: "BUY",
        quantity: 400,
        reason: "insufficient cash",
        order_ref: "ord-2",
      },
    ],
    total: 3,
    limit: 25,
    offset: 0,
    next_offset: null,
    // Deliberately larger than the page: the distribution describes the full
    // filtered set, not the rows on screen.
    reasons: [
      { reason: "insufficient cash", count: 2 },
      { reason: "position limit", count: 1 },
    ],
    ...overrides,
  };
}
