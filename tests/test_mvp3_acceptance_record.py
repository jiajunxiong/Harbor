"""MVP 3 acceptance record tests (MVP 3 / SP 3.86).

Verifies that the acceptance record (``docs/mvp3_acceptance_record.md``)
固化了 命令 / 清单指纹 / 切分哈希 / 参数预算 / 测试集版本 / 运行 ID / 结论 /
未解决限制 so it is ready for MVP 4 review, and that the recorded values stay
truthful (anti-drift): the documented config hash (SP 3.3) is recomputed from
the same validation configuration, the documented dataset fingerprint (SP 3.7)
is recomputed from the same manifest, and the documented conclusion is
re-derived from the fixed stability signals + unresolved limitations
(SP 3.58 / 3.64) — any drift in the acceptance config, manifest or engine
output fails the record.
"""

import unittest
from datetime import date
from pathlib import Path

from harbor.core.backtest_domain import Currency, Market
from harbor.core.dataset_fingerprint import dataset_fingerprint
from harbor.core.dataset_manifest import build_dataset_manifest, component_manifest
from harbor.core.stability_rule import (
    StabilitySignals,
    adjudicate_stability,
    default_stability_rule,
)
from harbor.core.validation_config import (
    ConclusionRulesConfig,
    CoverageThresholdConfig,
    MetricDirection,
    RetrainFrequency,
    RollingWindowConfig,
    RollingWindowMode,
    SplitConfig,
    TuningConfig,
    ValidationConfig,
)
from harbor.core.validation_config_loader import config_hash
from harbor.core.validation_domain import (
    ManifestComponent,
    OOSConclusion,
)

_DOC = Path(__file__).resolve().parents[1] / "docs" / "mvp3_acceptance_record.md"

# Values recorded in docs/mvp3_acceptance_record.md (kept in sync, SP 3.86).
_RECORDED_CONFIG_HASH = "34bfecfe33f31c839e62729087ecb89518008050321709c6ea99258810ff6e8a"
_RECORDED_FINGERPRINT = "f2400c7e8b858b82553a9836a4411cfca039220f3ec4fbe25ba14d5fd25fbca5"
_RECORDED_RUN_ID = "mvp3-acceptance-001"
_RECORDED_TEST_SET_ID = "mvp3-acceptance-001"
_RECORDED_CONCLUSION = "INCONCLUSIVE"
_RECORDED_MAX_TRIALS = 20
_RECORDED_LIMITATION_COUNT = 2

_MANIFEST_START = date(2019, 1, 1)
_MANIFEST_END = date(2024, 12, 31)
_SPLIT_END = date(2022, 12, 31)

_LIMITATIONS = (
    "limited OOS horizon (illustrative acceptance run)",
    "no real broker order execution",
)


def _acceptance_config() -> ValidationConfig:
    """The validation configuration recorded in the doc (SP 3.2)."""
    return ValidationConfig(
        strategy="shareholder-return",
        strategy_version="1.0.0",
        description="MVP 3 acceptance record, research only",
        markets=(Market.HK,),
        base_currency=Currency.HKD,
        data_cutoff=_SPLIT_END,
        code_version="1.0.0",
        split=SplitConfig(
            train_start=date(2019, 1, 1),
            train_end=date(2020, 12, 31),
            validation_start=date(2021, 1, 1),
            validation_end=date(2021, 12, 31),
            test_start=date(2022, 1, 1),
            test_end=_SPLIT_END,
        ),
        rolling=RollingWindowConfig(
            mode=RollingWindowMode.EXPANDING,
            step_days=252,
            retrain_frequency=RetrainFrequency.EVERY_FOLD,
        ),
        tuning=TuningConfig(
            primary_metric="sharpe",
            metric_direction=MetricDirection.HIGHER_BETTER,
            max_trials=_RECORDED_MAX_TRIALS,
            random_seed=42,
            min_validation_days=63,
        ),
        coverage=CoverageThresholdConfig(),
        stress=(),
        conclusion=ConclusionRulesConfig(),
    )


def _acceptance_manifest():
    """The frozen data manifest recorded in the doc (SP 3.6/3.7)."""
    manifest = build_dataset_manifest(
        markets=(Market.HK,),
        base_currency=Currency.HKD,
        start_date=_MANIFEST_START,
        end_date=_MANIFEST_END,
        data_cutoff=_SPLIT_END,
        config_hash=_RECORDED_CONFIG_HASH,
        code_version="1.0.0",
        calendar_version="cal-1",
        fx_source="mock",
        fingerprint="placeholder",
        components=tuple(
            component_manifest(kind, "mock", "1.0", start=_MANIFEST_START, end=_MANIFEST_END)
            for kind in ManifestComponent
        ),
    )
    return manifest


def _acceptance_conclusion() -> OOSConclusion:
    """Re-derive the recorded conclusion from the fixed signals + limitations."""
    signals = StabilitySignals(
        market=Market.HK,
        dataset_fingerprint=_RECORDED_FINGERPRINT,
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
    stability = adjudicate_stability(signals, config=default_stability_rule()).conclusion
    if stability is OOSConclusion.NOT_QUALIFIED:
        return OOSConclusion.NOT_QUALIFIED
    if stability is OOSConclusion.INCONCLUSIVE or _LIMITATIONS:
        return OOSConclusion.INCONCLUSIVE
    return OOSConclusion.QUALIFIED


class AcceptanceRecordDocumentationTests(unittest.TestCase):
    """The record exists and covers the SP 3.86 acceptance dimensions."""

    def setUp(self) -> None:
        self.text = _DOC.read_text(encoding="utf-8")

    def test_documentation_covers_acceptance_dimensions(self) -> None:
        self.assertTrue(_DOC.is_file())
        for marker in (
            "命令",
            "清单指纹",
            "切分哈希",
            "参数预算",
            "测试集版本",
            "运行 ID",
            "结论",
            "未解决限制",
            "MVP 4",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.text)

    def test_recorded_values_are_stated_in_documentation(self) -> None:
        self.assertIn(_RECORDED_CONFIG_HASH, self.text)
        self.assertIn(_RECORDED_FINGERPRINT, self.text)
        self.assertIn(_RECORDED_RUN_ID, self.text)
        self.assertIn(_RECORDED_TEST_SET_ID, self.text)
        self.assertIn(_RECORDED_CONCLUSION, self.text)
        self.assertIn(str(_RECORDED_MAX_TRIALS), self.text)
        self.assertIn(str(_RECORDED_LIMITATION_COUNT), self.text)
        self.assertIn("不构成投资建议", self.text)

    def test_documents_the_cli_commands(self) -> None:
        for command in (
            "validation run",
            "validation freeze",
            "validation tune",
            "validation lock",
            "validation evaluate",
            "validation show",
            "validation report",
        ):
            with self.subTest(command=command):
                self.assertIn(command, self.text)


class AcceptanceRecordTruthfulnessTests(unittest.TestCase):
    """The recorded hash, fingerprint and conclusion match the engine (anti-drift)."""

    def test_recorded_config_hash_matches_the_acceptance_config(self) -> None:
        self.assertEqual(config_hash(_acceptance_config()), _RECORDED_CONFIG_HASH)

    def test_recorded_fingerprint_matches_the_acceptance_manifest(self) -> None:
        self.assertEqual(dataset_fingerprint(_acceptance_manifest()), _RECORDED_FINGERPRINT)

    def test_recorded_conclusion_is_rederivable(self) -> None:
        self.assertEqual(_acceptance_conclusion(), OOSConclusion(_RECORDED_CONCLUSION))
        self.assertEqual(OOSConclusion(_RECORDED_CONCLUSION), OOSConclusion.INCONCLUSIVE)

    def test_recorded_budget_and_limitations_match(self) -> None:
        config = _acceptance_config()
        self.assertEqual(config.tuning.max_trials, _RECORDED_MAX_TRIALS)
        self.assertEqual(len(_LIMITATIONS), _RECORDED_LIMITATION_COUNT)
        # A clean stability + recorded limitations degrade QUALIFIED to INCONCLUSIVE.
        self.assertNotEqual(_acceptance_conclusion(), OOSConclusion.QUALIFIED)


if __name__ == "__main__":
    unittest.main()
