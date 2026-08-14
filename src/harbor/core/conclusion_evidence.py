"""Conclusion evidence chain (MVP 3 / SP 3.65).

Links every structured OOS conclusion (SP 3.64) to the evidence that produced
it: the test-set version (测试集版本), the dataset fingerprint (数据集指纹), the
split (切分), the fold / trial / MVP 2 run evidence chains (折叠 / 试验 / MVP 2
运行, SP 3.36), the registered stress results (压力结果, SP 3.59) and the
warnings (告警). A conclusion without a fully linked evidence chain cannot be
re-audited, so the chain cross-validates every link against the conclusion.

- :class:`ConclusionEvidence` carries the SP 3.64 conclusion plus its evidence
  links. The SP 3.36 :class:`OosChainIntegrity` provides the per-fold trial id
  + fingerprint and MVP 2 run id + replay fingerprint links; the SP 3.59
  :class:`StressScenarioRegistry` provides the registered stress results (与
  基线的差异); the split's test interval must equal the conclusion's OOS
  performance period and the dataset fingerprint must match the conclusion's,
  so the evidence always points back to the exact frozen inputs that produced
  the conclusion.

Pure core layer: depends only on the SP 3.64 conclusion, the SP 3.36 chain
report, the SP 3.59 stress registry and the SP 3.4 split; never touches
storage, services or CLI.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, replace

from harbor.core.oos_chain import OosChainIntegrity
from harbor.core.oos_conclusion import OosStructuredConclusion
from harbor.core.stress_registry import StressScenarioRegistry
from harbor.core.validation_domain import EvaluationSplit


class ConclusionEvidenceError(ValueError):
    """Raised when a conclusion's evidence chain is invalid (SP 3.65)."""


@dataclass(frozen=True)
class ConclusionEvidence:
    """The evidence chain linked to one OOS conclusion (SP 3.65).

    ``test_set_version`` (测试集版本) is the holdout test-set version (e.g.
    ``holdout-1-v2``); ``dataset_fingerprint`` (数据集指纹) must match the
    conclusion's; ``split`` (切分) is the SP 3.4 split whose test interval must
    equal the conclusion's OOS performance period; ``chains`` (折叠 / 试验 / MVP
    2 运行) is the SP 3.36 evidence-chain integrity report; ``stress_registry``
    (压力结果) is the SP 3.59 registered stress results; ``warnings`` (告警) the
    collected warnings.
    """

    conclusion: OosStructuredConclusion
    test_set_version: str
    dataset_fingerprint: str
    split: EvaluationSplit
    chains: OosChainIntegrity
    stress_registry: StressScenarioRegistry
    warnings: tuple[str, ...]
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.test_set_version:
            raise ConclusionEvidenceError("test set version must be non-empty.")
        if not self.dataset_fingerprint:
            raise ConclusionEvidenceError("dataset fingerprint must be non-empty.")
        if self.dataset_fingerprint != self.conclusion.dataset_fingerprint:
            raise ConclusionEvidenceError(
                "evidence dataset fingerprint does not match the conclusion."
            )
        if self.chains.dataset_fingerprint != self.conclusion.dataset_fingerprint:
            raise ConclusionEvidenceError(
                "evidence chains dataset fingerprint does not match the conclusion."
            )
        if self.chains.code_version != self.conclusion.code_version:
            raise ConclusionEvidenceError(
                "evidence chains code version does not match the conclusion."
            )
        if self.split.test_start != self.conclusion.performance.start_date:
            raise ConclusionEvidenceError(
                "split test start does not match the conclusion performance start."
            )
        if self.split.test_end != self.conclusion.performance.end_date:
            raise ConclusionEvidenceError(
                "split test end does not match the conclusion performance end."
            )
        if not self.stress_registry.registrations:
            raise ConclusionEvidenceError(
                "evidence requires at least one registered stress scenario."
            )
        if not all(warning for warning in self.warnings):
            raise ConclusionEvidenceError("every warning must be non-empty.")
        if not self.fingerprint:
            raise ConclusionEvidenceError("conclusion evidence fingerprint must be non-empty.")

    @property
    def fold_count(self) -> int:
        """The number of linked folds (折叠)."""
        return len(self.chains)

    @property
    def trial_ids(self) -> tuple[str, ...]:
        """The linked trial ids in fold order (试验)."""
        return tuple(chain.trial_id for chain in self.chains if chain.trial_id is not None)

    @property
    def run_ids(self) -> tuple[str, ...]:
        """The linked MVP 2 run ids in fold order (MVP 2 运行)."""
        return tuple(chain.run_id for chain in self.chains if chain.run_id is not None)

    @property
    def warning_count(self) -> int:
        """The number of warnings (告警)."""
        return len(self.warnings)

    @property
    def stress_scenario_count(self) -> int:
        """The number of registered stress results (压力结果)."""
        return self.stress_registry.count

    def readable(self) -> str:
        """Render the evidence chain as one line."""
        return (
            f"evidence for {self.conclusion.overall.value} "
            f"({self.conclusion.market.value}): test set {self.test_set_version}, "
            f"{self.fold_count} fold(s), {len(self.trial_ids)} trial(s), "
            f"{len(self.run_ids)} run(s), {self.stress_scenario_count} stress "
            f"scenario(s), {self.warning_count} warning(s) fp {self.fingerprint}"
        )


def build_conclusion_evidence(
    *,
    conclusion: OosStructuredConclusion,
    test_set_version: str,
    split: EvaluationSplit,
    chains: OosChainIntegrity,
    stress_registry: StressScenarioRegistry,
    warnings: Sequence[str] = (),
    dataset_fingerprint: str | None = None,
) -> ConclusionEvidence:
    """Assemble a fingerprint-stamped conclusion evidence chain (SP 3.65).

    ``dataset_fingerprint`` defaults to the conclusion's own fingerprint; every
    link is cross-validated against the conclusion in ``__post_init__``.
    """
    evidence = ConclusionEvidence(
        conclusion=conclusion,
        test_set_version=test_set_version,
        dataset_fingerprint=(
            dataset_fingerprint
            if dataset_fingerprint is not None
            else conclusion.dataset_fingerprint
        ),
        split=split,
        chains=chains,
        stress_registry=stress_registry,
        warnings=tuple(warnings),
        fingerprint="unfingerprinted",
    )
    return replace(evidence, fingerprint=conclusion_evidence_fingerprint(evidence))


def _split_payload(split: EvaluationSplit) -> dict[str, str]:
    """The SP 3.4 split as a JSON payload."""
    return {
        "train_start": split.train_start.isoformat(),
        "train_end": split.train_end.isoformat(),
        "validation_start": split.validation_start.isoformat(),
        "validation_end": split.validation_end.isoformat(),
        "test_start": split.test_start.isoformat(),
        "test_end": split.test_end.isoformat(),
    }


def conclusion_evidence_json(evidence: ConclusionEvidence) -> str:
    """Return a stable, key-sorted JSON serialization of an evidence chain.

    The derived ``fingerprint`` field is excluded so the digest can be
    re-derived (SP 3.7 style); every link — test-set version, dataset
    fingerprint, split, the SP 3.36 chain report, the SP 3.59 stress registry
    and the warnings — is embedded so the conclusion is fully auditable.
    """
    payload: dict[str, object] = {
        "conclusion_fingerprint": evidence.conclusion.fingerprint,
        "conclusion_overall": evidence.conclusion.overall.value,
        "test_set_version": evidence.test_set_version,
        "dataset_fingerprint": evidence.dataset_fingerprint,
        "split": _split_payload(evidence.split),
        "chains_fingerprint": evidence.chains.fingerprint,
        "chains_code_version": evidence.chains.code_version,
        "stress_registry_fingerprint": evidence.stress_registry.fingerprint,
        "warnings": list(evidence.warnings),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def conclusion_evidence_fingerprint(evidence: ConclusionEvidence) -> str:
    """Return the stable SHA-256 fingerprint of an evidence chain (SP 3.65)."""
    return hashlib.sha256(conclusion_evidence_json(evidence).encode("utf-8")).hexdigest()


__all__: tuple[str, ...] = (
    "ConclusionEvidenceError",
    "ConclusionEvidence",
    "build_conclusion_evidence",
    "conclusion_evidence_json",
    "conclusion_evidence_fingerprint",
)
