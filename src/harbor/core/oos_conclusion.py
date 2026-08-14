"""Out-of-sample conclusion model (MVP 3 / SP 3.64).

Aggregates performance (性能), risk (风险), coverage (覆盖), stability (稳定性),
the trial budget (试验预算) and unresolved limitations (未解决限制) into a
structured, auditable conclusion (样本外结论模型); the conclusion contains NO
return promise (结论不含收益承诺).

- :class:`OosStructuredConclusion` carries the SP 3.38 OOS performance and risk
  metrics (returns, volatility, drawdown, Sharpe, Calmar, benchmark excess),
  the SP 3.9 per-market coverage, the SP 3.58 stability conclusion, the SP 3.17
  trial budget and the unresolved limitations, all bound to one market, dataset
  fingerprint and code version so the conclusion can always be re-audited from
  its fingerprint.
- :attr:`OosStructuredConclusion.overall` is the SP 3.1 OOS conclusion:
  ``NOT_QUALIFIED`` when the stability conclusion fails, ``INCONCLUSIVE`` when
  the stability evidence is inconclusive OR any limitation is unresolved (an
  unresolved limitation prevents a clean ``QUALIFIED``), and ``QUALIFIED`` only
  when the stability is qualified AND there are no unresolved limitations.
- The model carries NO projected / expected / future return field: the only
  performance data is the HISTORICAL out-of-sample performance, and
  :func:`no_return_promise_statement` states that explicitly (结论不含收益承
  诺).

Pure core layer: depends only on the SP 3.38 metrics, the SP 3.9 coverage, the
SP 3.58 stability conclusion, the SP 3.17 budget and the SP 3.1 conclusion;
never touches storage, services or CLI.
"""

import hashlib
import json
import math
from dataclasses import dataclass, replace

from harbor.core.backtest_domain import Market
from harbor.core.coverage_scoring import CoverageScore, MarketCoverage
from harbor.core.performance_metrics import PerformanceMetrics
from harbor.core.stability_rule import StabilityConclusion
from harbor.core.trial_budget import TrialBudget
from harbor.core.validation_domain import OOSConclusion


class OosConclusionError(ValueError):
    """Raised when a structured OOS conclusion is invalid (SP 3.64)."""


@dataclass(frozen=True)
class OosStructuredConclusion:
    """The structured out-of-sample conclusion (SP 3.64).

    ``performance`` (性能/风险) is the SP 3.38 return/risk metrics with the
    benchmark return and excess return (基准超额表现); ``coverage`` (覆盖) the
    SP 3.9 per-market coverage; ``stability`` (稳定性) the SP 3.58 stability
    conclusion; ``budget`` (试验预算) the SP 3.17 declared search budget;
    ``unresolved_limitations`` (未解决限制) the known caveats that prevent a
    clean ``QUALIFIED``.
    """

    version: str
    source: str
    market: Market
    dataset_fingerprint: str
    code_version: str
    performance: PerformanceMetrics
    benchmark_return: float
    excess_return: float
    coverage: MarketCoverage
    stability: StabilityConclusion
    budget: TrialBudget
    unresolved_limitations: tuple[str, ...]
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.version:
            raise OosConclusionError("conclusion version must be non-empty.")
        if not self.source:
            raise OosConclusionError("conclusion source must be non-empty.")
        if not self.dataset_fingerprint:
            raise OosConclusionError("dataset fingerprint must be non-empty.")
        if not self.code_version:
            raise OosConclusionError("code version must be non-empty.")
        if self.coverage.market is not self.market:
            raise OosConclusionError("coverage market does not match the conclusion market.")
        if self.stability.market is not self.market:
            raise OosConclusionError("stability market does not match the conclusion market.")
        if not math.isfinite(self.benchmark_return):
            raise OosConclusionError("benchmark return must be a finite number.")
        if not math.isfinite(self.excess_return):
            raise OosConclusionError("excess return must be a finite number.")
        if not all(limitation for limitation in self.unresolved_limitations):
            raise OosConclusionError("every unresolved limitation must be non-empty.")
        if not self.fingerprint:
            raise OosConclusionError("OOS conclusion fingerprint must be non-empty.")

    @property
    def overall(self) -> OOSConclusion:
        """The aggregate SP 3.1 OOS conclusion (SP 3.64)."""
        return _aggregate(self.stability.conclusion, self.unresolved_limitations)

    @property
    def overall_coverage_pct(self) -> float:
        """The overall data-coverage percentage (覆盖)."""
        return self.coverage.overall_pct

    @property
    def max_trials(self) -> int:
        """The declared trial budget cap (试验预算)."""
        return self.budget.max_trials

    @property
    def limitation_count(self) -> int:
        """The number of unresolved limitations (未解决限制)."""
        return len(self.unresolved_limitations)

    def readable(self) -> str:
        """Render the conclusion as one line."""
        return (
            f"OOS conclusion {self.overall.value} ({self.market.value}): "
            f"stability {self.stability.conclusion.value} "
            f"coverage {self.coverage.overall_pct:.1f}% "
            f"budget {self.budget.max_trials} "
            f"limitations {self.limitation_count}; "
            f"no return promise fp {self.fingerprint}"
        )


def _aggregate(
    stability: OOSConclusion,
    unresolved_limitations: tuple[str, ...],
) -> OOSConclusion:
    """Aggregate the stability conclusion and limitations (SP 3.64).

    A failing stability conclusion dominates to ``NOT_QUALIFIED``; an
    inconclusive stability conclusion or any unresolved limitation degrades to
    ``INCONCLUSIVE``; only a qualified stability with no unresolved limitation
    is ``QUALIFIED``.
    """
    if stability is OOSConclusion.NOT_QUALIFIED:
        return OOSConclusion.NOT_QUALIFIED
    if stability is OOSConclusion.INCONCLUSIVE or unresolved_limitations:
        return OOSConclusion.INCONCLUSIVE
    return OOSConclusion.QUALIFIED


def build_oos_conclusion(
    *,
    version: str = "conclusion-1.0",
    source: str = "pre-registered",
    market: Market,
    dataset_fingerprint: str,
    code_version: str,
    performance: PerformanceMetrics,
    benchmark_return: float,
    excess_return: float,
    coverage: MarketCoverage,
    stability: StabilityConclusion,
    budget: TrialBudget,
    unresolved_limitations: tuple[str, ...] = (),
) -> OosStructuredConclusion:
    """Assemble a fingerprint-stamped structured OOS conclusion (SP 3.64)."""
    conclusion = OosStructuredConclusion(
        version=version,
        source=source,
        market=market,
        dataset_fingerprint=dataset_fingerprint,
        code_version=code_version,
        performance=performance,
        benchmark_return=benchmark_return,
        excess_return=excess_return,
        coverage=coverage,
        stability=stability,
        budget=budget,
        unresolved_limitations=tuple(unresolved_limitations),
        fingerprint="unfingerprinted",
    )
    return replace(conclusion, fingerprint=oos_conclusion_fingerprint(conclusion))


def no_return_promise_statement() -> str:
    """Return the standard no-return-promise statement (SP 3.64)."""
    return (
        "This conclusion reports the historical out-of-sample performance only; "
        "it contains no projection, guarantee or promise of future returns "
        "(结论不含收益承诺, SP 3.64)."
    )


def _performance_payload(performance: PerformanceMetrics) -> dict[str, object]:
    """The SP 3.38 return/risk metrics as a JSON payload."""
    return {
        "start_date": performance.start_date.isoformat(),
        "end_date": performance.end_date.isoformat(),
        "periods": performance.periods,
        "cumulative_return": performance.cumulative_return,
        "annualized_return": performance.annualized_return,
        "annualized_volatility": performance.annualized_volatility,
        "max_drawdown": performance.max_drawdown,
        "sharpe_ratio": performance.sharpe_ratio,
        "calmar_ratio": performance.calmar_ratio,
        "downside_deviation": performance.downside_deviation,
    }


def _coverage_payload(coverage: MarketCoverage) -> dict[str, object]:
    """The SP 3.9 per-market coverage as a JSON payload."""
    return {
        "market": coverage.market.value,
        "overall_pct": coverage.overall_pct,
        "scores": [_score_payload(score) for score in coverage.scores],
    }


def _score_payload(score: CoverageScore) -> dict[str, object]:
    """One coverage score as a JSON payload."""
    return {
        "item": score.item.value,
        "coverage_pct": score.coverage_pct,
        "gap": score.measurement.gap,
    }


def _stability_payload(stability: StabilityConclusion) -> dict[str, object]:
    """The SP 3.58 stability conclusion as a JSON payload."""
    return {
        "conclusion": stability.conclusion.value,
        "reasons": list(stability.reasons),
        "fingerprint": stability.fingerprint,
    }


def _budget_payload(budget: TrialBudget) -> dict[str, object]:
    """The SP 3.17 trial budget as a JSON payload."""
    return {
        "max_trials": budget.max_trials,
        "random_seed": budget.random_seed,
        "tie_breaker": budget.tie_breaker.value,
        "early_stop": budget.early_stop.value,
    }


def oos_conclusion_json(conclusion: OosStructuredConclusion) -> str:
    """Return a stable, key-sorted JSON serialization of a conclusion.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style);
    every aggregate — performance, risk, coverage, stability, budget and the
    unresolved limitations — is embedded so the conclusion is fully auditable.
    """
    payload: dict[str, object] = {
        "version": conclusion.version,
        "source": conclusion.source,
        "market": conclusion.market.value,
        "dataset_fingerprint": conclusion.dataset_fingerprint,
        "code_version": conclusion.code_version,
        "overall": conclusion.overall.value,
        "performance": _performance_payload(conclusion.performance),
        "benchmark_return": conclusion.benchmark_return,
        "excess_return": conclusion.excess_return,
        "coverage": _coverage_payload(conclusion.coverage),
        "stability": _stability_payload(conclusion.stability),
        "budget": _budget_payload(conclusion.budget),
        "unresolved_limitations": list(conclusion.unresolved_limitations),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def oos_conclusion_fingerprint(conclusion: OosStructuredConclusion) -> str:
    """Return the stable SHA-256 fingerprint of a conclusion (SP 3.64)."""
    return hashlib.sha256(oos_conclusion_json(conclusion).encode("utf-8")).hexdigest()


__all__: tuple[str, ...] = (
    "OosConclusionError",
    "OosStructuredConclusion",
    "build_oos_conclusion",
    "no_return_promise_statement",
    "oos_conclusion_json",
    "oos_conclusion_fingerprint",
)
