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

/* -- Stage 2 batch 2: replay, export and comparison (SP 5.21-5.23) ------ */

/** The capability document, including the formats the API can render. */
export function apiInfo(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    name: "harbor-api",
    version: "0.1.0",
    api_version: "v1",
    read_only: true,
    auth_required: true,
    report_formats: ["json", "csv", "html"],
    ...overrides,
  };
}

/**
 * A replay manifest with one sibling of each kind.
 *
 * The three siblings are the three outcomes a reader has to be able to tell
 * apart: the same results and status (a genuine reproduction), the same result
 * sections but a different status (the vacuous-agreement trap), and located
 * differences.
 */
export function replayResponse(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    run_id: "bt-0001",
    manifest: {
      run_id: "bt-0001",
      config_hash: "hash-1",
      code_version: "abc1234",
      start_date: "2020-01-01",
      end_date: "2026-08-27",
      data_cutoff: "2026-08-27",
      fx_source: null,
      calendar_version: null,
      random_seed: null,
      // A composite key, not a digest: config hash | code version | boundaries.
      fingerprint: "hash-1|abc1234|2020-01-01|2026-08-27|2026-08-27|||",
    },
    siblings: [
      {
        run_id: "bt-0002",
        status: "COMPLETED",
        same_status: true,
        consistent: true,
        outcome_agrees: true,
        difference_count: 0,
        differences: [],
      },
      {
        run_id: "bt-0003",
        status: "FAILED",
        same_status: false,
        consistent: true,
        outcome_agrees: false,
        difference_count: 0,
        differences: [],
      },
      {
        run_id: "bt-0004",
        status: "COMPLETED",
        same_status: true,
        consistent: false,
        outcome_agrees: false,
        difference_count: 2,
        differences: [
          { section: "net_values", location: "length", expected: "4", actual: "3" },
          { section: "trades", location: "length", expected: "2", actual: "0" },
        ],
      },
    ],
    sibling_total: 3,
    truncated: false,
    notes: ["指纹不覆盖数据内容本身：两次运行可能指纹相同而数据已不同。"],
    ...overrides,
  };
}

/** A comparison payload: an HKD run, a USD run and a run with no valuations. */
export function comparisonResponse(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  const metrics = metricsResponse()["metrics"] as Record<string, unknown>;
  return {
    runs: [
      {
        run_id: "bt-0001",
        status: "COMPLETED",
        strategy: "momentum",
        strategy_version: "1.0.0",
        code_version: "abc1234",
        data_cutoff: "2026-01-02",
        currency: "HKD",
        start_date: "2026-01-02",
        end_date: "2026-01-09",
        point_count: 2,
        available: true,
        unavailable_reason: null,
        metrics,
        points: [
          { as_of_date: "2026-01-02", cumulative_return: 0 },
          { as_of_date: "2026-01-09", cumulative_return: 0.26 },
        ],
      },
      {
        run_id: "bt-0002",
        status: "COMPLETED",
        strategy: "momentum",
        strategy_version: "1.0.0",
        code_version: "def5678",
        data_cutoff: "2026-01-02",
        currency: "USD",
        start_date: "2026-02-02",
        end_date: "2026-02-04",
        point_count: 3,
        available: true,
        unavailable_reason: null,
        metrics: { ...metrics, cumulative_return: 0.3, max_drawdown: 0.0714, sharpe_ratio: 1.6 },
        points: [
          { as_of_date: "2026-02-02", cumulative_return: 0 },
          { as_of_date: "2026-02-03", cumulative_return: 0.4 },
          { as_of_date: "2026-02-04", cumulative_return: 0.3 },
        ],
      },
      {
        run_id: "bt-0003",
        status: "FAILED",
        strategy: "momentum",
        strategy_version: "1.0.0",
        code_version: "abc1234",
        data_cutoff: "2026-01-02",
        currency: null,
        start_date: null,
        end_date: null,
        point_count: 0,
        available: false,
        unavailable_reason: "This run has no persisted net values.",
        metrics: null,
        points: [],
      },
    ],
    warnings: ["所选运行的基准币种不同（HKD、USD）；曲线为累计收益，币种不同时不可据此比较金额规模。"],
    notes: ["曲线以各运行自身首个净值点为基准，归一为累计收益。"],
    ...overrides,
  };
}
