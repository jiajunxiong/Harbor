"""Factor composite scoring (MVP 2 / SP 2.24).

Combines per-symbol standardized factor scores (SP 2.22, already adjusted to a
"higher is better" orientation) into a single composite score using configured
weights.

The weight set is validated and verifiable:

- at least one factor, no duplicate names, every weight non-negative, and the
  weights sum to 1.0 (within a tolerance) — enforced at construction;
- missing factor handling is configurable via :class:`MissingPolicy`:
  ``RENORMALIZE`` re-weights the available factors, ``REQUIRE_ALL`` returns
  ``None`` unless every factor is present, and ``MIN_COVERAGE`` requires a
  minimum fraction of the total weight to be available;
- tie breaking is deterministic: :func:`rank_symbols` orders symbols by score
  descending and breaks ties by symbol ascending, so selection (SP 2.25/2.26)
  is replayable.

Pure core logic: depends only on the backtest domain types and never touches
storage or CLI code.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

_WEIGHT_TOLERANCE = 1e-6


class MissingPolicy(StrEnum):
    """How to combine a symbol's score when some factors are missing (SP 2.24)."""

    RENORMALIZE = "renormalize"
    REQUIRE_ALL = "require_all"
    MIN_COVERAGE = "min_coverage"


@dataclass(frozen=True)
class FactorScoreConfig:
    """The composite scoring rule (factor weights + missing policy).

    ``weights`` is stored as a deterministic, key-sorted tuple of
    ``(factor, weight)`` pairs so the rule is replayable and canonicalizable.
    Use :meth:`from_mapping` to build it from a plain mapping.
    """

    weights: tuple[tuple[str, float], ...]
    missing_policy: MissingPolicy = MissingPolicy.RENORMALIZE
    min_available_weight: float = 0.0

    def __post_init__(self) -> None:
        if not self.weights:
            raise ValueError("At least one factor weight must be configured.")
        names = [name for name, _ in self.weights]
        if len(set(names)) != len(names):
            raise ValueError("Factor names must not contain duplicates.")
        for name, weight in self.weights:
            if weight < 0:
                raise ValueError(f"Weight for factor {name!r} must be non-negative.")
        total = sum(weight for _, weight in self.weights)
        if abs(total - 1.0) > _WEIGHT_TOLERANCE:
            raise ValueError(f"Factor weights must sum to 1.0 (got {total}).")
        if self.missing_policy is MissingPolicy.MIN_COVERAGE:
            if not 0.0 <= self.min_available_weight <= 1.0:
                raise ValueError("min_available_weight must be in [0.0, 1.0].")

    @classmethod
    def from_mapping(
        cls,
        weights: Mapping[str, float],
        *,
        missing_policy: MissingPolicy = MissingPolicy.RENORMALIZE,
        min_available_weight: float = 0.0,
    ) -> "FactorScoreConfig":
        """Build a config from a factor-to-weight mapping (key-sorted)."""
        return cls(
            tuple(sorted(weights.items())),
            missing_policy=missing_policy,
            min_available_weight=min_available_weight,
        )

    @property
    def factor_names(self) -> tuple[str, ...]:
        """Return the configured factor names in canonical order."""
        return tuple(name for name, _ in self.weights)

    def weight(self, factor: str) -> float:
        """Return the configured weight for ``factor``.

        Raises:
            KeyError: If the factor is not configured.
        """
        for name, weight in self.weights:
            if name == factor:
                return weight
        raise KeyError(factor)


def _composite_for_symbol(
    factor_values: Mapping[str, float | None],
    config: FactorScoreConfig,
) -> float | None:
    """Compute one symbol's composite score under the configured policy."""
    available_weight = 0.0
    weighted_sum = 0.0
    for name, weight in config.weights:
        score = factor_values.get(name)
        if score is None:
            if config.missing_policy is MissingPolicy.REQUIRE_ALL:
                return None
            continue
        available_weight += weight
        weighted_sum += weight * score
    if config.missing_policy is MissingPolicy.MIN_COVERAGE:
        if available_weight < config.min_available_weight:
            return None
    if available_weight <= 0:
        return None
    return weighted_sum / available_weight


def composite_score(
    values: Mapping[str, Mapping[str, float | None]],
    config: FactorScoreConfig,
) -> dict[str, float | None]:
    """Compute the weighted composite score per symbol (SP 2.24).

    ``values`` maps each symbol to its standardized factor scores (``None``
    meaning the factor is unavailable). Only factors configured in ``config``
    participate; unknown factor entries are ignored. The composite is the
    weighted average of the available factors, renormalized (or gated) per the
    configured :class:`MissingPolicy`.
    """
    return {
        symbol: _composite_for_symbol(factor_values, config)
        for symbol, factor_values in values.items()
    }


def rank_symbols(scores: Mapping[str, float | None]) -> tuple[str, ...]:
    """Return symbols ranked best-first with a deterministic tie rule.

    Symbols with a ``None`` score are excluded. Ordering is by score descending;
    equal scores are broken by symbol ascending, so the ranking is replayable
    and can be consumed by the selectors (SP 2.25/2.26).
    """
    ranked = [(symbol, score) for symbol, score in scores.items() if score is not None]
    ranked.sort(key=lambda item: (-item[1], item[0]))
    return tuple(symbol for symbol, _ in ranked)
