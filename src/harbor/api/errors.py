"""Problem-details error contract for the read-only API (MVP 5 / SP 5.7).

Every failure — validation, authentication, missing resource or an unexpected
error — is rendered as ``application/problem+json`` carrying a stable error
``code``, an actionable ``detail`` and the ``request_id`` so a report can be
traced back to its server log line. Internal stack traces and driver messages
are never returned (SP 5.5): unexpected errors are logged and reported as a
generic detail.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from contextvars import ContextVar
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

PROBLEM_MEDIA_TYPE = "application/problem+json"

_request_id: ContextVar[str | None] = ContextVar("harbor_request_id", default=None)

_logger = logging.getLogger("harbor.api")


class ApiError(Exception):
    """An API failure rendered as a problem+json response (SP 5.7)."""

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        detail: str,
        title: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(detail)
        if status_code < 400:
            raise ValueError("ApiError status_code must be an error status.")
        if not code or not detail:
            raise ValueError("ApiError requires a non-empty code and detail.")
        self.status_code = status_code
        self.code = code
        self.detail = detail
        self.title = title if title is not None else _TITLES.get(status_code, "Request failed")
        self.headers: dict[str, str] = dict(headers) if headers else {}


_TITLES = {
    400: "Bad request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not found",
    405: "Method not allowed",
    422: "Unprocessable request",
    500: "Internal server error",
    503: "Service unavailable",
}


class ProblemDetail(BaseModel):
    """RFC 9457-style problem details, plus a stable ``code`` and ``request_id``."""

    type: str
    title: str
    status: int
    detail: str
    code: str
    instance: str | None = None
    request_id: str | None = None


def current_request_id() -> str:
    """Return a request id, always a value (generating one if unset)."""
    existing = _request_id.get()
    if existing:
        return existing
    generated = uuid.uuid4().hex
    _request_id.set(generated)
    return generated


def request_id_of(request: Request) -> str:
    """Return the request id the middleware assigned to ``request`` (SP 5.7).

    Prefers the id stashed on ``request.state`` so a response produced outside
    the middleware stack (for example by the catch-all handler) still carries
    the same id as the request that caused it.
    """
    existing = getattr(request.state, "request_id", None)
    if isinstance(existing, str) and existing:
        return existing
    return current_request_id()


def _problem_payload(
    *,
    status: int,
    code: str,
    detail: str,
    title: str,
    instance: str | None,
    request_id: str,
) -> dict[str, Any]:
    """Build the problem+json document (``type`` defaults to the error code)."""
    return ProblemDetail(
        type=f"https://harbor.local/problems/{code}",
        title=title,
        status=status,
        detail=detail,
        code=code,
        instance=instance,
        request_id=request_id,
    ).model_dump()


def problem_response(
    *,
    status: int,
    code: str,
    detail: str,
    title: str | None = None,
    instance: str | None = None,
    request_id: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Render a problem+json response with the request id header attached."""
    resolved_title = title if title is not None else _TITLES.get(status, "Request failed")
    resolved_request_id = request_id if request_id else current_request_id()
    response_headers = dict(headers) if headers else {}
    response_headers["X-Request-ID"] = resolved_request_id
    response = JSONResponse(
        status_code=status,
        content=_problem_payload(
            status=status,
            code=code,
            detail=detail,
            title=resolved_title,
            instance=instance,
            request_id=resolved_request_id,
        ),
        media_type=PROBLEM_MEDIA_TYPE,
        headers=response_headers,
    )
    return response


async def request_id_middleware(request: Request, call_next: Any) -> Any:
    """Assign a request id (honouring a client-supplied one) for correlation."""
    supplied = request.headers.get("X-Request-ID")
    request_id = supplied if supplied else uuid.uuid4().hex
    token = _request_id.set(request_id)
    request.state.request_id = request_id
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response
    finally:
        _request_id.reset(token)


def register_error_handlers(app: FastAPI) -> None:
    """Attach the uniform problem+json handlers to ``app`` (SP 5.7)."""

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return problem_response(
            status=exc.status_code,
            code=exc.code,
            detail=exc.detail,
            title=exc.title,
            request_id=request_id_of(request),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return problem_response(
            status=422,
            code="invalid_request",
            detail=_validation_detail(exc),
            request_id=request_id_of(request),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return problem_response(
            status=exc.status_code,
            code="http_error",
            detail=detail,
            request_id=request_id_of(request),
        )

    @app.exception_handler(Exception)
    async def _unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        # Log the real cause; never return it (SP 5.5).
        request_id = request_id_of(request)
        _logger.exception("unhandled API error [request_id=%s]: %s", request_id, type(exc).__name__)
        return problem_response(
            status=500,
            code="internal_error",
            detail=(
                "The server failed to handle the request; see the server log for the request id."
            ),
            request_id=request_id,
        )


def _validation_detail(exc: RequestValidationError) -> str:
    """Summarize validation failures as actionable text (no raw input echo)."""
    problems: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()))
        message = str(error.get("msg", "invalid value"))
        problems.append(f"{location}: {message}" if location else message)
    return "; ".join(problems) if problems else "The request parameters are invalid."
