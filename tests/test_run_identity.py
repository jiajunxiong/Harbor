"""Idempotent run semantics tests (MVP 2 / SP 2.48).

Verifies that the same config hash, data cutoff and code version identify the
same research run, that an identical completed run is reused rather than
overwritten by default, and that non-completed identical runs are not silently
overwritten.
"""

import unittest
from datetime import date

from harbor.core.backtest_config import BacktestConfig, MarketQuota, RebalanceFrequency
from harbor.core.backtest_config_loader import config_hash
from harbor.core.backtest_domain import BacktestStatus, Currency, Market
from harbor.core.run_identity import (
    ExistingRun,
    RunAction,
    RunIdentity,
    RunResolution,
    identity_from_config,
    resolve_run,
    run_identity,
)

_CUTOFF = date(2024, 12, 31)


def _config() -> BacktestConfig:
    return BacktestConfig(
        markets=(Market.HK,),
        market_quotas=(MarketQuota(market=Market.HK, target_count=15, weight=1.0),),
        start_date=date(2020, 1, 1),
        end_date=date(2024, 12, 31),
        base_currency=Currency.HKD,
        rebalance_frequency=RebalanceFrequency.QUARTERLY,
    )


def _identity(
    *,
    config_hash: str = "a" * 64,
    data_cutoff: date = _CUTOFF,
    code_version: str = "1.0.0",
) -> RunIdentity:
    return run_identity(
        config_hash=config_hash,
        data_cutoff=data_cutoff,
        code_version=code_version,
    )


def _existing(
    *,
    run_id: str,
    status: BacktestStatus,
    identity: RunIdentity | None = None,
) -> ExistingRun:
    return ExistingRun(
        run_id=run_id,
        status=status,
        identity=identity if identity is not None else _identity(),
    )


class RunIdentityTests(unittest.TestCase):
    """Verify the identity and its fingerprint."""

    def test_identity_fields(self) -> None:
        identity = _identity(config_hash="h", data_cutoff=_CUTOFF, code_version="2.0")
        self.assertEqual(identity.config_hash, "h")
        self.assertEqual(identity.data_cutoff, _CUTOFF)
        self.assertEqual(identity.code_version, "2.0")

    def test_fingerprint_is_stable(self) -> None:
        identity = _identity(config_hash="h", data_cutoff=_CUTOFF, code_version="2.0")
        self.assertEqual(identity.fingerprint(), "h:2024-12-31:2.0")

    def test_equal_identities_match(self) -> None:
        self.assertEqual(_identity(), _identity())
        self.assertEqual(_identity().fingerprint(), _identity().fingerprint())

    def test_different_cutoff_changes_identity(self) -> None:
        self.assertNotEqual(
            _identity(data_cutoff=_CUTOFF).fingerprint(),
            _identity(data_cutoff=date(2025, 1, 31)).fingerprint(),
        )

    def test_different_code_version_changes_identity(self) -> None:
        self.assertNotEqual(
            _identity(code_version="1.0.0").fingerprint(),
            _identity(code_version="1.0.1").fingerprint(),
        )

    def test_empty_config_hash_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "config_hash"):
            _identity(config_hash="")

    def test_empty_code_version_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "code_version"):
            _identity(code_version="")


class IdentityFromConfigTests(unittest.TestCase):
    """Verify identity built from a configuration uses the SP 2.5 hash."""

    def test_identity_from_config_matches_config_hash(self) -> None:
        config = _config()
        identity = identity_from_config(config=config, data_cutoff=_CUTOFF, code_version="1.0.0")
        self.assertEqual(identity.config_hash, config_hash(config))

    def test_identical_configs_yield_identical_identities(self) -> None:
        first = identity_from_config(config=_config(), data_cutoff=_CUTOFF, code_version="1.0.0")
        second = identity_from_config(config=_config(), data_cutoff=_CUTOFF, code_version="1.0.0")
        self.assertEqual(first.fingerprint(), second.fingerprint())


class ResolveRunTests(unittest.TestCase):
    """Verify the idempotency decision."""

    def test_no_existing_run_is_new(self) -> None:
        resolution = resolve_run(requested=_identity(), existing=[])
        self.assertIsInstance(resolution, RunResolution)
        self.assertEqual(resolution.action, RunAction.NEW)
        self.assertIsNone(resolution.existing_run_id)

    def test_completed_identical_run_is_reused(self) -> None:
        existing = _existing(run_id="run-1", status=BacktestStatus.COMPLETED)
        resolution = resolve_run(requested=_identity(), existing=[existing])
        self.assertEqual(resolution.action, RunAction.REUSE)
        self.assertEqual(resolution.existing_run_id, "run-1")
        self.assertIn("reusing", resolution.reason)

    def test_identical_run_with_different_identity_is_new(self) -> None:
        other = _identity(config_hash="b" * 64)
        existing = _existing(run_id="run-1", status=BacktestStatus.COMPLETED, identity=other)
        resolution = resolve_run(requested=_identity(), existing=[existing])
        self.assertEqual(resolution.action, RunAction.NEW)

    def test_running_identical_run_is_conflict(self) -> None:
        existing = _existing(run_id="run-1", status=BacktestStatus.RUNNING)
        resolution = resolve_run(requested=_identity(), existing=[existing])
        self.assertEqual(resolution.action, RunAction.CONFLICT)
        self.assertEqual(resolution.existing_run_id, "run-1")

    def test_failed_identical_run_is_conflict_not_resumed(self) -> None:
        existing = _existing(run_id="run-1", status=BacktestStatus.FAILED)
        resolution = resolve_run(requested=_identity(), existing=[existing])
        self.assertEqual(resolution.action, RunAction.CONFLICT)

    def test_overwrite_allows_new_run(self) -> None:
        existing = _existing(run_id="run-1", status=BacktestStatus.COMPLETED)
        resolution = resolve_run(requested=_identity(), existing=[existing], overwrite=True)
        self.assertEqual(resolution.action, RunAction.NEW)
        self.assertIsNone(resolution.existing_run_id)

    def test_prefers_completed_run_when_multiple_match(self) -> None:
        existing = [
            _existing(run_id="run-1", status=BacktestStatus.FAILED),
            _existing(run_id="run-2", status=BacktestStatus.COMPLETED),
        ]
        resolution = resolve_run(requested=_identity(), existing=existing)
        self.assertEqual(resolution.action, RunAction.REUSE)
        self.assertEqual(resolution.existing_run_id, "run-2")

    def test_readable(self) -> None:
        existing = _existing(run_id="run-1", status=BacktestStatus.COMPLETED)
        resolution = resolve_run(requested=_identity(), existing=[existing])
        summary = resolution.readable()
        self.assertIn("reuse", summary)
        self.assertIn("run-1", summary)


if __name__ == "__main__":
    unittest.main()
