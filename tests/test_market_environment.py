"""Market environment classification config tests (MVP 3 / SP 3.48).

Verifies the pre-defined up (上涨), down (下跌), high-volatility (高波动),
low-liquidity (低流动性) and FX-volatility (汇率波动) regimes are versioned
with their sources and classify purely from a measured value — never from
results (将预先定义的...区间与来源版本化；不根据结果事后划分).
"""

import json
import unittest
from dataclasses import replace

from harbor.core.market_environment import (
    EnvironmentComparison,
    EnvironmentDefinitionSet,
    EnvironmentDimension,
    MarketEnvironmentRegime,
    active_regimes,
    build_environment_set,
    default_environment_set,
    define_regime,
    environment_set_fingerprint,
    environment_set_json,
)


def _regime(**overrides: object) -> MarketEnvironmentRegime:
    """A minimal pre-registered regime for value-level tests."""
    fields: dict[str, object] = {
        "name": "bull_market",
        "dimension": EnvironmentDimension.TREND,
        "comparison": EnvironmentComparison.AT_OR_ABOVE,
        "threshold": 0.0,
        "window_days": 63,
        "source": "pre-registered",
        "version": "1.0",
    }
    fields.update(overrides)
    return MarketEnvironmentRegime(**fields)  # type: ignore[arg-type]


def _set(**overrides: object) -> EnvironmentDefinitionSet:
    """A minimal environment set for value-level tests."""
    fields: dict[str, object] = {
        "version": "1.0",
        "source": "pre-registered",
        "regimes": (
            _regime(),
            _regime(
                name="bear_market",
                comparison=EnvironmentComparison.AT_OR_BELOW,
            ),
        ),
        "fingerprint": "x" * 64,
    }
    fields.update(overrides)
    return EnvironmentDefinitionSet(**fields)  # type: ignore[arg-type]


class EnvironmentDimensionTests(unittest.TestCase):
    """The four measured dimensions."""

    def test_dimensions(self) -> None:
        self.assertEqual(
            tuple(EnvironmentDimension),
            (
                EnvironmentDimension.TREND,
                EnvironmentDimension.VOLATILITY,
                EnvironmentDimension.LIQUIDITY,
                EnvironmentDimension.FX,
            ),
        )

    def test_lowercase_values(self) -> None:
        self.assertEqual(EnvironmentDimension.TREND.value, "trend")
        self.assertEqual(EnvironmentDimension.VOLATILITY.value, "volatility")
        self.assertEqual(EnvironmentDimension.LIQUIDITY.value, "liquidity")
        self.assertEqual(EnvironmentDimension.FX.value, "fx")


class EnvironmentComparisonTests(unittest.TestCase):
    """The two comparison directions."""

    def test_comparisons(self) -> None:
        self.assertEqual(EnvironmentComparison.AT_OR_ABOVE.value, "at_or_above")
        self.assertEqual(EnvironmentComparison.AT_OR_BELOW.value, "at_or_below")


class MarketEnvironmentRegimeTests(unittest.TestCase):
    """One pre-registered regime and its pure classifier."""

    def test_valid_regime(self) -> None:
        regime = _regime()
        self.assertEqual(regime.name, "bull_market")
        self.assertEqual(regime.window_days, 63)
        self.assertEqual(regime.source, "pre-registered")
        self.assertEqual(regime.version, "1.0")

    def test_empty_name_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _regime(name="")

    def test_empty_source_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _regime(source="")

    def test_empty_version_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _regime(version="")

    def test_non_positive_window_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _regime(window_days=0)
        with self.assertRaises(ValueError):
            _regime(window_days=-5)

    def test_non_finite_threshold_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _regime(threshold=float("nan"))
        with self.assertRaises(ValueError):
            _regime(threshold=float("inf"))

    def test_at_or_above_applies(self) -> None:
        regime = _regime(threshold=0.10)
        self.assertTrue(regime.applies(0.15))
        self.assertTrue(regime.applies(0.10))
        self.assertFalse(regime.applies(0.05))

    def test_at_or_below_applies(self) -> None:
        regime = _regime(comparison=EnvironmentComparison.AT_OR_BELOW, threshold=0.05)
        self.assertTrue(regime.applies(0.03))
        self.assertTrue(regime.applies(0.05))
        self.assertFalse(regime.applies(0.08))

    def test_readable(self) -> None:
        text = _regime().readable()
        self.assertIn("bull_market", text)
        self.assertIn("pre-registered", text)


class EnvironmentDefinitionSetTests(unittest.TestCase):
    """The versioned, source-tagged, fingerprint-stamped collection."""

    def test_valid_set(self) -> None:
        definition_set = _set()
        self.assertEqual(len(definition_set.regimes), 2)
        self.assertEqual(definition_set.source, "pre-registered")

    def test_empty_version_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _set(version="")

    def test_empty_source_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _set(source="")

    def test_empty_regimes_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _set(regimes=())

    def test_duplicate_names_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _set(regimes=(_regime(), _regime()))

    def test_regime_lookup(self) -> None:
        definition_set = _set()
        self.assertEqual(definition_set.regime("bear_market").name, "bear_market")  # type: ignore[union-attr]
        self.assertIsNone(definition_set.regime("missing"))

    def test_for_dimension(self) -> None:
        definition_set = _set()
        trend = definition_set.for_dimension(EnvironmentDimension.TREND)
        self.assertEqual(len(trend), 2)
        self.assertEqual(definition_set.for_dimension(EnvironmentDimension.FX), ())

    def test_readable(self) -> None:
        self.assertIn("environment set", _set().readable())


class DefineRegimeTests(unittest.TestCase):
    """The declaration factory and pre-registered defaults."""

    def test_define_regime_defaults(self) -> None:
        regime = define_regime(
            "high_volatility",
            dimension=EnvironmentDimension.VOLATILITY,
            comparison=EnvironmentComparison.AT_OR_ABOVE,
            threshold=0.20,
            window_days=63,
        )
        self.assertEqual(regime.source, "pre-registered")
        self.assertEqual(regime.version, "1.0")

    def test_default_set_has_five_regimes(self) -> None:
        definition_set = default_environment_set()
        self.assertEqual(len(definition_set.regimes), 5)
        for name in (
            "bull_market",
            "bear_market",
            "high_volatility",
            "low_liquidity",
            "fx_volatile",
        ):
            self.assertIsNotNone(definition_set.regime(name))
        # sources + versions are recorded per regime.
        for regime in definition_set.regimes:
            self.assertTrue(regime.source)
            self.assertTrue(regime.version)


class ActiveRegimesTests(unittest.TestCase):
    """Classifying a measured value into the pre-defined regimes (no results)."""

    def test_high_return_activates_bull(self) -> None:
        definition_set = default_environment_set()
        active = active_regimes(definition_set, value=0.05, dimension=EnvironmentDimension.TREND)
        self.assertEqual(active, ("bull_market",))

    def test_low_return_activates_bear(self) -> None:
        definition_set = default_environment_set()
        active = active_regimes(definition_set, value=-0.05, dimension=EnvironmentDimension.TREND)
        self.assertEqual(active, ("bear_market",))

    def test_zero_return_activates_both(self) -> None:
        definition_set = default_environment_set()
        active = active_regimes(definition_set, value=0.0, dimension=EnvironmentDimension.TREND)
        self.assertEqual(active, ("bull_market", "bear_market"))

    def test_high_volatility_activates(self) -> None:
        definition_set = default_environment_set()
        active = active_regimes(
            definition_set, value=0.30, dimension=EnvironmentDimension.VOLATILITY
        )
        self.assertEqual(active, ("high_volatility",))

    def test_low_liquidity_activates(self) -> None:
        definition_set = default_environment_set()
        active = active_regimes(
            definition_set, value=0.02, dimension=EnvironmentDimension.LIQUIDITY
        )
        self.assertEqual(active, ("low_liquidity",))

    def test_fx_change_activates(self) -> None:
        definition_set = default_environment_set()
        active = active_regimes(definition_set, value=0.03, dimension=EnvironmentDimension.FX)
        self.assertEqual(active, ("fx_volatile",))


class FingerprintTests(unittest.TestCase):
    """Stable, re-derivable, sensitive environment-set fingerprints."""

    def test_sha256_hex(self) -> None:
        digest = default_environment_set().fingerprint
        self.assertEqual(len(digest), 64)
        int(digest, 16)

    def test_rederivable(self) -> None:
        definition_set = default_environment_set()
        self.assertEqual(
            definition_set.fingerprint,
            environment_set_fingerprint(definition_set),
        )

    def test_stable(self) -> None:
        self.assertEqual(
            default_environment_set().fingerprint,
            default_environment_set().fingerprint,
        )

    def test_changes_with_threshold(self) -> None:
        first = default_environment_set()
        second = build_environment_set(
            version=first.version,
            source=first.source,
            regimes=(replace(first.regimes[0], threshold=0.05),) + first.regimes[1:],
        )
        self.assertNotEqual(first.fingerprint, second.fingerprint)

    def test_changes_with_version(self) -> None:
        first = default_environment_set()
        second = build_environment_set(
            version="2.0",
            source=first.source,
            regimes=first.regimes,
        )
        self.assertNotEqual(first.fingerprint, second.fingerprint)

    def test_json_excludes_fingerprint_and_key_sorted(self) -> None:
        definition_set = default_environment_set()
        serialized = environment_set_json(definition_set)
        self.assertNotIn('"fingerprint"', serialized)
        self.assertIn('"dimension":"trend"', serialized)
        payload = json.loads(serialized)
        self.assertEqual(
            serialized,
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        )
