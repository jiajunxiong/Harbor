"""Dataset fingerprinting (MVP 3 / SP 3.7).

Computes a stable SHA-256 fingerprint over the frozen dataset manifest
(SP 3.6): its content — markets, base currency, query range, data cutoff, the
SP 3.3 config hash, code version, calendar version, FX source, random seed
and every per-component query/source/version record. Equal manifests always
produce the same fingerprint, so the same input is replayable to the same
fingerprint (SP 3.7). The stored ``DatasetManifest.fingerprint`` is a derived
value and is excluded from the digest so the fingerprint can be re-derived and
verified against what the manifest records (SP 3.11 data-drift check).

Core layer: depends only on the validation-domain types, never on storage,
services or CLI code.
"""

import hashlib
import json

from harbor.core.validation_domain import DatasetManifest


def manifest_json(manifest: DatasetManifest) -> str:
    """Return a stable, key-sorted JSON serialization of the manifest.

    Dates and enums serialize to scalars and component records appear in
    declaration order, so equal manifests always produce identical text. The
    derived ``fingerprint`` field is intentionally excluded so the digest can
    be re-derived and compared against the recorded value.
    """
    components = [
        {
            "component": entry.component.value,
            "source": entry.source,
            "version": entry.version,
            "start": entry.start.isoformat() if entry.start is not None else None,
            "end": entry.end.isoformat() if entry.end is not None else None,
        }
        for entry in manifest.components
    ]
    payload = {
        "markets": [market.value for market in manifest.markets],
        "base_currency": manifest.base_currency.value,
        "start_date": manifest.start_date.isoformat(),
        "end_date": manifest.end_date.isoformat(),
        "data_cutoff": manifest.data_cutoff.isoformat(),
        "config_hash": manifest.config_hash,
        "code_version": manifest.code_version,
        "calendar_version": manifest.calendar_version,
        "fx_source": manifest.fx_source,
        "random_seed": manifest.random_seed,
        "components": components,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def dataset_fingerprint(manifest: DatasetManifest) -> str:
    """Return the stable SHA-256 fingerprint of a frozen dataset (SP 3.7).

    The digest covers the manifest content, the data cutoff, the SP 3.3
    config hash and the code version, so identical inputs fingerprint
    identically and any change to a recorded boundary, source, version or
    cutoff changes the fingerprint.
    """
    return hashlib.sha256(manifest_json(manifest).encode("utf-8")).hexdigest()
