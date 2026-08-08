"""Static checks test (MVP 2 / SP 2.85).

Locks in the static-check acceptance: new code passes ``ruff format --check``,
``ruff check`` and ``mypy`` (SP 2.85). Each test runs the actual tool through
the venv interpreter against the whole repository (``src`` + ``tests`` for
ruff; ``src`` for mypy, matching the project's ``files = ["src"]`` scope) and
asserts a clean exit code, so any formatting or type-regression is caught in
the test suite. The tests skip when the tool is not installed.
"""

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


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
    """``ruff format --check`` is clean across the repository (SP 2.85)."""

    def test_ruff_format_check_passes(self) -> None:
        result = _run_tool(["ruff", "format", "--check", "src", "tests"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipUnless(_module_available("ruff"), "ruff is not installed")
class RuffCheckTests(unittest.TestCase):
    """``ruff check`` is clean across the repository (SP 2.85)."""

    def test_ruff_check_passes(self) -> None:
        result = _run_tool(["ruff", "check", "src", "tests"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


@unittest.skipUnless(_module_available("mypy"), "mypy is not installed")
class MypyCheckTests(unittest.TestCase):
    """``mypy src`` is clean (SP 2.85; scope matches ``files = ["src"]``)."""

    def test_mypy_src_passes(self) -> None:
        result = _run_tool(["mypy", "src"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
