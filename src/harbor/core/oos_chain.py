"""Fold chain integrity (MVP 3 / SP 3.36).

Verifies that every out-of-sample result can be linked to its complete
evidence chain (折叠链路完整性): the frozen data manifest (数据清单, SP 3.7
fingerprint), the training fit snapshot (拟合快照, SP 3.19 fingerprint), the
selected parameter trial (参数试验, SP 3.18 trial id + fingerprint), the MVP 2
run and its replay manifest (MVP 2 运行, SP 2.61) and the report artifacts
(报告产物). A fold whose chain is missing any link is surfaced with exactly
which links are missing — never silently assumed complete.

The links are extracted from the SP 3.35 :class:`RollingOosRun` (which already
enforced the per-artifact consistency) and re-verified here: a selected trial
must be the fold's trial (dataset fingerprint + train/validation bounds match)
or the chain is rejected as inconsistent. The aggregate
:class:`OosChainIntegrity` records one :class:`FoldChain` per fold and reports
whether every fold's chain is complete — the evidence linkage the SP 3.12
artifact tables persist.

Pure core layer: depends only on the SP 3.35 run and the SP 3.18 trial
fingerprint, never on storage, services or CLI.
"""

import hashlib
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace

from harbor.core.rolling_oos import RollingOosRun
from harbor.core.trial_registry import trial_fingerprint
from harbor.core.validation_domain import WalkForwardFold


class OosChainError(ValueError):
    """Raised when a fold's evidence chain is inconsistent (SP 3.36)."""


@dataclass(frozen=True)
class FoldChain:
    """One fold's evidence-chain links (SP 3.36).

    ``dataset_fingerprint`` (数据清单), ``fit_fingerprint`` (拟合快照),
    ``trial_id`` / ``trial_fingerprint`` (参数试验), ``run_id`` /
    ``replay_fingerprint`` (MVP 2 运行) and ``report_artifact_fingerprint``
    (报告产物) are the links every OOS result must carry. ``trial`` and ``run``
    links must be paired (id + fingerprint together).
    """

    fold_index: int
    dataset_fingerprint: str | None = None
    fit_fingerprint: str | None = None
    trial_id: str | None = None
    trial_fingerprint: str | None = None
    run_id: str | None = None
    replay_fingerprint: str | None = None
    report_artifact_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if self.fold_index < 0:
            raise OosChainError("fold index must be non-negative.")
        if (self.trial_id is None) != (self.trial_fingerprint is None):
            raise OosChainError("trial id and trial fingerprint must be paired.")
        if (self.run_id is None) != (self.replay_fingerprint is None):
            raise OosChainError("run id and replay fingerprint must be paired.")

    @property
    def missing_links(self) -> tuple[str, ...]:
        """The names of the evidence links this fold is missing, in order."""
        missing: list[str] = []
        if not self.dataset_fingerprint:
            missing.append("dataset_manifest")
        if not self.fit_fingerprint:
            missing.append("fit_snapshot")
        if not self.trial_id or not self.trial_fingerprint:
            missing.append("parameter_trial")
        if not self.run_id or not self.replay_fingerprint:
            missing.append("mvp2_run")
        if not self.report_artifact_fingerprint:
            missing.append("report_artifact")
        return tuple(missing)

    @property
    def complete(self) -> bool:
        """Whether every evidence link is present."""
        return not self.missing_links

    def readable(self) -> str:
        """Render the fold's chain as one line."""
        if self.complete:
            return f"fold {self.fold_index} chain complete ({self.run_id})"
        return f"fold {self.fold_index} chain missing: {', '.join(self.missing_links)}"


@dataclass(frozen=True)
class OosChainIntegrity:
    """The aggregate evidence-chain integrity of a rolling OOS run (SP 3.36).

    One :class:`FoldChain` per fold, ordered by ``fold_index`` from 0. The
    run is ``complete`` only when every fold's chain has every evidence link.
    ``dataset_fingerprint`` / ``code_version`` are inherited from the SP 3.35
    run and ``fingerprint`` is the derived SHA-256 digest.
    """

    chains: tuple[FoldChain, ...]
    dataset_fingerprint: str
    code_version: str
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.chains:
            raise OosChainError("a chain integrity report requires at least one fold.")
        if not self.dataset_fingerprint:
            raise OosChainError("dataset fingerprint must be non-empty.")
        if not self.code_version:
            raise OosChainError("code version must be non-empty.")
        for index, chain in enumerate(self.chains):
            if chain.fold_index != index:
                raise OosChainError(
                    f"chain {index} must carry fold_index {index}, got {chain.fold_index}."
                )
            if chain.dataset_fingerprint != self.dataset_fingerprint:
                raise OosChainError(
                    f"fold {index} chain dataset fingerprint does not match the run."
                )
        if not self.fingerprint:
            raise OosChainError("chain integrity fingerprint must be non-empty.")

    def __len__(self) -> int:
        return len(self.chains)

    def __iter__(self) -> Iterator[FoldChain]:
        return iter(self.chains)

    def __getitem__(self, index: int) -> FoldChain:
        return self.chains[index]

    def chain_for(self, fold_index: int) -> FoldChain | None:
        """Return the chain for ``fold_index``, or ``None``."""
        for chain in self.chains:
            if chain.fold_index == fold_index:
                return chain
        return None

    def missing_links_for(self, fold_index: int) -> tuple[str, ...] | None:
        """Return the missing links for ``fold_index`` (``None`` when absent)."""
        chain = self.chain_for(fold_index)
        return chain.missing_links if chain is not None else None

    @property
    def complete(self) -> bool:
        """Whether every fold's evidence chain is complete."""
        return all(chain.complete for chain in self.chains)

    @property
    def incomplete_chains(self) -> tuple[FoldChain, ...]:
        """The folds whose evidence chain is incomplete."""
        return tuple(chain for chain in self.chains if not chain.complete)

    def readable(self) -> str:
        """Render the chain integrity as one line."""
        return (
            f"{self.complete_chains_count()}/{len(self.chains)} folds with "
            f"complete evidence chains, dataset {self.dataset_fingerprint[:12]} "
            f"code {self.code_version} fp {self.fingerprint}"
        )

    def complete_chains_count(self) -> int:
        """Number of folds whose evidence chain is complete."""
        return sum(1 for chain in self.chains if chain.complete)


def verify_fold_chains(
    oos_run: RollingOosRun,
    *,
    report_artifact_for: Callable[[WalkForwardFold], str | None] | None = None,
) -> OosChainIntegrity:
    """Verify every OOS result links to its full evidence chain (SP 3.36).

    Extracts the data-manifest, fit-snapshot, parameter-trial, MVP 2 run and
    report-artifact links from the SP 3.35 run; a selected trial that is not
    the fold's own trial (dataset fingerprint or train/validation bounds
    mismatch) is rejected as an inconsistent chain.
    """
    chains: list[FoldChain] = []
    for result in oos_run.results:
        fold = result.validation.fold
        application = result.validation.application
        dataset_fingerprint = application.dataset_fingerprint
        fit_fingerprint = application.fit_fingerprint
        selected = result.validation.training.selection.selected
        trial_id: str | None = None
        trial_fp: str | None = None
        if selected is not None:
            if selected.dataset_fingerprint != dataset_fingerprint:
                raise OosChainError(
                    f"fold {fold.fold_index} selected trial dataset fingerprint "
                    "does not match the fold's data manifest."
                )
            if (
                selected.train_start != fold.train_start
                or selected.train_end != fold.train_end
                or selected.validation_start != fold.validation_start
                or selected.validation_end != fold.validation_end
            ):
                raise OosChainError(
                    f"fold {fold.fold_index} selected trial is not bound to the "
                    "fold's train / validation interval."
                )
            trial_id = selected.trial_id
            trial_fp = trial_fingerprint(selected)
        run_id = result.run_id
        replay_fp = (
            result.replay_manifest.fingerprint() if result.replay_manifest is not None else None
        )
        report_artifact = report_artifact_for(fold) if report_artifact_for is not None else None
        chains.append(
            FoldChain(
                fold_index=fold.fold_index,
                dataset_fingerprint=dataset_fingerprint,
                fit_fingerprint=fit_fingerprint,
                trial_id=trial_id,
                trial_fingerprint=trial_fp,
                run_id=run_id,
                replay_fingerprint=replay_fp,
                report_artifact_fingerprint=report_artifact,
            )
        )
    integrity = OosChainIntegrity(
        chains=tuple(chains),
        dataset_fingerprint=oos_run.dataset_fingerprint,
        code_version=oos_run.code_version,
        fingerprint="unfingerprinted",
    )
    return replace(integrity, fingerprint=oos_chain_fingerprint(integrity))


def oos_chain_json(integrity: OosChainIntegrity) -> str:
    """Return a stable, key-sorted JSON serialization of a chain report.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style).
    """
    payload: dict[str, object] = {
        "dataset_fingerprint": integrity.dataset_fingerprint,
        "code_version": integrity.code_version,
        "chains": [
            {
                "fold_index": chain.fold_index,
                "dataset_fingerprint": chain.dataset_fingerprint,
                "fit_fingerprint": chain.fit_fingerprint,
                "trial_id": chain.trial_id,
                "trial_fingerprint": chain.trial_fingerprint,
                "run_id": chain.run_id,
                "replay_fingerprint": chain.replay_fingerprint,
                "report_artifact_fingerprint": chain.report_artifact_fingerprint,
            }
            for chain in integrity.chains
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def oos_chain_fingerprint(integrity: OosChainIntegrity) -> str:
    """Return the stable SHA-256 fingerprint of a chain report (SP 3.36)."""
    return hashlib.sha256(oos_chain_json(integrity).encode("utf-8")).hexdigest()
