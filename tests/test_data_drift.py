"""Data version drift check tests (MVP 3 / SP 3.11).

Verifies that the dataset fingerprint (SP 3.7) is re-derived and compared
against the recorded one before an experiment is executed or a report is
generated, and that a mismatch — meaning the data, calendar or quality records
changed — refuses to reuse the old conclusion.
"""

import unittest
from datetime import date

from harbor.core.backtest_domain import Currency, Market
from harbor.core.backtest_interfaces import BacktestDataReader
from harbor.core.data_drift import (
    DataDriftError,
    DriftCheckResult,
    check_fingerprint,
    require_fingerprint_matches,
    verify_reader_fingerprint,
)
from harbor.core.dataset_fingerprint import dataset_fingerprint
from harbor.core.frozen_data_reader import FrozenDataReader
from harbor.core.validation_domain import (
    DataComponentManifest,
    DatasetManifest,
    ManifestComponent,
)


class _FakeReader(BacktestDataReader):
    """Minimal reader; only the manifest is used by the drift check."""

    def list_securities(self, market: Market, as_of: date):
        return ()

    def daily_quotes(self, market: Market, symbol: str, start: date, end: date):
        return ()

    def dividends(self, market: Market, symbol: str, start: date, end: date):
        return ()

    def fundamentals(self, market: Market, symbol: str, as_of: date):
        return ()

    def corporate_actions(self, market: Market, symbol: str, start: date, end: date):
        return ()

    def adjustment_factors(self, market: Market, symbol: str, start: date, end: date):
        return ()


def _component(kind: ManifestComponent) -> DataComponentManifest:
    return DataComponentManifest(
        component=kind,
        source="mock",
        version="2024-12",
        start=date(2019, 1, 1),
        end=date(2024, 12, 31),
    )


def _manifest(**overrides: object) -> DatasetManifest:
    """Return a valid frozen manifest with the main components recorded."""
    fields: dict[str, object] = {
        "markets": (Market.HK, Market.US),
        "base_currency": Currency.HKD,
        "start_date": date(2019, 1, 1),
        "end_date": date(2024, 12, 31),
        "data_cutoff": date(2024, 12, 31),
        "config_hash": "abc123",
        "code_version": "1.0.0",
        "calendar_version": "hkex-2024",
        "fx_source": "mock",
        "fingerprint": "fp-1",
        "components": (
            _component(ManifestComponent.PRICES),
            _component(ManifestComponent.CALENDAR),
            _component(ManifestComponent.QUALITY_ISSUES),
        ),
    }
    fields.update(overrides)
    return DatasetManifest(**fields)  # type: ignore[arg-type]


class DriftCheckResultTests(unittest.TestCase):
    """Verify the drift check result value."""

    def test_matching_fingerprints_are_not_drifted(self) -> None:
        fingerprint = dataset_fingerprint(_manifest())
        result = DriftCheckResult(fingerprint, fingerprint)
        self.assertFalse(result.drifted)
        self.assertIn("matches", result.readable())

    def test_differing_fingerprints_are_drifted(self) -> None:
        result = DriftCheckResult(recorded_fingerprint="a" * 64, current_fingerprint="b" * 64)
        self.assertTrue(result.drifted)
        self.assertIn("data drift", result.readable())
        self.assertIn("a" * 64, result.readable())


class CheckFingerprintTests(unittest.TestCase):
    """Verify the non-raising fingerprint re-verification (SP 3.11)."""

    def test_matches_when_recorded_equals_current(self) -> None:
        manifest = _manifest()
        result = check_fingerprint(manifest, dataset_fingerprint(manifest))
        self.assertFalse(result.drifted)
        self.assertEqual(result.current_fingerprint, dataset_fingerprint(manifest))

    def test_drifted_when_recorded_differs(self) -> None:
        manifest = _manifest()
        result = check_fingerprint(manifest, "deadbeef" * 8)
        self.assertTrue(result.drifted)

    def test_current_fingerprint_is_deterministic(self) -> None:
        self.assertEqual(
            check_fingerprint(_manifest(), "x").current_fingerprint,
            check_fingerprint(_manifest(), "x").current_fingerprint,
        )

    def test_changed_calendar_version_changes_fingerprint(self) -> None:
        manifest = _manifest()
        changed = _manifest(calendar_version="hkex-2025")
        self.assertNotEqual(
            check_fingerprint(manifest, dataset_fingerprint(manifest)).current_fingerprint,
            check_fingerprint(changed, dataset_fingerprint(manifest)).current_fingerprint,
        )


class RequireFingerprintMatchesTests(unittest.TestCase):
    """Verify the raising guard used at execution/report time (SP 3.11)."""

    def test_passes_when_fingerprints_match(self) -> None:
        manifest = _manifest()
        require_fingerprint_matches(manifest, dataset_fingerprint(manifest))

    def test_raises_on_drift(self) -> None:
        manifest = _manifest()
        with self.assertRaises(DataDriftError):
            require_fingerprint_matches(manifest, "deadbeef" * 8)

    def test_error_mentions_the_context(self) -> None:
        manifest = _manifest()
        with self.assertRaisesRegex(DataDriftError, "validation report"):
            require_fingerprint_matches(
                manifest,
                "deadbeef" * 8,
                context="validation report",
            )

    def test_error_mentions_drift_and_fingerprints(self) -> None:
        manifest = _manifest()
        with self.assertRaisesRegex(DataDriftError, "data has drifted"):
            require_fingerprint_matches(manifest, "deadbeef" * 8)

    def test_is_a_value_error_subclass(self) -> None:
        self.assertTrue(issubclass(DataDriftError, ValueError))


class VerifyReaderFingerprintTests(unittest.TestCase):
    """Verify the reader-backed re-verification (SP 3.8 tie)."""

    def test_matches_the_reader_manifest(self) -> None:
        manifest = _manifest()
        reader = FrozenDataReader(_FakeReader(), manifest)
        result = verify_reader_fingerprint(reader, dataset_fingerprint(manifest))
        self.assertFalse(result.drifted)

    def test_drifted_against_reader_manifest(self) -> None:
        manifest = _manifest()
        reader = FrozenDataReader(_FakeReader(), manifest)
        result = verify_reader_fingerprint(reader, "deadbeef" * 8)
        self.assertTrue(result.drifted)

    def test_uses_the_reader_bound_manifest(self) -> None:
        manifest = _manifest(calendar_version="hkex-2025")
        reader = FrozenDataReader(_FakeReader(), manifest)
        result = verify_reader_fingerprint(reader, "deadbeef" * 8)
        self.assertEqual(result.current_fingerprint, dataset_fingerprint(manifest))


if __name__ == "__main__":
    unittest.main()
