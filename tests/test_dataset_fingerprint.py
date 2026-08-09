"""Dataset fingerprint tests (MVP 3 / SP 3.7).

Verifies that a stable SHA-256 fingerprint is derived from the frozen dataset
manifest content, the data cutoff, the SP 3.3 config hash and the code
version, so identical inputs always fingerprint identically and any change to
a recorded boundary, source, version or cutoff changes the fingerprint.
"""

import hashlib
import json
import unittest
from datetime import date

from harbor.core.backtest_domain import Currency, Market
from harbor.core.dataset_fingerprint import dataset_fingerprint, manifest_json
from harbor.core.validation_config import SplitConfig, ValidationConfig
from harbor.core.validation_config_loader import config_hash
from harbor.core.validation_domain import (
    DataComponentManifest,
    DatasetManifest,
    ManifestComponent,
)


def _component(
    kind: ManifestComponent = ManifestComponent.PRICES,
    **overrides: object,
) -> DataComponentManifest:
    """Return a valid component record with overridable fields."""
    fields: dict[str, object] = {
        "component": kind,
        "source": "mock",
        "version": "2024-12",
        "start": date(2019, 1, 1),
        "end": date(2024, 12, 31),
    }
    fields.update(overrides)
    return DataComponentManifest(**fields)  # type: ignore[arg-type]


def _manifest(**overrides: object) -> DatasetManifest:
    """Return a valid dataset manifest with overridable fields."""
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
        "random_seed": 42,
        "components": (_component(),),
    }
    fields.update(overrides)
    return DatasetManifest(**fields)  # type: ignore[arg-type]


def _frozen_config(version: str = "1.0.0") -> ValidationConfig:
    """Return a validated config with a distinct strategy version."""
    return ValidationConfig(
        strategy="shareholder-return",
        strategy_version=version,
        markets=(Market.HK, Market.US),
        base_currency=Currency.HKD,
        split=SplitConfig(
            train_start=date(2019, 1, 1),
            train_end=date(2021, 12, 31),
            validation_start=date(2022, 1, 3),
            validation_end=date(2022, 12, 30),
            test_start=date(2023, 1, 2),
            test_end=date(2024, 12, 31),
        ),
    )


class ManifestJsonTests(unittest.TestCase):
    """Verify the canonical serialization the fingerprint is built on."""

    def test_json_is_stable_across_equal_manifests(self) -> None:
        self.assertEqual(manifest_json(_manifest()), manifest_json(_manifest()))

    def test_json_is_key_sorted(self) -> None:
        payload = manifest_json(_manifest())
        self.assertLess(payload.index('"base_currency"'), payload.index('"calendar_version"'))
        self.assertLess(payload.index('"config_hash"'), payload.index('"data_cutoff"'))

    def test_json_serializes_dates_and_enums_as_scalars(self) -> None:
        payload = manifest_json(_manifest())
        self.assertIn('"start_date":"2019-01-01"', payload)
        self.assertIn('"markets":["HK","US"]', payload)
        self.assertIn('"random_seed":42', payload)

    def test_json_includes_component_records(self) -> None:
        payload = manifest_json(_manifest())
        self.assertIn('"component":"prices"', payload)
        self.assertIn('"source":"mock"', payload)

    def test_json_excludes_the_derived_fingerprint(self) -> None:
        parsed = json.loads(manifest_json(_manifest()))
        self.assertNotIn("fingerprint", parsed)
        self.assertEqual(parsed["config_hash"], "abc123")


class DatasetFingerprintTests(unittest.TestCase):
    """Verify the stable SHA-256 fingerprint (SP 3.7)."""

    def test_is_sha256_hex_digest(self) -> None:
        fingerprint = dataset_fingerprint(_manifest())
        self.assertEqual(len(fingerprint), 64)
        int(fingerprint, 16)  # must be valid hex

    def test_stable_across_equal_manifests(self) -> None:
        self.assertEqual(
            dataset_fingerprint(_manifest()),
            dataset_fingerprint(_manifest()),
        )

    def test_equals_sha256_of_manifest_json(self) -> None:
        expected = hashlib.sha256(manifest_json(_manifest()).encode("utf-8")).hexdigest()
        self.assertEqual(dataset_fingerprint(_manifest()), expected)

    def test_changes_when_data_cutoff_changes(self) -> None:
        changed = _manifest(data_cutoff=date(2024, 6, 30))
        self.assertNotEqual(dataset_fingerprint(_manifest()), dataset_fingerprint(changed))

    def test_changes_when_config_hash_changes(self) -> None:
        changed = _manifest(config_hash="changed")
        self.assertNotEqual(dataset_fingerprint(_manifest()), dataset_fingerprint(changed))

    def test_changes_when_code_version_changes(self) -> None:
        changed = _manifest(code_version="2.0.0")
        self.assertNotEqual(dataset_fingerprint(_manifest()), dataset_fingerprint(changed))

    def test_changes_when_markets_change(self) -> None:
        changed = _manifest(markets=(Market.HK,))
        self.assertNotEqual(dataset_fingerprint(_manifest()), dataset_fingerprint(changed))

    def test_changes_when_component_source_changes(self) -> None:
        changed = _manifest(components=(_component(source="yfinance"),))
        self.assertNotEqual(dataset_fingerprint(_manifest()), dataset_fingerprint(changed))

    def test_changes_when_component_version_changes(self) -> None:
        changed = _manifest(components=(_component(version="2025-06"),))
        self.assertNotEqual(dataset_fingerprint(_manifest()), dataset_fingerprint(changed))

    def test_changes_when_component_boundary_changes(self) -> None:
        changed = _manifest(
            components=(_component(start=date(2020, 1, 1), end=date(2024, 12, 31)),)
        )
        self.assertNotEqual(dataset_fingerprint(_manifest()), dataset_fingerprint(changed))

    def test_changes_when_random_seed_changes(self) -> None:
        changed = _manifest(random_seed=7)
        self.assertNotEqual(dataset_fingerprint(_manifest()), dataset_fingerprint(changed))

    def test_matches_manifest_stored_fingerprint_when_consistent(self) -> None:
        # The derived field is excluded from the digest, so a manifest built
        # with its own computed fingerprint is self-consistent.
        placeholder = _manifest(fingerprint="placeholder")
        computed = dataset_fingerprint(placeholder)
        consistent = _manifest(fingerprint=computed)
        self.assertEqual(dataset_fingerprint(consistent), computed)
        self.assertEqual(consistent.fingerprint, computed)


class ConfigHashTieTests(unittest.TestCase):
    """Verify the fingerprint covers the SP 3.3 config hash (SP 3.7)."""

    def test_fingerprint_changes_with_the_config_hash(self) -> None:
        base_config = _frozen_config()
        other_config = _frozen_config(version="2.0.0")
        base = _manifest(config_hash=config_hash(base_config))
        other = _manifest(config_hash=config_hash(other_config))
        self.assertNotEqual(dataset_fingerprint(base), dataset_fingerprint(other))

    def test_fingerprint_is_replayable_from_frozen_config(self) -> None:
        config = _frozen_config()
        first = _manifest(config_hash=config_hash(config))
        second = _manifest(config_hash=config_hash(_frozen_config()))
        self.assertEqual(dataset_fingerprint(first), dataset_fingerprint(second))


if __name__ == "__main__":
    unittest.main()
