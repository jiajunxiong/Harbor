"""Pre-release research boundary review (MVP 3 / SP 3.87).

Confirms three research boundaries hold before release (deps 3.69–3.86):

- 不创建模拟盘/券商订单 (no paper/broker orders): the entire validation path —
  the core validation modules, the validation service, the validation storage
  and the CLI — imports no broker SDK, no order-placement call, no webhook and
  no network client; a validation run only reads research data and writes local
  results (SP 3.69–3.86);
- 不访问未授权测试集 (no unauthorized test-set access): the independent test
  interval is readable only from ``TEST_LOCKED``/``EVALUATED`` (SP 3.13 / 3.24 /
  3.41) — every earlier stage is denied, parameter comparison is always denied
  (SP 3.21), and an ``evaluate`` before the lock is refused;
- 报告不含收益或回撤保证 (no return/drawdown guarantees): the OOS reports
  (json / csv / html) and the validation docs carry the research disclaimer and
  contain no return-promise or drawdown-guarantee language.

The review is DB-free and scans the actual source files, so it runs everywhere.
"""

import unittest
from datetime import datetime, timezone
from pathlib import Path

from harbor.core.holdout_registry import HoldoutRegistration
from harbor.core.test_access_guard import AccessKind, decide_access
from harbor.core.validation_domain import ValidationStatus
from harbor.core.validation_state_machine import (
    ValidationStateError,
    is_test_authorized,
    require_test_authorized,
)
from harbor.services.validation import (
    ValidationServiceError,
    _render_report,
    _report_artifact_from_rows,
    advance_validation,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CORE = _REPO_ROOT / "src" / "harbor" / "core"
_SERVICES = _REPO_ROOT / "src" / "harbor" / "services"
_STORAGE = _REPO_ROOT / "src" / "harbor" / "storage"
_CLI = _REPO_ROOT / "src" / "harbor" / "cli.py"

_VALIDATION_CORE_FILES = tuple(
    sorted(
        set(_CORE.glob("validation*.py"))
        | set(_CORE.glob("oos_*.py"))
        | set(_CORE.glob("trial_*.py"))
        | set(_CORE.glob("*_stress.py"))
        | set(_CORE.glob("coverage_*.py"))
        | set(_CORE.glob("dataset_*.py"))
        | {
            _CORE / "rolling_oos.py",
            _CORE / "stability_rule.py",
            _CORE / "conclusion_evidence.py",
            _CORE / "replay_manifest.py",
            _CORE / "multiple_trial_penalty.py",
            _CORE / "final_holdout.py",
            _CORE / "holdout_registry.py",
            _CORE / "test_access_guard.py",
            _CORE / "stress_registry.py",
            _CORE / "oos_performance.py",
        }
    )
)
_VALIDATION_PATH_FILES = _VALIDATION_CORE_FILES + (
    _SERVICES / "validation.py",
    _STORAGE / "validation_repositories.py",
    _CLI,
)

# Broker SDKs / order-placement / webhook / HTTP-trading markers (side effects).
_FORBIDDEN_SIDE_EFFECT_MARKERS = (
    "place_order(",
    "send_order(",
    "submit_order(",
    "placeorder(",
    "webhook",
    "import alpaca",
    "from alpaca",
    "ib_insync",
    "import futu",
    "from futu",
    "shinny",
    "easyquotation",
    "vnpy",
    "import ctp",
)
# Network clients: importing one into the validation path would allow external calls.
_FORBIDDEN_NETWORK_IMPORTS = (
    "import requests",
    "from requests",
    "import httpx",
    "from httpx",
    "import aiohttp",
    "from aiohttp",
    "import urllib",
    "from urllib",
    "import socket",
    "from socket",
    "import subprocess",
    "from subprocess",
)

_RETURN_PROMISE_PHRASES = ("guaranteed", "保证收益", "稳赚", "必然收益", "will return")
_DRAWDOWN_GUARANTEE_PHRASES = ("保证回撤", "回撤保证", "won't lose", "保本", "no drawdown")

_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _run_row() -> dict[str, object]:
    """A persisted validation-run row (SP 3.12), mirroring the SP 3.71 fixture."""
    return {
        "run_id": "run-1",
        "status": "EVALUATED",
        "config_hash": "cfg-hash",
        "config_snapshot": {
            "rolling": {"mode": "EXPANDING"},
            "budget": {"max_trials": 3},
            "tuning": {"primary_metric": "sharpe"},
        },
        "code_version": "1.0.0",
        "test_set_id": "holdout-1",
    }


def _split_row() -> dict[str, object]:
    return {
        "train_start": datetime(2019, 1, 1).date(),
        "train_end": datetime(2021, 12, 31).date(),
        "validation_start": datetime(2022, 1, 1).date(),
        "validation_end": datetime(2022, 12, 31).date(),
        "test_start": datetime(2023, 1, 1).date(),
        "test_end": datetime(2026, 12, 30).date(),
    }


def _manifest_row() -> dict[str, object]:
    return {
        "fingerprint": "manifest-fp",
        "markets": ["HK"],
        "base_currency": "HKD",
        "data_cutoff": datetime(2026, 12, 30).date(),
        "calendar_version": "cal-1",
        "fx_source": "fx-1",
    }


def _conclusion_row() -> dict[str, object]:
    return {
        "conclusion": "QUALIFIED",
        "evidence": {"fingerprint": "conclusion-fp"},
        "limitations": [{"message": "limited OOS horizon"}],
    }


def _artifact_kwargs() -> dict[str, object]:
    """The row inputs for the SP 3.71 artifact builder."""
    return {
        "run_row": _run_row(),
        "manifest_row": _manifest_row(),
        "split_row": _split_row(),
        "conclusion_row": _conclusion_row(),
        "trial_rows": ({"trial_id": "t-1"}, {"trial_id": "t-2"}),
        "fold_rows": (
            {"fold_index": 0, "backtest_run_id": "oos-run-0"},
            {"fold_index": 1, "backtest_run_id": "oos-run-1"},
        ),
        "stress_rows": (
            {
                "scenario_type": "cost",
                "scenario_name": "cost-stress-2x",
                "applicable_markets": ["HK"],
                "delta": {"net_value_impact_pct": -1.5},
            },
        ),
        "warning_rows": (
            {
                "warning_code": "w1",
                "severity": "warning",
                "message": "fx missing",
                "created_at": datetime(2026, 1, 1),
            },
        ),
    }


def _acceptance_artifact() -> dict[str, object]:
    """An SP 3.66-shaped report artifact as the CLI would reassemble it."""
    return _report_artifact_from_rows(**_artifact_kwargs())  # type: ignore[arg-type]


def _registration() -> HoldoutRegistration:
    return HoldoutRegistration(test_set_id="holdout-1", created_at=_AT)


class NoBrokerOrExternalSideEffectTests(unittest.TestCase):
    """The validation path never creates paper/broker orders or side effects."""

    def test_validation_path_has_no_broker_or_order_placement(self) -> None:
        for path in _VALIDATION_PATH_FILES:
            text = path.read_text(encoding="utf-8")
            for marker in _FORBIDDEN_SIDE_EFFECT_MARKERS:
                with self.subTest(file=path.name, marker=marker):
                    self.assertNotIn(marker, text)

    def test_validation_path_imports_no_network_client(self) -> None:
        for path in _VALIDATION_PATH_FILES:
            text = path.read_text(encoding="utf-8")
            for marker in _FORBIDDEN_NETWORK_IMPORTS:
                with self.subTest(file=path.name, marker=marker):
                    self.assertNotIn(marker, text)


class NoUnauthorizedTestAccessTests(unittest.TestCase):
    """The independent test set is only readable after TEST_LOCKED (SP 3.24)."""

    def test_test_access_is_denied_before_lock(self) -> None:
        for stage in (
            ValidationStatus.DRAFT,
            ValidationStatus.DATA_FROZEN,
            ValidationStatus.TUNING,
        ):
            with self.subTest(stage=stage.value):
                self.assertFalse(is_test_authorized(stage))
                with self.assertRaises(ValidationStateError):
                    require_test_authorized(stage)

    def test_test_access_is_granted_only_from_lock(self) -> None:
        for stage in (ValidationStatus.TEST_LOCKED, ValidationStatus.EVALUATED):
            with self.subTest(stage=stage.value):
                self.assertTrue(is_test_authorized(stage))
                require_test_authorized(stage)  # must not raise

    def test_evaluate_is_refused_before_lock(self) -> None:
        with self.assertRaises(ValidationServiceError) as context:
            advance_validation("run-x", ValidationStatus.TUNING, command="evaluate")
        self.assertIn("TEST_LOCKED", str(context.exception))

    def test_parameter_comparison_is_always_denied(self) -> None:
        for stage in (
            ValidationStatus.DRAFT,
            ValidationStatus.DATA_FROZEN,
            ValidationStatus.TUNING,
            ValidationStatus.TEST_LOCKED,
            ValidationStatus.EVALUATED,
        ):
            with self.subTest(stage=stage.value):
                decision = decide_access(
                    AccessKind.PARAMETER_COMPARISON,
                    registration=_registration(),
                    current_stage=stage,
                )
                self.assertFalse(decision.granted)

    def test_decide_access_denies_reads_before_lock(self) -> None:
        for kind in (
            AccessKind.DATA_READ,
            AccessKind.METRIC_COMPUTATION,
            AccessKind.REPORT_PREVIEW,
        ):
            with self.subTest(kind=kind.value):
                decision = decide_access(
                    kind,
                    registration=_registration(),
                    current_stage=ValidationStatus.DATA_FROZEN,
                )
                self.assertFalse(decision.granted)

    def test_decide_access_grants_reads_from_lock(self) -> None:
        for kind in (
            AccessKind.DATA_READ,
            AccessKind.METRIC_COMPUTATION,
            AccessKind.REPORT_PREVIEW,
        ):
            with self.subTest(kind=kind.value):
                decision = decide_access(
                    kind,
                    registration=_registration(),
                    current_stage=ValidationStatus.TEST_LOCKED,
                )
                self.assertTrue(decision.granted)

    def test_test_set_reads_are_gated_in_source(self) -> None:
        for name in (
            "validation_state_machine.py",
            "test_access_guard.py",
            "final_holdout.py",
            "holdout_registry.py",
        ):
            with self.subTest(file=name):
                self.assertIn("TEST_LOCKED", (_CORE / name).read_text(encoding="utf-8"))


class NoReturnOrDrawdownPromiseTests(unittest.TestCase):
    """Validation reports and docs never promise returns or drawdowns (SP 3.87)."""

    def test_html_report_carries_research_disclaimer(self) -> None:
        html = _render_report(_acceptance_artifact(), "html", limitations=("limited OOS horizon",))
        self.assertIn("不构成投资建议", html)
        self.assertIn("不表示未来收益或回撤", html)
        self.assertIn("research only", html)

    def test_html_report_contains_no_promise_language(self) -> None:
        html = _render_report(_acceptance_artifact(), "html", limitations=("limited OOS horizon",))
        for phrase in _RETURN_PROMISE_PHRASES + _DRAWDOWN_GUARANTEE_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, html)

    def test_json_report_contains_no_promise_language(self) -> None:
        text = _render_report(_acceptance_artifact(), "json")
        for phrase in _RETURN_PROMISE_PHRASES + _DRAWDOWN_GUARANTEE_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, text)

    def test_csv_report_contains_no_promise_language(self) -> None:
        text = _render_report(_acceptance_artifact(), "csv")
        for phrase in _RETURN_PROMISE_PHRASES + _DRAWDOWN_GUARANTEE_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, text)

    def test_validation_docs_carry_disclaimer(self) -> None:
        for relative in (
            "README.md",
            "docs/oos_method_and_limitations.md",
            "docs/mvp3_acceptance_record.md",
        ):
            with self.subTest(doc=relative):
                text = (_REPO_ROOT / relative).read_text(encoding="utf-8")
                self.assertIn("不构成投资建议", text)
        baseline = (_REPO_ROOT / "docs" / "performance_baseline.md").read_text(encoding="utf-8")
        self.assertIn("不构成", baseline)

    def test_example_validation_configs_carry_research_intent(self) -> None:
        configs_dir = _REPO_ROOT / "examples" / "configs" / "validation"
        for path in sorted(p for p in configs_dir.iterdir() if p.suffix in (".yaml", ".json")):
            with self.subTest(config=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("research", text)
                if path.suffix == ".yaml":
                    # The disclaimer comment is split across lines in the YAML header.
                    self.assertIn("不构成", text)


if __name__ == "__main__":
    unittest.main()
