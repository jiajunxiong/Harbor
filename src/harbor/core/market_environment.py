"""Market environment classification config (MVP 3 / SP 3.48).

Versioned, pre-registered definitions of the market-environment regimes —
up (上涨), down (下跌), high volatility (高波动), low liquidity (低流动性) and
FX volatility (汇率波动) — each with its named interval, thresholds, source
and version (将预先定义的上涨、下跌、高波动、低流动性和汇率波动区间与来源版本
化). The definitions are frozen BEFORE any evaluation and the classification
never uses results: :meth:`MarketEnvironmentRegime.applies` maps only a
measured market value (a return, volatility, liquidity or FX-change figure) to
the regime's pre-defined interval — so a regime is never carved out
retrospectively from an outcome (不根据结果事后划分).

- :class:`EnvironmentDimension` names the four measured dimensions.
- :class:`MarketEnvironmentRegime` is one pre-registered interval (name,
  dimension, comparison direction, threshold, lookback window, source,
  version) plus the pure :meth:`~MarketEnvironmentRegime.applies` classifier.
- :class:`EnvironmentDefinitionSet` is the versioned, source-tagged,
  fingerprint-stamped collection (the SP 3.3-style stable identity for the
  environment sub-config); :func:`active_regimes` reports which pre-defined
  regimes a measured value falls into.

Pure core layer: depends only on the standard library, never on storage,
services or CLI.
"""

import hashlib
import json
import math
from dataclasses import dataclass, replace
from enum import StrEnum


class EnvironmentDimension(StrEnum):
    """The measured market dimension a regime classifies (SP 3.48)."""

    TREND = "trend"  # 上涨 / 下跌
    VOLATILITY = "volatility"  # 高波动
    LIQUIDITY = "liquidity"  # 低流动性
    FX = "fx"  # 汇率波动


class EnvironmentComparison(StrEnum):
    """Which side of the threshold the regime interval lies on (SP 3.48)."""

    AT_OR_ABOVE = "at_or_above"
    AT_OR_BELOW = "at_or_below"


@dataclass(frozen=True)
class MarketEnvironmentRegime:
    """One pre-defined, versioned market-environment regime (SP 3.48).

    ``name`` labels the regime (e.g. ``bull_market`` for 上涨); ``dimension``
    is the measured quantity; ``comparison`` decides whether the measured
    value must be at/above or at/below ``threshold`` for the regime to hold;
    ``window_days`` is the lookback window the value is measured over;
    ``source`` and ``version`` record where the definition came from. The
    classification is a pure function of the measured ``value`` — results are
    never an input, so a regime cannot be carved out from an outcome.
    """

    name: str
    dimension: EnvironmentDimension
    comparison: EnvironmentComparison
    threshold: float
    window_days: int
    source: str
    version: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("regime name must be non-empty.")
        if not self.source:
            raise ValueError("regime source must be non-empty.")
        if not self.version:
            raise ValueError("regime version must be non-empty.")
        if self.window_days <= 0:
            raise ValueError("regime window_days must be positive.")
        if not math.isfinite(self.threshold):
            raise ValueError("regime threshold must be finite.")

    def applies(self, value: float) -> bool:
        """Return whether the measured ``value`` falls in the regime interval.

        Pure classification: only the measured market value is consulted, never
        any result or outcome (不根据结果事后划分).
        """
        if self.comparison is EnvironmentComparison.AT_OR_ABOVE:
            return value >= self.threshold
        return value <= self.threshold

    def readable(self) -> str:
        """Render the regime definition as one line."""
        operator = ">=" if self.comparison is EnvironmentComparison.AT_OR_ABOVE else "<="
        return (
            f"{self.name} ({self.dimension.value}) value {operator} "
            f"{self.threshold} over {self.window_days}d "
            f"source {self.source} v{self.version}"
        )


@dataclass(frozen=True)
class EnvironmentDefinitionSet:
    """The versioned, pre-registered environment definitions (SP 3.48).

    ``version`` / ``source`` identify the definition set and every regime is
    individually sourced + versioned. ``fingerprint`` is the stable SP 3.3-style
    SHA-256 identity over the definitions, so a replay (SP 3.46) can verify the
    same pre-registered environments were used.
    """

    version: str
    source: str
    regimes: tuple[MarketEnvironmentRegime, ...]
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("environment set version must be non-empty.")
        if not self.source:
            raise ValueError("environment set source must be non-empty.")
        if not self.regimes:
            raise ValueError("an environment set requires at least one regime.")
        names = [regime.name for regime in self.regimes]
        if len(set(names)) != len(names):
            raise ValueError("environment regime names must be unique.")
        if not self.fingerprint:
            raise ValueError("environment set fingerprint must be non-empty.")

    def regime(self, name: str) -> MarketEnvironmentRegime | None:
        """Return the regime with ``name``, or ``None`` when absent."""
        for regime in self.regimes:
            if regime.name == name:
                return regime
        return None

    def for_dimension(self, dimension: EnvironmentDimension) -> tuple[MarketEnvironmentRegime, ...]:
        """Return the regimes measuring ``dimension``, in declaration order."""
        return tuple(regime for regime in self.regimes if regime.dimension is dimension)

    def readable(self) -> str:
        """Render the environment set as one line."""
        names = ", ".join(regime.name for regime in self.regimes)
        return (
            f"environment set {self.version} ({self.source}): "
            f"{len(self.regimes)} regimes [{names}] fp {self.fingerprint}"
        )


def define_regime(
    name: str,
    *,
    dimension: EnvironmentDimension,
    comparison: EnvironmentComparison,
    threshold: float,
    window_days: int,
    source: str = "pre-registered",
    version: str = "1.0",
) -> MarketEnvironmentRegime:
    """Declare one pre-registered environment regime (SP 3.48)."""
    return MarketEnvironmentRegime(
        name=name,
        dimension=dimension,
        comparison=comparison,
        threshold=threshold,
        window_days=window_days,
        source=source,
        version=version,
    )


def build_environment_set(
    *,
    version: str,
    source: str,
    regimes: tuple[MarketEnvironmentRegime, ...],
) -> EnvironmentDefinitionSet:
    """Assemble a versioned, fingerprint-stamped environment set (SP 3.48)."""
    definition_set = EnvironmentDefinitionSet(
        version=version,
        source=source,
        regimes=regimes,
        fingerprint="unfingerprinted",
    )
    return replace(definition_set, fingerprint=environment_set_fingerprint(definition_set))


def default_environment_set() -> EnvironmentDefinitionSet:
    """Return the pre-defined five-regime environment set (SP 3.48).

    Up (上涨) and down (下跌) classify the 63-day trend around a zero
    threshold; high volatility (高波动) an annualized 63-day volatility above
    20%; low liquidity (低流动性) a 21-day turnover below 5%; and FX volatility
    (汇率波动) a 21-day FX change of 2% or more. These are pre-registered,
    sourced defaults — never derived from results.
    """
    return build_environment_set(
        version="1.0",
        source="pre-registered",
        regimes=(
            define_regime(
                "bull_market",
                dimension=EnvironmentDimension.TREND,
                comparison=EnvironmentComparison.AT_OR_ABOVE,
                threshold=0.0,
                window_days=63,
            ),
            define_regime(
                "bear_market",
                dimension=EnvironmentDimension.TREND,
                comparison=EnvironmentComparison.AT_OR_BELOW,
                threshold=0.0,
                window_days=63,
            ),
            define_regime(
                "high_volatility",
                dimension=EnvironmentDimension.VOLATILITY,
                comparison=EnvironmentComparison.AT_OR_ABOVE,
                threshold=0.20,
                window_days=63,
            ),
            define_regime(
                "low_liquidity",
                dimension=EnvironmentDimension.LIQUIDITY,
                comparison=EnvironmentComparison.AT_OR_BELOW,
                threshold=0.05,
                window_days=21,
            ),
            define_regime(
                "fx_volatile",
                dimension=EnvironmentDimension.FX,
                comparison=EnvironmentComparison.AT_OR_ABOVE,
                threshold=0.02,
                window_days=21,
            ),
        ),
    )


def active_regimes(
    definition_set: EnvironmentDefinitionSet,
    *,
    value: float,
    dimension: EnvironmentDimension,
) -> tuple[str, ...]:
    """Return the pre-defined regime names a measured value falls into.

    Only regimes measuring ``dimension`` are considered, and only the measured
    ``value`` is consulted — the classification is never result-based.
    """
    return tuple(
        regime.name for regime in definition_set.for_dimension(dimension) if regime.applies(value)
    )


def environment_set_json(definition_set: EnvironmentDefinitionSet) -> str:
    """Return a stable, key-sorted JSON serialization of an environment set.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style).
    """
    payload: dict[str, object] = {
        "version": definition_set.version,
        "source": definition_set.source,
        "regimes": [
            {
                "name": regime.name,
                "dimension": regime.dimension.value,
                "comparison": regime.comparison.value,
                "threshold": regime.threshold,
                "window_days": regime.window_days,
                "source": regime.source,
                "version": regime.version,
            }
            for regime in definition_set.regimes
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def environment_set_fingerprint(definition_set: EnvironmentDefinitionSet) -> str:
    """Return the stable SHA-256 fingerprint of an environment set (SP 3.48)."""
    return hashlib.sha256(environment_set_json(definition_set).encode("utf-8")).hexdigest()


__all__: tuple[str, ...] = (
    "EnvironmentComparison",
    "EnvironmentDefinitionSet",
    "EnvironmentDimension",
    "MarketEnvironmentRegime",
    "active_regimes",
    "build_environment_set",
    "default_environment_set",
    "define_regime",
    "environment_set_fingerprint",
    "environment_set_json",
)
