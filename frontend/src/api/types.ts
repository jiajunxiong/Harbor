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
  conclusion: ValidationConclusion | null;
  warning_count: number;
  warnings_by_severity: Record<string, number>;
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
