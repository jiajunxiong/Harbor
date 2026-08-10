"""Test-set re-access policy (MVP 3 / SP 3.42).

After the final test completes — the SP 3.41 :class:`FinalHoldoutRelease`
exists and records the inputs frozen at unlock — any substantive adjustment
to the strategy (策略), parameters (参数), data (数据) or code (代码) requires
creating a new test-set version and a new validation run (最终测试完成后任何策
略、参数、数据或代码实质性调整均要求创建新的测试集版本和新的验证运行).

The policy compares the proposed inputs against the inputs frozen at final
evaluation unlock:

- strategy (策略)    ↔ ``config_hash``
- parameters (参数)  ↔ ``selection_fingerprint``
- data (数据)        ↔ ``dataset_fingerprint``
- code (代码)        ↔ ``code_version``

When any dimension changed the finalized test set cannot be reused:

- ``requires_new_validation_run`` is mandated for any substantive change —
  the old run's conclusion is stale;
- ``requires_new_test_set`` is mandated when the changed inputs still target
  the finalized test set id — a new test-set version must be created. A caller
  that already proposes a different test set id has created the new version
  and only the new validation run remains.

:func:`check_test_reaccess` returns the non-raising policy decision;
:func:`require_test_reaccess_compliance` raises :class:`ReaccessPolicyError`
when the finalized test set is being reused for changed inputs;
:func:`bump_test_set_version` derives the next test-set version id so the
mandated new test set can be registered through SP 3.5.

Pure core layer: depends only on the SP 3.41 final-holdout release, never on
storage, services or CLI.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

from harbor.core.final_holdout import FinalHoldoutInputs, FinalHoldoutRelease


class ReaccessPolicyError(ValueError):
    """Raised when the finalized test set is reused for changed inputs (SP 3.42)."""


class ReaccessInputChange(StrEnum):
    """A substantive adjustment detected after the final test (SP 3.42).

    ``STRATEGY`` (策略) maps to the frozen config hash, ``PARAMETERS`` (参数)
    to the frozen parameter-selection fingerprint, ``DATA`` (数据) to the
    frozen dataset fingerprint and ``CODE`` (代码) to the code version.
    """

    STRATEGY = "strategy"
    PARAMETERS = "parameters"
    DATA = "data"
    CODE = "code"


#: The canonical change-detection order (data, strategy, parameters, code).
_CHANGE_ORDER: tuple[ReaccessInputChange, ...] = (
    ReaccessInputChange.DATA,
    ReaccessInputChange.STRATEGY,
    ReaccessInputChange.PARAMETERS,
    ReaccessInputChange.CODE,
)


@dataclass(frozen=True)
class ReaccessPolicyDecision:
    """The test-set re-access policy decision (SP 3.42).

    ``changes`` lists the substantive adjustments detected (in canonical
    order; empty when the inputs are unchanged). ``reuses_finalized_test_set``
    records whether the proposal still targets the finalized test set id.
    ``requires_new_test_set`` is mandated exactly when inputs changed on the
    finalized test set — a new test-set version must be created; and
    ``requires_new_validation_run`` is mandated for any substantive change.
    ``reason`` explains the mandate (``None`` when unchanged).
    """

    changes: tuple[ReaccessInputChange, ...]
    reuses_finalized_test_set: bool
    requires_new_test_set: bool
    requires_new_validation_run: bool
    reason: str | None

    def __post_init__(self) -> None:
        changed = bool(self.changes)
        if self.requires_new_validation_run != changed:
            raise ReaccessPolicyError(
                "a new validation run is required exactly when the inputs changed."
            )
        expected_new_test_set = changed and self.reuses_finalized_test_set
        if self.requires_new_test_set != expected_new_test_set:
            raise ReaccessPolicyError(
                "a new test set is required exactly when changed inputs target "
                "the finalized test set."
            )
        if changed and (self.reason is None or not self.reason):
            raise ReaccessPolicyError("a changed decision must carry a reason.")
        if not changed and self.reason is not None:
            raise ReaccessPolicyError("a no-change decision must not carry a reason.")

    def readable(self) -> str:
        """Render the decision as one line."""
        if not self.changes:
            return "no substantive change; the finalized test set may be reused"
        names = ", ".join(change.value for change in self.changes)
        new_test_set = "required" if self.requires_new_test_set else "created"
        return f"changed: {names}; new test set {new_test_set}, new validation run required"


def compare_reaccess_inputs(
    release: FinalHoldoutRelease,
    current_inputs: FinalHoldoutInputs,
) -> tuple[ReaccessInputChange, ...]:
    """Compare proposed inputs against the finalized inputs (SP 3.42).

    Returns the substantive changes in canonical order (data, strategy,
    parameters, code); empty when the inputs are unchanged.
    """
    changes: list[ReaccessInputChange] = []
    if current_inputs.dataset_fingerprint != release.inputs.dataset_fingerprint:
        changes.append(ReaccessInputChange.DATA)
    if current_inputs.config_hash != release.inputs.config_hash:
        changes.append(ReaccessInputChange.STRATEGY)
    if current_inputs.selection_fingerprint != release.inputs.selection_fingerprint:
        changes.append(ReaccessInputChange.PARAMETERS)
    if current_inputs.code_version != release.inputs.code_version:
        changes.append(ReaccessInputChange.CODE)
    return tuple(changes)


def check_test_reaccess(
    release: FinalHoldoutRelease,
    current_inputs: FinalHoldoutInputs,
) -> ReaccessPolicyDecision:
    """Decide whether the finalized test set may be reused (SP 3.42, non-raising).

    Unchanged inputs may reuse the finalized test set and its validation run.
    Any substantive change mandates a new validation run; changed inputs that
    still target the finalized test set id also mandate a new test-set
    version.
    """
    changes = compare_reaccess_inputs(release, current_inputs)
    reuses = current_inputs.test_set_id == release.inputs.test_set_id
    if not changes:
        return ReaccessPolicyDecision(
            changes=(),
            reuses_finalized_test_set=reuses,
            requires_new_test_set=False,
            requires_new_validation_run=False,
            reason=None,
        )
    names = ", ".join(change.value for change in changes)
    reason = (
        f"finalized test set {release.inputs.test_set_id} inputs changed "
        f"({names}); a new test-set version and a new validation run are "
        "required (SP 3.42)."
    )
    return ReaccessPolicyDecision(
        changes=changes,
        reuses_finalized_test_set=reuses,
        requires_new_test_set=reuses,
        requires_new_validation_run=True,
        reason=reason,
    )


def require_test_reaccess_compliance(
    release: FinalHoldoutRelease,
    current_inputs: FinalHoldoutInputs,
) -> ReaccessPolicyDecision:
    """Enforce the test-set re-access policy (SP 3.42, raising).

    Raises :class:`ReaccessPolicyError` when changed inputs still target the
    finalized test set — the finalized holdout cannot be reused for the new
    strategy/parameters/data/code; a new test-set version and a new
    validation run are mandated. Otherwise returns the decision unchanged.
    """
    decision = check_test_reaccess(release, current_inputs)
    if decision.requires_new_test_set:
        raise ReaccessPolicyError(decision.reason or "a new test set is required.")
    return decision


def bump_test_set_version(test_set_id: str) -> str:
    """Return the next test-set version id (SP 3.42).

    Appends or increments a trailing ``-vN`` suffix so the mandated new
    test-set version can be registered through SP 3.5: ``holdout-1`` becomes
    ``holdout-1-v2`` and ``holdout-1-v2`` becomes ``holdout-1-v3``.

    Raises:
        ReaccessPolicyError: If ``test_set_id`` is empty.
    """
    if not test_set_id:
        raise ReaccessPolicyError("test set id must be non-empty.")
    match = re.fullmatch(r"(.*)-v(\d+)", test_set_id)
    if match is None:
        return f"{test_set_id}-v2"
    base, version = match.group(1), int(match.group(2))
    return f"{base}-v{version + 1}"


__all__: tuple[str, ...] = (
    "ReaccessInputChange",
    "ReaccessPolicyDecision",
    "ReaccessPolicyError",
    "bump_test_set_version",
    "check_test_reaccess",
    "compare_reaccess_inputs",
    "require_test_reaccess_compliance",
)
