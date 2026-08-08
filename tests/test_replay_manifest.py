"""Replayable manifest tests (MVP 2 / SP 2.61).

Verifies that the replay manifest records the config hash, code version, data
query boundaries, FX source, calendar version and random seed, and that the
fingerprint identifies replay-identical runs while excluding the run id.
"""

import unittest
from datetime import date, timedelta

from harbor.core.backtest_config import BacktestConfig, MarketQuota
from harbor.core.backtest_domain import BacktestStatus, Currency, Market
from harbor.core.backtest_runner import BacktestTrace
from harbor.core.backtest_state_machine import RunState
from harbor.core.replay_manifest import (
    DataQueryBoundaries,
    ReplayManifest,
    ReplayManifestError,
    build_replay_manifest,
    manifest_from_artifact,
)
from harbor.core.result_export import export_run_to_dict
from harbor.core.run_identity import RunIdentity

HKD = Currency.HKD
HK = Market.HK

_DAY = date(2024, 1, 2)


def _day(offset: int) -> date:
    return _DAY + timedelta(days=offset)


def _config() -> BacktestConfig:
    return BacktestConfig(
        markets=(Market.HK,),
        market_quotas=(MarketQuota(market=Market.HK, target_count=15, weight=1.0),),
        start_date=_DAY,
        end_date=_day(1),
        base_currency=HKD,
        initial_capital=100_000.0,
    )


def _identity() -> RunIdentity:
    return RunIdentity(config_hash="abc123", data_cutoff=_DAY, code_version="1.0.0")


def _state() -> RunState:
    return RunState(run_id="r1", status=BacktestStatus.COMPLETED)


def _trace() -> BacktestTrace:
    return BacktestTrace(
        run_id="r1",
        config=_config(),
        identity=_identity(),
        state=_state(),
        results=(),
    )


def _manifest(**extras: object) -> ReplayManifest:
    return build_replay_manifest(run_id="r1", config=_config(), identity=_identity(), **extras)


class ManifestBuildTests(unittest.TestCase):
    """Verify manifest construction and the fingerprint (SP 2.61)."""

    def test_fields_populated(self) -> None:
        manifest = _manifest(fx_source="ecb", calendar_version="2026.1", random_seed=42)
        self.assertEqual(manifest.run_id, "r1")
        self.assertEqual(manifest.config_hash, "abc123")
        self.assertEqual(manifest.code_version, "1.0.0")
        self.assertEqual(manifest.fx_source, "ecb")
        self.assertEqual(manifest.calendar_version, "2026.1")
        self.assertEqual(manifest.random_seed, 42)
        self.assertEqual(manifest.data_boundaries.start_date, _DAY)
        self.assertEqual(manifest.data_boundaries.end_date, _day(1))
        self.assertEqual(manifest.data_boundaries.data_cutoff, _DAY)

    def test_defaults_are_none(self) -> None:
        manifest = _manifest()
        self.assertIsNone(manifest.fx_source)
        self.assertIsNone(manifest.calendar_version)
        self.assertIsNone(manifest.random_seed)

    def test_fingerprint_is_stable(self) -> None:
        first = _manifest(fx_source="ecb", random_seed=7).fingerprint()
        second = _manifest(fx_source="ecb", random_seed=7).fingerprint()
        self.assertEqual(first, second)
        self.assertIn("abc123", first)
        self.assertIn("1.0.0", first)
        self.assertIn(_DAY.isoformat(), first)

    def test_fingerprint_changes_with_inputs(self) -> None:
        base = _manifest(fx_source="ecb", random_seed=7).fingerprint()
        self.assertNotEqual(base, _manifest(fx_source="fed", random_seed=7).fingerprint())
        self.assertNotEqual(base, _manifest(fx_source="ecb", random_seed=8).fingerprint())
        self.assertNotEqual(
            base,
            _manifest(fx_source="ecb", random_seed=7, calendar_version="2025.4").fingerprint(),
        )

    def test_fingerprint_excludes_run_id(self) -> None:
        other = build_replay_manifest(
            run_id="r-other", config=_config(), identity=_identity(), fx_source="ecb"
        )
        self.assertEqual(_manifest(fx_source="ecb").fingerprint(), other.fingerprint())

    def test_readable(self) -> None:
        text = _manifest(fx_source="ecb", random_seed=3).readable()
        self.assertIn("replay manifest r1", text)
        self.assertIn("config hash: abc123", text)
        self.assertIn("fx source: ecb", text)
        self.assertIn("random seed: 3", text)
        self.assertIn("fingerprint:", text)


class ManifestFromArtifactTests(unittest.TestCase):
    """Verify building the manifest from the SP 2.58 artifact."""

    def test_from_artifact_matches_build(self) -> None:
        artifact = export_run_to_dict(trace=_trace())
        from_artifact = manifest_from_artifact(
            artifact, fx_source="ecb", calendar_version="2026.1", random_seed=9
        )
        from_build = _manifest(fx_source="ecb", calendar_version="2026.1", random_seed=9)
        self.assertEqual(from_artifact, from_build)
        self.assertEqual(from_artifact.fingerprint(), from_build.fingerprint())

    def test_invalid_artifact_raises(self) -> None:
        with self.assertRaisesRegex(ReplayManifestError, "SP 2.58"):
            manifest_from_artifact({})

    def test_malformed_date_raises(self) -> None:
        artifact = export_run_to_dict(trace=_trace())
        artifact["config"]["start_date"] = "not-a-date"
        with self.assertRaisesRegex(ReplayManifestError, "ISO date"):
            manifest_from_artifact(artifact)

    def test_missing_config_dates_raises(self) -> None:
        artifact = export_run_to_dict(trace=_trace())
        del artifact["config"]["start_date"]
        with self.assertRaisesRegex(ReplayManifestError, "missing start_date/end_date"):
            manifest_from_artifact(artifact)


class ValidationTests(unittest.TestCase):
    """Verify refusal on invalid inputs (SP 2.61)."""

    def test_empty_run_id_rejected(self) -> None:
        with self.assertRaisesRegex(ReplayManifestError, "run_id"):
            build_replay_manifest(run_id="", config=_config(), identity=_identity())

    def test_empty_config_hash_rejected(self) -> None:
        boundaries = DataQueryBoundaries(start_date=_DAY, end_date=_day(1), data_cutoff=_DAY)
        with self.assertRaisesRegex(ReplayManifestError, "config_hash"):
            ReplayManifest(
                run_id="r1",
                config_hash="",
                code_version="1.0.0",
                data_boundaries=boundaries,
                fx_source=None,
                calendar_version=None,
                random_seed=None,
            )

    def test_empty_code_version_rejected(self) -> None:
        boundaries = DataQueryBoundaries(start_date=_DAY, end_date=_day(1), data_cutoff=_DAY)
        with self.assertRaisesRegex(ReplayManifestError, "code_version"):
            ReplayManifest(
                run_id="r1",
                config_hash="abc",
                code_version="",
                data_boundaries=boundaries,
                fx_source=None,
                calendar_version=None,
                random_seed=None,
            )

    def test_end_before_start_rejected(self) -> None:
        with self.assertRaisesRegex(ReplayManifestError, "on or after"):
            DataQueryBoundaries(start_date=_day(1), end_date=_DAY, data_cutoff=_DAY)

    def test_negative_seed_rejected(self) -> None:
        with self.assertRaisesRegex(ReplayManifestError, "non-negative"):
            _manifest(random_seed=-1)


if __name__ == "__main__":
    unittest.main()
