"""Factor composite scoring tests (MVP 2 / SP 2.24).

Verifies weight validation (sum to 1, non-negative, unique), missing-factor
handling under each policy, renormalized weighted averages, and deterministic
tie breaking.
"""

import unittest

from harbor.core.factor_scoring import (
    FactorScoreConfig,
    MissingPolicy,
    composite_score,
    rank_symbols,
)


class FactorScoreConfigTests(unittest.TestCase):
    """Verify weight-set validation (SP 2.24)."""

    def test_rejects_empty_weights(self) -> None:
        with self.assertRaisesRegex(ValueError, "At least one"):
            FactorScoreConfig(())

    def test_rejects_duplicate_factor_names(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicates"):
            FactorScoreConfig((("yield", 0.5), ("yield", 0.5)))

    def test_rejects_negative_weight(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-negative"):
            FactorScoreConfig((("yield", -0.5), ("quality", 1.5)))

    def test_rejects_weights_not_summing_to_one(self) -> None:
        with self.assertRaisesRegex(ValueError, "sum to 1.0"):
            FactorScoreConfig((("yield", 0.4), ("quality", 0.4)))

    def test_valid_config(self) -> None:
        config = FactorScoreConfig((("yield", 0.5), ("quality", 0.5)))
        self.assertEqual(config.factor_names, ("yield", "quality"))
        self.assertEqual(config.weight("yield"), 0.5)

    def test_weight_unknown_factor_raises(self) -> None:
        config = FactorScoreConfig((("yield", 1.0),))
        with self.assertRaises(KeyError):
            config.weight("quality")

    def test_from_mapping_sorts_and_builds(self) -> None:
        config = FactorScoreConfig.from_mapping({"quality": 0.5, "yield": 0.5})
        self.assertEqual(config.factor_names, ("quality", "yield"))
        self.assertEqual(config.weight("quality"), 0.5)

    def test_min_available_weight_range(self) -> None:
        with self.assertRaisesRegex(ValueError, "min_available_weight"):
            FactorScoreConfig.from_mapping(
                {"yield": 1.0}, missing_policy=MissingPolicy.MIN_COVERAGE, min_available_weight=1.1
            )


class CompositeScoreTests(unittest.TestCase):
    """Verify composite scoring and missing handling (SP 2.24)."""

    def _config(
        self,
        policy: MissingPolicy = MissingPolicy.RENORMALIZE,
        min_available_weight: float = 0.0,
    ) -> FactorScoreConfig:
        return FactorScoreConfig(
            (("yield", 0.5), ("quality", 0.5)),
            missing_policy=policy,
            min_available_weight=min_available_weight,
        )

    def test_weighted_average_with_full_data(self) -> None:
        scores = composite_score(
            {"A": {"yield": 1.0, "quality": 0.0}},
            self._config(),
        )
        self.assertAlmostEqual(scores["A"], 0.5)

    def test_renormalize_over_available_factors(self) -> None:
        scores = composite_score(
            {"A": {"yield": 0.8, "quality": None}},
            self._config(),
        )
        self.assertAlmostEqual(scores["A"], 0.8)

    def test_all_missing_yields_none(self) -> None:
        scores = composite_score(
            {"A": {"yield": None, "quality": None}},
            self._config(),
        )
        self.assertIsNone(scores["A"])

    def test_require_all_yields_none_when_any_missing(self) -> None:
        scores = composite_score(
            {"A": {"yield": 0.8, "quality": None}},
            self._config(policy=MissingPolicy.REQUIRE_ALL),
        )
        self.assertIsNone(scores["A"])

    def test_require_all_full_data(self) -> None:
        scores = composite_score(
            {"A": {"yield": 1.0, "quality": 1.0}},
            self._config(policy=MissingPolicy.REQUIRE_ALL),
        )
        self.assertAlmostEqual(scores["A"], 1.0)

    def test_min_coverage_gates(self) -> None:
        config = self._config(
            policy=MissingPolicy.MIN_COVERAGE,
            min_available_weight=0.6,
        )
        below = composite_score({"A": {"yield": 0.8, "quality": None}}, config)
        self.assertIsNone(below["A"])
        at_or_above = composite_score({"A": {"yield": 0.8, "quality": 0.4}}, config)
        self.assertAlmostEqual(at_or_above["A"], 0.6)

    def test_unknown_factor_entries_ignored(self) -> None:
        scores = composite_score(
            {"A": {"yield": 1.0, "quality": 1.0, "extra": 0.0}},
            self._config(),
        )
        self.assertAlmostEqual(scores["A"], 1.0)

    def test_output_preserves_symbols(self) -> None:
        scores = composite_score(
            {
                "A": {"yield": 1.0, "quality": 1.0},
                "B": {"yield": None, "quality": None},
            },
            self._config(),
        )
        self.assertEqual(set(scores), {"A", "B"})


class RankSymbolsTests(unittest.TestCase):
    """Verify deterministic tie breaking (SP 2.24)."""

    def test_ranks_best_first(self) -> None:
        ordered = rank_symbols({"A": 0.3, "B": 0.9, "C": 0.6})
        self.assertEqual(ordered, ("B", "C", "A"))

    def test_ties_broken_by_symbol_ascending(self) -> None:
        ordered = rank_symbols({"B": 0.5, "A": 0.5, "C": 0.5})
        self.assertEqual(ordered, ("A", "B", "C"))

    def test_none_scores_excluded(self) -> None:
        ordered = rank_symbols({"A": None, "B": 0.5, "C": None})
        self.assertEqual(ordered, ("B",))


class DeterminismTests(unittest.TestCase):
    """Verify scoring and ranking are replayable (SP 2.24)."""

    def test_repeat_calls_identical(self) -> None:
        values = {
            "A": {"yield": 1.0, "quality": 0.2},
            "B": {"yield": None, "quality": 0.8},
            "C": {"yield": 0.5, "quality": None},
        }
        config = FactorScoreConfig((("yield", 0.4), ("quality", 0.6)))
        first = composite_score(values, config)
        second = composite_score(values, config)
        self.assertEqual(first, second)
        self.assertEqual(rank_symbols(first), rank_symbols(second))


if __name__ == "__main__":
    unittest.main()
