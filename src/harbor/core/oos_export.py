"""OOS JSON artifacts (MVP 3 / SP 3.66).

Exports the frozen configuration (冻结配置), data manifest (数据清单), trial log
(试验日志), fit snapshots (拟合快照), fold results (折叠结果), stress results
(压力结果), conclusion (结论) and audit events (审计事件) into one canonical JSON
document, based on the SP 3.65 conclusion evidence chain.

The evidence chain supplies the split (切分), the per-fold trial / fit / run
links (试验 / 拟合快照 / 折叠结果), the registered stress results (压力结果) and
the conclusion; the rolling window and tuning configs, the data manifest and
the audit events are passed in as JSON-safe payloads. Every section is built
from stable, key-sorted fields so the exported document is deterministic and
replayable (SP 3.7 style).

Pure core layer: depends only on the SP 3.65 evidence chain, the SP 3.59
registry serialization and the SP 3.4 split; never touches storage, services
or CLI.
"""

import json
from collections.abc import Mapping, Sequence

from harbor.core.conclusion_evidence import ConclusionEvidence
from harbor.core.stress_registry import StressScenarioRegistry, registry_json
from harbor.core.trial_budget import TrialBudget
from harbor.core.validation_domain import EvaluationSplit


class OosExportError(ValueError):
    """Raised when an OOS JSON export is invalid (SP 3.66)."""


def _split_section(split: EvaluationSplit) -> dict[str, str]:
    """The SP 3.4 split as a stable section."""
    return {
        "train_start": split.train_start.isoformat(),
        "train_end": split.train_end.isoformat(),
        "validation_start": split.validation_start.isoformat(),
        "validation_end": split.validation_end.isoformat(),
        "test_start": split.test_start.isoformat(),
        "test_end": split.test_end.isoformat(),
    }


def _budget_section(budget: TrialBudget) -> dict[str, object]:
    """The SP 3.17 trial budget as a stable section."""
    return {
        "max_trials": budget.max_trials,
        "random_seed": budget.random_seed,
        "tie_breaker": budget.tie_breaker.value,
        "early_stop": budget.early_stop.value,
    }


def _trial_log_section(evidence: ConclusionEvidence) -> list[dict[str, object]]:
    """The per-fold trial log (试验日志)."""
    return [
        {
            "fold_index": chain.fold_index,
            "trial_id": chain.trial_id,
            "trial_fingerprint": chain.trial_fingerprint,
        }
        for chain in evidence.chains
    ]


def _fit_snapshot_section(evidence: ConclusionEvidence) -> list[dict[str, object]]:
    """The per-fold fit snapshots (拟合快照)."""
    return [
        {
            "fold_index": chain.fold_index,
            "fit_fingerprint": chain.fit_fingerprint,
        }
        for chain in evidence.chains
    ]


def _fold_result_section(evidence: ConclusionEvidence) -> list[dict[str, object]]:
    """The per-fold results (折叠结果 / MVP 2 运行)."""
    return [
        {
            "fold_index": chain.fold_index,
            "run_id": chain.run_id,
            "replay_fingerprint": chain.replay_fingerprint,
            "report_artifact_fingerprint": chain.report_artifact_fingerprint,
        }
        for chain in evidence.chains
    ]


def _stress_section(registry: StressScenarioRegistry) -> dict[str, object]:
    """The registered stress results (压力结果, SP 3.59)."""
    payload: dict[str, object] = json.loads(registry_json(registry))
    return payload


def _conclusion_section(evidence: ConclusionEvidence) -> dict[str, object]:
    """The structured conclusion (结论, SP 3.64)."""
    return {
        "overall": evidence.conclusion.overall.value,
        "conclusion_fingerprint": evidence.conclusion.fingerprint,
        "test_set_version": evidence.test_set_version,
        "dataset_fingerprint": evidence.dataset_fingerprint,
        "code_version": evidence.conclusion.code_version,
    }


def export_oos_to_dict(
    *,
    run_id: str,
    evidence: ConclusionEvidence,
    rolling: Mapping[str, object],
    tuning: Mapping[str, object],
    manifest: Mapping[str, object],
    audit_events: Sequence[Mapping[str, object]],
    schema_version: str = "1.0",
) -> dict[str, object]:
    """Export the full OOS validation state as one canonical dict (SP 3.66).

    Args:
        run_id: The validation run id.
        evidence: The SP 3.65 conclusion evidence chain (supplies split, trial /
            fit / fold links, stress results and the conclusion).
        rolling: The frozen rolling-window config as a JSON-safe payload.
        tuning: The frozen tuning config as a JSON-safe payload.
        manifest: The frozen data manifest as a JSON-safe payload.
        audit_events: The recorded audit events (审计事件) as JSON-safe payloads.
        schema_version: The export schema version.
    """
    if not run_id:
        raise OosExportError("run id must be non-empty.")
    if not schema_version:
        raise OosExportError("schema version must be non-empty.")
    return {
        "schema_version": schema_version,
        "run": {"run_id": run_id},
        "frozen_config": {
            "split": _split_section(evidence.split),
            "rolling": dict(rolling),
            "budget": _budget_section(evidence.conclusion.budget),
            "tuning": dict(tuning),
        },
        "dataset": {
            "fingerprint": evidence.dataset_fingerprint,
            "manifest": dict(manifest),
        },
        "trial_log": _trial_log_section(evidence),
        "fit_snapshots": _fit_snapshot_section(evidence),
        "fold_results": _fold_result_section(evidence),
        "stress_results": _stress_section(evidence.stress_registry),
        "conclusion": _conclusion_section(evidence),
        "audit_events": [dict(event) for event in audit_events],
    }


def export_oos_to_json(
    *,
    run_id: str,
    evidence: ConclusionEvidence,
    rolling: Mapping[str, object],
    tuning: Mapping[str, object],
    manifest: Mapping[str, object],
    audit_events: Sequence[Mapping[str, object]],
    schema_version: str = "1.0",
    indent: int = 2,
) -> str:
    """Export the full OOS validation state as a deterministic JSON string."""
    return json.dumps(
        export_oos_to_dict(
            run_id=run_id,
            evidence=evidence,
            rolling=rolling,
            tuning=tuning,
            manifest=manifest,
            audit_events=audit_events,
            schema_version=schema_version,
        ),
        sort_keys=True,
        ensure_ascii=False,
        indent=indent,
    )


__all__: tuple[str, ...] = (
    "OosExportError",
    "export_oos_to_dict",
    "export_oos_to_json",
)
