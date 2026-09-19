"""Report-export plumbing shared by the backtest and validation reports (SP 5.22, SP 5.34).

Both reports are rendered server-side and served as a download, so the format
vocabulary, the media types and the filename sanitising are defined once. A
second copy of ``REPORT_FORMATS`` is how an API and its CLI drift apart: the
dashboard would offer a format the renderer refuses, or refuse one it supports.

The sanitising matters more than it looks. The filename travels in a
``Content-Disposition`` header and the run id inside it is caller-supplied, so a
path separator or a quote there would change the header's meaning.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The formats every report renderer must support, in menu order.
REPORT_FORMATS = ("json", "csv", "html")

#: Media types for those formats.
REPORT_MEDIA_TYPES = {
    "json": "application/json",
    "csv": "text/csv",
    "html": "text/html",
}


@dataclass(frozen=True)
class RenderedReport:
    """A rendered report plus the headers needed to serve it as a download."""

    report_format: str
    content: str
    media_type: str
    filename: str


def safe_filename_part(value: str) -> str:
    """Reduce a run id to characters that are safe inside a filename.

    Anything outside a conservative set is replaced rather than escaped, and the
    result is never empty: the fallback keeps a download from being named after
    nothing at all.
    """
    safe = [character if character.isalnum() or character in "-_." else "_" for character in value]
    collapsed = "".join(safe).strip("._")
    return collapsed or "run"
