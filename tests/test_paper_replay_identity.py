"""Paper replay identity tests (MVP 4 / SP 4.9).

Verifies that every paper run records its config hash, dataset fingerprint,
code version, random seed and authoritative calendar/FX sources, and that two
runs with the same fingerprint are replay-identical while the run id (absent
from this identity) never affects it.
"""

import unittest

from harbor.core.backtest_domain import Currency, Market
from harbor.core.paper_config import PaperConfig
from harbor.core.paper_config_loader import config_hash
from harbor.core.paper_domain import PaperRunMode
from harbor.core.paper_replay_identity import (
    PaperReplayIdentity,
    PaperReplayIdentityError,
    build_paper_replay_identity,
)


def _config(**overrides: object) -> PaperConfig:
    """Return a valid paper config with overridable fields."""
    fields: dict[str, object] = {
        "strategy": "mvp3-qualified",
        "strategy_version": "1.0.0",
        "markets": (Market.HK, Market.US),
        "base_currency": Currency.HKD,
        "currencies": (Currency.HKD, Currency.USD),
        "initial_capital": 1_000_000.0,
        "run_mode": PaperRunMode.MANUAL,
        "calendar_version": "hkex-2026",
        "fx_source": "mock",
        "random_seed": 42,
    }
    fields.update(overrides)
    return PaperConfig(**fields)  # type: ignore[arg-type]


def _identity(**overrides: object) -> PaperReplayIdentity:
    """Return a valid replay identity with overridable fields."""
    fields: dict[str, object] = {
        "config_hash": "a" * 64,
        "dataset_fingerprint": "d" * 64,
        "code_version": "1.0.0",
        "random_seed": 42,
        "calendar_version": "hkex-2026",
        "fx_source": "mock",
    }
    fields.update(overrides)
    return PaperReplayIdentity(**fields)  # type: ignore[arg-type]


class PaperReplayIdentityTests(unittest.TestCase):
    """The replay identity record (SP 4.9)."""

    def test_valid_identity(self) -> None:
        identity = _identity()
        self.assertEqual(identity.config_hash, "a" * 64)
        self.assertEqual(identity.dataset_fingerprint, "d" * 64)
        self.assertEqual(identity.calendar_version, "hkex-2026")
        self.assertEqual(identity.fx_source, "mock")
        self.assertEqual(identity.random_seed, 42)
        self.assertIn("hkex-2026", identity.readable())

    def test_empty_fields_rejected(self) -> None:
        with self.assertRaises(PaperReplayIdentityError):
            _identity(config_hash="")
        with self.assertRaises(PaperReplayIdentityError):
            _identity(dataset_fingerprint="")
        with self.assertRaises(PaperReplayIdentityError):
            _identity(code_version="")

    def test_negative_seed_rejected(self) -> None:
        with self.assertRaises(PaperReplayIdentityError):
            _identity(random_seed=-1)

    def test_fingerprint_stable(self) -> None:
        self.assertEqual(_identity().fingerprint(), _identity().fingerprint())

    def test_fingerprint_changes_on_each_input(self) -> None:
        base = _identity().fingerprint()
        self.assertNotEqual(base, _identity(config_hash="b" * 64).fingerprint())
        self.assertNotEqual(base, _identity(dataset_fingerprint="e" * 64).fingerprint())
        self.assertNotEqual(base, _identity(code_version="2.0.0").fingerprint())
        self.assertNotEqual(base, _identity(random_seed=1).fingerprint())
        self.assertNotEqual(base, _identity(calendar_version=None).fingerprint())
        self.assertNotEqual(base, _identity(fx_source=None).fingerprint())

    def test_none_inputs_recorded_verbatim(self) -> None:
        identity = _identity(random_seed=None, calendar_version=None, fx_source=None)
        self.assertIn("|", identity.fingerprint())
        self.assertTrue(identity.fingerprint().endswith("||"))
        self.assertIn("seed unset", identity.readable())


class BuildPaperReplayIdentityTests(unittest.TestCase):
    """Building the identity from a validated config (SP 4.9)."""

    def test_build_from_config(self) -> None:
        config = _config()
        identity = build_paper_replay_identity(
            config=config,
            dataset_fingerprint="d" * 64,
            code_version="1.0.0",
        )
        self.assertEqual(identity.config_hash, config_hash(config))
        self.assertEqual(identity.random_seed, 42)
        self.assertEqual(identity.calendar_version, "hkex-2026")
        self.assertEqual(identity.fx_source, "mock")

    def test_build_equals_direct_construction(self) -> None:
        config = _config()
        built = build_paper_replay_identity(
            config=config, dataset_fingerprint="d" * 64, code_version="1.0.0"
        )
        direct = PaperReplayIdentity(
            config_hash=config_hash(config),
            dataset_fingerprint="d" * 64,
            code_version="1.0.0",
            random_seed=42,
            calendar_version="hkex-2026",
            fx_source="mock",
        )
        self.assertEqual(built, direct)
        self.assertEqual(built.fingerprint(), direct.fingerprint())

    def test_config_change_changes_fingerprint(self) -> None:
        first = build_paper_replay_identity(
            config=_config(), dataset_fingerprint="d" * 64, code_version="1.0.0"
        )
        second = build_paper_replay_identity(
            config=_config(random_seed=7), dataset_fingerprint="d" * 64, code_version="1.0.0"
        )
        self.assertNotEqual(first.fingerprint(), second.fingerprint())
