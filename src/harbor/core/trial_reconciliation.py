"""Training/validation result reconciliation (MVP 3 / SP 3.23).

Every parameter trial's validation run must close before its metric is trusted
(使用 MVP 2 的账本、净值和归因对账): the MVP 2 ledger reconciliation (SP 2.63),
the net-value series (净值) and the MVP 2 attribution reconciliation (SP 2.57)
must all reconcile. A trial that does not close is marked failed (任一试验不
闭合则标记失败) rather than participating in ranking — the failed replacement
carries no metric, so the SP 3.17/3.21 selection excludes it structurally
(而非参与排序).

- :class:`TrialReconciliation` records one trial's three checks and whether it
  closes.
- :func:`reconcile_trial` computes them from the MVP 2 reports; a missing
  report (or an empty net-value series) is treated as NOT reconciled — the
  never-assume rule: a trial that cannot prove it closes is not closed.
- :func:`net_values_reconcile` is the net-value 对账: non-empty, strictly
  ascending dates and every snapshot satisfying total = cash + securities.
- :func:`mark_trial_failed` turns a closing trial into a failed trial
  (metric cleared, failure reason set); :func:`reconcile_trials` applies it to
  every non-closing trial and returns the reconciled set plus a fingerprinted
  :class:`TrialReconciliationSummary` (SP 3.28).

Pure core layer: depends on the SP 3.18 trial domain and the MVP 2 ledger,
net-value and attribution types, never on storage, services or CLI code.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum

from harbor.core.attribution import AttributionReport
from harbor.core.backtest_domain import NetValue
from harbor.core.ledger_reconciliation import LedgerReconciliationReport
from harbor.core.validation_domain import ParameterTrial


class TrialReconciliationError(ValueError):
    """Raised when a trial result cannot be reconciled (SP 3.23)."""


class ReconciliationCheck(StrEnum):
    """The three MVP 2 reconciliation checks applied to a trial (SP 3.23)."""

    LEDGER = "ledger"
    NET_VALUE = "net_value"
    ATTRIBUTION = "attribution"


@dataclass(frozen=True)
class TrialReconciliation:
    """One trial's ledger / net-value / attribution reconciliation (SP 3.23).

    ``reconciled`` is true only when all three MVP 2 checks close; ``failures``
    lists the checks that did not.
    """

    trial_id: str
    ledger_reconciled: bool
    net_value_reconciled: bool
    attribution_reconciled: bool

    def __post_init__(self) -> None:
        if not self.trial_id:
            raise TrialReconciliationError("trial_id must be non-empty.")

    @property
    def reconciled(self) -> bool:
        """Whether ledger, net value and attribution all close."""
        return self.ledger_reconciled and self.net_value_reconciled and self.attribution_reconciled

    @property
    def failures(self) -> tuple[ReconciliationCheck, ...]:
        """The checks that did not close, in declaration order."""
        checks: list[ReconciliationCheck] = []
        if not self.ledger_reconciled:
            checks.append(ReconciliationCheck.LEDGER)
        if not self.net_value_reconciled:
            checks.append(ReconciliationCheck.NET_VALUE)
        if not self.attribution_reconciled:
            checks.append(ReconciliationCheck.ATTRIBUTION)
        return tuple(checks)

    def readable(self) -> str:
        """Render the trial reconciliation as one line."""
        status = "reconciled" if self.reconciled else "MISMATCH"
        return (
            f"trial {self.trial_id} ledger {self.ledger_reconciled} "
            f"net_value {self.net_value_reconciled} "
            f"attribution {self.attribution_reconciled} [{status}]"
        )


@dataclass(frozen=True)
class TrialReconciliationSummary:
    """The reconciliation summary over every trial (SP 3.23).

    ``reconciliations`` are key-sorted by trial id; ``failed_trial_ids`` lists
    the trials that did not close. ``fingerprint`` is the derived SHA-256
    digest and is excluded from its own digest so it can be re-derived.
    """

    reconciliations: tuple[TrialReconciliation, ...]
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.fingerprint:
            raise TrialReconciliationError("summary fingerprint must be non-empty.")
        trial_ids = [reconciliation.trial_id for reconciliation in self.reconciliations]
        if sorted(trial_ids) != trial_ids:
            raise TrialReconciliationError("reconciliations must be key-sorted by trial id.")
        if len(set(trial_ids)) != len(trial_ids):
            raise TrialReconciliationError("reconciliations must be unique per trial id.")

    @property
    def failed_trial_ids(self) -> tuple[str, ...]:
        """The key-sorted ids of trials that did not close."""
        return tuple(
            reconciliation.trial_id
            for reconciliation in self.reconciliations
            if not reconciliation.reconciled
        )

    @property
    def all_reconciled(self) -> bool:
        """Whether every trial closes."""
        return not self.failed_trial_ids

    @property
    def reconciled_count(self) -> int:
        """Number of closing trials."""
        return sum(1 for reconciliation in self.reconciliations if reconciliation.reconciled)

    @property
    def failed_count(self) -> int:
        """Number of non-closing trials."""
        return len(self.failed_trial_ids)

    def readable(self) -> str:
        """Render the summary as one line."""
        return (
            f"reconciliation trials {len(self.reconciliations)} "
            f"reconciled {self.reconciled_count} failed {self.failed_count} "
            f"all_reconciled {self.all_reconciled}"
        )


@dataclass(frozen=True)
class ReconciledTrials:
    """The reconciliation gate output (SP 3.23).

    ``trials`` keeps every input trial, with each non-closing trial replaced
    by a failed trial (no metric, so SP 3.21 ranking excludes it);
    ``failed_trials`` lists those failed replacements; ``summary`` is the
    fingerprinted reconciliation summary.
    """

    trials: tuple[ParameterTrial, ...]
    failed_trials: tuple[ParameterTrial, ...]
    summary: TrialReconciliationSummary

    def __post_init__(self) -> None:
        failed_ids = set(self.summary.failed_trial_ids)
        replacement_ids = {trial.trial_id for trial in self.failed_trials}
        if failed_ids != replacement_ids:
            raise TrialReconciliationError(
                "failed_trials must match the summary's failed trial ids."
            )

    def readable(self) -> str:
        """Render the gate output as one line."""
        return (
            f"reconciled trials {len(self.trials)} "
            f"failed {len(self.failed_trials)}: {self.summary.readable()}"
        )


def net_values_reconcile(net_values: Sequence[NetValue]) -> bool:
    """Return whether the net-value series closes (净值对账, SP 3.23).

    The series must be non-empty, its snapshots strictly ascending by date,
    every snapshot's total value must equal cash + securities and no
    component may be negative.
    """
    if not net_values:
        return False
    previous: date | None = None
    for snapshot in net_values:
        if snapshot.cash < 0 or snapshot.securities_value < 0 or snapshot.fees_paid < 0:
            return False
        if snapshot.total_value != snapshot.cash + snapshot.securities_value:
            return False
        if previous is not None and not (previous < snapshot.as_of_date):
            return False
        previous = snapshot.as_of_date
    return True


def reconcile_trial(
    trial_id: str,
    *,
    ledger: LedgerReconciliationReport | None = None,
    net_values: Sequence[NetValue] = (),
    attribution: AttributionReport | None = None,
) -> TrialReconciliation:
    """Reconcile one trial from the MVP 2 ledger, net-value and attribution.

    A missing ledger or attribution report — or an empty net-value series —
    is treated as NOT reconciled (never-assume rule): a trial that cannot
    prove it closes does not close.
    """
    if not trial_id:
        raise TrialReconciliationError("trial_id must be non-empty.")
    return TrialReconciliation(
        trial_id=trial_id,
        ledger_reconciled=ledger.reconciled if ledger is not None else False,
        net_value_reconciled=net_values_reconcile(net_values),
        attribution_reconciled=(attribution.reconciled if attribution is not None else False),
    )


def mark_trial_failed(trial: ParameterTrial, *, reason: str) -> ParameterTrial:
    """Mark a trial failed (no metric) so it cannot participate in ranking.

    An already-failed trial is returned unchanged; otherwise the trial's
    identity fields are preserved and only the outcome is replaced with a
    failure reason (SP 3.23).
    """
    if not reason:
        raise TrialReconciliationError("failure reason must be non-empty.")
    if trial.metric is None:
        return trial
    return replace(trial, metric=None, failed_reason=reason)


def _missing_reconciliation(trial_id: str) -> TrialReconciliation:
    """Return an all-failed reconciliation for a trial with no record."""
    return TrialReconciliation(
        trial_id=trial_id,
        ledger_reconciled=False,
        net_value_reconciled=False,
        attribution_reconciled=False,
    )


def _failure_reason(reconciliation: TrialReconciliation) -> str:
    """Render the non-closing checks as one reason string."""
    names = ", ".join(check.value for check in reconciliation.failures)
    return f"reconciliation failed: {names}"


def build_reconciliation_summary(
    reconciliations: Sequence[TrialReconciliation],
) -> TrialReconciliationSummary:
    """Assemble a key-sorted, fingerprinted reconciliation summary (SP 3.23)."""
    entries = tuple(sorted(reconciliations, key=lambda entry: entry.trial_id))
    summary = TrialReconciliationSummary(reconciliations=entries, fingerprint="unfingerprinted")
    return replace(summary, fingerprint=reconciliation_fingerprint(summary))


def reconcile_trials(
    trials: Sequence[ParameterTrial],
    *,
    reconciliations: Mapping[str, TrialReconciliation],
) -> ReconciledTrials:
    """Gate every trial: non-closing trials are marked failed, not ranked.

    Every input trial is kept; a trial with no reconciliation record or one
    that does not close is replaced by a failed trial (no metric), so the
    SP 3.17/3.21 selection excludes it from ranking. The output includes the
    fingerprinted summary over all trials.
    """
    entries: list[TrialReconciliation] = []
    output: list[ParameterTrial] = []
    failed: list[ParameterTrial] = []
    for trial in trials:
        reconciliation = reconciliations.get(trial.trial_id)
        if reconciliation is None:
            reconciliation = _missing_reconciliation(trial.trial_id)
            reason = f"no reconciliation recorded for trial {trial.trial_id}"
        elif reconciliation.reconciled:
            reason = None
        else:
            reason = _failure_reason(reconciliation)
        entries.append(reconciliation)
        if reason is None:
            output.append(trial)
        else:
            failed_trial = mark_trial_failed(trial, reason=reason)
            failed.append(failed_trial)
            output.append(failed_trial)
    summary = build_reconciliation_summary(entries)
    return ReconciledTrials(
        trials=tuple(output),
        failed_trials=tuple(failed),
        summary=summary,
    )


def reconciliation_json(summary: TrialReconciliationSummary) -> str:
    """Return a stable, key-sorted JSON serialization of a summary.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style).
    """
    payload: dict[str, object] = {
        "reconciliations": [
            {
                "trial_id": reconciliation.trial_id,
                "ledger_reconciled": reconciliation.ledger_reconciled,
                "net_value_reconciled": reconciliation.net_value_reconciled,
                "attribution_reconciled": reconciliation.attribution_reconciled,
            }
            for reconciliation in summary.reconciliations
        ]
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def reconciliation_fingerprint(summary: TrialReconciliationSummary) -> str:
    """Return the stable SHA-256 fingerprint of a summary (SP 3.23).

    Identical summaries always fingerprint identically; the digest excludes
    the derived fingerprint field so it can be re-derived and verified.
    """
    return hashlib.sha256(reconciliation_json(summary).encode("utf-8")).hexdigest()
