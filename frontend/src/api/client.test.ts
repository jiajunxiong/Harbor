import { describe, expect, it, vi } from "vitest";

import { ApiError, API_ROOT, buildQuery, getJson } from "./client";
import { fetchHealth } from "./endpoints";
import { describeApiError } from "./problem";
import { fetchCall, jsonResponse, problemResponse } from "../testing";

describe("getJson", () => {
  it("requests the versioned route with a bearer token and no body", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse({ ok: true })));
    vi.stubGlobal("fetch", fetchMock);

    const result = await getJson<{ ok: boolean }>("/backtests", { token: "read-token" });

    expect(result).toEqual({ ok: true });
    const { url, init } = fetchCall(fetchMock);
    expect(url).toBe(`${API_ROOT}/backtests`);
    expect(init.method).toBe("GET");
    expect(init.body).toBeUndefined();
    expect((init.headers as Record<string, string>)["Authorization"]).toBe("Bearer read-token");
  });

  it("omits the Authorization header when no token is configured", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse({})));
    vi.stubGlobal("fetch", fetchMock);

    await getJson("/backtests", { token: "" });

    const { init } = fetchCall(fetchMock);
    expect(init.headers).not.toHaveProperty("Authorization");
  });

  it("never sends a body, so the dashboard cannot mutate server state", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse({})));
    vi.stubGlobal("fetch", fetchMock);

    await getJson("/backtests", { token: "t" });

    const { init } = fetchCall(fetchMock);
    expect(init.method).toBe("GET");
    expect(init.body).toBeUndefined();
  });

  it("can address an unversioned route explicitly", async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse({})));
    vi.stubGlobal("fetch", fetchMock);

    await getJson("/health", { token: "t", absolutePath: true });

    expect(fetchCall(fetchMock).url).toBe("/health");
  });

  it("parses a problem+json failure into an ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(problemResponse("missing_credentials", 401, "req-42"))),
    );

    const failure = await getJson("/backtests", { token: "t" }).catch(
      (error: unknown) => error,
    );

    expect(failure).toBeInstanceOf(ApiError);
    const apiError = failure as ApiError;
    expect(apiError.status).toBe(401);
    expect(apiError.code).toBe("missing_credentials");
    expect(apiError.requestId).toBe("req-42");
    expect(apiError.retryable).toBe(false);
  });

  it("treats a 5xx as retryable and a 4xx as not", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(problemResponse("database_unavailable", 503))),
    );
    const failure = (await getJson("/backtests", { token: "t" }).catch(
      (error: unknown) => error,
    )) as ApiError;
    expect(failure.retryable).toBe(true);
  });

  it("survives a failure that carries no problem document", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response("<html>502</html>", { status: 502 }))),
    );

    const failure = (await getJson("/backtests", { token: "t" }).catch(
      (error: unknown) => error,
    )) as ApiError;

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure.problem).toBeNull();
    expect(failure.message).toContain("502");
  });

  it("reports a transport failure as a status-0 ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))),
    );

    const failure = (await getJson("/backtests", { token: "t" }).catch(
      (error: unknown) => error,
    )) as ApiError;

    expect(failure.status).toBe(0);
    expect(failure.retryable).toBe(true);
  });

  it("lets an abort propagate rather than masking it as an API error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new DOMException("aborted", "AbortError"))),
    );

    const failure = await getJson("/backtests", { token: "t" }).catch((error: unknown) => error);

    expect(failure).not.toBeInstanceOf(ApiError);
    expect((failure as DOMException).name).toBe("AbortError");
  });
});

describe("buildQuery", () => {
  it("renders only the parameters that are present", () => {
    expect(buildQuery({ limit: 25, offset: 0 })).toBe("?limit=25&offset=0");
    expect(buildQuery({ limit: undefined, offset: 0 })).toBe("?offset=0");
    expect(buildQuery({})).toBe("");
  });
});

describe("fetchHealth", () => {
  it("calls the unversioned liveness probe", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(jsonResponse({ status: "ok", database: "ok", read_only: true, version: "1" })),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchHealth({ signal: new AbortController().signal });

    expect(fetchCall(fetchMock).url).toBe("/health");
  });
});

describe("describeApiError", () => {
  it("surfaces the code and request id for correlation", () => {
    const description = describeApiError(
      new ApiError(404, {
        type: "https://harbor.local/problems/backtest_run_not_found",
        title: "Not found",
        status: 404,
        detail: "No backtest run 'x' exists.",
        code: "backtest_run_not_found",
        instance: null,
        request_id: "req-9",
      }, "[backtest_run_not_found] No backtest run 'x' exists."),
    );

    expect(description.title).toBe("未找到资源");
    expect(description.code).toBe("backtest_run_not_found");
    expect(description.requestId).toBe("req-9");
    expect(description.status).toBe(404);
  });

  it("describes a plain error without inventing a code", () => {
    const description = describeApiError(new Error("boom"));
    expect(description.code).toBeNull();
    expect(description.requestId).toBeNull();
    expect(description.detail).toBe("boom");
  });
});
