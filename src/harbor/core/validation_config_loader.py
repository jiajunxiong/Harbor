"""Versioned validation-config loading and hashing (MVP 3 / SP 3.3).

Loads a validated :class:`~harbor.core.validation_config.ValidationConfig`
from a YAML or JSON file and produces a stable hash of the validated content.
The hash is derived from the canonical key-sorted serialization (SP 3.2), so
equal configurations always hash equal regardless of field or key order; the
split boundaries, markets, base currency and strategy version are all part of
that serialization and therefore of the hash. SP 3.7 fingerprints the frozen
dataset and SP 3.86 records the hash so a validation run can be reproduced.
"""

import hashlib
import json
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from harbor.core.validation_config import ValidationConfig

_YAML_SUFFIXES = {".yaml", ".yml"}


class ValidationConfigFormat(StrEnum):
    """The file format a validation configuration is stored in."""

    YAML = "yaml"
    JSON = "json"


def _detect_format(path: Path, fmt: ValidationConfigFormat | None) -> ValidationConfigFormat:
    """Return the explicit format or infer it from the file suffix."""
    if fmt is not None:
        return fmt
    suffix = path.suffix.lower()
    if suffix in _YAML_SUFFIXES:
        return ValidationConfigFormat.YAML
    if suffix == ".json":
        return ValidationConfigFormat.JSON
    raise ValueError(
        f"Cannot infer validation config format from {path.name!r}; "
        "use a .yaml/.yml/.json file or pass fmt explicitly."
    )


def load_validation_config(
    path: str | Path,
    fmt: ValidationConfigFormat | None = None,
) -> ValidationConfig:
    """Load, validate, and return a validation config from a YAML or JSON file.

    Args:
        path: Path to the configuration file.
        fmt: Explicit format; when omitted it is inferred from the suffix.

    Returns:
        The validated, immutable :class:`ValidationConfig`.

    Raises:
        ValueError: If the file is empty, the root is not a mapping, the
            content cannot be parsed, or the format cannot be inferred.
        pydantic.ValidationError: If the parsed content violates the config
            model constraints (SP 3.2 / 3.4).
    """
    config_path = Path(path)
    format_ = _detect_format(config_path, fmt)
    content = config_path.read_text(encoding="utf-8")
    if format_ is ValidationConfigFormat.YAML:
        try:
            data = yaml.safe_load(content)
        except yaml.YAMLError as exc:
            raise ValueError(f"Invalid YAML in {config_path}: {exc}") from exc
    else:
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {config_path}: {exc}") from exc
    if not isinstance(data, Mapping):
        raise ValueError("Validation config must be a mapping at the root.")
    return ValidationConfig.model_validate(data)


def load_validation_config_from_mapping(data: Mapping[str, Any]) -> ValidationConfig:
    """Validate a parsed config mapping into a :class:`ValidationConfig`.

    Useful when the YAML/JSON parsing has already happened (e.g. a config read
    from a CLI flag or a database blob); validation rules are identical to
    :func:`load_validation_config`.
    """
    return ValidationConfig.model_validate(data)


def config_hash(config: ValidationConfig) -> str:
    """Return a stable SHA-256 hex digest of the validated config content.

    The digest is computed over :meth:`ValidationConfig.canonical_json`, so
    two equal configurations hash identically independent of field order. It
    covers the split boundaries, markets, base currency and strategy version.
    """
    return hashlib.sha256(config.canonical_json().encode("utf-8")).hexdigest()


def load_config_hash(
    path: str | Path,
    fmt: ValidationConfigFormat | None = None,
) -> str:
    """Load a validation config file and hash its validated content."""
    return config_hash(load_validation_config(path, fmt))
