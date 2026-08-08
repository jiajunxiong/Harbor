"""Docker backtest smoke test (MVP 2 / SP 2.86).

Brings up the full compose stack (postgres + redis) in an isolated project,
migrates a fresh database to head, prepares a small Mock dataset (securities +
daily quotes for the whole Hong Kong pool), runs a backtest from a versioned
config, exports the run report (JSON and HTML) and queries the run back
(SP 2.67-2.69). This is the Docker-equivalent of the acceptance flow: 迁移 →
Mock 数据准备 → 回测 → 报告导出 → 结果查询. Skipped when the Docker CLI or
compose plugin is unavailable.

The smoke config zeroes the cost model so the full-equity equal-weight buy of
the 16-symbol pool fits exactly within the initial capital (the CLI runs the
end-to-end runner with the default equal-weight, cash_weight=0.0 rule; any fee
would overdraw the last buy, SP 2.51 / 2.67).
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
_POSTGRES_PORT = "5435"
_REDIS_PORT = "6381"

_START = "2024-01-01"
_END = "2024-01-08"

_HK_SYMBOLS = (
    "0001.HK",
    "0002.HK",
    "0003.HK",
    "0005.HK",
    "0011.HK",
    "0016.HK",
    "0017.HK",
    "0027.HK",
    "0066.HK",
    "0123.HK",
    "0388.HK",
    "0700.HK",
    "0883.HK",
    "0939.HK",
    "0941.HK",
    "0998.HK",
)

_CONFIG_YAML = """\
strategy: shareholder-return
strategy_version: "1.0.0"
description: "docker backtest smoke, research only"
markets:
  - HK
market_quotas:
  - market: HK
    target_count: 2
    weight: 1.0
start_date: "2024-01-01"
end_date: "2024-01-08"
base_currency: HKD
rebalance_frequency: quarterly
initial_capital: 1000000
cost:
  commission_rate: 0
  min_commission: 0
  stamp_duty_rate: 0
  transaction_levy_rate: 0
  trading_fee_rate: 0
  regulatory_fee_rate: 0
  slippage_bps: 0
  lot_size: 100
risk:
  max_position_pct: 1.0
  max_market_pct: 1.0
  min_cash_pct: 0.0
fill:
  fill_rule: close
volume:
  participation_rate: 0.2
  on_unfilled: cancel
suspension:
  valuation: last_price
  warn: true
dividend:
  include_special: true
benchmark:
  kind: cash
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
class DockerBacktestSmokeTests(unittest.TestCase):
    """SP 2.86: migrate, prepare Mock data, run a backtest, export and query."""

    def test_docker_backtest_flow(self) -> None:
        project = f"harbor-bt-{uuid.uuid4().hex[:8]}"
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

            # 迁移 (migration).
            self._migrate(database_url)

            # Mock 数据准备 (mock data prep): the whole HK pool + daily quotes.
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

            # 回测 (backtest run).
            with tempfile.TemporaryDirectory() as tmp:
                config_path = Path(tmp) / "smoke.yaml"
                config_path.write_text(_CONFIG_YAML, encoding="utf-8")
                run_output = self._cli(cli_env, ["backtest", "run", "--config", str(config_path)])
            run = json.loads(run_output)
            run_id = run["run_id"]
            self.assertEqual(run["status"], "COMPLETED")

            # 报告导出 (report export: JSON and HTML).
            json_report = self._cli(cli_env, ["backtest", "report", run_id, "--format", "json"])
            artifact = json.loads(json_report)
            self.assertEqual(artifact["run"]["run_id"], run_id)
            self.assertTrue(artifact["net_values"])
            html_report = self._cli(cli_env, ["backtest", "report", run_id, "--format", "html"])
            self.assertIn("<html", html_report)
            self.assertIn(run_id, html_report)

            # 结果查询 (result query).
            show = json.loads(self._cli(cli_env, ["backtest", "show", run_id]))
            self.assertEqual(show["run_id"], run_id)
            self.assertEqual(show["status"], "COMPLETED")
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
        self.assertEqual(exit_code, 0)
        return output.getvalue()


if __name__ == "__main__":
    unittest.main()
