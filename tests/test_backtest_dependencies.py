"""Backtest dependency lock tests (MVP 2, SP 2.1).

Verifies that the research/backtest stack (numpy, pandas, packaging and, when the
``backtest`` extra is installed, numba and vectorbt) is inside the explicit
version ranges locked in ``harbor.dependencies``, and that the locked VectorBT
engine can run a minimal portfolio simulation with the installed stack.
"""

import unittest
from unittest.mock import patch

import numpy as np
from packaging.specifiers import SpecifierSet

import harbor.dependencies

try:
    import vectorbt as vbt
except ImportError:  # pragma: no cover - optional backtest extra absent
    vbt = None  # type: ignore[assignment]


class DependencyLockTests(unittest.TestCase):
    """Verify the installed stack satisfies the locked version ranges."""

    def test_locked_dependencies_have_explicit_ranges(self) -> None:
        self.assertGreaterEqual(len(harbor.dependencies.LOCKED_DEPENDENCIES), 4)
        for pin in harbor.dependencies.LOCKED_DEPENDENCIES:
            self.assertTrue(pin.range_spec, f"{pin.name} must pin an explicit range")

    def test_verify_lock_passes_for_installed_stack(self) -> None:
        messages = harbor.dependencies.verify_lock()

        required = {pin.name for pin in harbor.dependencies.LOCKED_DEPENDENCIES if not pin.optional}
        for name in required:
            self.assertTrue(
                any(message.startswith(f"{name} ") for message in messages),
                f"no verification message for required package {name}",
            )

    def test_verify_lock_raises_when_required_package_is_missing(self) -> None:
        real_version = harbor.dependencies.installed_version

        def missing_numpy(package: str) -> str:
            if package == "numpy":
                raise harbor.dependencies.DependencyLockError("numpy is not installed.")
            return real_version(package)

        with patch("harbor.dependencies.installed_version", side_effect=missing_numpy):
            with self.assertRaises(harbor.dependencies.DependencyLockError):
                harbor.dependencies.verify_lock()

    def test_verify_lock_raises_when_version_is_out_of_range(self) -> None:
        real_version = harbor.dependencies.installed_version

        def old_pandas(package: str) -> str:
            if package == "pandas":
                return "2.2.0"
            return real_version(package)

        with patch("harbor.dependencies.installed_version", side_effect=old_pandas):
            with self.assertRaises(harbor.dependencies.DependencyLockError):
                harbor.dependencies.verify_lock()

    def test_installed_versions_satisfy_locked_ranges(self) -> None:
        for pin in harbor.dependencies.LOCKED_DEPENDENCIES:
            try:
                installed = harbor.dependencies.installed_version(pin.name)
            except harbor.dependencies.DependencyLockError:
                if pin.optional:
                    continue
                self.fail(f"required package {pin.name} is not installed")
            self.assertTrue(
                SpecifierSet(pin.range_spec).contains(installed),
                f"{pin.name} {installed} not in {pin.range_spec!r}",
            )


@unittest.skipUnless(vbt is not None, "vectorbt not installed (pip install 'harbor[backtest]')")
class VectorBTSmokeTests(unittest.TestCase):
    """SP 2.1: the locked VectorBT engine runs with the installed stack."""

    def test_minimal_portfolio_from_signals(self) -> None:
        price = np.array([100.0, 101.0, 99.0, 102.0, 103.0, 104.0, 105.0, 106.0])
        entries = np.array([True, False, False, False, False, False, False, False])
        exits = np.array([False, False, False, False, False, True, False, False])

        portfolio = vbt.Portfolio.from_signals(
            price, entries, exits, init_cash=100.0, fees=0.001, freq="1D"
        )

        self.assertTrue(np.isfinite(portfolio.total_return()))
