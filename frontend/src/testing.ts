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
    // The same three caveats the server sends (`COMPARISON_NOTES` in the
    // backtest router), so a test cannot pass against a fixture that promises
    // less than the API does.
    notes: [
      "曲线以各运行自身首个净值点为基准，归一为累计收益；基准与 SP 5.16 的累计收益一致。",
      "不提供跨币种金额比较：本项目数据没有汇率表，任何隐式 1:1 换算都会被拒绝。",
      "单指标最优不等于策略更优；样本期、市场与标的不同时，指标之间不具备可比性。",
    ],
    ...overrides,
  };
}

/* -- Stage 3 fixtures (SP 5.26-SP 5.35) --------------------------------- */

/** A validation run row shaped like the API schema. */
export function validationRun(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    run_id: "val-1",
    // The persisted status vocabulary is the SP 3.13 state machine's.
    status: "DATA_FROZEN",
    code_version: "abc1234",
    config_hash: "hash-val-1",
    test_set_id: null,
    created_at: "2026-02-01T00:00:00+00:00",
    updated_at: "2026-02-01T01:00:00+00:00",
    error_summary: null,
    ...overrides,
  };
}

/** A validation run detail: frozen, with its events and notices (SP 5.26). */
export function validationRunDetail(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    ...validationRun(),
    config_snapshot: { markets: ["HK"], base_currency: "HKD" },
    dataset_fingerprint: "fp-oos-1",
    frozen_at: "2026-02-01T01:00:00+00:00",
    conclusion: null,
    warning_count: 5,
    warnings_by_severity: { error: 1, warning: 4 },
    counts: { trials: 0, folds: 0, stress_results: 0, warnings: 5, events: 2 },
    notices: [
      "测试集只解锁一次：评测后不得为调参再次解锁（状态机不可回退）",
      "调参后用同一切分重测得到的通过不算通过：参数试验必须在冻结切分上完成",
      "该运行尚未产生结论：结论只由验证流水线评测写入",
    ],
    ...overrides,
  };
}

/** A concluded run, so the verdict and its limitations can be rendered. */
export function validationConclusion(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    conclusion: "INCONCLUSIVE",
    rule_version: "oos-rule-1",
    created_at: "2026-02-02T00:00:00+00:00",
    limitations: [{ code: "single_regime", detail: "只覆盖单一市场状态" }],
    evidence: { sharpe: 0.9, note: "样本内为参考" },
    ...overrides,
  };
}

/** A frozen split (SP 5.28). */
export function validationSplitResponse(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    run_id: "val-1",
    available: true,
    unavailable_reason: null,
    status: "DATA_FROZEN",
    split: {
      split_hash: "split-hash-1",
      train_start: "2019-01-01",
      train_end: "2020-12-31",
      validation_start: "2021-01-01",
      validation_end: "2021-12-31",
      test_start: "2022-01-01",
      test_end: "2022-12-31",
    },
    notes: ["切分在冻结时写入 validation_splits 并带 split_hash"],
    ...overrides,
  };
}

/** Measured coverage with one failing and one passing item (SP 5.30). */
export function validationCoverageResponse(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    run_id: "val-1",
    available: true,
    unavailable_reason: null,
    source: "live_measurement",
    measured_at: "2026-02-02T00:00:00+00:00",
    frozen_fingerprint: "fp-oos-1",
    current_fingerprint: "fp-oos-1",
    fingerprint_matches: true,
    markets: ["HK"],
    items: [
      {
        market: "HK",
        item: "prices",
        covered: 741,
        denominator: 989,
        coverage_pct: 74.92416582406472,
        severity: "error",
        reason: "price coverage 74.9% below threshold 95.0%",
        gap: "248 个交易日缺少行情数据",
      },
      {
        market: "HK",
        item: "stock_pool",
        covered: 89,
        denominator: 89,
        coverage_pct: 100,
        severity: null,
        reason: null,
        gap: "",
      },
    ],
    notes: ["覆盖百分比为本次请求实测", "缺少覆盖数据只会记为缺口或警告"],
    ...overrides,
  };
}

/** Recorded coverage warnings (SP 5.33). */
export function validationWarningsResponse(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    run_id: "val-1",
    warning_count: 1,
    warnings_by_severity: { error: 1 },
    items: [
      {
        warning_code: "coverage.prices",
        severity: "error",
        message: "248 个交易日缺少行情数据",
        context: { market: "HK", item: "prices", severity: "error" },
        created_at: "2026-02-01T01:00:00+00:00",
      },
    ],
    notes: [
      "警告只记录未通过门槛的覆盖项；通过项不会写入警告行",
      "警告表的 severity 只存 warning / error 两档（数据库 CHECK 约束）",
    ],
    ...overrides,
  };
}

/** Lifecycle events, creation first (SP 5.26 / SP 5.33). */
export function validationEventsResponse(
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    run_id: "val-1",
    event_count: 2,
    frozen_at: "2026-02-01T01:00:00+00:00",
    events: [
      {
        from_status: null,
        to_status: "DRAFT",
        reason: "validation run created",
        recorded_at: "2026-02-01T00:00:00+00:00",
      },
      {
        from_status: "DRAFT",
        to_status: "DATA_FROZEN",
        reason: "validation freeze",
        recorded_at: "2026-02-01T01:00:00+00:00",
      },
    ],
    notes: ["审计事件按时间累积；创建事件的 from_status 为空"],
    ...overrides,
  };
}

/** An artifact list the pipeline has not written yet (SP 5.27 / 5.29 / 5.31). */
export function validationArtifactResponse(
  key: "trials" | "folds" | "results",
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  const countKey = key === "trials" ? "trial_count" : key === "folds" ? "fold_count" : "stress_count";
  return {
    run_id: "val-1",
    available: false,
    unavailable_reason: "该运行没有记录：写入者是验证流水线，当前代码库中尚无调用方",
    [countKey]: 0,
    [key]: [],
    notes: ["参数试验在训练/验证切分上进行，测试集不参与调参"],
    ...overrides,
  };
}
