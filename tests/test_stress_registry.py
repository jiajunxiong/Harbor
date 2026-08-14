"""Tests for the stress-scenario registration (MVP 3 / SP 3.59).

Covers the scenario registration record (假设 / 参数 / 适用市场 / 运行指纹 /
与基线的差异 and its validation), the versioned registry (unique keys, ordering,
lookup, immutable append), the required-scenario gate that forbids unregistered
scenarios from entering a conclusion (禁止未登记情景进入结论), the structural
ref extraction from the SP 3.51–3.56 stress reports, and the re-derivable
fingerprints.
"""

import hashlib
import json
import math
import unittest

from harbor.core.backtest_domain import Market
from harbor.core.stress_registry import (
    RequiredScenario,
    StressRegistryError,
    StressScenarioCategory,
    StressScenarioRegistration,
    StressScenarioRegistry,
    build_scenario_registration,
    build_stress_registry,
    check_scenarios_registered,
    register_scenario,
    registration_check_fingerprint,
    registration_check_json,
    registration_fingerprint,
    registration_json,
    registry_fingerprint,
    registry_json,
    require_scenarios_registered,
    scenario_refs_from_reports,
    single_scenario_ref,
)


def _registration(**overrides: object) -> StressScenarioRegistration:
    """Build one registered cost scenario plus overrides."""
    fields: dict[str, object] = {
        "category": StressScenarioCategory.COST,
        "scenario_id": "cost-stress-2x",
        "market": Market.HK,
        "assumptions": ("rates x2", "min commission raised"),
        "parameters": {
            "rate_multiplier": 2.0,
            "min_commission": 10,
            "slippage_bps": 10,
        },
        "dataset_fingerprint": "dataset-fp",
        "code_version": "test",
        "baseline_difference": -1.5,
        "difference_summary": None,
    }
    fields.update(overrides)
    return build_scenario_registration(**fields)  # type: ignore[arg-type]


def _fx_registration() -> StressScenarioRegistration:
    """One cross-market FX scenario whose impact is not quantifiable."""
    return build_scenario_registration(
        category=StressScenarioCategory.FX,
        scenario_id="fx-missing",
        market=None,
        assumptions=("missing FX refused",),
        parameters={"refuse_1to1": True},
        dataset_fingerprint="dataset-fp",
        code_version="test",
        baseline_difference=None,
        difference_summary="all foreign fills refused; impact not quantifiable",
    )


def _registry(**overrides: object) -> StressScenarioRegistry:
    """Build a two-scenario registry (cost + fx) plus overrides."""
    fields: dict[str, object] = {
        "version": "reg-1",
        "source": "pre-registered",
        "registrations": (_registration(), _fx_registration()),
    }
    fields.update(overrides)
    return build_stress_registry(**fields)  # type: ignore[arg-type]


class TestStressScenarioRegistration(unittest.TestCase):
    """The authoritative scenario record and its validation (SP 3.59)."""

    def test_records_all_five_items(self) -> None:
        registration = _registration()
        self.assertEqual(registration.category, StressScenarioCategory.COST)
        self.assertEqual(registration.scenario_id, "cost-stress-2x")
        self.assertEqual(registration.market, Market.HK)
        self.assertEqual(registration.assumptions, ("rates x2", "min commission raised"))
        self.assertEqual(dict(registration.parameters)["rate_multiplier"], 2.0)
        self.assertEqual(registration.dataset_fingerprint, "dataset-fp")
        self.assertEqual(registration.code_version, "test")
        self.assertEqual(registration.baseline_difference, -1.5)
        self.assertEqual(len(registration.fingerprint), 64)

    def test_build_fingerprints(self) -> None:
        registration = _registration()
        self.assertEqual(registration.fingerprint, registration_fingerprint(registration))

    def test_cross_market_registration(self) -> None:
        registration = _fx_registration()
        self.assertIsNone(registration.market)
        self.assertIn("cross-market", registration.readable())

    def test_readable(self) -> None:
        registration = _registration()
        text = registration.readable()
        self.assertIn("cost/cost-stress-2x", text)
        self.assertIn("HK", text)
        self.assertIn("diff -1.5", text)
        self.assertIn("3 parameter(s)", text)

    def test_rejects_empty_scenario_id(self) -> None:
        with self.assertRaises(StressRegistryError):
            _registration(scenario_id="")

    def test_rejects_empty_assumptions(self) -> None:
        with self.assertRaises(StressRegistryError):
            _registration(assumptions=())

    def test_rejects_empty_assumption_string(self) -> None:
        with self.assertRaises(StressRegistryError):
            _registration(assumptions=("valid", ""))

    def test_rejects_empty_parameters(self) -> None:
        with self.assertRaises(StressRegistryError):
            _registration(parameters={})

    def test_rejects_duplicate_parameter_keys(self) -> None:
        with self.assertRaises(StressRegistryError):
            StressScenarioRegistration(
                category=StressScenarioCategory.COST,
                scenario_id="cost-stress-2x",
                market=Market.HK,
                assumptions=("a",),
                parameters=(("rate", 2.0), ("rate", 2.0)),
                dataset_fingerprint="fp",
                code_version="test",
                baseline_difference=-1.0,
                difference_summary=None,
                fingerprint="fp",
            )

    def test_rejects_non_json_parameters(self) -> None:
        with self.assertRaises(StressRegistryError):
            _registration(parameters={"bad": {1, 2}})

    def test_rejects_empty_dataset_fingerprint(self) -> None:
        with self.assertRaises(StressRegistryError):
            _registration(dataset_fingerprint="")

    def test_rejects_empty_code_version(self) -> None:
        with self.assertRaises(StressRegistryError):
            _registration(code_version="")

    def test_rejects_non_finite_baseline_difference(self) -> None:
        with self.assertRaises(StressRegistryError):
            _registration(baseline_difference=math.nan)
        with self.assertRaises(StressRegistryError):
            _registration(baseline_difference=math.inf)

    def test_requires_summary_when_difference_none(self) -> None:
        with self.assertRaises(StressRegistryError):
            _registration(baseline_difference=None, difference_summary=None)

    def test_allows_summary_when_difference_none(self) -> None:
        registration = _registration(
            baseline_difference=None,
            difference_summary="not quantifiable: all fills refused",
        )
        self.assertIsNone(registration.baseline_difference)
        self.assertEqual(registration.difference_summary, "not quantifiable: all fills refused")

    def test_rejects_empty_fingerprint(self) -> None:
        with self.assertRaises(StressRegistryError):
            StressScenarioRegistration(
                category=StressScenarioCategory.COST,
                scenario_id="cost-stress-2x",
                market=Market.HK,
                assumptions=("a",),
                parameters=(("rate", 2.0),),
                dataset_fingerprint="fp",
                code_version="test",
                baseline_difference=-1.0,
                difference_summary=None,
                fingerprint="",
            )


class TestStressScenarioRegistry(unittest.TestCase):
    """The versioned ledger and the immutable append (SP 3.59)."""

    def test_build_orders_by_category_then_id(self) -> None:
        registry = _registry()
        self.assertEqual(
            [r.scenario_id for r in registry],
            ["cost-stress-2x", "fx-missing"],
        )
        self.assertEqual(registry.count, 2)

    def test_build_fingerprints(self) -> None:
        registry = _registry()
        self.assertEqual(registry.fingerprint, registry_fingerprint(registry))

    def test_rejects_empty_version(self) -> None:
        with self.assertRaises(StressRegistryError):
            _registry(version="")

    def test_rejects_empty_source(self) -> None:
        with self.assertRaises(StressRegistryError):
            _registry(source="")

    def test_rejects_empty_registrations(self) -> None:
        with self.assertRaises(StressRegistryError):
            _registry(registrations=())

    def test_rejects_duplicate_key(self) -> None:
        with self.assertRaises(StressRegistryError):
            _registry(registrations=(_registration(), _registration()))

    def test_rejects_unsorted_direct_construction(self) -> None:
        with self.assertRaises(StressRegistryError):
            StressScenarioRegistry(
                version="v",
                source="s",
                registrations=(_fx_registration(), _registration()),
                fingerprint="fp",
            )

    def test_rejects_empty_fingerprint(self) -> None:
        with self.assertRaises(StressRegistryError):
            StressScenarioRegistry(
                version="v",
                source="s",
                registrations=(_registration(),),
                fingerprint="",
            )

    def test_len_iter_getitem(self) -> None:
        registry = _registry()
        self.assertEqual(len(registry), 2)
        self.assertEqual(registry[0].scenario_id, "cost-stress-2x")
        self.assertEqual(registry[1].scenario_id, "fx-missing")

    def test_registration_lookup(self) -> None:
        registry = _registry()
        self.assertIsNotNone(registry.registration(StressScenarioCategory.COST, "cost-stress-2x"))
        self.assertIsNone(registry.registration(StressScenarioCategory.COST, "cost-stress-10x"))

    def test_contains(self) -> None:
        registry = _registry()
        self.assertTrue(registry.contains(StressScenarioCategory.FX, "fx-missing"))
        self.assertFalse(registry.contains(StressScenarioCategory.FX, "fx-shock"))

    def test_for_category(self) -> None:
        registry = _registry()
        self.assertEqual(len(registry.for_category(StressScenarioCategory.FX)), 1)
        self.assertEqual(registry.for_category(StressScenarioCategory.CALENDAR), ())

    def test_readable(self) -> None:
        self.assertIn("2 registered scenario(s)", _registry().readable())

    def test_register_scenario_appends_and_refingerprints(self) -> None:
        original = _registry()
        extra = build_scenario_registration(
            category=StressScenarioCategory.CALENDAR,
            scenario_id="calendar-stress-closure",
            market=Market.HK,
            assumptions=("closure day added",),
            parameters={"holiday": "2026-01-02"},
            dataset_fingerprint="dataset-fp",
            code_version="test",
            baseline_difference=-0.5,
            difference_summary=None,
        )
        updated = register_scenario(original, extra)
        self.assertEqual(original.count, 2)
        self.assertEqual(updated.count, 3)
        self.assertTrue(
            updated.contains(StressScenarioCategory.CALENDAR, "calendar-stress-closure")
        )
        self.assertEqual(
            [r.category for r in updated],
            [
                StressScenarioCategory.CALENDAR,
                StressScenarioCategory.COST,
                StressScenarioCategory.FX,
            ],
        )
        self.assertNotEqual(original.fingerprint, updated.fingerprint)

    def test_register_scenario_rejects_duplicate(self) -> None:
        registry = _registry()
        with self.assertRaises(StressRegistryError):
            register_scenario(registry, _registration())

    def test_register_scenario_keeps_ordering(self) -> None:
        registry = _registry()
        # A cost scenario whose id sorts before the existing cost one.
        earlier = build_scenario_registration(
            category=StressScenarioCategory.COST,
            scenario_id="cost-stress-1x",
            market=Market.HK,
            assumptions=("rates x1",),
            parameters={"rate_multiplier": 1.0},
            dataset_fingerprint="dataset-fp",
            code_version="test",
            baseline_difference=-0.5,
            difference_summary=None,
        )
        updated = register_scenario(registry, earlier)
        self.assertEqual(
            [r.scenario_id for r in updated],
            ["cost-stress-1x", "cost-stress-2x", "fx-missing"],
        )


class TestRequiredScenario(unittest.TestCase):
    """The required-scenario reference type (SP 3.59)."""

    def test_readable(self) -> None:
        ref = single_scenario_ref(StressScenarioCategory.FX, "fx-shock")
        self.assertEqual(ref.readable(), "fx/fx-shock")

    def test_equality(self) -> None:
        self.assertEqual(
            single_scenario_ref(StressScenarioCategory.FX, "fx-shock"),
            RequiredScenario(StressScenarioCategory.FX, "fx-shock"),
        )

    def test_rejects_empty_id(self) -> None:
        with self.assertRaises(StressRegistryError):
            single_scenario_ref(StressScenarioCategory.FX, "")


class TestRegistrationGate(unittest.TestCase):
    """The forbidden-unregistered-scenario gate (SP 3.59)."""

    def test_check_all_registered(self) -> None:
        registry = _registry()
        required = (
            single_scenario_ref(StressScenarioCategory.COST, "cost-stress-2x"),
            single_scenario_ref(StressScenarioCategory.FX, "fx-missing"),
        )
        check = check_scenarios_registered(
            registry, required=required, conclusion_label="stability-HK"
        )
        self.assertTrue(check.all_registered)
        self.assertEqual(check.missing, ())
        self.assertEqual(len(check.required), 2)
        self.assertIn("all 2 required scenario(s) registered", check.readable())

    def test_check_with_missing(self) -> None:
        registry = _registry()
        required = (
            single_scenario_ref(StressScenarioCategory.COST, "cost-stress-2x"),
            single_scenario_ref(StressScenarioCategory.COST, "cost-stress-10x"),
        )
        check = check_scenarios_registered(
            registry, required=required, conclusion_label="stability-HK"
        )
        self.assertFalse(check.all_registered)
        self.assertEqual(
            check.missing,
            (single_scenario_ref(StressScenarioCategory.COST, "cost-stress-10x"),),
        )
        self.assertIn("cost/cost-stress-10x", check.readable())

    def test_check_empty_required(self) -> None:
        check = check_scenarios_registered(
            _registry(), required=(), conclusion_label="stability-HK"
        )
        self.assertTrue(check.all_registered)
        self.assertEqual(check.missing, ())

    def test_require_passes_when_all_registered(self) -> None:
        registry = _registry()
        required = (
            single_scenario_ref(StressScenarioCategory.COST, "cost-stress-2x"),
            single_scenario_ref(StressScenarioCategory.FX, "fx-missing"),
        )
        # Must not raise.
        require_scenarios_registered(registry, required=required, conclusion_label="stability-HK")

    def test_require_raises_when_missing(self) -> None:
        registry = _registry()
        required = (
            single_scenario_ref(StressScenarioCategory.COST, "cost-stress-2x"),
            single_scenario_ref(StressScenarioCategory.FX, "fx-shock"),
        )
        with self.assertRaises(StressRegistryError) as context:
            require_scenarios_registered(
                registry, required=required, conclusion_label="stability-HK"
            )
        message = str(context.exception)
        self.assertIn("fx/fx-shock", message)
        self.assertIn("stability-HK", message)
        self.assertIn("SP 3.59", message)

    def test_require_raises_with_multiple_missing(self) -> None:
        registry = _registry()
        required = (
            single_scenario_ref(StressScenarioCategory.COST, "cost-stress-10x"),
            single_scenario_ref(StressScenarioCategory.FX, "fx-shock"),
        )
        with self.assertRaises(StressRegistryError) as context:
            require_scenarios_registered(
                registry, required=required, conclusion_label="stability-HK"
            )
        message = str(context.exception)
        self.assertIn("cost/cost-stress-10x", message)
        self.assertIn("fx/fx-shock", message)


class TestScenarioRefsExtraction(unittest.TestCase):
    """Extracting the required refs from stress-report scenarios (SP 3.59)."""

    def _stress(self, version: str) -> object:
        """A minimal stand-in for a SP 3.51–3.56 scenario result."""
        return type("Scenario", (), {"stress": type("Stress", (), {"version": version})()})()

    def test_refs_from_reports_in_order(self) -> None:
        scenarios = (self._stress("cost-stress-2x"), self._stress("cost-stress-5x"))
        refs = scenario_refs_from_reports(StressScenarioCategory.COST, scenarios)
        self.assertEqual(
            [ref.readable() for ref in refs],
            ["cost/cost-stress-2x", "cost/cost-stress-5x"],
        )

    def test_refs_from_reports_empty(self) -> None:
        self.assertEqual(scenario_refs_from_reports(StressScenarioCategory.COST, ()), ())

    def test_refs_from_reports_rejects_missing_stress(self) -> None:
        with self.assertRaises(StressRegistryError):
            scenario_refs_from_reports(StressScenarioCategory.COST, (object(),))

    def test_refs_from_reports_rejects_empty_version(self) -> None:
        with self.assertRaises(StressRegistryError):
            scenario_refs_from_reports(StressScenarioCategory.COST, (self._stress(""),))

    def test_extracted_refs_gate(self) -> None:
        registry = _registry()
        refs = scenario_refs_from_reports(
            StressScenarioCategory.COST,
            (self._stress("cost-stress-2x"),),
        ) + (single_scenario_ref(StressScenarioCategory.FX, "fx-missing"),)
        check = check_scenarios_registered(registry, required=refs, conclusion_label="stability-HK")
        self.assertTrue(check.all_registered)

    def test_single_scenario_ref(self) -> None:
        ref = single_scenario_ref(
            StressScenarioCategory.PARAMETER_NEIGHBORHOOD, "neighborhood-default"
        )
        self.assertEqual(ref.readable(), "parameter_neighborhood/neighborhood-default")


class TestStressRegistryFingerprints(unittest.TestCase):
    """The re-derivable, stable fingerprints of the registry artifacts."""

    def test_registration_fingerprint_rederivable(self) -> None:
        registration = _registration()
        digest = hashlib.sha256(registration_json(registration).encode("utf-8")).hexdigest()
        self.assertEqual(registration.fingerprint, digest)

    def test_registry_fingerprint_rederivable(self) -> None:
        registry = _registry()
        digest = hashlib.sha256(registry_json(registry).encode("utf-8")).hexdigest()
        self.assertEqual(registry.fingerprint, digest)

    def test_check_fingerprint_rederivable(self) -> None:
        check = check_scenarios_registered(
            _registry(),
            required=(single_scenario_ref(StressScenarioCategory.COST, "cost-stress-2x"),),
            conclusion_label="stability-HK",
        )
        digest = hashlib.sha256(registration_check_json(check).encode("utf-8")).hexdigest()
        self.assertEqual(check.fingerprint, digest)
        self.assertEqual(check.fingerprint, registration_check_fingerprint(check))

    def test_registration_json_excludes_fingerprint(self) -> None:
        payload = json.loads(registration_json(_registration()))
        self.assertNotIn("fingerprint", payload)
        self.assertEqual(payload["category"], "cost")
        self.assertEqual(payload["market"], "HK")

    def test_registry_json_excludes_fingerprint(self) -> None:
        payload = json.loads(registry_json(_registry()))
        self.assertNotIn("fingerprint", payload)
        self.assertEqual(len(payload["registrations"]), 2)
        self.assertEqual(payload["version"], "reg-1")

    def test_registry_fingerprint_sensitive_to_registration(self) -> None:
        original = _registry()
        extra = build_scenario_registration(
            category=StressScenarioCategory.CALENDAR,
            scenario_id="calendar-stress-closure",
            market=Market.HK,
            assumptions=("closure day added",),
            parameters={"holiday": "2026-01-02"},
            dataset_fingerprint="dataset-fp",
            code_version="test",
            baseline_difference=-0.5,
            difference_summary=None,
        )
        self.assertNotEqual(original.fingerprint, register_scenario(original, extra).fingerprint)

    def test_registry_fingerprint_sensitive_to_assumption(self) -> None:
        registry = _registry()
        changed = _registry(
            registrations=(
                _registration(assumptions=("rates x2",)),
                _fx_registration(),
            )
        )
        self.assertNotEqual(registry.fingerprint, changed.fingerprint)

    def test_check_fingerprint_sensitive_to_missing(self) -> None:
        registered = check_scenarios_registered(
            _registry(),
            required=(single_scenario_ref(StressScenarioCategory.COST, "cost-stress-2x"),),
            conclusion_label="stability-HK",
        )
        missing = check_scenarios_registered(
            _registry(),
            required=(single_scenario_ref(StressScenarioCategory.COST, "cost-stress-10x"),),
            conclusion_label="stability-HK",
        )
        self.assertNotEqual(registered.fingerprint, missing.fingerprint)


if __name__ == "__main__":
    unittest.main()
