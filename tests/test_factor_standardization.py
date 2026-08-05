"""Factor standardization and direction tests (MVP 2 / SP 2.22).

Verifies quantile, z-score and rank standardization, the higher/lower-is-better
direction, winsorization, missing-value handling and deterministic tie
breaking.
"""

import unittest

from harbor.core.factor_standardization import (
    FactorDirection,
    StandardizationConfig,
    StandardizationMethod,
    standardize_factor,
)


class StandardizationConfigTests(unittest.TestCase):
    """Verify configuration validation and description (SP 2.22)."""

    def test_rejects_negative_winsorize(self) -> None:
        with self.assertRaisesRegex(ValueError, "winsorize"):
            StandardizationConfig(winsorize=-0.01)

    def test_rejects_winsorize_at_or_above_half(self) -> None:
        with self.assertRaisesRegex(ValueError, "winsorize"):
            StandardizationConfig(winsorize=0.5)
        with self.assertRaisesRegex(ValueError, "winsorize"):
            StandardizationConfig(winsorize=0.6)

    def test_describe_records_rule(self) -> None:
        config = StandardizationConfig(
            method=StandardizationMethod.ZSCORE,
            direction=FactorDirection.LOWER_IS_BETTER,
            winsorize=0.01,
        )
        summary = config.describe()
        self.assertIn("zscore", summary)
        self.assertIn("lower_is_better", summary)
        self.assertIn("winsorize=1%", summary)


class QuantileStandardizationTests(unittest.TestCase):
    """Verify quantile standardization and direction (SP 2.22)."""

    def test_quantile_ranks_values(self) -> None:
        result = standardize_factor(
            {"A": 1.0, "B": 2.0, "C": 3.0},
            config=StandardizationConfig(method=StandardizationMethod.QUANTILE),
        )
        self.assertEqual(result["A"], 0.0)
        self.assertEqual(result["B"], 0.5)
        self.assertEqual(result["C"], 1.0)

    def test_lower_is_better_inverts_quantile(self) -> None:
        result = standardize_factor(
            {"A": 1.0, "B": 2.0, "C": 3.0},
            config=StandardizationConfig(
                method=StandardizationMethod.QUANTILE,
                direction=FactorDirection.LOWER_IS_BETTER,
            ),
        )
        self.assertEqual(result["A"], 1.0)
        self.assertEqual(result["B"], 0.5)
        self.assertEqual(result["C"], 0.0)

    def test_single_value_is_neutral(self) -> None:
        result = standardize_factor(
            {"A": 7.0},
            config=StandardizationConfig(method=StandardizationMethod.QUANTILE),
        )
        self.assertEqual(result["A"], 0.5)

    def test_ties_broken_deterministically_by_symbol(self) -> None:
        result = standardize_factor(
            {"A": 1.0, "B": 1.0, "C": 2.0},
            config=StandardizationConfig(method=StandardizationMethod.QUANTILE),
        )
        self.assertEqual(result["A"], 0.0)
        self.assertEqual(result["B"], 0.5)
        self.assertEqual(result["C"], 1.0)


class ZScoreStandardizationTests(unittest.TestCase):
    """Verify z-score standardization and direction (SP 2.22)."""

    def test_zscore_centers_and_scales(self) -> None:
        result = standardize_factor(
            {"A": 1.0, "B": 2.0, "C": 3.0},
            config=StandardizationConfig(method=StandardizationMethod.ZSCORE),
        )
        self.assertAlmostEqual(result["A"], -1.224744871, places=9)
        self.assertAlmostEqual(result["B"], 0.0, places=9)
        self.assertAlmostEqual(result["C"], 1.224744871, places=9)

    def test_lower_is_better_negates_zscore(self) -> None:
        result = standardize_factor(
            {"A": 1.0, "B": 2.0, "C": 3.0},
            config=StandardizationConfig(
                method=StandardizationMethod.ZSCORE,
                direction=FactorDirection.LOWER_IS_BETTER,
            ),
        )
        self.assertAlmostEqual(result["A"], 1.224744871, places=9)
        self.assertAlmostEqual(result["C"], -1.224744871, places=9)

    def test_all_equal_yields_neutral_zero(self) -> None:
        result = standardize_factor(
            {"A": 5.0, "B": 5.0, "C": 5.0},
            config=StandardizationConfig(method=StandardizationMethod.ZSCORE),
        )
        self.assertEqual(result["A"], 0.0)
        self.assertEqual(result["B"], 0.0)
        self.assertEqual(result["C"], 0.0)

    def test_single_value_is_neutral_zero(self) -> None:
        result = standardize_factor(
            {"A": 5.0},
            config=StandardizationConfig(method=StandardizationMethod.ZSCORE),
        )
        self.assertEqual(result["A"], 0.0)


class RankStandardizationTests(unittest.TestCase):
    """Verify ordinal ranking with direction (SP 2.22)."""

    def test_higher_is_better_ranks_highest_first(self) -> None:
        result = standardize_factor(
            {"A": 1.0, "B": 2.0, "C": 3.0},
            config=StandardizationConfig(method=StandardizationMethod.RANK),
        )
        self.assertEqual(result["C"], 1)
        self.assertEqual(result["B"], 2)
        self.assertEqual(result["A"], 3)

    def test_lower_is_better_ranks_lowest_first(self) -> None:
        result = standardize_factor(
            {"A": 1.0, "B": 2.0, "C": 3.0},
            config=StandardizationConfig(
                method=StandardizationMethod.RANK,
                direction=FactorDirection.LOWER_IS_BETTER,
            ),
        )
        self.assertEqual(result["A"], 1)
        self.assertEqual(result["B"], 2)
        self.assertEqual(result["C"], 3)

    def test_ties_broken_by_symbol(self) -> None:
        result = standardize_factor(
            {"A": 1.0, "B": 1.0, "C": 2.0},
            config=StandardizationConfig(method=StandardizationMethod.RANK),
        )
        self.assertEqual(result["C"], 1)
        self.assertEqual(result["B"], 2)
        self.assertEqual(result["A"], 3)


class MissingValueTests(unittest.TestCase):
    """Verify missing values never participate (SP 2.22)."""

    def test_missing_values_stay_none_and_are_ignored(self) -> None:
        result = standardize_factor(
            {"A": 1.0, "B": None, "C": 3.0},
            config=StandardizationConfig(method=StandardizationMethod.QUANTILE),
        )
        self.assertIsNone(result["B"])
        self.assertEqual(result["A"], 0.0)
        self.assertEqual(result["C"], 1.0)

    def test_all_missing_yields_all_none(self) -> None:
        result = standardize_factor(
            {"A": None, "B": None},
            config=StandardizationConfig(method=StandardizationMethod.QUANTILE),
        )
        self.assertEqual(result, {"A": None, "B": None})

    def test_empty_mapping(self) -> None:
        result = standardize_factor({}, config=StandardizationConfig())
        self.assertEqual(result, {})


class WinsorizationTests(unittest.TestCase):
    """Verify winsorization clips extremes before z-scoring (SP 2.22)."""

    def test_extreme_values_clipped_before_zscore(self) -> None:
        result = standardize_factor(
            {"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0},
            config=StandardizationConfig(
                method=StandardizationMethod.ZSCORE,
                winsorize=0.25,
            ),
        )
        # Bounds are the 25th/75th percentiles of [1,2,3,4] -> [2,3]; clipped
        # series is [2,2,3,3] with mean 2.5 and population std 0.5.
        self.assertAlmostEqual(result["A"], -1.0, places=9)
        self.assertAlmostEqual(result["B"], -1.0, places=9)
        self.assertAlmostEqual(result["C"], 1.0, places=9)
        self.assertAlmostEqual(result["D"], 1.0, places=9)

    def test_winsorize_does_not_change_ranks(self) -> None:
        result = standardize_factor(
            {"A": 1.0, "B": 2.0, "C": 100.0},
            config=StandardizationConfig(
                method=StandardizationMethod.RANK,
                winsorize=0.25,
            ),
        )
        self.assertEqual(result["C"], 1)
        self.assertEqual(result["B"], 2)
        self.assertEqual(result["A"], 3)


class DeterminismTests(unittest.TestCase):
    """Verify standardization is replayable (SP 2.22)."""

    def test_repeat_calls_are_identical(self) -> None:
        values = {"A": 1.0, "B": None, "C": 3.0, "D": 2.0}
        config = StandardizationConfig(method=StandardizationMethod.ZSCORE, winsorize=0.01)
        first = standardize_factor(values, config=config)
        second = standardize_factor(values, config=config)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
