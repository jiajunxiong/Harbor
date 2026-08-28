"""MVP 3 status update tests (MVP 3 / SP 3.88).

Verifies that the README marks MVP 3 (样本外验证) as 已完成 — in both the MVP
roadmap and the development-status table — and that it lists the paper-trading
(模拟盘), risk-approval (风控审批) and live-diff (实盘差异验证) conditions that
must be satisfied before MVP 4, mirroring the `.github/mvp3.md` ⚠️ 前置条件.
Guards against the completion claim drifting from the acceptance record and
against the status update turning the research-only disclaimer into a return
promise (SP 3.87).
"""

import unittest
from pathlib import Path

_README_PATH = Path(__file__).resolve().parents[1] / "README.md"
_ACCEPTANCE_RECORD = Path(__file__).resolve().parents[1] / "docs" / "mvp3_acceptance_record.md"

_RECORDED_RUN_ID = "mvp3-acceptance-001"
_RECORDED_CONCLUSION = "INCONCLUSIVE"

# MVP 4 prerequisites from `.github/mvp3.md` ⚠️ section (SP 3.88).
_MVP4_REQUIRED_PHRASES = (
    "模拟盘",
    "风控审批",
    "实盘差异验证",
    "日熔断",
    "月熔断",
    "可审计",
    "可重放",
    "券商凭据",
    "信号→订单映射",
    "外部下单",
    "INCONCLUSIVE",
    "NOT_QUALIFIED",
)


def _status_table_mvp3_row(readme: str) -> str:
    """The development-status table row for MVP 3 (no bold, unlike the roadmap)."""
    for line in readme.splitlines():
        if "MVP 3：样本外验证" in line and "**" not in line:
            return line
    raise AssertionError("MVP 3 development-status row not found in README")


class Mvp3MarkedCompleteTests(unittest.TestCase):
    """The README marks MVP 3 as completed (SP 3.88)."""

    def setUp(self) -> None:
        self.readme = _README_PATH.read_text(encoding="utf-8")

    def test_status_table_marks_mvp3_completed(self) -> None:
        row = _status_table_mvp3_row(self.readme)
        self.assertIn("已完成", row)
        self.assertIn("✅", row)
        self.assertNotIn("进行中", row)

    def test_roadmap_marks_mvp3_completed(self) -> None:
        row = next(line for line in self.readme.splitlines() if "**MVP 3：样本外验证**" in line)
        self.assertIn("已完成", row)

    def test_completion_note_carries_research_disclaimer(self) -> None:
        """Marking MVP 3 done must not introduce a return promise (SP 3.87)."""
        self.assertIn("不构成投资建议", self.readme)
        self.assertIn("不表示未来收益或回撤", self.readme)


class Mvp4PrerequisiteListTests(unittest.TestCase):
    """The README lists what must be satisfied before MVP 4 (SP 3.88)."""

    def setUp(self) -> None:
        self.readme = _README_PATH.read_text(encoding="utf-8")

    def test_lists_all_mvp4_prerequisite_topics(self) -> None:
        for phrase in _MVP4_REQUIRED_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.readme)

    def test_links_the_prerequisite_sources(self) -> None:
        self.assertIn("docs/mvp3_acceptance_record.md", self.readme)
        self.assertIn("docs/oos_method_and_limitations.md", self.readme)
        self.assertIn(".github/mvp3.md", self.readme)

    def test_explicitly_defers_trading_to_mvp4(self) -> None:
        self.assertIn("外部下单和自动实盘交易仍不属于该阶段", self.readme)


class CompletionBackedByAcceptanceTests(unittest.TestCase):
    """The completion claim is backed by the SP 3.86 acceptance record."""

    def test_acceptance_record_exists_and_is_inconclusive(self) -> None:
        self.assertTrue(_ACCEPTANCE_RECORD.is_file())
        text = _ACCEPTANCE_RECORD.read_text(encoding="utf-8")
        self.assertIn(_RECORDED_RUN_ID, text)
        self.assertIn(_RECORDED_CONCLUSION, text)
        self.assertIn("不构成投资建议", text)

    def test_readme_completion_note_references_acceptance_record(self) -> None:
        readme = _README_PATH.read_text(encoding="utf-8")
        self.assertIn("mvp3_acceptance_record.md", readme)
        self.assertIn("已完成", readme)


if __name__ == "__main__":
    unittest.main()
