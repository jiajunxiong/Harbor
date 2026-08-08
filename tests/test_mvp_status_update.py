"""MVP status update tests (MVP 2 / SP 2.90).

Verifies that the README marks MVP 2 (研究回测) as 已完成 — in both the MVP
roadmap and the development-status table — and that it lists the data and
research limitations that must still be resolved before MVP 3 (样本外验证),
mirroring the `.github/mvp2.md` 前置条件. Guards against the completion claim
drifting from the acceptance record and against the status update turning the
research-only disclaimer into a return promise.
"""

import unittest
from pathlib import Path

_README_PATH = Path(__file__).resolve().parents[1] / "README.md"
_ACCEPTANCE_RECORD = Path(__file__).resolve().parents[1] / "docs" / "mvp2_acceptance_record.md"

_RECORDED_RUN_ID = "mvp2-acceptance-001"
_RECORDED_STATUS = "COMPLETED"

# MVP 3 prerequisites from `.github/mvp2.md` ⚠️ section (SP 2.90).
_MVP3_REQUIRED_PHRASES = (
    "独立保留期数据集",
    "训练、验证和测试",
    "边界在策略配置中冻结",
    "历史股票池",
    "财报可得日期",
    "企业行动条款",
    "交易日历",
    "FX",
    "覆盖范围进行量化",
    "反复调参",
    "参数搜索",
    "滚动窗口",
    "压力测试",
    "稳定性",
)


def _status_table_mvp2_row(readme: str) -> str:
    """The development-status table row for MVP 2 (no bold, unlike the roadmap)."""
    for line in readme.splitlines():
        if "MVP 2：研究回测" in line and "**" not in line:
            return line
    raise AssertionError("MVP 2 development-status row not found in README")


class Mvp2MarkedCompleteTests(unittest.TestCase):
    """The README marks MVP 2 as completed (SP 2.90)."""

    def setUp(self) -> None:
        self.readme = _README_PATH.read_text(encoding="utf-8")

    def test_status_table_marks_mvp2_completed(self) -> None:
        row = _status_table_mvp2_row(self.readme)
        self.assertIn("已完成", row)
        self.assertIn("✅", row)
        self.assertNotIn("进行中", row)

    def test_roadmap_marks_mvp2_completed(self) -> None:
        row = next(line for line in self.readme.splitlines() if "**MVP 2：研究回测**" in line)
        self.assertIn("已完成", row)

    def test_completion_note_carries_research_disclaimer(self) -> None:
        """Marking MVP 2 done must not introduce a return promise (SP 2.89)."""
        self.assertIn("不构成投资建议", self.readme)
        self.assertIn("不表示未来收益或回撤", self.readme)


class Mvp3PrerequisiteListTests(unittest.TestCase):
    """The README lists what must be resolved before MVP 3 (SP 2.90)."""

    def setUp(self) -> None:
        self.readme = _README_PATH.read_text(encoding="utf-8")

    def test_lists_all_mvp3_prerequisite_topics(self) -> None:
        for phrase in _MVP3_REQUIRED_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.readme)

    def test_links_the_prerequisite_sources(self) -> None:
        self.assertIn("docs/backtest_limitations.md", self.readme)
        self.assertIn("docs/mvp2_acceptance_record.md", self.readme)
        self.assertIn(".github/mvp2.md", self.readme)

    def test_explicitly_defers_tuning_to_mvp3(self) -> None:
        self.assertIn("MVP 3 独立完成", self.readme)


class CompletionBackedByAcceptanceTests(unittest.TestCase):
    """The completion claim is backed by the SP 2.87 acceptance record."""

    def test_acceptance_record_exists_and_is_completed(self) -> None:
        self.assertTrue(_ACCEPTANCE_RECORD.is_file())
        text = _ACCEPTANCE_RECORD.read_text(encoding="utf-8")
        self.assertIn(_RECORDED_RUN_ID, text)
        self.assertIn(_RECORDED_STATUS, text)

    def test_readme_completion_note_references_acceptance_record(self) -> None:
        readme = _README_PATH.read_text(encoding="utf-8")
        self.assertIn("mvp2_acceptance_record.md", readme)
        self.assertIn("已完成", readme)


if __name__ == "__main__":
    unittest.main()
