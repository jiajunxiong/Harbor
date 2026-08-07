"""Backtest execution orchestration tests (MVP 2 / SP 2.47).

Verifies that the pipeline runs the stages in canonical order
(preflight → signal → rebalance → fill → corporate action → valuation →
persist), rejects out-of-order steps, accumulates warnings, and fails at the
failing stage while retaining diagnostics (SP 2.46).
"""

import unittest

from harbor.core.backtest_domain import BacktestStatus
from harbor.core.backtest_engine import (
    BACKTEST_PIPELINE,
    BacktestRunResult,
    BacktestStage,
    BacktestStep,
    OrchestrationError,
    run_backtest,
)


def _step(
    stage: BacktestStage,
    warnings: tuple[str, ...] = (),
    error: str | None = None,
) -> BacktestStep:
    def run() -> tuple[str, ...]:
        if error is not None:
            raise ValueError(error)
        return warnings

    return BacktestStep(stage=stage, run=run)


def _all_steps(**overrides: tuple[str, ...]) -> list[BacktestStep]:
    warnings = {
        BacktestStage.PREFLIGHT: (),
        BacktestStage.SIGNAL: (),
        BacktestStage.REBALANCE: (),
        BacktestStage.FILL: (),
        BacktestStage.CORPORATE_ACTION: (),
        BacktestStage.VALUATION: (),
        BacktestStage.PERSIST: (),
    }
    warnings.update(overrides)
    return [_step(stage, warnings[stage]) for stage in BACKTEST_PIPELINE]


class PipelineOrderTests(unittest.TestCase):
    """Verify the canonical stage order and its enforcement."""

    def test_canonical_order(self) -> None:
        self.assertEqual(
            tuple(stage.value for stage in BACKTEST_PIPELINE),
            (
                "preflight",
                "signal",
                "rebalance",
                "fill",
                "corporate_action",
                "valuation",
                "persist",
            ),
        )

    def test_duplicate_stage_is_rejected(self) -> None:
        steps = _all_steps()
        steps.insert(2, _step(BacktestStage.PREFLIGHT))
        with self.assertRaisesRegex(OrchestrationError, "out of canonical order"):
            run_backtest(run_id="r1", steps=steps)

    def test_out_of_order_stage_is_rejected(self) -> None:
        steps = [_step(BacktestStage.FILL), _step(BacktestStage.REBALANCE)]
        with self.assertRaisesRegex(OrchestrationError, "out of canonical order"):
            run_backtest(run_id="r1", steps=steps)


class ExecutionTests(unittest.TestCase):
    """Verify the pipeline executes in order and completes."""

    def test_all_stages_complete(self) -> None:
        result = run_backtest(run_id="r1", steps=_all_steps())
        self.assertIsInstance(result, BacktestRunResult)
        self.assertTrue(result.succeeded)
        self.assertEqual(result.state.status, BacktestStatus.COMPLETED)
        self.assertEqual(result.completed_stages, BACKTEST_PIPELINE)

    def test_empty_pipeline_completes(self) -> None:
        result = run_backtest(run_id="r1", steps=[])
        self.assertTrue(result.succeeded)
        self.assertEqual(result.completed_stages, ())

    def test_warnings_accumulate(self) -> None:
        steps = _all_steps(
            **{
                BacktestStage.PREFLIGHT: ("stale price",),
                BacktestStage.FILL: ("partial fill",),
            }
        )
        result = run_backtest(run_id="r1", steps=steps)
        self.assertTrue(result.succeeded)
        self.assertEqual(result.warnings, ("stale price", "partial fill"))

    def test_failure_at_stage_retains_diagnostics(self) -> None:
        steps = _all_steps()
        steps[2] = _step(BacktestStage.REBALANCE, error="no candidates")
        result = run_backtest(run_id="r1", steps=steps)
        self.assertFalse(result.succeeded)
        self.assertEqual(result.state.status, BacktestStatus.FAILED)
        self.assertEqual(result.completed_stages, (BacktestStage.PREFLIGHT, BacktestStage.SIGNAL))
        self.assertEqual(result.state.diagnostics.error_summary, "no candidates")
        self.assertEqual(result.state.diagnostics.stage, "rebalance")

    def test_preflight_failure(self) -> None:
        steps = _all_steps()
        steps[0] = _step(BacktestStage.PREFLIGHT, error="insufficient data")
        result = run_backtest(run_id="r1", steps=steps)
        self.assertFalse(result.succeeded)
        self.assertEqual(result.state.status, BacktestStatus.FAILED)
        self.assertEqual(result.completed_stages, ())
        self.assertEqual(result.state.diagnostics.stage, "preflight")

    def test_failure_keeps_prior_warnings(self) -> None:
        steps = _all_steps(**{BacktestStage.SIGNAL: ("low coverage",)})
        steps[4] = _step(BacktestStage.CORPORATE_ACTION, error="bad action")
        result = run_backtest(run_id="r1", steps=steps)
        self.assertFalse(result.succeeded)
        self.assertIn("low coverage", result.state.diagnostics.warnings)
        self.assertEqual(result.state.diagnostics.error_summary, "bad action")

    def test_run_id_is_preserved(self) -> None:
        result = run_backtest(run_id="run-42", steps=_all_steps())
        self.assertEqual(result.run_id, "run-42")
        self.assertEqual(result.state.run_id, "run-42")


class ReadableTests(unittest.TestCase):
    """Verify the run outcome is renderable."""

    def test_readable_success(self) -> None:
        result = run_backtest(run_id="r1", steps=_all_steps())
        summary = result.readable()
        self.assertIn("run r1: COMPLETED", summary)
        self.assertIn("preflight", summary)

    def test_readable_failure(self) -> None:
        steps = _all_steps()
        steps[5] = _step(BacktestStage.VALUATION, error="missing fx")
        result = run_backtest(run_id="r1", steps=steps)
        summary = result.readable()
        self.assertIn("run r1: FAILED", summary)
        self.assertIn("error: missing fx", summary)


if __name__ == "__main__":
    unittest.main()
