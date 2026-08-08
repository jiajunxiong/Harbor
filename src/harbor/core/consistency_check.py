"""Result consistency check (MVP 2 / SP 2.62).

Confirms that running the same inputs twice produces identical fills (成交),
net values (净值) and metrics (指标), and that any difference is locatable
(差异必须可定位).

Two SP 2.58 run artifacts are compared section by section. Every difference is
reported as a :class:`ConsistencyIssue` with the artifact section, the exact
path within it (e.g. ``[1].total_value`` or ``performance.cumulative_return``)
and the two values, so the difference can be located precisely. The replay
fingerprints (SP 2.61) must also match: if they differ, the runs were not fed
the same inputs and are not replay-identical, which is flagged even when the
compared sections happen to line up.

The check is research-only and deterministic.

Pure core logic: depends only on the SP 2.58 artifact and SP 2.61 manifest
types; never touches storage or CLI code.
"""

import json
from dataclasses import dataclass
from typing import Any

from harbor.core.replay_manifest import manifest_from_artifact


class ConsistencyError(ValueError):
    """Raised when two artifacts cannot be compared (SP 2.62)."""


_SECTIONS = ("net_values", "trades", "positions", "metrics")


@dataclass(frozen=True)
class ConsistencyIssue:
    """One locatable difference between two runs (SP 2.62)."""

    section: str
    location: str
    expected: str
    actual: str

    def readable(self) -> str:
        """Render the difference with its exact location."""
        if not self.location:
            path = self.section
        elif self.location.startswith("["):
            path = self.section + self.location
        else:
            path = f"{self.section}.{self.location}"
        return f"{path}: expected {self.expected}, actual {self.actual}"


@dataclass(frozen=True)
class ConsistencyReport:
    """The result of comparing two runs of the same inputs (SP 2.62)."""

    run_a_id: str
    run_b_id: str
    fingerprints_match: bool
    issues: tuple[ConsistencyIssue, ...]

    @property
    def consistent(self) -> bool:
        """Whether the runs are replay-identical and show no differences."""
        return self.fingerprints_match and not self.issues

    def readable(self) -> str:
        """Render the consistency outcome."""
        lines = [f"consistency {self.run_a_id} vs {self.run_b_id}:"]
        if self.fingerprints_match:
            lines.append("  replay fingerprints: match")
        else:
            lines.append("  replay fingerprints: MISMATCH (inputs differ; not replay-identical)")
        if not self.issues:
            lines.append("  no differences found in net values, trades, positions or metrics")
        for issue in self.issues:
            lines.append(f"  {issue.readable()}")
        lines.append(f"  consistent: {self.consistent}")
        return "\n".join(lines)


def _render(value: Any) -> str:
    """Render a JSON-safe value as a stable string."""
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def _add_issue(
    section: str,
    path: str,
    field: str,
    expected: Any,
    actual: Any,
    issues: list[ConsistencyIssue],
) -> None:
    location = f"{path}.{field}" if path else field
    issues.append(
        ConsistencyIssue(
            section=section,
            location=location,
            expected=_render(expected),
            actual=_render(actual),
        )
    )


def _diff_values(
    section: str,
    path: str,
    a: Any,
    b: Any,
    issues: list[ConsistencyIssue],
) -> None:
    """Recursively compare two JSON-safe values, recording differences."""
    if a == b:
        return
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            child = f"{path}.{key}" if path else key
            if key not in a:
                _add_issue(section, path, key, None, b[key], issues)
            elif key not in b:
                _add_issue(section, path, key, a[key], None, issues)
            else:
                _diff_values(section, child, a[key], b[key], issues)
        return
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            _add_issue(section, path, "length", len(a), len(b), issues)
        for index in range(min(len(a), len(b))):
            _diff_values(section, f"{path}[{index}]", a[index], b[index], issues)
        return
    leaf = path.rsplit(".", 1)[-1] if path else "value"
    parent = path.rsplit(".", 1)[0] if "." in path else ""
    _add_issue(section, parent, leaf, a, b, issues)


def compare_artifacts(
    artifact_a: dict[str, Any],
    artifact_b: dict[str, Any],
) -> ConsistencyReport:
    """Compare two SP 2.58 run artifacts for consistency (SP 2.62).

    Args:
        artifact_a: The first run's results artifact.
        artifact_b: The second run's results artifact.

    Returns:
        A :class:`ConsistencyReport` listing every locatable difference in net
        values, trades, positions and metrics, plus whether the replay
        fingerprints (SP 2.61) match.

    Raises:
        ConsistencyError: If either artifact is not an SP 2.58 results artifact.
    """
    for artifact in (artifact_a, artifact_b):
        if (
            "run" not in artifact
            or "config" not in artifact
            or any(section not in artifact for section in _SECTIONS)
        ):
            raise ConsistencyError(
                "Expected an SP 2.58 results artifact with run, config and result sections."
            )
    fingerprints_match = (
        manifest_from_artifact(artifact_a).fingerprint()
        == manifest_from_artifact(artifact_b).fingerprint()
    )
    issues: list[ConsistencyIssue] = []
    for section in _SECTIONS:
        _diff_values(section, "", artifact_a[section], artifact_b[section], issues)
    return ConsistencyReport(
        run_a_id=artifact_a["run"]["run_id"],
        run_b_id=artifact_b["run"]["run_id"],
        fingerprints_match=fingerprints_match,
        issues=tuple(issues),
    )
