"""Data version drift check (MVP 3 / SP 3.11).

Re-verifies the dataset fingerprint (SP 3.7) before an experiment is executed
or a report is generated, and refuses to reuse an old conclusion when the
recorded fingerprint no longer matches the current frozen manifest — i.e.
when the data, calendar, quality records or any other manifest content
changed since the conclusion was recorded (SP 3.11 acceptance).

The raising guard is used at experiment-execution and report-generation time;
the non-raising check is used for diagnostics. The reader-backed variant
(SP 3.8) re-verifies against the frozen manifest a
:class:`~harbor.core.frozen_data_reader.FrozenDataReader` is bound to. Core
layer: depends only on the fingerprint, frozen-reader and validation types,
never on storage, services or CLI code.
"""

from dataclasses import dataclass

from harbor.core.dataset_fingerprint import dataset_fingerprint
from harbor.core.frozen_data_reader import FrozenDataReader
from harbor.core.validation_domain import DatasetManifest


class DataDriftError(ValueError):
    """Raised when the recorded fingerprint no longer matches the data (SP 3.11)."""


@dataclass(frozen=True)
class DriftCheckResult:
    """Outcome of re-verifying a recorded fingerprint against current data."""

    recorded_fingerprint: str
    current_fingerprint: str

    @property
    def drifted(self) -> bool:
        """Whether the data changed since the fingerprint was recorded."""
        return self.recorded_fingerprint != self.current_fingerprint

    def readable(self) -> str:
        """Render the drift check as one line."""
        if not self.drifted:
            return f"fingerprint {self.current_fingerprint} matches"
        return (
            f"data drift: recorded {self.recorded_fingerprint} != "
            f"current {self.current_fingerprint}"
        )


def check_fingerprint(
    manifest: DatasetManifest,
    recorded_fingerprint: str,
) -> DriftCheckResult:
    """Re-derive the manifest fingerprint and compare it to the recorded one.

    Args:
        manifest: The current frozen dataset manifest (SP 3.6).
        recorded_fingerprint: The fingerprint recorded with a conclusion or
            validation run (SP 3.7).

    Returns:
        The drift check result; ``drifted`` is True when the recorded
        fingerprint differs from the current data.
    """
    return DriftCheckResult(
        recorded_fingerprint=recorded_fingerprint,
        current_fingerprint=dataset_fingerprint(manifest),
    )


def require_fingerprint_matches(
    manifest: DatasetManifest,
    recorded_fingerprint: str,
    *,
    context: str = "validation run",
) -> None:
    """Reject reusing an old conclusion when the data has drifted (SP 3.11).

    Call this before executing an experiment or generating a report that
    would reuse a previously recorded conclusion. A mismatch means the data,
    calendar or quality records changed since the conclusion was recorded.

    Raises:
        DataDriftError: If the recorded fingerprint differs from the current
            manifest's fingerprint.
    """
    result = check_fingerprint(manifest, recorded_fingerprint)
    if result.drifted:
        raise DataDriftError(
            f"refusing to reuse {context}: data has drifted "
            f"(recorded {recorded_fingerprint[:12]}... != "
            f"current {result.current_fingerprint[:12]}...)"
        )


def verify_reader_fingerprint(
    reader: FrozenDataReader,
    recorded_fingerprint: str,
) -> DriftCheckResult:
    """Re-verify a recorded fingerprint against the reader's frozen manifest.

    Delegates to :func:`check_fingerprint` using the frozen manifest the
    reader is bound to (SP 3.8), so the check runs against the same boundaries
    the experiment will read.
    """
    return check_fingerprint(reader.manifest, recorded_fingerprint)
