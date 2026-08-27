"""README validation usage guide tests (MVP 3 / SP 3.74).

Verifies the top-level README documents the MVP 3 sample-out-of-sample usage
guide: validation prerequisites (验证前置条件), running (运行), reporting
(报告), replay (重放) and the "not qualified ≠ re-tune and claim valid"
workflow (未通过不等于可调参重测), and that it links the shipped validation
examples (SP 3.72) and the OOS method/limitation documentation (SP 3.73).
Guards against the guide disappearing or losing a required topic.
"""

import unittest
from pathlib import Path

_README_PATH = Path(__file__).resolve().parents[1] / "README.md"

_REQUIRED_TOPICS = (
    "验证前置条件",
    "运行",
    "报告",
    "重放",
    "未通过不等于可调参重测",
)


class ReadmeValidationUsageGuideTests(unittest.TestCase):
    """Verify the README covers every required MVP 3 usage area (SP 3.74)."""

    def setUp(self) -> None:
        self.readme = _README_PATH.read_text(encoding="utf-8")

    def test_readme_exists(self) -> None:
        self.assertTrue(_README_PATH.is_file())

    def test_covers_all_required_topics(self) -> None:
        for topic in _REQUIRED_TOPICS:
            with self.subTest(topic=topic):
                self.assertIn(topic, self.readme)

    def test_documents_all_validation_cli_commands(self) -> None:
        for command in (
            "validation run",
            "validation freeze",
            "validation tune",
            "validation evaluate",
            "validation show",
            "validation report",
        ):
            with self.subTest(command=command):
                self.assertIn(command, self.readme)

    def test_documents_the_state_machine_order(self) -> None:
        self.assertIn("DRAFT → DATA_FROZEN → TUNING → TEST_LOCKED → EVALUATED", self.readme)
        self.assertIn("顺序违反状态机时给出可行动错误", self.readme)

    def test_documents_prerequisites(self) -> None:
        for marker in ("冻结切分", "一次性解锁", "预注册", "config_hash"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.readme)

    def test_references_shipped_validation_examples(self) -> None:
        self.assertIn("examples/configs/validation/", self.readme)
        self.assertIn("hk_validation.yaml", self.readme)
        self.assertIn("cross_market_validation.yaml", self.readme)

    def test_links_oos_method_documentation(self) -> None:
        self.assertIn("oos_method_and_limitations.md", self.readme)

    def test_documents_report_formats(self) -> None:
        for marker in ("--format json", "--format csv", "--format html"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.readme)

    def test_documents_replay_determinism(self) -> None:
        for marker in ("dataset_fingerprint", "config_hash", "随机种子"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.readme)

    def test_documents_conclusion_vocabulary(self) -> None:
        for marker in ("QUALIFIED", "NOT_QUALIFIED", "INCONCLUSIVE"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.readme)

    def test_states_not_qualified_is_not_retune_and_claim(self) -> None:
        self.assertIn("不等于“调参后重测即通过”", self.readme)
        self.assertIn("新的测试集版本 + 新的验证运行", self.readme)
        self.assertIn("SP 3.42", self.readme)

    def test_states_inconclusive_is_not_a_pass(self) -> None:
        self.assertIn("INCONCLUSIVE 表示证据不足，不是通过", self.readme)

    def test_states_research_only_disclaimer(self) -> None:
        self.assertIn("不构成投资建议", self.readme)


if __name__ == "__main__":
    unittest.main()
