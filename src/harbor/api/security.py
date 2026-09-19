"""Bearer-token authentication and role separation (MVP 5 / SP 5.4).

Two roles exist and are kept distinct: ``readonly`` (granted by the read
token) and ``ops`` (granted by the separate ops token). Every request must
authenticate unless unauthenticated local use was *explicitly* enabled, in
which case the caller still receives only the least-privileged ``readonly``
role.

Tokens are compared with :func:`hmac.compare_digest` so a wrong token cannot
be discovered by timing. Failures are problem+json responses carrying a
``WWW-Authenticate`` challenge (SP 5.7).
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from enum import StrEnum

from fastapi import Depends, Request

from harbor.api.config import ApiSettings
from harbor.api.errors import ApiError

_logger = logging.getLogger("harbor.api")

BEARER_PREFIX = "bearer "
BEARER_CHALLENGE = 'Bearer realm="harbor"'


class Role(StrEnum):
    """The permission a principal holds (SP 5.4)."""

    READONLY = "readonly"
    OPS = "ops"


@dataclass(frozen=True)
class Principal:
    """An authenticated caller (SP 5.4)."""

    subject: str
    role: Role

    def readable(self) -> str:
        """Render the principal as a compact audit line."""
        return f"{self.subject} ({self.role.value})"


def _matches(candidate: str, secret: str | None) -> bool:
    """Compare ``candidate`` to ``secret`` in constant time."""
    if not secret:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), secret.encode("utf-8"))


def _unauthorized(code: str, detail: str) -> ApiError:
    return ApiError(
        status_code=401,
        code=code,
        detail=detail,
        headers={"WWW-Authenticate": BEARER_CHALLENGE},
    )


def authenticate(*, settings: ApiSettings, authorization: str | None) -> Principal:
    """Resolve the caller's principal, or raise :class:`ApiError` (SP 5.4).

    Raises:
        ApiError: 401 when credentials are missing or invalid.
    """
    read_token = settings.token.get_secret_value() if settings.token is not None else None
    ops_token = settings.ops_token.get_secret_value() if settings.ops_token is not None else None

    if authorization is None or not authorization.strip():
        if not settings.auth_required:
            _logger.warning(
                "unauthenticated API request accepted because "
                "HARBOR_API_ALLOW_UNAUTHENTICATED is enabled; granting the read-only role"
            )
            return Principal(subject="local-unauthenticated", role=Role.READONLY)
        raise _unauthorized(
            "missing_credentials",
            "Provide an 'Authorization: Bearer <token>' header; the API requires authentication.",
        )

    header = authorization.strip()
    if not header.lower().startswith(BEARER_PREFIX):
        raise _unauthorized(
            "invalid_credentials",
            "Use the 'Bearer' authorization scheme, for example 'Authorization: Bearer <token>'.",
        )
    presented = header[len(BEARER_PREFIX) :].strip()
    if not presented:
        raise _unauthorized("invalid_credentials", "The bearer token is empty.")

    if _matches(presented, ops_token):
        return Principal(subject="ops-token", role=Role.OPS)
    if _matches(presented, read_token):
        return Principal(subject="readonly-token", role=Role.READONLY)
    raise _unauthorized("invalid_credentials", "The bearer token is not valid.")


def get_principal(request: Request) -> Principal:
    """FastAPI dependency returning the authenticated principal (SP 5.4)."""
    settings: ApiSettings = request.app.state.api_settings
    return authenticate(settings=settings, authorization=request.headers.get("Authorization"))


def require_readonly(principal: Principal = Depends(get_principal)) -> Principal:
    """Require any authenticated principal (SP 5.4)."""
    return principal


def require_ops(principal: Principal = Depends(get_principal)) -> Principal:
    """Require the ops role; read-only principals are refused (SP 5.4).

    No route uses this in MVP 5 (the API is read-only, SP 5.3); it exists so a
    future write path must explicitly opt in to the higher role.
    """
    if principal.role is not Role.OPS:
        raise ApiError(
            status_code=403,
            code="insufficient_role",
            detail=f"This operation requires the {Role.OPS.value!r} role.",
        )
    return principal
