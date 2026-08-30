"""Docker paper smoke test (MVP 4 / SP 4.92).

Brings up the full compose stack (postgres + redis) in an isolated project,
migrates a fresh database to head, runs the complete paper CLI flow — init ->
start -> signal -> order list -> approve -> reconcile -> report (JSON/CSV/HTML)
-> status -> stop — and verifies the outputs are traceable and reconcilable.
This is the Docker-equivalent of the paper acceptance flow: 迁移 → 模拟盘运行 →
对账 → 报告导出. Skipped when the Docker CLI or compose plugin is unavailable.

The paper CLI derives orders from explicitly-passed prices, so no Mock data
preparation is needed (unlike the backtest smoke).
"""

import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from harbor.cli import main

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_POSTGRES_PORT = "5439"
_REDIS_PORT = "6385"

_PAPER_YAML = """\
strategy: paper-demo
strategy_version: "1.0.0"
description: "docker paper smoke, research only"
markets:
  - HK
base_currency: HKD
currencies:
  - HKD
initial_capital: 1000000
rebalance_frequency: QUARTERLY
run_mode: MANUAL
risk:
  max_position_pct: 0.005
  max_single_stock_pct: 0.05
  max_industry_pct: 0.20
stop:
  max_days: 365
"""


def _docker_available() -> bool:
    """Return whether the docker CLI and compose plugin are usable."""
    if shutil.which("docker") is None:
        return False
    try:
        probe = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return probe.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


@unittest.skipUnless(_docker_available(), "Docker is not available")
class DockerPaperSmokeTests(unittest.TestCase):
    """SP 4.92: migrate, run the paper flow, reconcile and export reports."""

    def test_docker_paper_flow(self) -> None:
        project = f"harbor-paper-{uuid.uuid4().hex[:8]}"
        database_url = f"postgresql+psycopg://harbor:harbor@localhost:{_POSTGRES_PORT}/harbor"
        compose_env = {
            **os.environ,
            "POSTGRES_PORT": _POSTGRES_PORT,
            "REDIS_PORT": _REDIS_PORT,
        }
        cli_env = {
            "DATABASE_URL": database_url,
            "DATA_PROVIDER_HK": "mock",
            "DATA_PROVIDER_US": "mock",
            "LOG_LEVEL": "ERROR",
        }

        def compose(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["docker", "compose", "-p", project, *args],
                cwd=str(_PROJECT_ROOT),
                env=compose_env,
                capture_output=True,
                text=True,
                timeout=300,
            )

        try:
            up = compose("up", "-d")
            self.assertEqual(up.returncode, 0, up.stderr)
            self.assertTrue(self._wait_for_postgres(compose), "postgres did not become ready")

            # 迁移 (migration, SP 4.89 / 4.92).
            self._migrate(database_url)

            # 模拟盘运行 (paper run, SP 4.84).
            with tempfile.TemporaryDirectory() as tmp:
                config_path = Path(tmp) / "paper.yaml"
                config_path.write_text(_PAPER_YAML, encoding="utf-8")
                init_output = self._cli(
                    cli_env,
                    [
                        "paper",
                        "init",
                        "--config",
                        str(config_path),
                        "--dataset-fingerprint",
                        "dataset-docker",
                    ],
                )
            init = json.loads(init_output)
            run_id = init["run_id"]
            self.assertEqual(init["status"], "DRAFT")

            start = json.loads(
                self._cli(cli_env, ["paper", "start", run_id, "--approver", "smoke"])
            )
            self.assertEqual(start["status"], "ACTIVE")

            # 订单 (signal -> orders, SP 4.85).
            signal = json.loads(
                self._cli(
                    cli_env,
                    [
                        "paper",
                        "signal",
                        run_id,
                        "--rebalance-date",
                        "2026-01-02",
                        "--target",
                        "0001.HK:0.5",
                        "--price",
                        "0001.HK:50.0",
                    ],
                )
            )
            self.assertEqual(signal["order_count"], 1)
            orders = json.loads(self._cli(cli_env, ["paper", "order", "list", run_id]))
            self.assertEqual(len(orders), 1)
            order_id = orders[0]["order_id"]

            # 审批 (approval, SP 4.86).
            approved = json.loads(
                self._cli(
                    cli_env,
                    ["paper", "approve", run_id, "--order-id", order_id, "--approver", "smoke"],
                )
            )
            self.assertEqual(approved["status"], "APPROVED")

            # 对账 (reconcile, SP 4.87): no net value yet -> difference recorded.
            reconcile = json.loads(
                self._cli(cli_env, ["paper", "reconcile", run_id, "--as-of", "2026-01-02"])
            )
            self.assertFalse(reconcile["reconciled"])
            self.assertGreaterEqual(reconcile["difference_count"], 1)

            # 报告导出 (report export: JSON, CSV, HTML, SP 4.87).
            json_report = self._cli(cli_env, ["paper", "report", run_id, "--format", "json"])
            payload = json.loads(json_report)
            self.assertEqual(payload["status"]["run_id"], run_id)
            self.assertEqual(len(payload["orders"]), 1)
            csv_report = self._cli(cli_env, ["paper", "report", run_id, "--format", "csv"])
            self.assertIn("order_id,market,symbol", csv_report)
            html_report = self._cli(cli_env, ["paper", "report", run_id, "--format", "html"])
            self.assertIn("<!doctype html>", html_report)

            # 状态与停止 (status / stop, SP 4.84).
            status = json.loads(self._cli(cli_env, ["paper", "status", run_id]))
            self.assertEqual(status["status"], "ACTIVE")
            self.assertEqual(status["order_count"], 1)
            stopped = json.loads(self._cli(cli_env, ["paper", "stop", run_id]))
            self.assertEqual(stopped["status"], "STOPPED")
        finally:
            compose("down", "-v")

    def _wait_for_postgres(self, compose: Callable[..., subprocess.CompletedProcess[str]]) -> bool:
        deadline = time.time() + 120
        while time.time() < deadline:
            result = compose("exec", "-T", "postgres", "pg_isready", "-U", "harbor", "-d", "harbor")
            if result.returncode == 0:
                return True
            time.sleep(2)
        return False

    def _migrate(self, database_url: str) -> None:
        from alembic.config import Config

        from alembic import command

        config = Config(str(_PROJECT_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(_PROJECT_ROOT / "alembic"))
        original_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = database_url
        try:
            command.upgrade(config, "head")
        finally:
            if original_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = original_url

    def _cli(self, env: dict[str, str], argv: list[str]) -> str:
        """Run a Harbor CLI command with the given environment, returning stdout."""
        output = io.StringIO()
        with (
            patch.dict(os.environ, env, clear=True),
            redirect_stdout(output),
            redirect_stderr(io.StringIO()),
        ):
            exit_code = main(argv)
        self.assertEqual(exit_code, 0, f"CLI {argv} failed")
        return output.getvalue()
