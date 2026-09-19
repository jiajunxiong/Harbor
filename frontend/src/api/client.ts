/**
 * Low-level HTTP client for the read-only Harbor API (MVP 5 / SP 5.7, SP 5.9).
 *
 * Two rules shape this module:
 *
 * 1. **Read-only.** There is no helper that sends a body or a non-GET verb, so
 *    no page can accidentally mutate server state (SP 5.3).
 * 2. **Failures are problem+json.** A non-2xx response is turned into an
 *    {@link ApiError} carrying the parsed {@link ProblemDetail}, so the UI can
 *    show the server's stable `code`, its actionable `detail` and the
 *    `request_id` that ties the screen to a server log line (SP 5.7).
 */

import { API_VERSION, type ProblemDetail } from "./types";

export const API_ROOT = `/api/${API_VERSION}`;

/** A failed API call, carrying the server's problem document when it sent one. */
export class ApiError extends Error {
  readonly status: number;
  readonly problem: ProblemDetail | null;
  readonly requestId: string | null;

  constructor(status: number, problem: ProblemDetail | null, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.problem = problem;
    this.requestId = problem?.request_id ?? null;
  }

  /** The server's stable error code, or `null` for a transport-level failure. */
  get code(): string | null {
    return this.problem?.code ?? null;
  }

  /** Whether retrying could plausibly succeed. */
  get retryable(): boolean {
    if (this.status === 0) {
      return true; // the request never reached the server
    }
    return this.status >= 500;
  }
}

function isProblemDetail(value: unknown): value is ProblemDetail {
  if (typeof value !== "object" || value === null) {
    return false;
  }
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate["code"] === "string" &&
    typeof candidate["detail"] === "string" &&
    typeof candidate["status"] === "number"
  );
}

async function readProblem(response: Response): Promise<ProblemDetail | null> {
  try {
    const body: unknown = await response.json();
    return isProblemDetail(body) ? body : null;
  } catch {
    return null;
  }
}

function describe(problem: ProblemDetail | null, status: number): string {
  if (problem !== null) {
    return `[${problem.code}] ${problem.detail}`;
  }
  return `请求失败（HTTP ${status}）`;
}

function readToken(): string {
  const token = import.meta.env.VITE_HARBOR_API_TOKEN;
  return typeof token === "string" ? token.trim() : "";
}

function readBaseUrl(): string {
  const baseUrl = import.meta.env.VITE_HARBOR_API_BASE_URL;
  return typeof baseUrl === "string" ? baseUrl.replace(/\/$/, "") : "";
}

export interface GetOptions {
  signal?: AbortSignal;
  /** Override the configured token; used by tests. */
  token?: string;
  /** Override the configured origin; used by tests. */
  baseUrl?: string;
  /**
   * Skip the `/api/v1` prefix. Only the unversioned liveness probe uses this,
   * and it is an explicit opt-in so no data route can escape versioning
   * by accident (SP 5.2).
   */
  absolutePath?: boolean;
}

/**
 * Perform one authenticated GET and parse the JSON body.
 *
 * @throws {ApiError} on a non-2xx response or a transport failure.
 */
export async function getJson<T>(path: string, options: GetOptions = {}): Promise<T> {
  const token = options.token ?? readToken();
  const baseUrl = options.baseUrl ?? readBaseUrl();
  const prefix = options.absolutePath === true ? "" : API_ROOT;
  const url = `${baseUrl}${prefix}${path}`;

  const headers: Record<string, string> = { Accept: "application/json" };
  if (token !== "") {
    headers["Authorization"] = `Bearer ${token}`;
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method: "GET",
      headers,
      signal: options.signal ?? null,
      credentials: "omit",
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") {
      throw cause;
    }
    throw new ApiError(0, null, "无法连接到 Harbor API，请确认服务已启动。");
  }

  if (!response.ok) {
    const problem = await readProblem(response);
    throw new ApiError(response.status, problem, describe(problem, response.status));
  }

  return (await response.json()) as T;
}

/** Build a query string from defined, non-empty parameters. */
export function buildQuery(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "") {
      continue;
    }
    search.set(key, String(value));
  }
  const rendered = search.toString();
  return rendered === "" ? "" : `?${rendered}`;
}
