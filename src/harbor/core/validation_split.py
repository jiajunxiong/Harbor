"""Time-split legality validation (MVP 3 / SP 3.4).

Enforces the frozen split ordering ``train_end < validation_start <=
validation_end < test_start``: every overlapping, inverted or empty range is
explicitly rejected with a readable reason, never silently normalized. The
underlying primitives (:class:`EvaluationSplit` and its
``_require_ordered_ranges``, SP 3.1) already enforce the same ordering inside
the frozen domain types, and the SP 3.2 / 3.3 config model and loader
propagate that validation on construction. This module exposes the rule as an
explicit config-validator-level API that enumerates ALL violations (so a
configuration can be fixed in one pass) and returns either a non-raising
:class:`SplitValidityReport` or a frozen :class:`EvaluationSplit`.

Core layer: depends only on the validation-domain and validation-config
modules and Pydantic, never on storage, services or CLI code.
"""

from dataclasses import dataclass
from datetime import date

from harbor.core.validation_config import SplitConfig
from harbor.core.validation_domain import EvaluationSplit, SplitBoundaryError

SPLIT_ORDER_RULE = "train_end < validation_start <= validation_end < test_start"


@dataclass(frozen=True)
class SplitValidityReport:
    """Non-raising outcome of a time-split legality check (SP 3.4).

    ``valid`` is True exactly when ``issues`` is empty; ``issues`` carries one
    readable reason per violation so a rejected split is explicitly
    diagnosable.
    """

    valid: bool
    issues: tuple[str, ...]

    def readable(self) -> str:
        """Render the report as a single line."""
        if self.valid:
            return f"valid time split (satisfies {SPLIT_ORDER_RULE})."
        return "invalid time split: " + "; ".join(self.issues)


def collect_split_boundary_issues(
    train_start: date,
    train_end: date,
    validation_start: date,
    validation_end: date,
    test_start: date,
    test_end: date,
) -> tuple[str, ...]:
    """Enumerate every time-split boundary violation (SP 3.4).

    Each of the three ranges must be non-empty (``start <= end``) and the
    ranges must satisfy ``train_end < validation_start <= validation_end <
    test_start``. Empty/inverted ranges, touching boundaries and overlapping
    intervals are each reported as a readable message containing the exact
    dates; an empty tuple means the split is valid.

    Unlike the first-error ``_require_ordered_ranges`` primitive (SP 3.1),
    this enumerates ALL violations so a configuration can be fixed in one
    pass.
    """
    issues: list[str] = []
    for label, start, end in (
        ("train", train_start, train_end),
        ("validation", validation_start, validation_end),
        ("test", test_start, test_end),
    ):
        if start > end:
            issues.append(
                f"{label} range is empty or reversed ({start.isoformat()} > {end.isoformat()})."
            )
    for previous_label, previous_end, next_label, next_start in (
        ("train", train_end, "validation", validation_start),
        ("validation", validation_end, "test", test_start),
    ):
        if previous_end >= next_start:
            issues.append(
                f"{previous_label} must end before {next_label} starts "
                f"({previous_label} end {previous_end.isoformat()} >= "
                f"{next_label} start {next_start.isoformat()})."
            )
    return tuple(issues)


def check_split_boundaries(
    train_start: date,
    train_end: date,
    validation_start: date,
    validation_end: date,
    test_start: date,
    test_end: date,
) -> SplitValidityReport:
    """Return a non-raising validity report for the given boundaries (SP 3.4)."""
    issues = collect_split_boundary_issues(
        train_start,
        train_end,
        validation_start,
        validation_end,
        test_start,
        test_end,
    )
    return SplitValidityReport(valid=not issues, issues=issues)


def validate_split_boundaries(
    train_start: date,
    train_end: date,
    validation_start: date,
    validation_end: date,
    test_start: date,
    test_end: date,
) -> EvaluationSplit:
    """Validate the boundaries and return the frozen split (SP 3.4).

    Raises:
        SplitBoundaryError: Describing every violation when the split is
            invalid; the message names the enforced rule and all offending
            ranges.
    """
    issues = collect_split_boundary_issues(
        train_start,
        train_end,
        validation_start,
        validation_end,
        test_start,
        test_end,
    )
    if issues:
        raise SplitBoundaryError(
            f"invalid time split (must satisfy {SPLIT_ORDER_RULE}): " + "; ".join(issues)
        )
    return EvaluationSplit(
        train_start=train_start,
        train_end=train_end,
        validation_start=validation_start,
        validation_end=validation_end,
        test_start=test_start,
        test_end=test_end,
    )


def check_split_config(split: SplitConfig) -> SplitValidityReport:
    """Return a non-raising validity report for a config split (SP 3.4).

    Re-verifies the SP 3.2 config boundaries against the SP 3.4 rule without
    raising; the report is useful for logging or precondition checks before a
    validation run.
    """
    issues = collect_split_boundary_issues(
        split.train_start,
        split.train_end,
        split.validation_start,
        split.validation_end,
        split.test_start,
        split.test_end,
    )
    return SplitValidityReport(valid=not issues, issues=issues)


def validate_split_config(split: SplitConfig) -> EvaluationSplit:
    """Validate a config split and return its immutable value (SP 3.4).

    Re-verifies the SP 3.2 config boundaries against the SP 3.4 rule and
    returns the equivalent frozen :class:`EvaluationSplit`. A config already
    rejects invalid boundaries at construction, so this is a defensive,
    explicit entry point for callers that hold only a config.

    Raises:
        SplitBoundaryError: If the boundaries are invalid (defensive).
    """
    issues = collect_split_boundary_issues(
        split.train_start,
        split.train_end,
        split.validation_start,
        split.validation_end,
        split.test_start,
        split.test_end,
    )
    if issues:
        raise SplitBoundaryError(
            f"invalid time split (must satisfy {SPLIT_ORDER_RULE}): " + "; ".join(issues)
        )
    return split.to_evaluation_split()
