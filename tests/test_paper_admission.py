"""Strategy admission tests (MVP 4 / SP 4.3).

Verifies that only MVP 3 ``QUALIFIED`` strategies enter the paper loop, that
``INCONCLUSIVE`` / ``NOT_QUALIFIED`` are rejected with an explicit reason, and
that the admission registry is immutable, deterministic and rejects duplicate
or out-of-order registrations.
"""

import unittest
from datetime import datetime, timezone

from harbor.core.paper_admission import (
    AdmissionDecision,
    AdmissionRegistry,
    AdmissionRegistryError,
    StrategyAdmission,
    admit_strategy,
)
from harbor.core.validation_domain import OOSConclusion

_ADMITTED_AT = datetime(2026, 1, 1, 9, tzinfo=timezone.utc)


def _admit(conclusion: OOSConclusion, **overrides: object) -> StrategyAdmission:
    """Admit/reject a strategy with overridable kwargs."""
    fields: dict[str, object] = {
        "strategy": "mvp3-qualified",
        "strategy_version": "1.0.0",
        "conclusion": conclusion,
        "source_run_id": "mvp3-run-1",
        "dataset_fingerprint": "fp-1",
        "admitted_at": _ADMITTED_AT,
    }
    fields.update(overrides)
    return admit_strategy(**fields)  # type: ignore[arg-type]


class AdmitStrategyTests(unittest.TestCase):
    """The admission decision rule (SP 4.3)."""

    def test_qualified_admitted(self) -> None:
        admission = _admit(OOSConclusion.QUALIFIED)
        self.assertEqual(admission.decision, AdmissionDecision.ADMITTED)
        self.assertIn("QUALIFIED", admission.reason)
        self.assertEqual(admission.source_run_id, "mvp3-run-1")
        self.assertEqual(admission.dataset_fingerprint, "fp-1")
        self.assertIn("ADMITTED", admission.readable())

    def test_inconclusive_rejected(self) -> None:
        admission = _admit(OOSConclusion.INCONCLUSIVE)
        self.assertEqual(admission.decision, AdmissionDecision.REJECTED)
        self.assertIn("INCONCLUSIVE", admission.reason)
        self.assertIn("insufficient", admission.reason.lower())

    def test_not_qualified_rejected(self) -> None:
        admission = _admit(OOSConclusion.NOT_QUALIFIED)
        self.assertEqual(admission.decision, AdmissionDecision.REJECTED)
        self.assertIn("NOT_QUALIFIED", admission.reason)

    def test_versioned_and_identity(self) -> None:
        admission = _admit(OOSConclusion.QUALIFIED, strategy_version="2.1.0")
        self.assertEqual(admission.strategy_version, "2.1.0")

    def test_empty_identity_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _admit(OOSConclusion.QUALIFIED, strategy="")
        with self.assertRaises(ValueError):
            _admit(OOSConclusion.QUALIFIED, strategy_version="")

    def test_naive_timestamp_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _admit(OOSConclusion.QUALIFIED, admitted_at=datetime(2026, 1, 1, 9))

    def test_timestamp_defaults_to_utc_now(self) -> None:
        admission = admit_strategy(
            strategy="s", strategy_version="1", conclusion=OOSConclusion.QUALIFIED
        )
        self.assertIsNotNone(admission.admitted_at.utcoffset())
        self.assertEqual(admission.admitted_at.utcoffset().total_seconds(), 0.0)

    def test_conclusion_recorded(self) -> None:
        admission = _admit(OOSConclusion.INCONCLUSIVE)
        self.assertEqual(admission.conclusion, OOSConclusion.INCONCLUSIVE)


class AdmissionRegistryTests(unittest.TestCase):
    """The immutable admission registry (SP 4.3)."""

    def test_register_and_query(self) -> None:
        registry = AdmissionRegistry()
        qualified = _admit(OOSConclusion.QUALIFIED, strategy="alpha", strategy_version="1.0.0")
        rejected = _admit(OOSConclusion.NOT_QUALIFIED, strategy="beta", strategy_version="1.0.0")
        registry = registry.register(qualified).register(rejected)
        self.assertEqual(registry.for_strategy("alpha", "1.0.0"), qualified)
        self.assertIsNone(registry.for_strategy("gamma", "1.0.0"))
        self.assertEqual(registry.admitted(), (qualified,))
        self.assertEqual(registry.rejected(), (rejected,))
        self.assertEqual(
            registry.readable(),
            "admission registry: 2 recorded (1 admitted, 1 rejected)",
        )

    def test_immutable(self) -> None:
        original = AdmissionRegistry()
        updated = original.register(_admit(OOSConclusion.QUALIFIED))
        self.assertEqual(original.admissions, ())
        self.assertEqual(len(updated.admissions), 1)

    def test_register_sorts_keys(self) -> None:
        alpha = _admit(OOSConclusion.QUALIFIED, strategy="alpha")
        beta = _admit(OOSConclusion.QUALIFIED, strategy="beta")
        registry = AdmissionRegistry().register(beta).register(alpha)
        self.assertEqual(
            [admission.strategy for admission in registry.admissions],
            ["alpha", "beta"],
        )

    def test_duplicate_rejected(self) -> None:
        duplicate = _admit(OOSConclusion.QUALIFIED)
        with self.assertRaises(AdmissionRegistryError):
            AdmissionRegistry(admissions=(duplicate, duplicate))

    def test_unsorted_rejected(self) -> None:
        beta = _admit(OOSConclusion.QUALIFIED, strategy="beta")
        alpha = _admit(OOSConclusion.QUALIFIED, strategy="alpha")
        with self.assertRaises(AdmissionRegistryError):
            AdmissionRegistry(admissions=(beta, alpha))

    def test_duplicate_register_rejected(self) -> None:
        registry = AdmissionRegistry()
        admission = _admit(OOSConclusion.QUALIFIED)
        registry = registry.register(admission)
        with self.assertRaises(AdmissionRegistryError):
            registry.register(admission)
