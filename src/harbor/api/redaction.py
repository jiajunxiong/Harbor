"""Redaction of sensitive values served by the API (MVP 5 / SP 5.5).

The rules are the ones the run logs already use
(:mod:`harbor.core.run_logging`), so a configuration snapshot served over HTTP
is masked **exactly** like the same snapshot written to a log record. A
sensitive key is kept but its value becomes ``<redacted>``, so the client still
sees that the field exists and was not silently dropped.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from harbor.core.run_logging import redact_config

REDACTED = "<redacted>"

_DSN_CREDENTIALS = re.compile(r"://[^/\s:@]+:[^/\s@]*@")


def redact_document(document: Mapping[str, object]) -> dict[str, object]:
    """Return a redacted deep copy of a JSON-like document (SP 5.5).

    Two independent rules apply: keys that *name* a secret are masked, and
    credentials *embedded* in a string value (such as a database URL carrying
    a password) are scrubbed. Either rule alone would leave a leak.
    """
    return {str(key): scrub_value(value) for key, value in redact_config(document).items()}


def scrub_value(value: object) -> object:
    """Mask credentials embedded in ``value``, recursing into containers (SP 5.5)."""
    if isinstance(value, str):
        return _DSN_CREDENTIALS.sub(f"://{REDACTED}@", value)
    if isinstance(value, Mapping):
        return {str(key): scrub_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub_value(item) for item in value)
    return value


def redact_rows(rows: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
    """Return a redacted copy of a sequence of row-like mappings (SP 5.5)."""
    return [redact_document(row) for row in rows]


def scrub_detail(detail: str) -> str:
    """Strip credentials embedded in a message, such as a database URL (SP 5.5)."""
    return _DSN_CREDENTIALS.sub(f"://{REDACTED}@", detail)
