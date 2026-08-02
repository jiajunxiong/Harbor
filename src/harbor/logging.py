"""Structured JSON logging for Harbor application entry points."""

import json
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import TextIO

from harbor.config import LogLevel

_BASE_LOG_RECORD_FIELDS = frozenset(logging.makeLogRecord({}).__dict__)


class JsonFormatter(logging.Formatter):
    """Formats log records as JSON with stable fields and structured extras."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize a log record and its caller-provided fields to JSON."""
        payload: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload.update(
            {
                key: value
                for key, value in record.__dict__.items()
                if key not in _BASE_LOG_RECORD_FIELDS
            }
        )
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=_json_default, sort_keys=True)


def configure_logging(
    log_level: LogLevel | str,
    stream: TextIO | None = None,
) -> logging.Logger:
    """Configure and return the Harbor namespace logger.

    Args:
        log_level: Minimum level emitted by Harbor loggers.
        stream: Optional destination for log output. Defaults to standard error.

    Returns:
        The configured ``harbor`` namespace logger.

    Raises:
        ValueError: If log_level is not supported by the Python logging module.
    """
    normalized_level = str(log_level).upper()
    level_number = logging.getLevelNamesMapping().get(normalized_level)
    if level_number is None:
        raise ValueError(f"Unsupported log level: {log_level!r}.")

    logger = logging.getLogger("harbor")
    logger.handlers.clear()
    logger.setLevel(level_number)
    logger.propagate = False

    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a Harbor logger, optionally nested beneath a component name."""
    if name is None:
        return logging.getLogger("harbor")
    return logging.getLogger(f"harbor.{name}")


def _json_default(value: object) -> str:
    """Serialize common Harbor value objects that JSON does not support natively."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)
