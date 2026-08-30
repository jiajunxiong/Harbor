"""Replayable paper-run identity (MVP 4 / SP 4.9).

Records everything needed to reproduce a paper run as an immutable
:class:`PaperReplayIdentity`: the strategy config hash (SP 4.2), the dataset
fingerprint (SP 3.7 / 4.5), the code version, the random seed, the
authoritative calendar version and the FX source. Two paper runs whose
identities carry the same :meth:`PaperReplayIdentity.fingerprint` are
replay-identical: identical inputs reproduce identical orders, fills, net
values and audit events (SP 4.9 / 4.59).

The run id is deliberately excluded — it identifies an execution, not the
research inputs (mirroring SP 2.61). Calendar version, FX source and random
seed are recorded verbatim from the validated configuration and omitted
(``None``) otherwise; the identity never fabricates a value the config did not
supply (never-assume rule).

Pure core logic: depends only on the paper config and its hashing; never
touches storage or CLI code.
"""

from dataclasses import dataclass

from harbor.core.paper_config import PaperConfig
from harbor.core.paper_config_loader import config_hash


class PaperReplayIdentityError(ValueError):
    """Raised when a paper replay identity cannot be built (SP 4.9)."""


@dataclass(frozen=True)
class PaperReplayIdentity:
    """Everything needed to reproduce one paper run (可重放运行标识, SP 4.9)."""

    config_hash: str
    dataset_fingerprint: str
    code_version: str
    random_seed: int | None
    calendar_version: str | None
    fx_source: str | None

    def __post_init__(self) -> None:
        if not self.config_hash:
            raise PaperReplayIdentityError("config_hash must be non-empty.")
        if not self.dataset_fingerprint:
            raise PaperReplayIdentityError("dataset_fingerprint must be non-empty.")
        if not self.code_version:
            raise PaperReplayIdentityError("code_version must be non-empty.")
        if self.random_seed is not None and self.random_seed < 0:
            raise PaperReplayIdentityError("random_seed must be non-negative when set.")

    def fingerprint(self) -> str:
        """Return a stable string identifying the replay inputs (SP 4.9).

        Two paper runs with the same fingerprint are replay-identical. The run
        id is deliberately absent: this identity identifies the inputs, not an
        execution. None values are recorded verbatim as the empty string
        (never fabricated).
        """
        seed = "" if self.random_seed is None else str(self.random_seed)
        return "|".join(
            [
                self.config_hash,
                self.dataset_fingerprint,
                self.code_version,
                seed,
                self.calendar_version or "",
                self.fx_source or "",
            ]
        )

    def readable(self) -> str:
        """Render the identity as a compact summary."""
        seed = "unset" if self.random_seed is None else str(self.random_seed)
        calendar = self.calendar_version or "unset"
        fx = self.fx_source or "unset"
        return (
            f"paper replay identity config {self.config_hash} "
            f"dataset {self.dataset_fingerprint} code {self.code_version} "
            f"seed {seed} calendar {calendar} fx {fx}"
        )


def build_paper_replay_identity(
    *,
    config: PaperConfig,
    dataset_fingerprint: str,
    code_version: str,
) -> PaperReplayIdentity:
    """Build a paper replay identity from a validated config (SP 4.9).

    The config hash is derived from the canonical serialization (SP 4.2); the
    random seed, calendar version and FX source are taken verbatim from the
    configuration. The dataset fingerprint and code version are supplied by
    the caller (they come from the frozen data layer / release).
    """
    return PaperReplayIdentity(
        config_hash=config_hash(config),
        dataset_fingerprint=dataset_fingerprint,
        code_version=code_version,
        random_seed=config.random_seed,
        calendar_version=config.calendar_version,
        fx_source=config.fx_source,
    )
