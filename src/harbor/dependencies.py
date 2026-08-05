"""Locked version ranges for the research and backtest dependency stack (SP 2.1).

The ranges declared here must stay in sync with ``pyproject.toml``. They are the
single source of truth that ``tests/test_backtest_dependencies.py`` enforces
against the packages actually installed in the environment, so any future
dependency bump is an explicit, reviewed decision rather than a silent upgrade.
"""

from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError

from packaging.specifiers import SpecifierSet


class DependencyLockError(RuntimeError):
    """Raised when an installed package violates its locked version range."""


@dataclass(frozen=True)
class DependencyPin:
    """A package name, its locked version range and why that range was chosen."""

    name: str
    range_spec: str
    purpose: str
    optional: bool = False


#: The locked research/backtest stack, verified together on 2026-08-05
#: (Python 3.12.3, numpy 2.4.6, pandas 3.0.5, numba 0.66.0, vectorbt 1.1.0).
LOCKED_DEPENDENCIES: tuple[DependencyPin, ...] = (
    DependencyPin(
        name="numpy",
        range_spec=">=2.4.6,<3",
        purpose="numerical arrays; lower bound matches vectorbt 1.1.0",
    ),
    DependencyPin(
        name="pandas",
        range_spec=">=3.0.3,<4",
        purpose="factor and panel data; matches vectorbt 1.1.0 constraint",
    ),
    DependencyPin(
        name="packaging",
        range_spec=">=24,<27",
        purpose="PEP 440 version range checks used by the lock itself",
    ),
    DependencyPin(
        name="pyyaml",
        range_spec=">=6,<7",
        purpose="strategy configuration files loaded from YAML (SP 2.5)",
    ),
    DependencyPin(
        name="numba",
        range_spec=">=0.66,<0.67",
        purpose="transitive JIT backend of vectorbt; pins numpy below 2.5",
        optional=True,
    ),
    DependencyPin(
        name="vectorbt",
        range_spec=">=1.1.0,<2",
        purpose="vectorized research backtest engine; see docs/decisions/0001",
        optional=True,
    ),
)


def installed_version(package: str) -> str:
    """Return the installed distribution version of ``package``.

    Raises:
        DependencyLockError: If the distribution is not installed.
    """
    try:
        return importlib.metadata.version(package)
    except PackageNotFoundError as exc:
        raise DependencyLockError(f"{package} is not installed.") from exc


def verify_lock() -> tuple[str, ...]:
    """Verify installed packages against ``LOCKED_DEPENDENCIES``.

    Required packages must be installed and inside their locked range; optional
    packages (the ``backtest`` extra) are only checked when present. Returns one
    human-readable message per pinned package. Raises ``DependencyLockError`` on
    the first violation.
    """
    messages: list[str] = []
    for pin in LOCKED_DEPENDENCIES:
        try:
            installed = installed_version(pin.name)
        except DependencyLockError:
            if pin.optional:
                messages.append(
                    f"{pin.name}: not installed (optional; install with harbor[backtest])"
                )
                continue
            raise
        if not SpecifierSet(pin.range_spec).contains(installed):
            raise DependencyLockError(
                f"{pin.name} {installed} is outside the locked range "
                f"{pin.range_spec!r}: {pin.purpose}"
            )
        messages.append(f"{pin.name} {installed}: OK ({pin.range_spec})")
    return tuple(messages)
