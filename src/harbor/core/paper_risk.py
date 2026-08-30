"""Versioned risk parameters for the paper loop (MVP 4 / SP 4.31).

The pre-registered risk parameters (单笔风险 ≤ 0.5%, 单股 ≤ 5%, 单行业 ≤ 20%
and the 5% / 8% / 10% drawdown tiers) are snapshotted under an explicit
version so every risk check, order and audit event can prove which parameter
version it was evaluated against (风控参数版本化). The :class:`RiskParameterVersion`
value is immutable and self-fingerprinting; :func:`require_risk_version`
refuses to accept a check or order that was evaluated against a different
version than the run's active one.

Pure core logic: depends on the paper config (SP 4.2) and never touches
storage or CLI code.
"""

import hashlib
import json
from dataclasses import dataclass

from harbor.core.paper_config import PaperRiskConfig


class PaperRiskError(ValueError):
    """Raised when risk parameters are invalid or version-mismatched (SP 4.31)."""


def _canonical(parameters: PaperRiskConfig) -> str:
    """Return the canonical, key-sorted serialization of the risk parameters."""
    return json.dumps(
        parameters.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def risk_parameters_fingerprint(*, version: str, parameters: PaperRiskConfig) -> str:
    """Return a stable SHA-256 digest of a risk parameter version (SP 4.31).

    The digest covers the version string and the canonical parameter values,
    so a change to any threshold or the version changes the fingerprint.
    """
    return hashlib.sha256(f"{version}|{_canonical(parameters)}".encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RiskParameterVersion:
    """A versioned snapshot of the risk parameters (SP 4.31)."""

    version: str
    parameters: PaperRiskConfig
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.version:
            raise PaperRiskError("Risk parameter version must be non-empty.")
        expected = risk_parameters_fingerprint(version=self.version, parameters=self.parameters)
        if self.fingerprint != expected:
            raise PaperRiskError("Risk parameter fingerprint does not match its content.")

    def readable(self) -> str:
        """Render the versioned parameters as a compact summary."""
        return (
            f"risk parameters {self.version} "
            f"(trade {self.parameters.max_position_pct:.1%}, stock "
            f"{self.parameters.max_single_stock_pct:.1%}, industry "
            f"{self.parameters.max_industry_pct:.1%}, drawdown "
            f"{self.parameters.drawdown_warn_pct:.0%}/"
            f"{self.parameters.drawdown_defend_pct:.0%}/"
            f"{self.parameters.drawdown_circuit_pct:.0%}) fp {self.fingerprint}"
        )


def build_risk_parameter_version(
    *, version: str, parameters: PaperRiskConfig
) -> RiskParameterVersion:
    """Snapshot risk parameters under a version (SP 4.31).

    Raises:
        PaperRiskError: If ``version`` is empty or the parameters are invalid
            (e.g. non-increasing drawdown tiers, propagated from the config).
    """
    fingerprint = risk_parameters_fingerprint(version=version, parameters=parameters)
    return RiskParameterVersion(
        version=version,
        parameters=parameters,
        fingerprint=fingerprint,
    )


def require_risk_version(*, active: RiskParameterVersion, actual_version: str) -> None:
    """Require that a check or order was evaluated against ``active`` (SP 4.31).

    Raises:
        PaperRiskError: If ``actual_version`` does not match the active
            version, so a stale risk evaluation is never silently accepted.
    """
    if actual_version != active.version:
        raise PaperRiskError(
            f"Risk evaluation version {actual_version!r} does not match the "
            f"active version {active.version!r}."
        )
