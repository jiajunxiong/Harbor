/**
 * TypeScript mirror of the published API schemas (MVP 5 / SP 5.2).
 *
 * These interfaces are the frontend half of the contract: they must match
 * `src/harbor/api/schemas.py` field for field, and `tests/test_api_contract.py`
 * on the Python side pins the responses these describe. A contract change is
 * therefore a review of both files, which is exactly what SP 5.2 requires.
 */

export const API_VERSION = "v1";

/** Paginated collection envelope (SP 5.6). */
export interface Page<T> {
  items: T[];
  /** Full count *before* pagination. */
  total: number;
  limit: number;
  offset: number;
  next_offset: number | null;
}

/** RFC 9457-style problem document plus a stable code (SP 5.7). */
export interface ProblemDetail {
  type: string;
  title: string;
  status: number;
  detail: string;
  code: string;
  instance: string | null;
  request_id: string | null;
}

export interface ApiInfo {
  name: string;
  version: string;
  api_version: string;
  read_only: boolean;
  auth_required: boolean;
  /**
   * The report formats this API can render (SP 5.22).
   *
   * Published by the server rather than hardcoded here, so the download buttons
   * can never offer a format the API would reject.
   */
  report_formats: string[];
  /** The formats a validation report can be exported in (SP 5.34). */
  validation_report_formats: string[];
}

export interface HealthStatus {
  status: "ok" | "degraded";
  database: "ok" | "unavailable";
  read_only: boolean;
  version: string;
}

export interface RunCounts {
  net_value_points: number;
  positions: number;
  fills: number;
  metrics: number;
  rejected_trades: number;
}

export interface BacktestRunSummary {
  run_id: string;
  status: string;
  strategy: string;
  strategy_version: string;
  code_version: string;
  config_hash: string;
  /** ISO date. */
  data_cutoff: string;
  /** ISO timestamp, timezone preserved. */
  started_at: string;
  finished_at: string | null;
  error_summary: string | null;
  resume_of: string | null;
}

export interface BacktestRunDetail extends BacktestRunSummary {
  /** Sensitive values already masked by the server (SP 5.5). */
  config_snapshot: Record<string, unknown>;
  counts: RunCounts;
}

export interface ValidationRunSummary {
  run_id: string;
  status: string;
  code_version: string;
  config_hash: string;
  test_set_id: string | null;
  created_at: string;
  updated_at: string | null;
  error_summary: string | null;
}

export type ValidationVerdict = "QUALIFIED" | "NOT_QUALIFIED" | "INCONCLUSIVE";

export interface ValidationConclusion {
  conclusion: ValidationVerdict;
  rule_version: string;
  created_at: string;
  /** Limitations always travel with the verdict so a cell cannot show it alone. */
  limitations: Record<string, unknown>[];
  evidence: Record<string, unknown>;
}

export interface ValidationRunDetail extends ValidationRunSummary {
  config_snapshot: Record<string, unknown>;
  dataset_fingerprint: string | null;
  /** When the dataset was frozen, read from the lifecycle event log (SP 5.26). */
  frozen_at: string | null;
  conclusion: ValidationConclusion | null;
  warning_count: number;
  warnings_by_severity: Record<string, number>;
  counts: ValidationArtifactCounts;
  /**
   * Anti-misreading notices that belong to this run (SP 5.35).
   *
   * Rendered next to the verdict: a conclusion is only readable together with
   * the rules about test-set reuse and re-tuning.
   */
  notices: string[];
}

/** How many of each artifact a validation run has persisted (SP 5.26). */
export interface ValidationArtifactCounts {
  trials: number;
  folds: number;
  stress_results: number;
  warnings: number;
  events: number;
}

/** The frozen train / validation / test boundaries (SP 5.28). */
export interface ValidationSplitView {
  split_hash: string;
  train_start: string;
  train_end: string;
  validation_start: string;
  validation_end: string;
  test_start: string;
  test_end: string;
}

export interface ValidationSplitResponse {
  run_id: string;
  available: boolean;
  unavailable_reason: string | null;
  status: string;
  split: ValidationSplitView | null;
  notes: string[];
}

/** One coverage item, with the gate verdict when the gate judged it (SP 5.30). */
export interface CoverageItemView {
  market: string;
  item: string;
  covered: number;
  denominator: number;
  coverage_pct: number;
  severity: "error" | "warning" | "not_qualified" | null;
  reason: string | null;
  gap: string;
}

export interface ValidationCoverageResponse {
  run_id: string;
  available: boolean;
  unavailable_reason: string | null;
  /** ``live_measurement``: the percentages are measured when asked (SP 5.30). */
  source: "live_measurement";
  measured_at: string;
  frozen_fingerprint: string | null;
  current_fingerprint: string | null;
  /** ``false`` means the data changed after the freeze. */
  fingerprint_matches: boolean | null;
  markets: string[];
  items: CoverageItemView[];
  notes: string[];
}

export interface ValidationWarningView {
  warning_code: string;
  severity: string;
  message: string;
  context: Record<string, unknown>;
  created_at: string;
}

export interface ValidationWarningsResponse {
  run_id: string;
  warning_count: number;
  warnings_by_severity: Record<string, number>;
  items: ValidationWarningView[];
  notes: string[];
}

/** One recorded state transition (SP 5.26). */
export interface LifecycleEventView {
  from_status: string | null;
  to_status: string;
  reason: string | null;
  recorded_at: string;
}

export interface ValidationEventsResponse {
  run_id: string;
  event_count: number;
  frozen_at: string | null;
  events: LifecycleEventView[];
  notes: string[];
}

/**
 * A pipeline artifact list (SP 5.27 trials, SP 5.29 folds, SP 5.31 stress).
 *
 * ``available: false`` with a reason is the honest answer while the pipeline has
 * not written the rows: an empty table would read as "measured and empty".
 */
export interface ValidationTrialsResponse {
  run_id: string;
  available: boolean;
  unavailable_reason: string | null;
  trial_count: number;
  trials: ValidationTrialView[];
  notes: string[];
}

export interface ValidationTrialView {
  trial_id: string;
  parameters: Record<string, unknown>[];
  dataset_fingerprint: string;
  train_start: string;
  train_end: string;
  validation_start: string;
  validation_end: string;
  seed: number;
  code_version: string;
  metric: number | null;
  failed_reason: string | null;
  backtest_run_id: string | null;
}

export interface ValidationFoldsResponse {
  run_id: string;
  available: boolean;
  unavailable_reason: string | null;
  fold_count: number;
  folds: ValidationFoldView[];
  notes: string[];
}

export interface ValidationFoldView {
  fold_index: number;
  train_start: string;
  train_end: string;
  validation_start: string;
  validation_end: string;
  test_start: string;
  test_end: string;
  retrain_date: string | null;
  dataset_fingerprint: string;
  backtest_run_id: string | null;
}

export interface ValidationStressResponse {
  run_id: string;
  available: boolean;
  unavailable_reason: string | null;
  stress_count: number;
  results: ValidationStressView[];
  notes: string[];
}

export interface ValidationStressView {
  scenario_name: string;
  scenario_type: string;
  assumptions: Record<string, unknown>;
  applicable_markets: string[];
  run_fingerprint: string;
  baseline_backtest_run_id: string | null;
  stressed_backtest_run_id: string | null;
  delta: Record<string, unknown>;
  notes: string | null;
}

export interface PaperRunSummary {
  run_id: string;
  status: string;
  strategy: string;
  strategy_version: string;
  markets: string[];
  base_currency: string;
  code_version: string;
  dataset_fingerprint: string;
  created_at: string;
  started_at: string | null;
  stopped_at: string | null;
}

export interface PaperRunCounts {
  orders: number;
  fills: number;
  net_value_points: number;
  approvals: number;
  circuit_breakers: number;
  reconciliation_differences: number;
}

export interface PaperRunDetail extends PaperRunSummary {
  config_snapshot: Record<string, unknown>;
  counts: PaperRunCounts;
}

export interface IngestionSummary {
  run_id: string;
  status: "running" | "completed" | "failed";
  source: string;
  start_time: string;
  end_time: string | null;
  records_processed: number;
}

export interface QualitySummary {
  market: "HK" | "US";
  security_count: number;
  bar_count: number;
  latest_bar_date: string | null;
  open_issue_count: number;
  issues_by_severity: Record<string, number>;
  latest_ingestion: IngestionSummary | null;
}

/* -- Stage 2: the backtest dashboard (SP 5.13-5.18) --------------------- */

/** The filter and sort values the server accepts on the run list (SP 5.13). */
export interface BacktestRunFilters {
  statuses: string[];
  strategies: string[];
  sort_fields: string[];
  sort_orders: string[];
}

/** One daily net-value snapshot. */
export interface NetValuePoint {
  as_of_date: string;
  currency: string;
  cash: number;
  securities_value: number;
  fees_paid: number;
}

/**
 * A run's net-value curve (SP 5.15).
 *
 * `point_count` is the full series length and `returned_count` what arrived, so a
 * chart can say plainly when it is drawing a subsample. `currency` and the date
 * range are null only when the run has no valuations at all (a failed run).
 */
export interface NetValueSeries {
  run_id: string;
  currency: string | null;
  point_count: number;
  returned_count: number;
  downsampled: boolean;
  first_date: string | null;
  last_date: string | null;
  points: NetValuePoint[];
}

/** Return and risk metrics over a run's net values (SP 5.16). */
export interface PerformanceMetricsView {
  start_date: string;
  end_date: string;
  periods: number;
  cumulative_return: number;
  annualized_return: number;
  annualized_volatility: number;
  max_drawdown: number;
  sharpe_ratio: number;
  calmar_ratio: number;
  downside_deviation: number;
}

/**
 * Metrics, or an explicit statement that they do not exist.
 *
 * A failed run has no net values, and a degenerate series has no Sharpe ratio;
 * both arrive as `available: false` with the reason, never as a zero.
 */
export interface BacktestMetricsResponse {
  run_id: string;
  source: "persisted_net_values";
  available: boolean;
  unavailable_reason: string | null;
  currency: string | null;
  metrics: PerformanceMetricsView | null;
}

/** One threshold-triggered drawdown interval (SP 5.17). */
export interface DrawdownEventView {
  threshold: number;
  start_date: string;
  peak_date: string;
  peak_value: number;
  trough_date: string;
  trough_value: number;
  depth: number;
  recovered_date: string | null;
  /** False because position values and FX P&L are not persisted. */
  position_detail_available: boolean;
}

export interface BacktestDrawdownResponse {
  run_id: string;
  available: boolean;
  unavailable_reason: string | null;
  currency: string | null;
  thresholds: number[];
  events: DrawdownEventView[];
}

/** One executed order. */
export interface FillRow {
  trade_date: string;
  market: string;
  symbol: string;
  side: string;
  quantity: number;
  price: number;
  fee: number;
  currency: string;
  order_ref: string;
}

export interface FillPage {
  run_id: string;
  items: FillRow[];
  total: number;
  limit: number;
  offset: number;
  next_offset: number | null;
}

/** One refused trade with its reason. */
export interface RejectedTradeRow {
  market: string;
  symbol: string;
  side: string | null;
  quantity: number | null;
  reason: string;
  order_ref: string | null;
}

export interface RejectionReasonCount {
  reason: string;
  count: number;
}

/** A page of refusals plus the full-set reason distribution (SP 5.18). */
export interface RejectedTradeResponse {
  run_id: string;
  items: RejectedTradeRow[];
  total: number;
  limit: number;
  offset: number;
  next_offset: number | null;
  reasons: RejectionReasonCount[];
}

/** Paging bounds shared by every collection endpoint. */
export interface PageQuery {
  limit?: number;
  offset?: number;
}

export type SortOrder = "asc" | "desc";

/** Run-list query parameters, mirroring the server's validation (SP 5.13). */
export interface RunQuery extends PageQuery {
  status?: string;
  strategy?: string;
  data_cutoff_from?: string;
  data_cutoff_to?: string;
  sort?: string;
  order?: SortOrder;
}

/* -- Stage 2 batch 2: replay, export and comparison (SP 5.21-5.23) ----- */

/**
 * A run's replay manifest (SP 2.61).
 *
 * `fingerprint` is a *composite key* over the inputs, not a digest. The three
 * unset fields are not persisted for backtest runs, so the server reports them
 * as unset rather than as facts about the run.
 */
export interface ReplayManifestView {
  run_id: string;
  config_hash: string;
  code_version: string;
  start_date: string;
  end_date: string;
  data_cutoff: string;
  fx_source: string | null;
  calendar_version: string | null;
  random_seed: number | null;
  fingerprint: string;
}

/** One located difference between two runs' results. */
export interface ConsistencyIssueView {
  section: string;
  location: string;
  expected: string;
  actual: string;
}

/**
 * Whether another run with the same inputs produced the same results.
 *
 * `consistent` covers the result sections only, so two runs that produced
 * nothing agree vacuously. `outcome_agrees` adds the recorded status; it is the
 * field a reader should trust for "these two runs did the same thing".
 */
export interface SiblingConsistencyView {
  run_id: string;
  status: string;
  same_status: boolean;
  consistent: boolean;
  outcome_agrees: boolean;
  difference_count: number;
  differences: ConsistencyIssueView[];
}

/** A run's replay manifest plus the runs claiming the same inputs (SP 5.21). */
export interface BacktestReplayResponse {
  run_id: string;
  manifest: ReplayManifestView;
  siblings: SiblingConsistencyView[];
  /** How many runs share these inputs, so a capped list is visible as such. */
  sibling_total: number;
  truncated: boolean;
  notes: string[];
}

/** One rebased point of a run's curve, in cumulative return terms. */
export interface ComparisonPointView {
  as_of_date: string;
  cumulative_return: number;
}

/** One run's stance in a multi-run comparison (SP 5.23). */
export interface RunComparisonView {
  run_id: string;
  status: string;
  strategy: string;
  strategy_version: string;
  code_version: string;
  data_cutoff: string;
  currency: string | null;
  start_date: string | null;
  end_date: string | null;
  point_count: number;
  /** Scoreable metrics exist; a curve can be present even when they do not. */
  available: boolean;
  unavailable_reason: string | null;
  metrics: PerformanceMetricsView | null;
  points: ComparisonPointView[];
}

/** Several runs side by side, with the reasons they are not like-for-like. */
export interface BacktestComparisonResponse {
  runs: RunComparisonView[];
  warnings: string[];
  notes: string[];
}
