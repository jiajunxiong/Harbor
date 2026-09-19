/**
 * Turn any thrown value into something a screen can show honestly
 * (MVP 5 / SP 5.7).
 *
 * The server's `code`, `detail` and `request_id` are surfaced verbatim so a
 * user can quote them, while internal causes are never invented: an unknown
 * error becomes a generic message rather than a fabricated explanation.
 */

import { ApiError } from "./client";

export interface ErrorDescription {
  title: string;
  detail: string;
  code: string | null;
  requestId: string | null;
  status: number | null;
  retryable: boolean;
}

const TITLE_BY_STATUS: Record<number, string> = {
  0: "无法连接到 API",
  401: "未认证",
  403: "权限不足",
  404: "未找到资源",
  422: "请求参数无效",
  503: "服务不可用",
};

export function describeApiError(error: unknown): ErrorDescription {
  if (error instanceof ApiError) {
    return {
      title: TITLE_BY_STATUS[error.status] ?? "请求失败",
      detail: error.message,
      code: error.code,
      requestId: error.requestId,
      status: error.status,
      retryable: error.retryable,
    };
  }
  if (error instanceof Error) {
    return {
      title: "发生未预期的错误",
      detail: error.message,
      code: null,
      requestId: null,
      status: null,
      retryable: false,
    };
  }
  return {
    title: "发生未预期的错误",
    detail: String(error),
    code: null,
    requestId: null,
    status: null,
    retryable: false,
  };
}
