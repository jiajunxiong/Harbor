"""Versioned strategy configuration loading and hashing (MVP 2 / SP 2.5).

Loads a validated :class:`~harbor.core.backtest_config.BacktestConfig` from a
YAML or JSON file and produces a stable hash of the validated content. The hash
is derived from the canonical key-sorted serialization (SP 2.4), so equal
configurations always hash equal regardless of field or key order; SP 2.6 uses
it to identify and de-duplicate research runs.
"""

import hashlib
import json
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from harbor.core.backtest_config import BacktestConfig

_YAML_SUFFIXES = {".yaml", ".yml"}


class ConfigFormat(StrEnum):
    """The file format a strategy configuration is stored in."""

    YAML = "yaml"
    JSON = "json"


def _detect_format(path: Path, fmt: ConfigFormat | None) -> ConfigFormat:
    """Return the explicit format or infer it from the file suffix."""
    if fmt is not None:
        return fmt
    suffix = path.suffix.lower()
    if suffix in _YAML_SUFFIXES:
        return ConfigFormat.YAML
    if suffix == ".json":
        return ConfigFormat.JSON
    raise ValueError(
        f"Cannot infer config format from {path.name!r}; "
        "use a .yaml/.yml/.json file or pass fmt explicitly."
    )


def load_backtest_config(
    path: str | Path,
    fmt: ConfigFormat | None = None,
) -> BacktestConfig:
    """Load, validate, and return a backtest config from a YAML or JSON file.

    Args:
        path: Path to the configuration file.
        fmt: Explicit format; when omitted it is inferred from the suffix.

    Returns:
        The validated, immutable :class:`BacktestConfig`.

    Raises:
        ValueError: If the file is empty, the root is not a mapping, the
            content cannot be parsed, or the format cannot be inferred.
        pydantic.ValidationError: If the parsed content violates the config
            model constraints (SP 2.4).
    """
    config_path = Path(path)
    format_ = _detect_format(config_path, fmt)
    content = config_path.read_text(encoding="utf-8")
    if format_ is ConfigFormat.YAML:
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
        raise ValueError("Backtest config must be a mapping at the root.")
    return BacktestConfig.model_validate(data)


def load_backtest_config_from_mapping(data: Mapping[str, Any]) -> BacktestConfig:
    """Validate a parsed config mapping into a :class:`BacktestConfig`.

    Useful when the YAML/JSON parsing has already happened (e.g. config read
    from a CLI flag or a database blob); validation rules are identical to
    :func:`load_backtest_config`.
    """
    return BacktestConfig.model_validate(data)


def config_hash(config: BacktestConfig) -> str:
    """Return a stable SHA-256 hex digest of the validated config content.

    The digest is computed over :meth:`BacktestConfig.canonical_json`, so two
    equal configurations hash identically independent of field order.
    """
    return hashlib.sha256(config.canonical_json().encode("utf-8")).hexdigest()


def load_config_hash(path: str | Path, fmt: ConfigFormat | None = None) -> str:
    """Load a config file and return the stable hash of its validated content."""
    return config_hash(load_backtest_config(path, fmt))
