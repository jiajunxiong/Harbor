"""Corporate action type mapping and validation tests."""

import unittest

from harbor.config import MarketTarget
from harbor.core.action_mapping import (
    allowed_action_types,
    canonical_action_type,
    resolve_action_type,
    validate_action_type,
)
from harbor.core.market_registry import CorporateActionType


class ResolveActionTypeTests(unittest.TestCase):
    """Verify raw vendor labels map to canonical action types."""

    def test_english_split_labels_resolve_to_split(self) -> None:
        for label in ("split", "stock split", "splits", "SPLIT"):
            self.assertIs(resolve_action_type(label), CorporateActionType.SPLIT)

    def test_hk_chinese_labels_resolve(self) -> None:
        cases = {
            "供股": CorporateActionType.RIGHTS_ISSUE,
            "配股": CorporateActionType.RIGHTS_ISSUE,
            "合股": CorporateActionType.CONSOLIDATION,
            "股份合并": CorporateActionType.CONSOLIDATION,
            "要约": CorporateActionType.TENDER_OFFER,
            "收购要约": CorporateActionType.TENDER_OFFER,
            "股息": CorporateActionType.DIVIDEND,
            "拆股": CorporateActionType.SPLIT,
            "分拆": CorporateActionType.SPIN_OFF,
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                self.assertIs(resolve_action_type(label), expected)

    def test_us_english_labels_resolve(self) -> None:
        cases = {
            "split": CorporateActionType.SPLIT,
            "reverse split": CorporateActionType.CONSOLIDATION,
            "rights issue": CorporateActionType.RIGHTS_ISSUE,
            "merger": CorporateActionType.MERGER,
            "merge": CorporateActionType.MERGER,
            "spin-off": CorporateActionType.SPIN_OFF,
            "spin off": CorporateActionType.SPIN_OFF,
            "cash dividend": CorporateActionType.DIVIDEND,
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                self.assertIs(resolve_action_type(label), expected)

    def test_canonical_values_resolve_to_themselves(self) -> None:
        for action_type in CorporateActionType:
            with self.subTest(action_type=action_type):
                self.assertIs(resolve_action_type(action_type.value), action_type)

    def test_whitespace_and_case_are_ignored(self) -> None:
        self.assertIs(resolve_action_type("  SPLIT "), CorporateActionType.SPLIT)

    def test_unknown_label_raises_value_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown corporate action type"):
            resolve_action_type("buyback")


class ValidateActionTypeHkTests(unittest.TestCase):
    """Verify Hong Kong corporate action type validation (SP 1.75)."""

    def test_hk_accepts_hk_specific_actions(self) -> None:
        for action_type in (
            CorporateActionType.RIGHTS_ISSUE,
            CorporateActionType.CONSOLIDATION,
            CorporateActionType.TENDER_OFFER,
            CorporateActionType.DIVIDEND,
        ):
            with self.subTest(action_type=action_type):
                self.assertIs(validate_action_type(MarketTarget.HK, action_type), action_type)

    def test_hk_rejects_us_specific_actions(self) -> None:
        for action_type in (
            CorporateActionType.SPLIT,
            CorporateActionType.MERGER,
            CorporateActionType.SPIN_OFF,
        ):
            with self.subTest(action_type=action_type):
                with self.assertRaisesRegex(ValueError, "not supported for HK"):
                    validate_action_type(MarketTarget.HK, action_type)

    def test_hk_canonical_mapping_for_hk_specific_labels(self) -> None:
        cases = {
            "供股": CorporateActionType.RIGHTS_ISSUE,
            "合股": CorporateActionType.CONSOLIDATION,
            "要约": CorporateActionType.TENDER_OFFER,
            "股息": CorporateActionType.DIVIDEND,
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                self.assertIs(canonical_action_type(MarketTarget.HK, label), expected)

    def test_hk_rejects_foreign_action_label(self) -> None:
        with self.assertRaisesRegex(ValueError, "not supported for HK"):
            canonical_action_type(MarketTarget.HK, "split")


class ValidateActionTypeUsTests(unittest.TestCase):
    """Verify United States corporate action type validation (SP 1.76)."""

    def test_us_accepts_us_specific_actions(self) -> None:
        for action_type in (
            CorporateActionType.SPLIT,
            CorporateActionType.MERGER,
            CorporateActionType.SPIN_OFF,
            CorporateActionType.DIVIDEND,
        ):
            with self.subTest(action_type=action_type):
                self.assertIs(validate_action_type(MarketTarget.US, action_type), action_type)

    def test_us_rejects_hk_specific_actions(self) -> None:
        for action_type in (
            CorporateActionType.RIGHTS_ISSUE,
            CorporateActionType.CONSOLIDATION,
            CorporateActionType.TENDER_OFFER,
        ):
            with self.subTest(action_type=action_type):
                with self.assertRaisesRegex(ValueError, "not supported for US"):
                    validate_action_type(MarketTarget.US, action_type)

    def test_us_canonical_mapping_for_us_specific_labels(self) -> None:
        cases = {
            "split": CorporateActionType.SPLIT,
            "merger": CorporateActionType.MERGER,
            "spin-off": CorporateActionType.SPIN_OFF,
            "dividend": CorporateActionType.DIVIDEND,
        }
        for label, expected in cases.items():
            with self.subTest(label=label):
                self.assertIs(canonical_action_type(MarketTarget.US, label), expected)

    def test_us_rejects_foreign_action_label(self) -> None:
        with self.assertRaisesRegex(ValueError, "not supported for US"):
            canonical_action_type(MarketTarget.US, "供股")


class AllowedActionTypesTests(unittest.TestCase):
    """Verify the per-market allowed action type surface."""

    def test_allowed_action_types_match_registry(self) -> None:
        self.assertEqual(
            allowed_action_types(MarketTarget.HK),
            frozenset(
                {
                    CorporateActionType.RIGHTS_ISSUE,
                    CorporateActionType.CONSOLIDATION,
                    CorporateActionType.TENDER_OFFER,
                    CorporateActionType.DIVIDEND,
                }
            ),
        )
        self.assertEqual(
            allowed_action_types(MarketTarget.US),
            frozenset(
                {
                    CorporateActionType.SPLIT,
                    CorporateActionType.MERGER,
                    CorporateActionType.SPIN_OFF,
                    CorporateActionType.DIVIDEND,
                }
            ),
        )
