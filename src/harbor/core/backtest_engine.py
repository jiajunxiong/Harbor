"""Backtest execution orchestration (MVP 2 / SP 2.47).

Runs a backtest through the canonical ordered pipeline — 预检 (preflight) →
信号 (signal) → 调仓 (rebalance) → 成交 (fill) → 企业行动 (corporate action) →
估值 (valuation) → 持久化 (persist) — while driving the SP 2.46 run state
machine. Each stage is a pluggable handler (wired with the concrete domain
modules and Mock / real data in the end-to-end SP 2.51); a handler that raises
fails the run at that stage, retaining the diagnostics generated so far
(SP 2.46). Warnings returned by a stage are accumulated into the run
diagnostics.

The stages must be supplied in canonical order (no duplicates, no out-of-order
steps), so the pipeline order is fixed and replayable.

Pure core logic: depends only on the domain types and the state machine; never
touches storage or CLI code.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum

from harbor.core.backtest_domain import BacktestStatus
from harbor.core.backtest_state_machine import RunState, initial_state


class BacktestStage(StrEnum):
    """A stage of the backtest execution pipeline (SP 2.47)."""

    PREFLIGHT = "preflight"
    SIGNAL = "signal"
    REBALANCE = "rebalance"
    FILL = "fill"
    CORPORATE_ACTION = "corporate_action"
    VALUATION = "valuation"
    PERSIST = "persist"


BACKTEST_PIPELINE: tuple[BacktestStage, ...] = (
    BacktestStage.PREFLIGHT,
    BacktestStage.SIGNAL,
    BacktestStage.REBALANCE,
    BacktestStage.FILL,
    BacktestStage.CORPORATE_ACTION,
    BacktestStage.VALUATION,
    BacktestStage.PERSIST,
)


class OrchestrationError(ValueError):
    """Raised when the pipeline steps are not in canonical order (SP 2.47)."""


@dataclass(frozen=True)
class BacktestStep:
    """One stage of the pipeline and its handler (SP 2.47).

    ``run`` executes the stage and returns the warnings it produced (an empty
    sequence when there are none). Concrete handlers are wired in the
    end-to-end engine (SP 2.51).
    """

    stage: BacktestStage
    run: Callable[[], Sequence[str]]


@dataclass(frozen=True)
class BacktestRunResult:
    """The outcome of executing the pipeline (SP 2.47)."""

    run_id: str
    state: RunState
    completed_stages: tuple[BacktestStage, ...]

    @property
    def succeeded(self) -> bool:
        """Whether the run reached COMPLETED."""
        return self.state.status is BacktestStatus.COMPLETED

    @property
    def warnings(self) -> tuple[str, ...]:
        """Warnings accumulated across the executed stages (SP 2.46)."""
        return self.state.diagnostics.warnings

    def readable(self) -> str:
        """Render the run outcome as a human-readable summary."""
        stages = ", ".join(stage.value for stage in self.completed_stages) or "none"
        return (
            f"run {self.run_id}: {self.state.status.value}\n"
            f"  completed stages: {stages}\n"
            f"{self.state.diagnostics.readable()}"
        )


def _validate_pipeline(steps: Sequence[BacktestStep]) -> None:
    """Ensure the steps are a strictly-ordered subset of the canonical pipeline."""
    order = {stage: index for index, stage in enumerate(BACKTEST_PIPELINE)}
    last_index = -1
    for step in steps:
        index = order[step.stage]
        if index <= last_index:
            raise OrchestrationError(
                f"Pipeline stage {step.stage.value!r} is out of canonical order; "
                "stages must run in the SP 2.47 pipeline order."
            )
        last_index = index


def run_backtest(
    *,
    run_id: str,
    steps: Sequence[BacktestStep],
) -> BacktestRunResult:
    """Execute the backtest pipeline stages in canonical order (SP 2.47).

    The run is started (RUNNING) and each stage is executed in order. A stage
    that raises fails the run at that stage, retaining the diagnostics
    generated so far (SP 2.46); otherwise the run completes.

    Args:
        run_id: The run identifier.
        steps: The pipeline stages in canonical order (a duplicate or an
            out-of-order stage is rejected).

    Returns:
        A :class:`BacktestRunResult` with the final run state.

    Raises:
        OrchestrationError: If the steps are not in canonical order.
    """
    _validate_pipeline(steps)
    state = initial_state(run_id).start()
    completed: list[BacktestStage] = []
    for step in steps:
        try:
            warnings = tuple(step.run())
        except Exception as exc:  # noqa: BLE001 - a stage failure fails the run
            failed = state.fail(str(exc), stage=step.stage.value)
            return BacktestRunResult(
                run_id=run_id,
                state=failed,
                completed_stages=tuple(completed),
            )
        for warning in warnings:
            state = state.with_warning(warning)
        completed.append(step.stage)
    done = state.complete()
    return BacktestRunResult(
        run_id=run_id,
        state=done,
        completed_stages=tuple(completed),
    )
