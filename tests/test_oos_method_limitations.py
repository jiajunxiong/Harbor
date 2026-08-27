"""Out-of-sample method and limitation documentation tests (MVP 3 / SP 3.73).

Verifies ``docs/oos_method_and_limitations.md`` documents the five required
areas — 切分规则 (split rules), 测试集一次性访问 (test-set one-time access),
数据覆盖口径 (data coverage criteria), 压力假设 (stress assumptions) and
INCONCLUSIVE 含义 (meaning of INCONCLUSIVE) — and states the research-only
disclaimer. Also cross-checks the two documented claims against real code:
(1) SP 3.58 ``adjudicate_stability`` returns ``INCONCLUSIVE`` when evidence is
missing but nothing fails (never a pass), and (2) the SP 3.68 OOS report
carries the prominent research-only banner the doc references.
"""

import unittest
from pathlib import Path

from harbor.core.backtest_domain import Market
from harbor.core.oos_report import _RESEARCH_BANNER
from harbor.core.stability_rule import (
    StabilitySignals,
    adjudicate_stability,
    default_stability_rule,
)
from harbor.core.validation_domain import OOSConclusion

_DOC_PATH = Path(__file__).resolve().parents[1] / "docs" / "oos_method_and_limitations.md"

_REQUIRED_TOPICS = (
    "切分规则",
    "测试集一次性访问",
    "数据覆盖口径",
    "压力假设",
    "INCONCLUSIVE",
)


class OosMethodDocumentationTests(unittest.TestCase):
    """Verify the method/limitation document covers every required area."""

    def setUp(self) -> None:
        self.doc = _DOC_PATH.read_text(encoding="utf-8")

    def test_doc_exists(self) -> None:
        self.assertTrue(_DOC_PATH.is_file())

    def test_doc_covers_all_required_topics(self) -> None:
        for topic in _REQUIRED_TOPICS:
            with self.subTest(topic=topic):
                self.assertIn(topic, self.doc)

    def test_doc_states_research_only(self) -> None:
        self.assertIn("不构成投资建议", self.doc)
        self.assertIn("仅用于研究", self.doc)
        self.assertIn("不表示未来收益或回撤", self.doc)

    def test_doc_explains_split_rules(self) -> None:
        for marker in ("train_end", "validation_start", "test_start", "SP 3.4", "冻结"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.doc)

    def test_doc_explains_one_time_test_access(self) -> None:
        for marker in ("TEST_LOCKED", "一次性", "解锁", "SP 3.41", "SP 3.42"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.doc)

    def test_doc_explains_coverage_criteria(self) -> None:
        for marker in ("SP 3.9", "SP 3.10", "覆盖率", "fx_required", "历史股票池"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.doc)

    def test_doc_explains_stress_assumptions(self) -> None:
        for marker in ("SP 3.59", "假设", "cost_multiplier", "slippage_bps"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.doc)

    def test_doc_explains_inconclusive_meaning(self) -> None:
        for marker in ("INCONCLUSIVE", "QUALIFIED", "NOT_QUALIFIED", "SP 3.58", "视为通过"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.doc)

    def test_doc_references_report_presentation(self) -> None:
        for marker in ("SP 3.68", "no promise of future returns"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.doc)


class InconclusiveSemanticsTests(unittest.TestCase):
    """Cross-check the documented INCONCLUSIVE rule against SP 3.58 code."""

    def setUp(self) -> None:
        self.config = default_stability_rule()

    def _signals(self, **overrides) -> StabilitySignals:
        fields = dict(
            market=Market.HK,
            dataset_fingerprint="fp",
            code_version="1.0.0",
            fold_spread=0.05,
            fold_count=4,
            fold_failure_count=0,
            neighborhood_cliff_ratio=0.1,
            neighborhood_infeasible_ratio=0.0,
            environment_insufficient_ratio=0.0,
            max_stress_loss_pct=2.0,
            stress_unquantifiable=False,
            coverage_blocked=False,
        )
        fields.update(overrides)
        return StabilitySignals(**fields)  # type: ignore[arg-type]

    def test_missing_evidence_is_inconclusive_not_a_pass(self) -> None:
        # Fold dispersion evidence missing but nothing fails -> INCONCLUSIVE.
        conclusion = adjudicate_stability(self._signals(fold_spread=None), config=self.config)
        self.assertEqual(conclusion.conclusion, OOSConclusion.INCONCLUSIVE)
        self.assertNotEqual(conclusion.conclusion, OOSConclusion.QUALIFIED)

    def test_all_pass_is_qualified(self) -> None:
        conclusion = adjudicate_stability(self._signals(), config=self.config)
        self.assertEqual(conclusion.conclusion, OOSConclusion.QUALIFIED)

    def test_any_fail_dominates_to_not_qualified(self) -> None:
        conclusion = adjudicate_stability(self._signals(fold_spread=0.5), config=self.config)
        self.assertEqual(conclusion.conclusion, OOSConclusion.NOT_QUALIFIED)


class ReportBannerTests(unittest.TestCase):
    """Cross-check the documented research banner against SP 3.68 code."""

    def test_banner_states_research_only(self) -> None:
        self.assertIn("不构成投资建议", _RESEARCH_BANNER)
        self.assertIn("仅用于研究", _RESEARCH_BANNER)
        self.assertIn("no promise of future returns", _RESEARCH_BANNER)

    def test_doc_matches_the_banner_phrasing(self) -> None:
        doc = _DOC_PATH.read_text(encoding="utf-8")
        self.assertIn("仅用于研究，不构成投资建议，也不表示未来收益或回撤", doc)


if __name__ == "__main__":
    unittest.main()
