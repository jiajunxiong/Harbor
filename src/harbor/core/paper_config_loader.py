"""Versioned paper-config loading and hashing (MVP 4 / SP 4.2).

Loads a validated :class:`~harbor.core.paper_config.PaperConfig` from a YAML
or JSON file and produces a stable hash of the validated content. The hash is
derived from the canonical key-sorted serialization (SP 4.2), so equal
configurations always hash equal regardless of field or key order; the
markets, base currency, strategy version, risk parameters, stop conditions
and running mode are all part of that serialization and therefore of the
hash. SP 4.9 fingerprints the replay identity and records the hash so a paper
run can be reproduced.
"""

import hashlib
import json
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from harbor.core.paper_config import PaperConfig

_YAML_SUFFIXES = {".yaml", ".yml"}


class PaperConfigFormat(StrEnum):
    """The file format a paper configuration is stored in."""

    YAML = "yaml"
    JSON = "json"


def _detect_format(path: Path, fmt: PaperConfigFormat | None) -> PaperConfigFormat:
    """Return the explicit format or infer it from the file suffix."""
    if fmt is not None:
        return fmt
    suffix = path.suffix.lower()
    if suffix in _YAML_SUFFIXES:
        return PaperConfigFormat.YAML
    if suffix == ".json":
        return PaperConfigFormat.JSON
    raise ValueError(
        f"Cannot infer paper config format from {path.name!r}; "
        "use a .yaml/.yml/.json file or pass fmt explicitly."
    )


def load_paper_config(
    path: str | Path,
    fmt: PaperConfigFormat | None = None,
) -> PaperConfig:
    """Load, validate, and return a paper config from a YAML or JSON file.

    Args:
        path: Path to the configuration file.
        fmt: Explicit format; when omitted it is inferred from the suffix.

    Returns:
        The validated, immutable :class:`PaperConfig`.

    Raises:
        ValueError: If the file is empty, the root is not a mapping, the
            content cannot be parsed, or the format cannot be inferred.
        pydantic.ValidationError: If the parsed content violates the config
            model constraints (SP 4.2).
    """
    config_path = Path(path)
    format_ = _detect_format(config_path, fmt)
    content = config_path.read_text(encoding="utf-8")
    if format_ is PaperConfigFormat.YAML:
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
        raise ValueError("Paper config must be a mapping at the root.")
    return PaperConfig.model_validate(data)


def load_paper_config_from_mapping(data: Mapping[str, Any]) -> PaperConfig:
    """Validate a parsed config mapping into a :class:`PaperConfig`.

    Useful when the YAML/JSON parsing has already happened (e.g. a config read
    from a CLI flag or a database blob); validation rules are identical to
    :func:`load_paper_config`.
    """
    return PaperConfig.model_validate(data)


def config_hash(config: PaperConfig) -> str:
    """Return a stable SHA-256 hex digest of the validated config content.

    The digest is computed over :meth:`PaperConfig.canonical_json`, so two
    equal configurations hash identically independent of field order. It
    covers the strategy version, markets, base currency, risk parameters,
    stop conditions and running mode.
    """
    return hashlib.sha256(config.canonical_json().encode("utf-8")).hexdigest()


def load_config_hash(
    path: str | Path,
    fmt: PaperConfigFormat | None = None,
) -> str:
    """Load a paper config file and hash its validated content."""
    return config_hash(load_paper_config(path, fmt))
