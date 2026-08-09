"""Dataset manifest assembly (MVP 3 / SP 3.6).

Assembles the frozen :class:`~harbor.core.validation_domain.DatasetManifest`
that records, for every data component — prices, dividends, fundamentals,
corporate actions, the historical stock pool, FX, the trading calendar, the
benchmark and quality issues — its query boundary, source and version,
alongside the global query range, data cutoff, config hash, code version and
fingerprint. The component domain types (:class:`ManifestComponent`,
:class:`DataComponentManifest`) live in the validation-domain module (SP 3.1);
this module provides the ergonomic assembly and coverage helpers that SP 3.7
fingerprints and SP 3.9 scores.

Core layer: depends only on the backtest/validation-domain types, never on
storage, services or CLI code.
"""

from datetime import date

from harbor.core.backtest_domain import Currency, Market
from harbor.core.validation_domain import (
    DataComponentManifest,
    DatasetManifest,
    ManifestComponent,
)

ALL_COMPONENTS: tuple[ManifestComponent, ...] = tuple(ManifestComponent)


def component_manifest(
    kind: ManifestComponent,
    source: str,
    version: str,
    *,
    start: date | None = None,
    end: date | None = None,
) -> DataComponentManifest:
    """Build one data-component manifest record (SP 3.6).

    Args:
        kind: Which data kind the record describes.
        source: Where the component was fetched from.
        version: The component's version as of the frozen dataset.
        start: Query boundary start; both start and end must be set, or both
            omitted.
        end: Query boundary end; both start and end must be set, or both
            omitted.

    Returns:
        The immutable component manifest record.
    """
    return DataComponentManifest(
        component=kind,
        source=source,
        version=version,
        start=start,
        end=end,
    )


def find_component(
    manifest: DatasetManifest,
    kind: ManifestComponent,
) -> DataComponentManifest | None:
    """Return the manifest record for ``kind``, or None when absent (SP 3.6)."""
    for entry in manifest.components:
        if entry.component is kind:
            return entry
    return None


def missing_components(
    manifest: DatasetManifest,
    required: tuple[ManifestComponent, ...] = ALL_COMPONENTS,
) -> tuple[ManifestComponent, ...]:
    """Return the required kinds absent from the manifest, in order (SP 3.6)."""
    present = {entry.component for entry in manifest.components}
    return tuple(kind for kind in required if kind not in present)


def build_dataset_manifest(
    *,
    markets: tuple[Market, ...],
    base_currency: Currency,
    start_date: date,
    end_date: date,
    data_cutoff: date,
    config_hash: str,
    code_version: str,
    calendar_version: str,
    fx_source: str,
    fingerprint: str,
    random_seed: int | None = None,
    components: tuple[DataComponentManifest, ...] = (),
    require: tuple[ManifestComponent, ...] | None = None,
) -> DatasetManifest:
    """Assemble and validate a frozen dataset manifest (SP 3.6).

    Args:
        markets: Markets covered by the frozen dataset.
        base_currency: Benchmark currency of the validation run.
        start_date: Global query start; components must lie within it.
        end_date: Global query end; components must lie within it.
        data_cutoff: The point-in-time cutoff for the frozen dataset.
        config_hash: Frozen-split config hash (SP 3.3).
        code_version: Version of the research code that built the dataset.
        calendar_version: Version of the authoritative trading calendar.
        fx_source: Source of the FX rates.
        fingerprint: The derived dataset fingerprint (SP 3.7).
        random_seed: Deterministic random seed, when used.
        components: Per-component query/source/version records (SP 3.6).
        require: When given, every listed component must be present; a
            missing kind makes the build fail.

    Raises:
        ValueError: If required components are missing or a component is
            duplicated.
        SplitBoundaryError: If a component range lies outside the manifest
            range.
    """
    manifest = DatasetManifest(
        markets=markets,
        base_currency=base_currency,
        start_date=start_date,
        end_date=end_date,
        data_cutoff=data_cutoff,
        config_hash=config_hash,
        code_version=code_version,
        calendar_version=calendar_version,
        fx_source=fx_source,
        fingerprint=fingerprint,
        random_seed=random_seed,
        components=components,
    )
    if require is not None:
        missing = missing_components(manifest, require)
        if missing:
            kinds = ", ".join(kind.value for kind in missing)
            raise ValueError(f"Dataset manifest is missing required components: {kinds}.")
    return manifest
