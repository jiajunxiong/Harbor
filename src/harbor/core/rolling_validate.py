"""Rolling validation orchestration (MVP 3 / SP 3.34).

For every fold of an SP 3.33 :class:`~harbor.core.rolling_train.RollingTrainRun`,
computes the fold's validation-period results: strategy performance (策略),
benchmark (基准), risk (风险) and data-quality coverage (数据质量), WITHOUT
writing information back to the training period (不向训练期回写信息).

The anti-write-back guarantee is structural and enforced at two levels:

- the validation application (SP 3.20) is built from the fold's FROZEN
  training fit — SP 3.20's apply functions take the fitted state as input, so
  re-fitting on validation is impossible; the orchestration verifies the
  application's ``fit_fingerprint`` is exactly the fold's fit and that its
  decision date lies in the fold's validation interval;
- the data-dependent result computations are injected
  (``application_factory`` + ``compute_validation``) so the core layer stays
  pure, and the recorded :class:`FoldValidationResult` re-verifies the
  application/fold linkage.

Reuses the existing value types as the four results: MVP 2
:class:`~harbor.core.performance_metrics.PerformanceMetrics` (策略),
:class:`~harbor.core.benchmark.BenchmarkSeries` (基准),
:class:`~harbor.core.drawdown_events.DrawdownSeries` (风险) and SP 3.9
:class:`~harbor.core.coverage_scoring.MarketCoverage` (数据质量).

Pure core layer: depends only on the SP 3.20/3.33 modules and the MVP 2 /
SP 3.9 value types, never on storage, services or CLI.
"""

import hashlib
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from datetime import date

from harbor.core.benchmark import BenchmarkSeries
from harbor.core.coverage_scoring import MarketCoverage
from harbor.core.drawdown_events import DrawdownSeries
from harbor.core.performance_metrics import PerformanceMetrics
from harbor.core.rolling_train import FoldTrainingResult, RollingTrainRun
from harbor.core.training_fit import TrainingFitError
from harbor.core.validation_apply import (
    ValidationApplication,
    ValidationApplyError,
    require_application_in_validation,
)
from harbor.core.validation_domain import EvaluationSplit, WalkForwardFold


class RollingValidationError(ValueError):
    """Raised when rolling validation cannot be orchestrated (SP 3.34)."""


@dataclass(frozen=True)
class ValidationComponents:
    """The four validation-period result kinds for one fold (SP 3.34).

    ``strategy`` (策略) is the strategy's performance metrics, ``benchmark``
    (基准) the benchmark series, ``risk`` (风险) the drawdown events and
    ``data_quality`` (数据质量) the data-coverage scores over the fold's
    validation period. This is what the injected ``compute_validation``
    callback returns.
    """

    strategy: PerformanceMetrics
    benchmark: BenchmarkSeries
    risk: DrawdownSeries
    data_quality: MarketCoverage


@dataclass(frozen=True)
class FoldValidationResult:
    """One fold's validation-period outcome (SP 3.34).

    ``training`` links back to the fold's SP 3.33 training result (fit,
    trials, selection); ``application`` is the SP 3.20 validation application
    of the fold's frozen fit at the fold's validation decision date; the four
    result kinds are ``strategy`` / ``benchmark`` / ``risk`` /
    ``data_quality``.
    """

    training: FoldTrainingResult
    application: ValidationApplication
    strategy: PerformanceMetrics
    benchmark: BenchmarkSeries
    risk: DrawdownSeries
    data_quality: MarketCoverage

    def __post_init__(self) -> None:
        fold_index = self.training.fold.fold_index
        if self.application.fit_fingerprint != self.training.fit.fingerprint:
            raise RollingValidationError(
                f"fold {fold_index} validation application does not use the fold's "
                "frozen training fit (fit fingerprint mismatch)."
            )
        if self.application.dataset_fingerprint != self.training.fit.dataset_fingerprint:
            raise RollingValidationError(
                f"fold {fold_index} validation application dataset fingerprint does "
                "not match the fold's frozen training fit."
            )
        validation_start = self.training.fold.validation_start
        validation_end = self.training.fold.validation_end
        if not (validation_start <= self.application.decision_date <= validation_end):
            raise RollingValidationError(
                f"fold {fold_index} validation application decision date "
                f"{self.application.decision_date.isoformat()} lies outside the "
                "fold's validation period "
                f"{validation_start.isoformat()}..{validation_end.isoformat()}."
            )

    @property
    def fold(self) -> WalkForwardFold:
        """The fold this validation result belongs to."""
        return self.training.fold

    def readable(self) -> str:
        """Render the fold's validation outcome as one line."""
        return (
            f"fold {self.training.fold.fold_index} applied "
            f"{self.application.decision_date.isoformat()} strategy "
            f"{self.strategy.cumulative_return:.4%} benchmark "
            f"{self.benchmark.total_return():.4%} drawdowns {len(self.risk.events)} "
            f"coverage {self.data_quality.overall_pct:.1f}%"
        )


@dataclass(frozen=True)
class RollingValidationRun:
    """The auditable rolling-validation orchestration result (SP 3.34).

    One :class:`FoldValidationResult` per fold, ordered by ``fold_index`` from
    0. ``dataset_fingerprint`` / ``code_version`` are inherited from the
    SP 3.33 training run and ``fingerprint`` is the derived SHA-256 digest.
    """

    results: tuple[FoldValidationResult, ...]
    dataset_fingerprint: str
    code_version: str
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.results:
            raise RollingValidationError("a rolling validation run requires at least one fold.")
        if not self.dataset_fingerprint:
            raise RollingValidationError("dataset fingerprint must be non-empty.")
        if not self.code_version:
            raise RollingValidationError("code version must be non-empty.")
        for index, result in enumerate(self.results):
            if result.training.fold.fold_index != index:
                raise RollingValidationError(
                    f"result {index} must carry fold_index {index}, "
                    f"got {result.training.fold.fold_index}."
                )
            if result.application.dataset_fingerprint != self.dataset_fingerprint:
                raise RollingValidationError(
                    f"fold {index} application dataset fingerprint does not match the run."
                )
        if not self.fingerprint:
            raise RollingValidationError("rolling validation run fingerprint must be non-empty.")

    def __len__(self) -> int:
        return len(self.results)

    def __iter__(self) -> Iterator[FoldValidationResult]:
        return iter(self.results)

    def __getitem__(self, index: int) -> FoldValidationResult:
        return self.results[index]

    def validation_for(self, fold_index: int) -> FoldValidationResult | None:
        """Return the validation result for ``fold_index``, or ``None``."""
        for result in self.results:
            if result.training.fold.fold_index == fold_index:
                return result
        return None

    def readable(self) -> str:
        """Render the rolling validation run as one line."""
        return (
            f"{len(self.results)} folds validated on "
            f"dataset {self.dataset_fingerprint[:12]} code {self.code_version} "
            f"fp {self.fingerprint}"
        )


def run_rolling_validation(
    training_run: RollingTrainRun,
    *,
    application_factory: Callable[[FoldTrainingResult, date], ValidationApplication],
    compute_validation: Callable[[FoldTrainingResult, ValidationApplication], ValidationComponents],
) -> RollingValidationRun:
    """Orchestrate per-fold validation on a rolling training run (SP 3.34).

    For every fold the SP 3.20 application of its frozen training fit is built
    at the fold's validation end (verified to lie in the validation interval,
    so the training period is never read back into), and the four validation
    results are computed through the injected callbacks and recorded.
    """
    results: list[FoldValidationResult] = []
    for index, training in enumerate(training_run.results):
        fold = training.fold
        split = EvaluationSplit(
            train_start=fold.train_start,
            train_end=fold.train_end,
            validation_start=fold.validation_start,
            validation_end=fold.validation_end,
            test_start=fold.test_start,
            test_end=fold.test_end,
        )
        decision_date = fold.validation_end
        try:
            require_application_in_validation(decision_date, training.fit, split)
        except (ValidationApplyError, TrainingFitError) as exc:
            raise RollingValidationError(f"fold {index}: {exc}") from exc
        application = application_factory(training, decision_date)
        components = compute_validation(training, application)
        results.append(
            FoldValidationResult(
                training=training,
                application=application,
                strategy=components.strategy,
                benchmark=components.benchmark,
                risk=components.risk,
                data_quality=components.data_quality,
            )
        )

    run = RollingValidationRun(
        results=tuple(results),
        dataset_fingerprint=training_run.dataset_fingerprint,
        code_version=training_run.code_version,
        fingerprint="unfingerprinted",
    )
    return replace(run, fingerprint=rolling_validation_fingerprint(run))


def rolling_validation_json(run: RollingValidationRun) -> str:
    """Return a stable, key-sorted JSON serialization of a validation run.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style).
    """
    payload: dict[str, object] = {
        "dataset_fingerprint": run.dataset_fingerprint,
        "code_version": run.code_version,
        "results": [
            {
                "fold_index": result.training.fold.fold_index,
                "application_fingerprint": result.application.fingerprint,
                "strategy": {
                    "start_date": result.strategy.start_date.isoformat(),
                    "end_date": result.strategy.end_date.isoformat(),
                    "periods": result.strategy.periods,
                    "cumulative_return": result.strategy.cumulative_return,
                    "annualized_return": result.strategy.annualized_return,
                    "annualized_volatility": result.strategy.annualized_volatility,
                    "max_drawdown": result.strategy.max_drawdown,
                    "sharpe_ratio": result.strategy.sharpe_ratio,
                    "calmar_ratio": result.strategy.calmar_ratio,
                    "downside_deviation": result.strategy.downside_deviation,
                },
                "benchmark": {
                    "kind": result.benchmark.kind.value,
                    "start_date": result.benchmark.start_date.isoformat(),
                    "end_date": result.benchmark.end_date.isoformat(),
                    "total_return": result.benchmark.total_return(),
                },
                "risk": {
                    "event_count": len(result.risk.events),
                    "max_depth": max(
                        (event.depth for event in result.risk.events),
                        default=None,
                    ),
                },
                "data_quality": {
                    "overall_pct": result.data_quality.overall_pct,
                    "gaps": [score.item.value for score in result.data_quality.gaps()],
                },
            }
            for result in run.results
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def rolling_validation_fingerprint(run: RollingValidationRun) -> str:
    """Return the stable SHA-256 fingerprint of a validation run (SP 3.34)."""
    return hashlib.sha256(rolling_validation_json(run).encode("utf-8")).hexdigest()
