"""Immutable validation-domain types (MVP 3 / SP 3.1).

These value types are the shared vocabulary for out-of-sample validation: a
frozen dataset manifest (SP 3.6), the train / validation / test split
(SP 3.4), a parameter trial (SP 3.18), a walk-forward fold (SP 3.31) and the
two enums that drive the validation state machine (SP 3.13) and the OOS
conclusion rules (SP 3.58). Every type is immutable — a frozen dataclass or
an enum — so recorded validation state can be replayed deterministically and
no later edit can silently change a frozen boundary or a recorded trial.

The split and fold types enforce a strict, non-overlapping time ordering
(SP 3.4): training ends strictly before validation starts, and validation
ends strictly before the test interval begins; a reversed, overlapping or
empty range is rejected with :class:`SplitBoundaryError` rather than silently
normalized. Pure core logic: depends only on the backtest domain types and
never touches storage, services or CLI code.
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from harbor.core.backtest_domain import Currency, Market


class SplitBoundaryError(ValueError):
    """Raised when a train / validation / test boundary is invalid (SP 3.4)."""


class ValidationStatus(StrEnum):
    """Lifecycle status of a validation run (SP 3.13 state machine).

    A run starts as a ``DRAFT`` and must freeze its data (``DATA_FROZEN``)
    before parameter tuning (``TUNING``) and locking the test set
    (``TEST_LOCKED``). Evaluation produces ``EVALUATED``; a run that cannot
    satisfy the coverage or stability rules lands in ``NOT_QUALIFIED`` and an
    execution failure in ``FAILED``.
    """

    DRAFT = "DRAFT"
    DATA_FROZEN = "DATA_FROZEN"
    TUNING = "TUNING"
    TEST_LOCKED = "TEST_LOCKED"
    EVALUATED = "EVALUATED"
    NOT_QUALIFIED = "NOT_QUALIFIED"
    FAILED = "FAILED"


class OOSConclusion(StrEnum):
    """The pre-registered out-of-sample conclusion (SP 3.58)."""

    QUALIFIED = "QUALIFIED"
    NOT_QUALIFIED = "NOT_QUALIFIED"
    INCONCLUSIVE = "INCONCLUSIVE"


def _require_ordered_ranges(*ranges: tuple[date, date]) -> None:
    """Require each range to be non-empty and successive ranges strictly ordered.

    Each ``(start, end)`` range must satisfy ``start <= end`` (a single day is
    non-empty); a range where ``start > end`` is empty or reversed and is
    rejected. Successive ranges must satisfy ``previous_end < next_start`` so
    the train / validation / test intervals never overlap and training always
    ends strictly before validation (SP 3.4).

    Raises:
        SplitBoundaryError: If a range is empty/reversed or two successive
            ranges overlap or touch.
    """
    previous_end: date | None = None
    for start, end in ranges:
        if start > end:
            raise SplitBoundaryError(
                f"empty or reversed range {start.isoformat()}..{end.isoformat()}."
            )
        if previous_end is not None and not (previous_end < start):
            raise SplitBoundaryError(
                f"ranges must be strictly ordered: {previous_end.isoformat()} "
                f"must be before {start.isoformat()}."
            )
        previous_end = end


@dataclass(frozen=True)
class DatasetManifest:
    """The frozen data version used by a validation run (SP 3.6 / 3.7).

    Records the data query boundaries, source versions, the strategy config
    hash, the code version and the derived fingerprint so a conclusion can
    always be reproduced against the exact data it was computed from
    (SP 3.7).
    """

    markets: tuple[Market, ...]
    base_currency: Currency
    start_date: date
    end_date: date
    data_cutoff: date
    config_hash: str
    code_version: str
    calendar_version: str
    fx_source: str
    fingerprint: str
    random_seed: int | None = None

    def __post_init__(self) -> None:
        if not self.markets:
            raise ValueError("Dataset manifest requires at least one market.")
        if not self.config_hash:
            raise ValueError("Dataset manifest config hash must be non-empty.")
        if not self.code_version:
            raise ValueError("Dataset manifest code version must be non-empty.")
        if not self.calendar_version:
            raise ValueError("Dataset manifest calendar version must be non-empty.")
        if not self.fx_source:
            raise ValueError("Dataset manifest FX source must be non-empty.")
        if not self.fingerprint:
            raise ValueError("Dataset manifest fingerprint must be non-empty.")
        if self.end_date < self.start_date:
            raise SplitBoundaryError("manifest end_date must be on or after start_date.")
        if self.data_cutoff < self.start_date or self.data_cutoff > self.end_date:
            raise SplitBoundaryError("manifest data_cutoff must lie within the manifest range.")

    def readable(self) -> str:
        """Render the manifest as a single-line data-version summary."""
        markets = ", ".join(market.value for market in self.markets)
        return (
            f"dataset {self.fingerprint} [{markets}, base {self.base_currency.value}] "
            f"{self.start_date.isoformat()}..{self.end_date.isoformat()} "
            f"cutoff {self.data_cutoff.isoformat()} config {self.config_hash} "
            f"code {self.code_version} calendar {self.calendar_version} "
            f"fx {self.fx_source} seed {self.random_seed}"
        )


@dataclass(frozen=True)
class EvaluationSplit:
    """The frozen train / validation / test intervals (SP 3.4).

    Training ends strictly before validation starts and validation ends
    strictly before the test interval begins; each interval must be
    non-empty. A reversed, overlapping or empty boundary is rejected by
    :class:`SplitBoundaryError`.
    """

    train_start: date
    train_end: date
    validation_start: date
    validation_end: date
    test_start: date
    test_end: date

    def __post_init__(self) -> None:
        _require_ordered_ranges(
            (self.train_start, self.train_end),
            (self.validation_start, self.validation_end),
            (self.test_start, self.test_end),
        )

    @property
    def train_days(self) -> int:
        """Number of training days (inclusive)."""
        return (self.train_end - self.train_start).days + 1

    @property
    def validation_days(self) -> int:
        """Number of validation days (inclusive)."""
        return (self.validation_end - self.validation_start).days + 1

    @property
    def test_days(self) -> int:
        """Number of test days (inclusive)."""
        return (self.test_end - self.test_start).days + 1

    def readable(self) -> str:
        """Render the split boundaries as one line."""
        return (
            f"split train {self.train_start.isoformat()}..{self.train_end.isoformat()} "
            f"validation {self.validation_start.isoformat()}..{self.validation_end.isoformat()} "
            f"test {self.test_start.isoformat()}..{self.test_end.isoformat()}"
        )


@dataclass(frozen=True)
class Parameter:
    """One declared parameter value in a trial (SP 3.15 / 3.18)."""

    name: str
    value: float | str | int | bool | None

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Parameter name must be non-empty.")


@dataclass(frozen=True)
class ParameterTrial:
    """A single recorded parameter trial (SP 3.18).

    Records the trial parameters, the train / validation boundaries it used,
    the frozen dataset fingerprint, the random seed, the code version and the
    resulting validation metric (or the failure reason) so the trial is
    auditable and replayable. A failed trial carries no metric; a completed
    trial must carry a metric.
    """

    trial_id: str
    parameters: tuple[Parameter, ...]
    dataset_fingerprint: str
    train_start: date
    train_end: date
    validation_start: date
    validation_end: date
    seed: int
    code_version: str
    metric: float | None = None
    failed_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.trial_id:
            raise ValueError("Trial id must be non-empty.")
        if not self.dataset_fingerprint:
            raise ValueError("Trial dataset fingerprint must be non-empty.")
        if not self.code_version:
            raise ValueError("Trial code version must be non-empty.")
        _require_ordered_ranges(
            (self.train_start, self.train_end),
            (self.validation_start, self.validation_end),
        )
        if self.failed_reason is not None and self.metric is not None:
            raise ValueError("A failed trial must not carry a metric.")
        if self.failed_reason is None and self.metric is None:
            raise ValueError("A trial must carry a metric or a failure reason.")

    def parameter(self, name: str) -> object:
        """Return the value of ``name``, or ``None`` when not declared."""
        for parameter in self.parameters:
            if parameter.name == name:
                return parameter.value
        return None

    def readable(self) -> str:
        """Render the trial as one line with its parameters and outcome."""
        values = ", ".join(f"{p.name}={p.value}" for p in self.parameters)
        outcome = (
            f"metric {self.metric:.4f}"
            if self.metric is not None
            else f"failed: {self.failed_reason}"
        )
        return (
            f"trial {self.trial_id} [{values}] {outcome} "
            f"fingerprint {self.dataset_fingerprint} seed {self.seed} "
            f"code {self.code_version}"
        )


@dataclass(frozen=True)
class WalkForwardFold:
    """One rolling out-of-sample fold (SP 3.31 / 3.35).

    A fold uses its own train / validation / test intervals, a retraining
    anchor, the frozen dataset fingerprint and the resulting MVP 2 backtest
    run id. The test interval is the fold's out-of-sample segment; folds are
    concatenated into the OOS equity path (SP 3.37).
    """

    fold_index: int
    train_start: date
    train_end: date
    validation_start: date
    validation_end: date
    test_start: date
    test_end: date
    retrain_date: date | None
    dataset_fingerprint: str
    run_id: str | None = None

    def __post_init__(self) -> None:
        if self.fold_index < 0:
            raise ValueError("Fold index must be non-negative.")
        if not self.dataset_fingerprint:
            raise ValueError("Fold dataset fingerprint must be non-empty.")
        _require_ordered_ranges(
            (self.train_start, self.train_end),
            (self.validation_start, self.validation_end),
            (self.test_start, self.test_end),
        )

    def readable(self) -> str:
        """Render the fold boundaries and its run link as one line."""
        retrain = self.retrain_date.isoformat() if self.retrain_date is not None else "none"
        run = self.run_id if self.run_id is not None else "pending"
        return (
            f"fold {self.fold_index} "
            f"train {self.train_start.isoformat()}..{self.train_end.isoformat()} "
            f"validation {self.validation_start.isoformat()}..{self.validation_end.isoformat()} "
            f"test {self.test_start.isoformat()}..{self.test_end.isoformat()} "
            f"retrain {retrain} run {run} fingerprint {self.dataset_fingerprint}"
        )
