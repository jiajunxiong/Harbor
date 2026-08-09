"""Fold dispersion analysis (MVP 3 / SP 3.39).

Outputs each fold's returns, drawdown, turnover, coverage score and failure
reason (输出各折叠的收益、回撤、换手、覆盖评分和失败原因分布) and surfaces
the dispersion explicitly — the per-fold values, the average, the return
spread and the worst fold — so unstable folds are not masked by an average
(不以平均值掩盖不稳定折叠).

Per-fold returns and drawdown are derived from each fold's OOS net-value
segment (SP 2.53) of the SP 3.37 concatenated path; per-fold coverage comes
from the SP 3.34 validation data-quality score carried by the SP 3.35 run;
per-fold turnover is not carried by the path so it is injected
(``turnover_for``); and the failure-reason distribution counts the SP 3.35
not-executed folds. A fold that did not execute has no return/drawdown metrics
and records its failure reason instead.

Pure core layer: depends only on the SP 3.35 run, the SP 3.38 performance
report and the MVP 2 metric helpers, never on storage, services or CLI.
"""

import hashlib
import json
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace

from harbor.core.backtest_domain import NetValue
from harbor.core.oos_concat import OosEquityPath
from harbor.core.oos_performance import OosPerformanceReport
from harbor.core.performance_metrics import cumulative_return, max_drawdown
from harbor.core.rolling_oos import RollingOosRun
from harbor.core.validation_domain import WalkForwardFold


class OosDispersionError(ValueError):
    """Raised when fold dispersion cannot be computed (SP 3.39)."""


def _fold_return_metrics(segment: Sequence[NetValue]) -> tuple[float, float]:
    """Cumulative return and max drawdown of one fold's OOS segment."""
    if len(segment) < 2:
        return 0.0, 0.0
    return cumulative_return(segment), max_drawdown(segment)


@dataclass(frozen=True)
class FoldDispersion:
    """One fold's dispersion metrics (SP 3.39).

    ``cumulative_return`` (收益) and ``max_drawdown`` (回撤) are the fold's OOS
    segment metrics (``None`` when the fold did not execute);
    ``turnover`` (换手) the injected per-fold turnover (``None`` when
    unmeasured); ``coverage_pct`` (覆盖评分) the validation data-quality score;
    ``failure_reason`` (失败原因) why the fold did not execute.
    """

    fold_index: int
    cumulative_return: float | None
    max_drawdown: float | None
    turnover: float | None
    coverage_pct: float | None
    failure_reason: str | None

    def __post_init__(self) -> None:
        if self.fold_index < 0:
            raise OosDispersionError("fold index must be non-negative.")
        executed = self.cumulative_return is not None
        if executed:
            if self.max_drawdown is None:
                raise OosDispersionError("an executed fold must carry a max drawdown.")
            if self.failure_reason is not None:
                raise OosDispersionError("an executed fold must not carry a failure reason.")
        else:
            if self.max_drawdown is not None:
                raise OosDispersionError("a non-executed fold must not carry a max drawdown.")
            if self.failure_reason is None:
                raise OosDispersionError("a non-executed fold must carry a failure reason.")

    def readable(self) -> str:
        """Render the fold's dispersion as one line."""
        if self.cumulative_return is None:
            return (
                f"fold {self.fold_index} NOT executed: {self.failure_reason} "
                f"coverage {self.coverage_pct}"
            )
        return (
            f"fold {self.fold_index} return {self.cumulative_return:.4%} "
            f"maxdd {self.max_drawdown:.4%} turnover {self.turnover} "
            f"coverage {self.coverage_pct}"
        )


@dataclass(frozen=True)
class OosDispersionReport:
    """The per-fold dispersion report on a rolling OOS run (SP 3.39).

    One :class:`FoldDispersion` per fold, ordered by ``fold_index`` from 0.
    The summary properties expose the average return alongside the return
    spread and the worst fold, so an unstable fold is not hidden by the
    average.
    """

    folds: tuple[FoldDispersion, ...]
    dataset_fingerprint: str
    code_version: str
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.folds:
            raise OosDispersionError("a dispersion report requires at least one fold.")
        if not self.dataset_fingerprint:
            raise OosDispersionError("dataset fingerprint must be non-empty.")
        if not self.code_version:
            raise OosDispersionError("code version must be non-empty.")
        for index, fold in enumerate(self.folds):
            if fold.fold_index != index:
                raise OosDispersionError(
                    f"fold {index} must carry fold_index {index}, got {fold.fold_index}."
                )
        if not self.fingerprint:
            raise OosDispersionError("dispersion report fingerprint must be non-empty.")

    def __len__(self) -> int:
        return len(self.folds)

    def __iter__(self) -> Iterator[FoldDispersion]:
        return iter(self.folds)

    def __getitem__(self, index: int) -> FoldDispersion:
        return self.folds[index]

    @property
    def cumulative_returns(self) -> tuple[float, ...]:
        """The executed folds' cumulative returns."""
        return tuple(
            fold.cumulative_return for fold in self.folds if fold.cumulative_return is not None
        )

    @property
    def average_return(self) -> float | None:
        """The mean of the executed folds' returns (``None`` when none)."""
        returns = self.cumulative_returns
        if not returns:
            return None
        return sum(returns) / len(returns)

    @property
    def return_spread(self) -> float | None:
        """The max-minus-min return spread (``None`` with fewer than two)."""
        returns = self.cumulative_returns
        if len(returns) < 2:
            return None
        return max(returns) - min(returns)

    @property
    def worst_fold_index(self) -> int | None:
        """The executed fold with the lowest return (``None`` when none)."""
        executed = [fold for fold in self.folds if fold.cumulative_return is not None]
        if not executed:
            return None
        return min(executed, key=lambda fold: fold.cumulative_return or 0.0).fold_index

    @property
    def failure_distribution(self) -> tuple[tuple[str, int], ...]:
        """The (reason, count) distribution of not-executed folds, sorted."""
        counts: dict[str, int] = {}
        for fold in self.folds:
            if fold.failure_reason is not None:
                counts[fold.failure_reason] = counts.get(fold.failure_reason, 0) + 1
        return tuple(sorted(counts.items(), key=lambda item: (-item[1], item[0])))

    def readable(self) -> str:
        """Render the dispersion report as one line."""
        average = "n/a" if self.average_return is None else f"{self.average_return:.4%}"
        spread = "n/a" if self.return_spread is None else f"{self.return_spread:.4%}"
        worst = "n/a" if self.worst_fold_index is None else str(self.worst_fold_index)
        failures = sum(1 for fold in self.folds if fold.failure_reason is not None)
        return (
            f"{len(self.folds)} folds avg return {average} spread {spread} "
            f"worst fold {worst} failures {failures} fp {self.fingerprint}"
        )


def compute_fold_dispersion(
    oos_run: RollingOosRun,
    performance_report: OosPerformanceReport,
    *,
    turnover_for: Callable[[WalkForwardFold], float | None] | None = None,
) -> OosDispersionReport:
    """Compute per-fold dispersion on a rolling OOS run (SP 3.39).

    Returns, drawdown and coverage come from the SP 3.35 run and the SP 3.38
    performance report's concatenated path; turnover is injected; a fold that
    did not execute records its failure reason instead of metrics.
    """
    path: OosEquityPath = performance_report.path
    if path.dataset_fingerprint != oos_run.dataset_fingerprint:
        raise OosDispersionError("dispersion inputs must share one dataset fingerprint.")
    if len(path.fold_ranges) != len(oos_run.results):
        raise OosDispersionError("dispersion inputs must cover the same folds.")
    folds: list[FoldDispersion] = []
    for result in oos_run.results:
        fold = result.validation.fold
        segment = path.fold_net_values(fold.fold_index)
        if segment:
            cumulative, max_dd = _fold_return_metrics(segment)
            turnover = turnover_for(fold) if turnover_for is not None else None
            failure_reason = None
        else:
            cumulative = None
            max_dd = None
            turnover = None
            failure_reason = result.failure_reason
        folds.append(
            FoldDispersion(
                fold_index=fold.fold_index,
                cumulative_return=cumulative,
                max_drawdown=max_dd,
                turnover=turnover,
                coverage_pct=result.validation.data_quality.overall_pct,
                failure_reason=failure_reason,
            )
        )
    report = OosDispersionReport(
        folds=tuple(folds),
        dataset_fingerprint=oos_run.dataset_fingerprint,
        code_version=oos_run.code_version,
        fingerprint="unfingerprinted",
    )
    return replace(report, fingerprint=oos_dispersion_fingerprint(report))


def oos_dispersion_json(report: OosDispersionReport) -> str:
    """Return a stable, key-sorted JSON serialization of a dispersion report.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style).
    """
    payload: dict[str, object] = {
        "dataset_fingerprint": report.dataset_fingerprint,
        "code_version": report.code_version,
        "folds": [
            {
                "fold_index": fold.fold_index,
                "cumulative_return": fold.cumulative_return,
                "max_drawdown": fold.max_drawdown,
                "turnover": fold.turnover,
                "coverage_pct": fold.coverage_pct,
                "failure_reason": fold.failure_reason,
            }
            for fold in report.folds
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def oos_dispersion_fingerprint(report: OosDispersionReport) -> str:
    """Return the stable SHA-256 fingerprint of a dispersion report (SP 3.39)."""
    return hashlib.sha256(oos_dispersion_json(report).encode("utf-8")).hexdigest()
