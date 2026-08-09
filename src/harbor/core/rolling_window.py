"""Rolling window generator (MVP 3 / SP 3.31).

Generates walk-forward folds from a frozen pre-registered split (SP 3.4) and
the rolling-window configuration (SP 3.2). Supports expanding windows
(扩展窗口 — the training window grows with every fold) and fixed-length
windows (固定长度窗口 — a constant ``train_length_days``). Every fold records
its train / validation / test boundaries and its retraining date so the whole
fold sequence is auditable (每个折叠的训练、验证、测试和再训练日均可审计).

Fold geometry
-------------
The base test interval ``[test_start, test_end]`` is the out-of-sample
horizon. It is tiled into contiguous, non-overlapping ``step_days``-long
segments (the last may be shorter) that can be concatenated into the OOS
equity path (SP 3.37). Fold ``i`` covers:

- test: ``[test_start + i*step, test_start + (i+1)*step - 1]``, clipped to the
  horizon end;
- validation: the ``validation_days`` days immediately before its test segment;
- training: ``[train_start, validation_start - 1]`` where ``train_start`` is
  the base ``train_start`` under ``EXPANDING`` or
  ``validation_start - train_length_days`` under ``FIXED``.

When the pre-registered split is tight (``train_end + 1 == validation_start``
and ``validation_end + 1 == test_start``), fold 0 reproduces the base split
exactly.

Retraining dates
----------------
The retraining date follows :class:`RetrainFrequency`: every fold retrains
under ``EVERY_FOLD``; under ``QUARTERLY`` / ``ANNUAL`` a fold retrains only
when its training end enters a new calendar quarter / year and otherwise
inherits the previous fold's retraining date — so every fold carries an
auditable, non-decreasing retraining date that SP 3.33 will act on.

Pure core layer: depends only on the SP 3.1 / 3.2 / 3.4 domain and config
types, never on storage, services or CLI.
"""

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import date, timedelta

from harbor.core.validation_config import (
    RetrainFrequency,
    RollingWindowConfig,
    RollingWindowMode,
)
from harbor.core.validation_domain import EvaluationSplit, WalkForwardFold


class RollingWindowError(ValueError):
    """Raised when a walk-forward fold sequence is invalid (SP 3.31)."""


def _add_days(day: date, days: int) -> date:
    """Return ``day`` shifted by ``days`` calendar days."""
    return day + timedelta(days=days)


def _quarter_key(day: date) -> tuple[int, int]:
    """Return the ``(year, quarter_index)`` bucket containing ``day``."""
    return (day.year, (day.month - 1) // 3)


@dataclass(frozen=True)
class FoldSequence:
    """An immutable, generated walk-forward fold sequence (SP 3.31).

    ``folds`` are ordered by ``fold_index`` from 0. Their out-of-sample (test)
    segments are contiguous and non-overlapping so they can be concatenated
    into the OOS equity path (SP 3.37). ``fingerprint`` is the derived
    SHA-256 digest of the whole sequence.
    """

    folds: tuple[WalkForwardFold, ...]
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.folds:
            raise RollingWindowError("a fold sequence requires at least one fold.")
        for index, fold in enumerate(self.folds):
            if fold.fold_index != index:
                raise RollingWindowError(
                    f"fold {index} must carry fold_index {index}, got {fold.fold_index}."
                )
        fingerprints = {fold.dataset_fingerprint for fold in self.folds}
        if len(fingerprints) != 1:
            raise RollingWindowError("all folds must share one dataset fingerprint.")
        for previous, current in zip(self.folds, self.folds[1:]):
            if _add_days(previous.test_end, 1) != current.test_start:
                raise RollingWindowError(
                    "out-of-sample segments must be contiguous and non-overlapping: "
                    f"fold {previous.fold_index} test ends {previous.test_end.isoformat()} "
                    f"but fold {current.fold_index} test starts {current.test_start.isoformat()}."
                )
            previous_retrain = previous.retrain_date
            current_retrain = current.retrain_date
            if previous_retrain is None or current_retrain is None:
                raise RollingWindowError("every fold must carry a retraining date.")
            if current_retrain < previous_retrain:
                raise RollingWindowError("retraining dates must be non-decreasing across folds.")
            if current_retrain > current.train_end:
                raise RollingWindowError(
                    "a fold's retraining date must not be after its training end."
                )
        if not self.fingerprint:
            raise RollingWindowError("fold sequence fingerprint must be non-empty.")

    def __len__(self) -> int:
        return len(self.folds)

    def __iter__(self) -> Iterator[WalkForwardFold]:
        return iter(self.folds)

    def __getitem__(self, index: int) -> WalkForwardFold:
        return self.folds[index]

    @property
    def oos_start(self) -> date:
        """The first out-of-sample day across the folds."""
        return self.folds[0].test_start

    @property
    def oos_end(self) -> date:
        """The last out-of-sample day across the folds."""
        return self.folds[-1].test_end

    def readable(self) -> str:
        """Render the fold sequence as one line."""
        return (
            f"{len(self.folds)} folds OOS "
            f"{self.oos_start.isoformat()}..{self.oos_end.isoformat()} fp {self.fingerprint}"
        )


def build_walk_forward_folds(
    split: EvaluationSplit,
    *,
    rolling: RollingWindowConfig,
    dataset_fingerprint: str,
) -> FoldSequence:
    """Generate the walk-forward fold sequence for a split (SP 3.31).

    See the module docstring for the fold geometry. The base test interval is
    the out-of-sample horizon and is tiled into contiguous ``step_days``
    segments; each fold's validation window is the ``validation_days`` days
    immediately before its segment, and its training window is expanding
    (``EXPANDING``, starting at the base ``train_start``) or fixed-length
    (``FIXED``).
    """
    if not dataset_fingerprint:
        raise RollingWindowError("dataset fingerprint must be non-empty.")
    base_train_start = split.train_start
    test_start = split.test_start
    test_end = split.test_end
    validation_days = split.validation_days
    step = rolling.step_days

    folds: list[WalkForwardFold] = []
    previous_train_end: date | None = None
    previous_retrain: date | None = None
    index = 0
    while True:
        fold_test_start = _add_days(test_start, index * step)
        if fold_test_start > test_end:
            break
        fold_test_end = min(_add_days(test_start, (index + 1) * step - 1), test_end)
        validation_start = _add_days(fold_test_start, -validation_days)
        validation_end = _add_days(fold_test_start, -1)
        train_end = _add_days(fold_test_start, -validation_days - 1)
        if rolling.mode is RollingWindowMode.EXPANDING:
            fold_train_start = base_train_start
        else:
            train_length = rolling.train_length_days
            if train_length is None:  # pragma: no cover - guarded below
                raise RollingWindowError("a fixed rolling window requires train_length_days.")
            fold_train_start = _add_days(train_end, -(train_length - 1))
        if index == 0:
            retrains = True
        elif rolling.retrain_frequency is RetrainFrequency.EVERY_FOLD:
            retrains = True
        else:
            previous_end = previous_train_end
            if previous_end is None:  # pragma: no cover - index > 0 implies a previous fold
                raise RollingWindowError("missing previous training end.")
            if rolling.retrain_frequency is RetrainFrequency.QUARTERLY:
                retrains = _quarter_key(train_end) != _quarter_key(previous_end)
            else:
                retrains = train_end.year != previous_end.year
        retrain_date = train_end if retrains else previous_retrain
        if retrain_date is None:  # pragma: no cover - index 0 always retrains
            raise RollingWindowError("missing previous retraining date.")
        folds.append(
            WalkForwardFold(
                fold_index=index,
                train_start=fold_train_start,
                train_end=train_end,
                validation_start=validation_start,
                validation_end=validation_end,
                test_start=fold_test_start,
                test_end=fold_test_end,
                retrain_date=retrain_date,
                dataset_fingerprint=dataset_fingerprint,
            )
        )
        previous_train_end = train_end
        previous_retrain = retrain_date
        index += 1

    sequence = FoldSequence(folds=tuple(folds), fingerprint="unfingerprinted")
    return replace(sequence, fingerprint=folds_fingerprint(sequence))


def folds_json(sequence: FoldSequence) -> str:
    """Return a stable, key-sorted JSON serialization of a fold sequence.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style).
    """
    payload: dict[str, object] = {
        "folds": [
            {
                "fold_index": fold.fold_index,
                "train_start": fold.train_start.isoformat(),
                "train_end": fold.train_end.isoformat(),
                "validation_start": fold.validation_start.isoformat(),
                "validation_end": fold.validation_end.isoformat(),
                "test_start": fold.test_start.isoformat(),
                "test_end": fold.test_end.isoformat(),
                "retrain_date": (
                    fold.retrain_date.isoformat() if fold.retrain_date is not None else None
                ),
                "dataset_fingerprint": fold.dataset_fingerprint,
                "run_id": fold.run_id,
            }
            for fold in sequence.folds
        ]
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def folds_fingerprint(sequence: FoldSequence) -> str:
    """Return the stable SHA-256 fingerprint of a fold sequence (SP 3.31)."""
    return hashlib.sha256(folds_json(sequence).encode("utf-8")).hexdigest()
