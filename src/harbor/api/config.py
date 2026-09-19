"""API settings for the read-only monitoring service (MVP 5 / SP 5.1).

The API is **read-only by design**: there is no setting that enables order
placement or any other write path (SP 5.3). Configuration covers the access
token, CORS allow-list and pagination bounds only.

Authentication is mandatory unless local unauthenticated use is *explicitly*
enabled: an unauthenticated API is a loud, recorded decision (SP 5.4), never
a silent default.
"""

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200


class ApiSettings(BaseSettings):
    """Settings supplied through ``HARBOR_API_*`` environment variables or `.env`."""

    model_config = SettingsConfigDict(
        env_prefix="HARBOR_API_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    token: SecretStr | None = Field(
        default=None,
        description="Bearer token granting the read-only role.",
    )
    ops_token: SecretStr | None = Field(
        default=None,
        description=(
            "Optional bearer token granting the ops role; kept separate from the read role."
        ),
    )
    allow_unauthenticated: bool = Field(
        default=False,
        description="Local-development escape hatch; logs a warning and is never the default.",
    )
    cors_origins: tuple[str, ...] = Field(
        default=(),
        description="Exact CORS origins allowed to call the API; empty means same-origin only.",
    )
    default_page_limit: int = Field(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT)
    max_page_limit: int = Field(default=MAX_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT)

    @model_validator(mode="after")
    def _validate_access(self) -> "ApiSettings":
        if self.token is None and not self.allow_unauthenticated:
            raise ValueError(
                "Set HARBOR_API_TOKEN, or explicitly set "
                "HARBOR_API_ALLOW_UNAUTHENTICATED=1 for local use."
            )
        if self.default_page_limit > self.max_page_limit:
            raise ValueError("default_page_limit must not exceed max_page_limit.")
        if any(origin.strip() != origin or not origin for origin in self.cors_origins):
            raise ValueError("CORS origins must be non-empty and must not contain whitespace.")
        return self

    @property
    def auth_required(self) -> bool:
        """Whether requests must present a valid bearer token (SP 5.4)."""
        return self.token is not None
