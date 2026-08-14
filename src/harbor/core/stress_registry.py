"""Stress-scenario registration (MVP 3 / SP 3.59).

Records every pre-registered stress scenario — its assumptions (假设), its
parameters (参数), the market it applies to (适用市场), the run fingerprint it was
measured on (运行指纹) and its difference from the baseline (与基线的差异) — and
forbids an unregistered scenario from entering a conclusion (禁止未登记情景进入
结论).

- :class:`StressScenarioRegistration` is the authoritative record of one
  scenario: a category, a stable ``scenario_id`` (the pre-registered stress
  version from SP 3.51–3.57), the assumptions, the parameter key/value pairs,
  the applicable market (``None`` for a cross-market scenario), the dataset /
  code fingerprint of the run and the difference from baseline. A scenario
  whose impact could not be quantified still records a difference summary — it
  is never silently dropped.
- :class:`StressScenarioRegistry` is the versioned ledger of registered
  scenarios (unique ``(category, scenario_id)`` keys, ordered, fingerprinted);
  :func:`register_scenario` appends one registration and returns the new
  ledger.
- The gate :func:`require_scenarios_registered` blocks any conclusion that
  references a scenario which is not registered, naming the missing scenario(s)
  and the conclusion (禁止未登记情景进入结论).
  :func:`scenario_refs_from_reports` extracts the referenced scenarios from the
  SP 3.51–3.56 stress reports.

Pure core layer: depends only on the domain types; never touches storage,
services or CLI.
"""

import hashlib
import json
import math
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from harbor.core.backtest_domain import Market


class StressRegistryError(ValueError):
    """Raised when a stress-scenario registration or gate is invalid (SP 3.59)."""


class StressScenarioCategory(StrEnum):
    """The stress category a registered scenario belongs to (SP 3.59)."""

    COST = "cost"
    LIQUIDITY = "liquidity"
    FX = "fx"
    CALENDAR = "calendar"
    CORPORATE_ACTION = "corporate_action"
    STOCK_POOL = "stock_pool"
    PARAMETER_NEIGHBORHOOD = "parameter_neighborhood"


@dataclass(frozen=True)
class StressScenarioRegistration:
    """The authoritative record of one pre-registered stress scenario (SP 3.59).

    ``category`` / ``scenario_id`` identify the scenario (the id is the
    pre-registered stress version from SP 3.51–3.57); ``assumptions`` (假设) the
    stated conservative assumptions; ``parameters`` (参数) the JSON-safe
    key/value parameters; ``market`` the applicable market (None for a
    cross-market scenario); ``dataset_fingerprint`` / ``code_version`` the run
    fingerprint (运行指纹); ``baseline_difference`` / ``difference_summary`` the
    difference from the baseline (与基线的差异) — a scenario whose impact could
    not be quantified records the summary instead of the number.
    """

    category: StressScenarioCategory
    scenario_id: str
    market: Market | None
    assumptions: tuple[str, ...]
    parameters: tuple[tuple[str, object], ...]
    dataset_fingerprint: str
    code_version: str
    baseline_difference: float | None
    difference_summary: str | None
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.scenario_id:
            raise StressRegistryError("scenario id must be non-empty.")
        if not self.assumptions:
            raise StressRegistryError("a registered scenario must record its assumptions.")
        if not all(assumption for assumption in self.assumptions):
            raise StressRegistryError("every assumption must be non-empty.")
        if not self.parameters:
            raise StressRegistryError("a registered scenario must record its parameters.")
        keys = [name for name, _ in self.parameters]
        if len(set(keys)) != len(keys):
            raise StressRegistryError("parameter names must be unique.")
        try:
            json.dumps(dict(self.parameters))
        except (TypeError, ValueError) as error:
            raise StressRegistryError(
                "parameters must be JSON-serializable (str/int/float/bool/None)."
            ) from error
        if not self.dataset_fingerprint:
            raise StressRegistryError("dataset fingerprint must be non-empty.")
        if not self.code_version:
            raise StressRegistryError("code version must be non-empty.")
        if self.baseline_difference is not None and not math.isfinite(self.baseline_difference):
            raise StressRegistryError("baseline difference must be a finite value.")
        if self.baseline_difference is None and not self.difference_summary:
            raise StressRegistryError(
                "a scenario without a baseline difference must record a summary."
            )
        if not self.fingerprint:
            raise StressRegistryError("stress scenario registration fingerprint must be non-empty.")

    def readable(self) -> str:
        """Render the registration as one line."""
        difference = (
            f"{self.baseline_difference}"
            if self.baseline_difference is not None
            else self.difference_summary
        )
        market = self.market.value if self.market is not None else "cross-market"
        return (
            f"{self.category.value}/{self.scenario_id} ({market}): "
            f"{len(self.assumptions)} assumption(s), {len(self.parameters)} "
            f"parameter(s), diff {difference} fp {self.fingerprint}"
        )


@dataclass(frozen=True)
class StressScenarioRegistry:
    """The versioned ledger of registered stress scenarios (SP 3.59).

    Registrations are ordered by ``(category.value, scenario_id)`` and their
    ``(category, scenario_id)`` keys are unique — a scenario can only be
    registered once. The ledger is fingerprinted so a conclusion can always be
    re-audited against the exact registry that admitted it.
    """

    version: str
    source: str
    registrations: tuple[StressScenarioRegistration, ...]
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.version:
            raise StressRegistryError("stress registry version must be non-empty.")
        if not self.source:
            raise StressRegistryError("stress registry source must be non-empty.")
        if not self.registrations:
            raise StressRegistryError("a stress registry requires at least one registration.")
        keys: list[tuple[StressScenarioCategory, str]] = []
        for registration in self.registrations:
            key = (registration.category, registration.scenario_id)
            if key in keys:
                raise StressRegistryError(
                    f"scenario {registration.category.value}/"
                    f"{registration.scenario_id} is already registered."
                )
            keys.append(key)
        ordered = tuple(self.registrations)
        if ordered != tuple(
            sorted(
                self.registrations,
                key=lambda registration: (
                    registration.category.value,
                    registration.scenario_id,
                ),
            )
        ):
            raise StressRegistryError("registrations must be ordered by (category, scenario_id).")
        if not self.fingerprint:
            raise StressRegistryError("stress registry fingerprint must be non-empty.")

    def __len__(self) -> int:
        return len(self.registrations)

    def __iter__(self) -> Iterator[StressScenarioRegistration]:
        return iter(self.registrations)

    def __getitem__(self, index: int) -> StressScenarioRegistration:
        return self.registrations[index]

    @property
    def count(self) -> int:
        """Number of registered scenarios."""
        return len(self.registrations)

    def contains(self, category: StressScenarioCategory, scenario_id: str) -> bool:
        """Whether a scenario is registered under the given category and id."""
        return self.registration(category, scenario_id) is not None

    def registration(
        self, category: StressScenarioCategory, scenario_id: str
    ) -> StressScenarioRegistration | None:
        """Return one registration (None when absent)."""
        for registration in self.registrations:
            if registration.category is category and registration.scenario_id == scenario_id:
                return registration
        return None

    def for_category(
        self, category: StressScenarioCategory
    ) -> tuple[StressScenarioRegistration, ...]:
        """Return every registration in one category, in id order."""
        return tuple(
            registration for registration in self.registrations if registration.category is category
        )

    def readable(self) -> str:
        """Render the registry as one line."""
        return (
            f"stress registry {self.version} ({self.source}): "
            f"{len(self.registrations)} registered scenario(s) fp {self.fingerprint}"
        )


@dataclass(frozen=True)
class RequiredScenario:
    """A scenario referenced by a conclusion and required to be registered."""

    category: StressScenarioCategory
    scenario_id: str

    def __post_init__(self) -> None:
        if not self.scenario_id:
            raise StressRegistryError("required scenario id must be non-empty.")

    def readable(self) -> str:
        """Render the ref as ``category/scenario_id``."""
        return f"{self.category.value}/{self.scenario_id}"


@dataclass(frozen=True)
class StressRegistrationCheck:
    """The result of checking that every referenced scenario is registered."""

    registry_version: str
    conclusion_label: str
    required: tuple[RequiredScenario, ...]
    all_registered: bool
    missing: tuple[RequiredScenario, ...]
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.registry_version:
            raise StressRegistryError("registry version must be non-empty.")
        if not self.conclusion_label:
            raise StressRegistryError("a registration check must name the conclusion.")
        if self.all_registered != (not self.missing):
            raise StressRegistryError("all_registered must agree with the missing refs.")
        if any(ref not in self.required for ref in self.missing):
            raise StressRegistryError("missing refs must be a subset of the required refs.")
        if not self.fingerprint:
            raise StressRegistryError("registration check fingerprint must be non-empty.")

    def readable(self) -> str:
        """Render the check as one line."""
        if self.all_registered:
            return (
                f"conclusion '{self.conclusion_label}': all {len(self.required)} "
                f"required scenario(s) registered fp {self.fingerprint}"
            )
        missing = ", ".join(ref.readable() for ref in self.missing)
        return (
            f"conclusion '{self.conclusion_label}': {len(self.missing)} of "
            f"{len(self.required)} required scenario(s) unregistered ({missing}) "
            f"fp {self.fingerprint}"
        )


def build_scenario_registration(
    *,
    category: StressScenarioCategory,
    scenario_id: str,
    market: Market | None,
    assumptions: Sequence[str],
    parameters: Mapping[str, object],
    dataset_fingerprint: str,
    code_version: str,
    baseline_difference: float | None = None,
    difference_summary: str | None = None,
) -> StressScenarioRegistration:
    """Assemble a fingerprint-stamped scenario registration (SP 3.59)."""
    registration = StressScenarioRegistration(
        category=category,
        scenario_id=scenario_id,
        market=market,
        assumptions=tuple(assumptions),
        parameters=tuple(parameters.items()),
        dataset_fingerprint=dataset_fingerprint,
        code_version=code_version,
        baseline_difference=baseline_difference,
        difference_summary=difference_summary,
        fingerprint="unfingerprinted",
    )
    return replace(registration, fingerprint=registration_fingerprint(registration))


def build_stress_registry(
    *,
    version: str,
    source: str = "pre-registered",
    registrations: Sequence[StressScenarioRegistration],
) -> StressScenarioRegistry:
    """Assemble a versioned, fingerprint-stamped stress registry (SP 3.59)."""
    ordered = tuple(sorted(registrations, key=lambda r: (r.category.value, r.scenario_id)))
    registry = StressScenarioRegistry(
        version=version,
        source=source,
        registrations=ordered,
        fingerprint="unfingerprinted",
    )
    return replace(registry, fingerprint=registry_fingerprint(registry))


def register_scenario(
    registry: StressScenarioRegistry,
    registration: StressScenarioRegistration,
) -> StressScenarioRegistry:
    """Register one more scenario and return the new ledger (SP 3.59).

    The registration must not duplicate an existing ``(category, scenario_id)``
    key; the new ledger is re-sorted and re-fingerprinted. The input registry
    is left unchanged (immutable append).
    """
    if registry.registration(registration.category, registration.scenario_id) is not None:
        raise StressRegistryError(
            f"scenario {registration.category.value}/{registration.scenario_id} "
            "is already registered."
        )
    return build_stress_registry(
        version=registry.version,
        source=registry.source,
        registrations=(*registry.registrations, registration),
    )


def scenario_refs_from_reports(
    category: StressScenarioCategory,
    scenarios: Sequence[object],
) -> tuple[RequiredScenario, ...]:
    """Extract the required scenario refs from a stress report's scenarios.

    Every SP 3.51–3.56 scenario result exposes a ``stress`` attribute whose
    ``version`` is the scenario id (e.g. ``cost-stress-2x``); pass e.g.
    ``scenario_refs_from_reports(StressScenarioCategory.COST,
    cost_report.scenarios)``. For the single SP 3.57 neighborhood scenario use
    :func:`single_scenario_ref` with the report's ``config.version``.
    """
    refs: list[RequiredScenario] = []
    for scenario in scenarios:
        stress = getattr(scenario, "stress", None)
        version = getattr(stress, "version", None)
        if not version:
            raise StressRegistryError(
                "a scenario must expose a `stress` attribute with a non-empty version."
            )
        refs.append(RequiredScenario(category=category, scenario_id=str(version)))
    return tuple(refs)


def single_scenario_ref(category: StressScenarioCategory, scenario_id: str) -> RequiredScenario:
    """Return a single required scenario ref (e.g. the SP 3.57 neighborhood)."""
    return RequiredScenario(category=category, scenario_id=scenario_id)


def check_scenarios_registered(
    registry: StressScenarioRegistry,
    *,
    required: Sequence[RequiredScenario],
    conclusion_label: str,
) -> StressRegistrationCheck:
    """Check every referenced scenario is registered (SP 3.59, non-raising)."""
    missing = tuple(ref for ref in required if not registry.contains(ref.category, ref.scenario_id))
    check = StressRegistrationCheck(
        registry_version=registry.version,
        conclusion_label=conclusion_label,
        required=tuple(required),
        all_registered=not missing,
        missing=missing,
        fingerprint="unfingerprinted",
    )
    return replace(check, fingerprint=registration_check_fingerprint(check))


def require_scenarios_registered(
    registry: StressScenarioRegistry,
    *,
    required: Sequence[RequiredScenario],
    conclusion_label: str,
) -> None:
    """Require every referenced scenario to be registered (SP 3.59).

    Raises:
        StressRegistryError: If any required scenario is not registered — an
            unregistered scenario is forbidden from entering a conclusion
            (禁止未登记情景进入结论); the error names the missing scenario(s) and
            the conclusion.
    """
    check = check_scenarios_registered(
        registry, required=required, conclusion_label=conclusion_label
    )
    if not check.all_registered:
        missing = ", ".join(ref.readable() for ref in check.missing)
        raise StressRegistryError(
            f"unregistered scenario(s) {missing} for conclusion "
            f"'{conclusion_label}'; every scenario must be registered before it "
            "can enter a conclusion (SP 3.59)."
        )


def _parameters_payload(
    parameters: tuple[tuple[str, object], ...],
) -> dict[str, object]:
    """The registration's parameters as a JSON payload."""
    return dict(parameters)


def _registration_payload(
    registration: StressScenarioRegistration,
) -> dict[str, object]:
    """The registration's JSON payload (its own fingerprint excluded)."""
    return {
        "category": registration.category.value,
        "scenario_id": registration.scenario_id,
        "market": registration.market.value if registration.market is not None else None,
        "assumptions": list(registration.assumptions),
        "parameters": _parameters_payload(registration.parameters),
        "dataset_fingerprint": registration.dataset_fingerprint,
        "code_version": registration.code_version,
        "baseline_difference": registration.baseline_difference,
        "difference_summary": registration.difference_summary,
    }


def registration_json(registration: StressScenarioRegistration) -> str:
    """Return a stable, key-sorted JSON serialization of one registration."""
    return json.dumps(
        _registration_payload(registration),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def registration_fingerprint(registration: StressScenarioRegistration) -> str:
    """Return the stable SHA-256 fingerprint of one registration (SP 3.59)."""
    return hashlib.sha256(registration_json(registration).encode("utf-8")).hexdigest()


def registry_json(registry: StressScenarioRegistry) -> str:
    """Return a stable, key-sorted JSON serialization of a stress registry.

    The derived ``fingerprint`` field is excluded so the digest can be
    re-derived and compared against the recorded value (SP 3.7 style).
    """
    payload: dict[str, object] = {
        "version": registry.version,
        "source": registry.source,
        "registrations": [
            _registration_payload(registration) for registration in registry.registrations
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def registry_fingerprint(registry: StressScenarioRegistry) -> str:
    """Return the stable SHA-256 fingerprint of a stress registry (SP 3.59)."""
    return hashlib.sha256(registry_json(registry).encode("utf-8")).hexdigest()


def registration_check_json(check: StressRegistrationCheck) -> str:
    """Return a stable, key-sorted JSON serialization of a registration check."""
    payload: dict[str, object] = {
        "registry_version": check.registry_version,
        "conclusion_label": check.conclusion_label,
        "required": [ref.readable() for ref in check.required],
        "all_registered": check.all_registered,
        "missing": [ref.readable() for ref in check.missing],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def registration_check_fingerprint(check: StressRegistrationCheck) -> str:
    """Return the stable SHA-256 fingerprint of a registration check (SP 3.59)."""
    return hashlib.sha256(registration_check_json(check).encode("utf-8")).hexdigest()


__all__: tuple[str, ...] = (
    "StressRegistryError",
    "StressScenarioCategory",
    "StressScenarioRegistration",
    "StressScenarioRegistry",
    "RequiredScenario",
    "StressRegistrationCheck",
    "build_scenario_registration",
    "build_stress_registry",
    "register_scenario",
    "scenario_refs_from_reports",
    "single_scenario_ref",
    "check_scenarios_registered",
    "require_scenarios_registered",
    "registration_json",
    "registration_fingerprint",
    "registry_json",
    "registry_fingerprint",
    "registration_check_json",
    "registration_check_fingerprint",
)
