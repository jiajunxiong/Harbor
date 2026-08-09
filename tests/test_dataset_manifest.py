"""Dataset manifest model tests (MVP 3 / SP 3.6).

Verifies that the frozen dataset manifest records, for every data component
(prices, dividends, fundamentals, corporate actions, stock pool, FX,
calendar, benchmark, quality issues), its query boundary, source and version,
alongside the global query range, data cutoff, config hash, code version and
fingerprint. Also verifies the assembly helpers in
:mod:`harbor.core.dataset_manifest` that SP 3.7 fingerprints and SP 3.9
scores.
"""

import unittest
from dataclasses import FrozenInstanceError
from datetime import date

from harbor.core.backtest_domain import Currency, Market
from harbor.core.dataset_manifest import (
    build_dataset_manifest,
    component_manifest,
    find_component,
    missing_components,
)
from harbor.core.validation_domain import (
    DataComponentManifest,
    DatasetManifest,
    ManifestComponent,
    SplitBoundaryError,
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
        "components": (_component(),),
    }
    fields.update(overrides)
    return DatasetManifest(**fields)  # type: ignore[arg-type]


def _all_components() -> tuple[DataComponentManifest, ...]:
    """Return one bounded component record for each of the nine kinds."""
    return tuple(
        component_manifest(
            kind,
            "mock",
            "2024-12",
            start=date(2019, 1, 1),
            end=date(2024, 12, 31),
        )
        for kind in ManifestComponent
    )


class ManifestComponentTests(unittest.TestCase):
    """Verify the nine data-kind vocabulary (SP 3.6)."""

    def test_all_nine_kinds_recorded(self) -> None:
        self.assertEqual(
            [kind.value for kind in ManifestComponent],
            [
                "prices",
                "dividends",
                "fundamentals",
                "corporate_actions",
                "stock_pool",
                "fx",
                "calendar",
                "benchmark",
                "quality_issues",
            ],
        )
        self.assertEqual(len(tuple(ManifestComponent)), 9)


class DataComponentManifestTests(unittest.TestCase):
    """Verify one component's query boundary / source / version record."""

    def test_valid_record(self) -> None:
        record = _component()
        self.assertIs(record.component, ManifestComponent.PRICES)
        self.assertEqual(record.source, "mock")
        self.assertEqual(record.version, "2024-12")
        self.assertEqual(record.start, date(2019, 1, 1))
        self.assertEqual(record.end, date(2024, 12, 31))

    def test_rejects_empty_source(self) -> None:
        with self.assertRaisesRegex(ValueError, "source must be non-empty"):
            _component(source="")

    def test_rejects_empty_version(self) -> None:
        with self.assertRaisesRegex(ValueError, "version must be non-empty"):
            _component(version="")

    def test_rejects_reversed_range(self) -> None:
        with self.assertRaises(SplitBoundaryError):
            _component(start=date(2024, 12, 31), end=date(2019, 1, 1))

    def test_rejects_partial_boundaries(self) -> None:
        with self.assertRaisesRegex(ValueError, "both or neither"):
            _component(start=date(2019, 1, 1), end=None)

    def test_unbounded_record_is_allowed(self) -> None:
        record = _component(start=None, end=None)
        self.assertIsNone(record.start)
        self.assertIsNone(record.end)

    def test_is_frozen(self) -> None:
        record = _component()
        with self.assertRaises(FrozenInstanceError):
            record.version = "changed"  # type: ignore[misc]

    def test_readable_contains_identity(self) -> None:
        readable = _component().readable()
        self.assertIn("prices", readable)
        self.assertIn("mock", readable)
        self.assertIn("2024-12", readable)
        self.assertIn("2019-01-01..2024-12-31", readable)


class DatasetManifestComponentTests(unittest.TestCase):
    """Verify the manifest aggregates component records (SP 3.6)."""

    def test_manifest_records_components(self) -> None:
        fx = _component(kind=ManifestComponent.FX)
        manifest = _manifest(components=(fx,))
        self.assertEqual(manifest.components, (fx,))
        self.assertIn("components 1", manifest.readable())

    def test_manifest_without_components_is_allowed(self) -> None:
        manifest = _manifest(components=())
        self.assertEqual(manifest.components, ())

    def test_rejects_duplicate_component_kinds(self) -> None:
        duplicate = (_component(), _component(kind=ManifestComponent.PRICES))
        with self.assertRaisesRegex(ValueError, "duplicates component prices"):
            _manifest(components=duplicate)

    def test_rejects_component_before_manifest_range(self) -> None:
        early = _component(start=date(2018, 1, 1), end=date(2024, 12, 31))
        with self.assertRaises(SplitBoundaryError):
            _manifest(components=(early,))

    def test_rejects_component_after_manifest_range(self) -> None:
        late = _component(start=date(2019, 1, 1), end=date(2025, 12, 31))
        with self.assertRaises(SplitBoundaryError):
            _manifest(components=(late,))


class ComponentManifestFactoryTests(unittest.TestCase):
    """Verify the assembly helper for one component record (SP 3.6)."""

    def test_builds_component_record(self) -> None:
        record = component_manifest(
            ManifestComponent.FX,
            "mock",
            "2024-12",
            start=date(2019, 1, 1),
            end=date(2024, 12, 31),
        )
        self.assertIs(record.component, ManifestComponent.FX)
        self.assertEqual(record.source, "mock")
        self.assertEqual(record.version, "2024-12")

    def test_defaults_to_unbounded(self) -> None:
        record = component_manifest(ManifestComponent.BENCHMARK, "mock", "2024-12")
        self.assertIsNone(record.start)
        self.assertIsNone(record.end)

    def test_does_not_share_components_across_kinds(self) -> None:
        first = component_manifest(ManifestComponent.PRICES, "mock", "2024-12")
        second = component_manifest(ManifestComponent.DIVIDENDS, "mock", "2024-12")
        self.assertIsNot(first.component, second.component)


class BuildDatasetManifestTests(unittest.TestCase):
    """Verify full-manifest assembly and coverage helpers (SP 3.6)."""

    def _base_kwargs(self) -> dict[str, object]:
        return {
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
        }

    def test_builds_full_manifest(self) -> None:
        components = _all_components()
        manifest = build_dataset_manifest(
            **self._base_kwargs(),  # type: ignore[arg-type]
            components=components,
        )
        self.assertIsInstance(manifest, DatasetManifest)
        self.assertEqual(len(manifest.components), 9)
        self.assertIsNotNone(find_component(manifest, ManifestComponent.PRICES))

    def test_find_component_returns_none_when_absent(self) -> None:
        manifest = build_dataset_manifest(
            **self._base_kwargs(),  # type: ignore[arg-type]
            components=(_component(),),
        )
        self.assertIsNone(find_component(manifest, ManifestComponent.BENCHMARK))

    def test_missing_components_reports_gaps_in_order(self) -> None:
        manifest = build_dataset_manifest(
            **self._base_kwargs(),  # type: ignore[arg-type]
            components=(_component(),),
        )
        self.assertEqual(
            missing_components(manifest),
            (
                ManifestComponent.DIVIDENDS,
                ManifestComponent.FUNDAMENTALS,
                ManifestComponent.CORPORATE_ACTIONS,
                ManifestComponent.STOCK_POOL,
                ManifestComponent.FX,
                ManifestComponent.CALENDAR,
                ManifestComponent.BENCHMARK,
                ManifestComponent.QUALITY_ISSUES,
            ),
        )

    def test_missing_components_empty_when_complete(self) -> None:
        manifest = build_dataset_manifest(
            **self._base_kwargs(),  # type: ignore[arg-type]
            components=_all_components(),
        )
        self.assertEqual(missing_components(manifest), ())

    def test_require_rejects_missing(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing required components: fx"):
            build_dataset_manifest(
                **self._base_kwargs(),  # type: ignore[arg-type]
                components=(_component(),),
                require=(ManifestComponent.FX,),
            )

    def test_require_passes_when_complete(self) -> None:
        manifest = build_dataset_manifest(
            **self._base_kwargs(),  # type: ignore[arg-type]
            components=(_component(),),
            require=(ManifestComponent.PRICES,),
        )
        self.assertIsNotNone(find_component(manifest, ManifestComponent.PRICES))

    def test_build_rejects_duplicates(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicates component"):
            build_dataset_manifest(
                **self._base_kwargs(),  # type: ignore[arg-type]
                components=(_component(), _component()),
            )


if __name__ == "__main__":
    unittest.main()
