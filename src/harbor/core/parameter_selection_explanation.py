"""Parameter selection explanation (MVP 3 / SP 3.29).

Out-of-sample validation must explain every parameter selection (参数选择说明):
the chosen parameters, their source basis (来源依据) — why that combination was
selected under the pre-registered rules — and an explicit confirmation that the
independent test set was never used (未使用测试集).

- :func:`selection_basis` renders the human-readable rationale from an SP 3.21
  :class:`~harbor.core.candidate_selection.CandidateSelection`: it names the
  selected parameter values, the validation primary metric that made them best,
  the pre-registered rules (direction / tie-break / minimum samples) and any
  exclusions — or explains that no candidate was eligible.
- :class:`ParameterSelectionExplanation` is the persistable, fingerprinted
  record: the selected trial (or none), the rules applied, the basis text, the
  ``test_set_used`` flag (always ``False`` — passing ``True`` is rejected by
  :func:`explain_selection`), the test-set confirmation statement, the source
  selection fingerprint, the frozen dataset fingerprint (SP 3.7) and the code
  version.
- :func:`verify_test_set_unused` checks the SP 3.24 :class:`AccessGuard` audit:
  it confirms the test set was untouched unless the audit records a granted
  parameter comparison (which SP 3.24 never permits) — the audit evidence that
  未使用测试集.

Pure core layer: depends on the SP 3.21 selection and the SP 3.24 access guard,
never on storage, services or CLI code.
"""

import hashlib
import json
from dataclasses import dataclass, replace

from harbor.core.candidate_selection import CandidateSelection, SelectionRules
from harbor.core.test_access_guard import AccessGuard, AccessKind
from harbor.core.validation_domain import ParameterTrial


class SelectionExplanationError(ValueError):
    """Raised when a selection explanation is invalid (SP 3.29)."""


def confirmation_statement() -> str:
    """Return the standard confirmation that the test set was not used (SP 3.29)."""
    return (
        "Parameter selection used only the pre-registered training and "
        "validation data; the independent test set was never read "
        "(SP 3.21 / SP 3.24)."
    )


def selection_basis(selection: CandidateSelection) -> str:
    """Render why the selected combination was chosen (来源依据, SP 3.29).

    For a selected trial: names the parameter values, the best validation
    primary metric under the pre-registered rules and the exclusions. For no
    selection: explains that every candidate was excluded, with the reasons.
    """
    rules = selection.rules
    if selection.selected is None:
        reasons = (
            "; ".join(f"{entry.trial_id}: {entry.reason}" for entry in selection.excluded)
            or "no trials supplied"
        )
        return f"no candidate selected: every trial was excluded ({reasons})."
    trial = selection.selected
    values = ", ".join(f"{parameter.name}={parameter.value}" for parameter in trial.parameters)
    metric = "n/a" if trial.metric is None else f"{trial.metric:.4g}"
    excluded = (
        f"{len(selection.excluded)} trial(s) excluded" if selection.excluded else "no exclusions"
    )
    return (
        f"selected [{values}] because it achieved the best validation "
        f"{rules.primary_metric} ({metric}) under the pre-registered rules "
        f"(direction {rules.direction.value}, tie-break {rules.tie_breaker.value}, "
        f"min-samples {rules.min_validation_samples}); {excluded}."
    )


@dataclass(frozen=True)
class ParameterSelectionExplanation:
    """The persistable explanation of a parameter selection (SP 3.29).

    ``selected`` is the chosen trial (or ``None`` when nothing was eligible),
    ``rules`` the pre-registered selection rules, ``basis`` the source rationale
    (来源依据) and ``test_set_confirmation`` the statement that the test set was
    never used (未使用测试集 — ``test_set_used`` is always ``False``).
    ``source_selection_fingerprint`` ties the explanation to its SP 3.21
    selection; ``fingerprint`` is the derived SHA-256 digest (SP 3.28).
    """

    selected: ParameterTrial | None
    rules: SelectionRules
    basis: str
    test_set_used: bool
    test_set_confirmation: str
    source_selection_fingerprint: str
    dataset_fingerprint: str
    code_version: str
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.basis:
            raise SelectionExplanationError("basis must be non-empty.")
        if self.test_set_used:
            raise SelectionExplanationError("a selection explanation must never use the test set.")
        if not self.test_set_confirmation:
            raise SelectionExplanationError("test_set_confirmation must be non-empty.")
        if not self.source_selection_fingerprint:
            raise SelectionExplanationError("source selection fingerprint must be non-empty.")
        if not self.dataset_fingerprint:
            raise SelectionExplanationError("dataset fingerprint must be non-empty.")
        if not self.code_version:
            raise SelectionExplanationError("code version must be non-empty.")
        if not self.fingerprint:
            raise SelectionExplanationError("explanation fingerprint must be non-empty.")

    def readable(self) -> str:
        """Render the explanation as one line."""
        if self.selected is None:
            chosen = "none"
        else:
            values = ", ".join(
                f"{parameter.name}={parameter.value}" for parameter in self.selected.parameters
            )
            chosen = f"{self.selected.trial_id} [{values}]"
        return (
            f"selection explanation: chosen {chosen}; basis: {self.basis} "
            f"test_set_used {self.test_set_used}"
        )


def explain_selection(
    selection: CandidateSelection,
    *,
    dataset_fingerprint: str,
    code_version: str,
    test_set_used: bool = False,
) -> ParameterSelectionExplanation:
    """Build the persistable explanation for an SP 3.21 selection (SP 3.29).

    ``test_set_used`` is validated to be ``False`` — the acceptance requires
    that the explanation confirms 未使用测试集, so passing ``True`` is rejected.
    """
    if test_set_used:
        raise SelectionExplanationError(
            "parameter selection must never use the test set (SP 3.21 / 3.24)."
        )
    if not dataset_fingerprint:
        raise SelectionExplanationError("dataset fingerprint must be non-empty.")
    if not code_version:
        raise SelectionExplanationError("code version must be non-empty.")
    explanation = ParameterSelectionExplanation(
        selected=selection.selected,
        rules=selection.rules,
        basis=selection_basis(selection),
        test_set_used=False,
        test_set_confirmation=confirmation_statement(),
        source_selection_fingerprint=selection.fingerprint,
        dataset_fingerprint=dataset_fingerprint,
        code_version=code_version,
        fingerprint="unfingerprinted",
    )
    return replace(explanation, fingerprint=explanation_fingerprint(explanation))


def verify_test_set_unused(guard: AccessGuard) -> str:
    """Confirm from the SP 3.24 audit that the test set was never used.

    The confirmation statement is returned unless the access audit records a
    GRANTED parameter comparison on the test set — which SP 3.24 never permits,
    so this is a defensive audit check that 未使用测试集 holds.
    """
    for entry in guard.audit:
        if entry.granted and entry.access_kind is AccessKind.PARAMETER_COMPARISON:
            raise SelectionExplanationError(
                "the access audit records a granted parameter comparison on "
                "the test set, which is forbidden (SP 3.21 / 3.24)."
            )
    return confirmation_statement()


def explanation_json(explanation: ParameterSelectionExplanation) -> str:
    """Return a stable, key-sorted JSON serialization of an explanation.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style).
    """
    selected: dict[str, object] | None = None
    if explanation.selected is not None:
        trial = explanation.selected
        selected = {
            "trial_id": trial.trial_id,
            "metric": trial.metric,
            "parameters": {parameter.name: parameter.value for parameter in trial.parameters},
        }
    payload: dict[str, object] = {
        "selected": selected,
        "rules": {
            "primary_metric": explanation.rules.primary_metric,
            "direction": explanation.rules.direction.value,
            "tie_breaker": explanation.rules.tie_breaker.value,
            "min_validation_samples": explanation.rules.min_validation_samples,
        },
        "basis": explanation.basis,
        "test_set_used": explanation.test_set_used,
        "test_set_confirmation": explanation.test_set_confirmation,
        "source_selection_fingerprint": explanation.source_selection_fingerprint,
        "dataset_fingerprint": explanation.dataset_fingerprint,
        "code_version": explanation.code_version,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def explanation_fingerprint(explanation: ParameterSelectionExplanation) -> str:
    """Return the stable SHA-256 fingerprint of an explanation (SP 3.29).

    Identical explanations always fingerprint identically; the digest excludes
    the derived fingerprint field so it can be re-derived and verified.
    """
    return hashlib.sha256(explanation_json(explanation).encode("utf-8")).hexdigest()
