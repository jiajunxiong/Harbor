"""Docker validation smoke test (MVP 3 / SP 3.85).

Brings up the full compose stack (postgres + redis) in an isolated project,
migrates a fresh database to head (including the SP 3.12 validation tables),
prepares a small Mock dataset (securities + daily quotes), then runs the
validation CLI acceptance flow — 迁移 → Mock 数据准备 → 冻结 → 调参 → 最终评估 →
报告导出: ``validation run`` (DRAFT), ``freeze`` (DATA_FROZEN), ``tune`` (TUNING),
the final-evaluation gate (``evaluate`` from TUNING gives an actionable order
error — the holdout is protected until its test set is locked), then after the
test-set lock the run completes evaluation and the report is exported
(``show`` / ``report --format json``). Skipped when the Docker CLI or compose
plugin is unavailable.

The CLI exposes the full state chain — ``run`` (DRAFT, persisted with its
frozen split), ``freeze`` (DATA_FROZEN), ``tune`` (TUNING), ``lock``
(TEST_LOCKED) and ``evaluate`` (EVALUATED) — and ``show`` / ``report``
(SP 3.69–3.71); the smoke test exercises each command through the real CLI.
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
_POSTGRES_PORT = "5436"
_REDIS_PORT = "6382"

_START = "2024-01-01"
_END = "2024-01-08"

_HK_SYMBOLS = ("0001.HK", "0002.HK", "0700.HK")

_CONFIG_YAML = """\
strategy: shareholder-return
strategy_version: "1.0.0"
description: "docker validation smoke, research only"
markets:
  - HK
base_currency: HKD
data_cutoff: "2024-01-08"
code_version: "1.0.0"
split:
  train_start: "2024-01-01"
  train_end: "2024-01-02"
  validation_start: "2024-01-03"
  validation_end: "2024-01-04"
  test_start: "2024-01-05"
  test_end: "2024-01-08"
rolling:
  mode: expanding
  step_days: 252
  retrain_frequency: every_fold
tuning:
  primary_metric: sharpe
  metric_direction: higher_better
  max_trials: 3
  random_seed: 42
  min_validation_days: 1
coverage:
  min_price_coverage_pct: 95.0
  min_stock_pool_coverage_pct: 90.0
  min_fundamental_coverage_pct: 70.0
  fx_required: true
  historical_stock_pool_required: true
  action_terms_required: true
stress:
  - name: cost-stress-2x
    cost_multiplier: 2.0
    slippage_bps: 0
    participation_rate: 0.1
    fx_shift_bps: 0
conclusion:
  min_qualified_fold_ratio: 0.8
  max_allowed_drawdown_pct: 30.0
  max_allowed_stress_drawdown_pct: 40.0
  max_parameter_dispersion: 0.5
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
class DockerValidationSmokeTests(unittest.TestCase):
    """SP 3.85: migrate, prepare Mock data, freeze, tune, evaluate, export."""

    def test_docker_validation_flow(self) -> None:
        project = f"harbor-val-{uuid.uuid4().hex[:8]}"
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

            # 迁移 (migration): head now includes the SP 3.12 validation tables.
            self._migrate(database_url)

            # Mock 数据准备 (mock data prep): a small HK pool + daily quotes.
            self._cli(cli_env, ["fetch", "securities", "--market", "HK"])
            for symbol in _HK_SYMBOLS:
                self._cli(
                    cli_env,
                    [
                        "fetch",
                        "daily",
                        "--market",
                        "HK",
                        "--symbol",
                        symbol,
                        "--start",
                        _START,
                        "--end",
                        _END,
                    ],
                )

            # 冻结 (freeze): create a DRAFT run and freeze the dataset/split.
            with tempfile.TemporaryDirectory() as tmp:
                config_path = Path(tmp) / "smoke.yaml"
                config_path.write_text(_CONFIG_YAML, encoding="utf-8")
                run_output = self._cli(cli_env, ["validation", "run", "--config", str(config_path)])
                run = json.loads(run_output)
                run_id = run["run_id"]
                self.assertEqual(run["status"], "DRAFT")

            frozen = json.loads(self._cli(cli_env, ["validation", "freeze", run_id]))
            self.assertEqual(frozen["status"], "DATA_FROZEN")

            # 调参 (tune): begin parameter tuning.
            tuned = json.loads(self._cli(cli_env, ["validation", "tune", run_id]))
            self.assertEqual(tuned["status"], "TUNING")

            # 最终评估 gate: evaluate from TUNING is refused (test set not locked).
            stderr = self._cli_error(cli_env, ["validation", "evaluate", run_id])
            self.assertIn("Validation evaluate failed", stderr)
            self.assertIn("TEST_LOCKED", stderr)

            # 测试集锁定 (test-set lock, SP 3.13) + 最终评估 (final evaluation).
            locked = json.loads(self._cli(cli_env, ["validation", "lock", run_id]))
            self.assertEqual(locked["status"], "TEST_LOCKED")

            evaluated = json.loads(self._cli(cli_env, ["validation", "evaluate", run_id]))
            self.assertEqual(evaluated["status"], "EVALUATED")

            # 结果查询 + 报告导出 (result query + report export).
            show = json.loads(self._cli(cli_env, ["validation", "show", run_id]))
            self.assertEqual(show["run_id"], run_id)
            self.assertEqual(show["status"], "EVALUATED")
            self.assertEqual(show["split"]["train_start"], "2024-01-01")

            report = json.loads(
                self._cli(cli_env, ["validation", "report", run_id, "--format", "json"])
            )
            self.assertEqual(report["run"]["run_id"], run_id)
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
        """Run a Harbor CLI command, returning stdout (asserts exit 0)."""
        output = io.StringIO()
        with (
            patch.dict(os.environ, env, clear=True),
            redirect_stdout(output),
            redirect_stderr(io.StringIO()),
        ):
            exit_code = main(argv)
        self.assertEqual(exit_code, 0)
        return output.getvalue()

    def _cli_error(self, env: dict[str, str], argv: list[str]) -> str:
        """Run a Harbor CLI command expecting SystemExit(2), returning stderr."""
        stderr = io.StringIO()
        with (
            patch.dict(os.environ, env, clear=True),
            redirect_stdout(io.StringIO()),
            redirect_stderr(stderr),
        ):
            with self.assertRaises(SystemExit) as context:
                main(argv)
        self.assertEqual(context.exception.code, 2)
        return stderr.getvalue()


if __name__ == "__main__":
    unittest.main()
