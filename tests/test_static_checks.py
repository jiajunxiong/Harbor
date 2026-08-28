"""Static checks test (MVP 2 / SP 2.85; MVP 3 / SP 3.84).

Locks in the static-check acceptance: new code passes ``ruff format --check``,
``ruff check`` and ``mypy`` (SP 2.85 / SP 3.84). Each test runs the actual tool
through the venv interpreter against the whole repository (``src`` + ``tests``
for ruff; ``src`` for mypy, matching the project's ``files = ["src"]`` scope) and
asserts a clean exit code, so any formatting or type-regression is caught in
the test suite. The tests skip when the tool is not installed.

SP 3.84 additionally locks in that every MVP 3 validation module (SP 3.1–3.74)
lives under ``src/harbor/core`` / ``src/harbor/services`` — i.e. inside the
repo-wide ruff / mypy ``src`` scope — so no MVP 3 module can silently escape the
static checks.
"""

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CORE_DIR = _REPO_ROOT / "src" / "harbor" / "core"

#: Every MVP 3 core module (SP 3.1–3.74) that must sit inside the ruff/mypy scope.
_MVP3_CORE_MODULES = (
    "validation_domain",
    "validation_config",
    "validation_config_loader",
    "validation_split",
    "validation_state_machine",
    "validation_apply",
    "frozen_data_reader",
    "holdout_registry",
    "dataset_manifest",
    "dataset_fingerprint",
    "coverage_scoring",
    "coverage_gate",
    "data_drift",
    "parameter_space",
    "parameter_constraints",
    "trial_budget",
    "trial_registry",
    "training_fit",
    "candidate_selection",
    "multiple_trial_penalty",
    "trial_reconciliation",
    "test_access_guard",
    "parameter_selection_explanation",
    "pre_registered_baseline",
    "rolling_window",
    "fold_calendar_align",
    "rolling_train",
    "rolling_validate",
    "rolling_oos",
    "oos_chain",
    "oos_concat",
    "oos_performance",
    "oos_dispersion",
    "oos_reconcile",
    "final_holdout",
    "reaccess_policy",
    "rolling_failure",
    "market_environment",
    "environment_attribution",
    "environment_segmented",
    "cost_stress",
    "liquidity_stress",
    "fx_stress",
    "calendar_stress",
    "corporate_action_stress",
    "stock_pool_stress",
    "parameter_neighborhood",
    "stability_rule",
    "stress_registry",
    "oos_conclusion",
    "conclusion_evidence",
    "oos_export",
    "oos_csv",
    "oos_report",
)


def _module_available(name: str) -> bool:
    """Return whether a Python module can be imported."""
    return importlib.util.find_spec(name) is not None


def _run_tool(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a tool through the venv interpreter from the repository root."""
    return subprocess.run(
        [sys.executable, "-m", *args],
        cwd=str(_REPO_ROOT),
        capture_output=True,
        text=True,
    )


@unittest.skipUnless(_module_available("ruff"), "ruff is not installed")
class RuffFormatCheckTests(unittest.TestCase):
    """``ruff format --check`` is clean across the repository (SP 2.85 / 3.84)."""

    def test_ruff_format_check_passes(self) -> None:
        result = _run_tool(["ruff", "format", "--check", "src", "tests"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipUnless(_module_available("ruff"), "ruff is not installed")
class RuffCheckTests(unittest.TestCase):
    """``ruff check`` is clean across the repository (SP 2.85 / 3.84)."""

    def test_ruff_check_passes(self) -> None:
        result = _run_tool(["ruff", "check", "src", "tests"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipUnless(_module_available("mypy"), "mypy is not installed")
class MypyCheckTests(unittest.TestCase):
    """``mypy src`` is clean (SP 2.85 / 3.84; scope matches ``files = ["src"]``)."""

    def test_mypy_src_passes(self) -> None:
        result = _run_tool(["mypy", "src"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class Mvp3StaticCheckScopeTests(unittest.TestCase):
    """Every MVP 3 module sits inside the ruff / mypy ``src`` scope (SP 3.84)."""

    def test_every_mvp3_core_module_is_in_the_static_check_scope(self) -> None:
        for module in _MVP3_CORE_MODULES:
            with self.subTest(module=module):
                self.assertTrue(
                    (_CORE_DIR / f"{module}.py").is_file(),
                    f"MVP 3 core module {module} must live under src/harbor/core "
                    "to be covered by ruff / mypy src.",
                )

    def test_mvp3_service_layer_is_in_the_static_check_scope(self) -> None:
        service = _REPO_ROOT / "src" / "harbor" / "services" / "validation.py"
        self.assertTrue(service.is_file())


if __name__ == "__main__":
    unittest.main()
