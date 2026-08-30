"""Versioned risk parameter tests (MVP 4 / SP 4.31).

Verifies that the risk parameters (单笔 ≤ 0.5%, 单股 ≤ 5%, 单行业 ≤ 20% and the
5% / 8% / 10% drawdown tiers) are snapshotted under an explicit version with a
self-fingerprint, and that a stale version is refused.
"""

import unittest

from harbor.core.paper_config import PaperRiskConfig
from harbor.core.paper_risk import (
    PaperRiskError,
    RiskParameterVersion,
    build_risk_parameter_version,
    require_risk_version,
    risk_parameters_fingerprint,
)


def _parameters(**overrides: object) -> PaperRiskConfig:
    fields: dict[str, object] = {}
    fields.update(overrides)
    return PaperRiskConfig(**fields)  # type: ignore[arg-type]


def _version(version: str = "v1", **overrides: object) -> RiskParameterVersion:
    return build_risk_parameter_version(version=version, parameters=_parameters(**overrides))


class BuildRiskParameterVersionTests(unittest.TestCase):
    """Snapshotting risk parameters (SP 4.31)."""

    def test_valid_version(self) -> None:
        snapshot = _version()
        self.assertEqual(snapshot.version, "v1")
        self.assertEqual(snapshot.parameters.max_position_pct, 0.005)
        self.assertEqual(snapshot.parameters.drawdown_warn_pct, 0.05)
        self.assertEqual(len(snapshot.fingerprint), 64)

    def test_empty_version_rejected(self) -> None:
        with self.assertRaises(PaperRiskError):
            _version(version="")

    def test_invalid_tiers_rejected(self) -> None:
        with self.assertRaises(Exception):
            build_risk_parameter_version(
                version="v1",
                parameters=_parameters(drawdown_warn_pct=0.10, drawdown_defend_pct=0.08),
            )

    def test_tampered_fingerprint_rejected(self) -> None:
        snapshot = _version()
        with self.assertRaises(PaperRiskError):
            RiskParameterVersion(
                version="v1",
                parameters=snapshot.parameters,
                fingerprint="0" * 64,
            )

    def test_fingerprint_stable_and_content_sensitive(self) -> None:
        first = _version()
        second = _version()
        self.assertEqual(first.fingerprint, second.fingerprint)
        changed = _version(version="v2")
        self.assertNotEqual(first.fingerprint, changed.fingerprint)
        changed_params = _version(max_position_pct=0.01)
        self.assertNotEqual(first.fingerprint, changed_params.fingerprint)

    def test_fingerprint_helper(self) -> None:
        parameters = _parameters()
        self.assertEqual(
            risk_parameters_fingerprint(version="v1", parameters=parameters),
            _version().fingerprint,
        )

    def test_readable(self) -> None:
        snapshot = _version()
        rendered = snapshot.readable()
        self.assertIn("risk parameters v1", rendered)
        self.assertIn("trade 0.5%", rendered)
        self.assertIn("5%/8%/10%", rendered)


class RequireRiskVersionTests(unittest.TestCase):
    """The version gate (SP 4.31)."""

    def test_matching_version_passes(self) -> None:
        active = _version(version="v1")
        require_risk_version(active=active, actual_version="v1")

    def test_stale_version_refused(self) -> None:
        active = _version(version="v2")
        with self.assertRaises(PaperRiskError):
            require_risk_version(active=active, actual_version="v1")
