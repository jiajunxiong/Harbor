"""README usage guide tests (MVP 2 / SP 2.75).

Verifies the top-level README documents the MVP 2 backtest usage guide:
dependency installation (including the backtest extra), database migration,
strategy configuration, running, status, cancel/resume, report export and
replay determinism, and that it links the shipped examples and the SP 2.73 /
SP 2.74 documentation. Guards against the guide disappearing or losing a
required topic.
"""

import unittest
from pathlib import Path

_README_PATH = Path(__file__).resolve().parents[1] / "README.md"

_REQUIRED_TOPICS = ("依赖安装", "数据库迁移", "策略配置", "运行", "报告", "重放")


class ReadmeUsageGuideTests(unittest.TestCase):
    """Verify the README covers every required usage area (SP 2.75)."""

    def setUp(self) -> None:
        self.readme = _README_PATH.read_text(encoding="utf-8")

    def test_readme_exists(self) -> None:
        self.assertTrue(_README_PATH.is_file())

    def test_covers_all_required_topics(self) -> None:
        for topic in _REQUIRED_TOPICS:
            with self.subTest(topic=topic):
                self.assertIn(topic, self.readme)

    def test_documents_backtest_dependency_install(self) -> None:
        self.assertIn(".[backtest]", self.readme)
        self.assertIn("alembic upgrade head", self.readme)

    def test_documents_all_backtest_cli_commands(self) -> None:
        for command in (
            "backtest run",
            "backtest show",
            "backtest cancel",
            "backtest resume",
            "backtest report",
        ):
            with self.subTest(command=command):
                self.assertIn(command, self.readme)

    def test_references_shipped_examples(self) -> None:
        self.assertIn("examples/configs/", self.readme)
        self.assertIn("hk_quarterly.yaml", self.readme)
        self.assertIn("cross_market_quarterly.yaml", self.readme)

    def test_links_defaults_and_limitations_docs(self) -> None:
        self.assertIn("backtest_default_parameters.md", self.readme)
        self.assertIn("backtest_limitations.md", self.readme)

    def test_states_research_only_disclaimer(self) -> None:
        self.assertIn("不构成投资建议", self.readme)

    def test_documents_replay_determinism(self) -> None:
        self.assertIn("同一研究运行", self.readme)
        self.assertIn("config_hash", self.readme)


if __name__ == "__main__":
    unittest.main()
