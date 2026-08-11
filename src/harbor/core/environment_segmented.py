"""Environment-segmented performance (MVP 3 / SP 3.50).

For each pre-registered environment regime (SP 3.48), outputs the strategy and
benchmark returns, drawdown, risk (volatility / Sharpe), turnover, costs and
data-coverage score over the OOS days classified into that regime by the SP
3.49 historical attribution (分环境输出策略与基准的收益、回撤、风险、换手、成本
和覆盖评分); a segment with insufficient samples is explicitly labeled instead
of being silently presented as meaningful (样本不足明确标注).

The strategy metrics are computed from the SP 3.37 OOS equity path's
day-over-day returns on the days the regime was active, so a sparse segment is
annualized over its own sample count rather than a fabricated contiguous
window. Benchmark returns, turnover, costs and coverage are injected per day
via callables and aggregated over the segment days. Every pre-registered
regime is reported — a regime that never occurred in OOS appears as a zero-day
insufficient segment, never silently omitted.

- :class:`EnvironmentSegmentPerformance` is one (dimension, regime) segment's
  metrics;
- :class:`EnvironmentSegmentedPerformance` spans all pre-registered regimes
  and records the definition-set version/fingerprint (SP 3.48) plus the frozen
  dataset fingerprint and code version, with a re-derivable SHA-256
  fingerprint for replayability (SP 3.46).

Pure core layer: depends only on the SP 3.49 attribution, the SP 3.37 path,
the SP 3.48 definitions, the SP 2.53 metric helper and the domain types, never
on storage, services or CLI.
"""

import hashlib
import json
import math
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date

from harbor.core.backtest_domain import NetValue
from harbor.core.environment_attribution import EnvironmentAttributionReport
from harbor.core.market_environment import (
    EnvironmentDefinitionSet,
    EnvironmentDimension,
)
from harbor.core.oos_concat import OosEquityPath
from harbor.core.performance_metrics import max_drawdown


class EnvironmentSegmentedError(ValueError):
    """Raised when environment-segmented performance is invalid (SP 3.50)."""


@dataclass(frozen=True)
class EnvironmentSegmentPerformance:
    """One (dimension, regime) segment's performance metrics (SP 3.50).

    ``day_count`` is the number of OOS days classified into the regime by the
    SP 3.49 attribution; below ``min_samples`` the segment is marked
    ``sufficient=False`` with a recorded ``insufficient_reason`` and NO
    computed metrics (样本不足明确标注 — never silently presented as
    meaningful). ``strategy_return`` / ``strategy_drawdown`` /
    ``strategy_volatility`` / ``strategy_sharpe`` come from the OOS path's
    day-over-day returns on the segment days; ``benchmark_return``,
    ``turnover``, ``costs`` and ``coverage_pct`` are injected per-day values
    aggregated over the segment days.
    """

    dimension: EnvironmentDimension
    regime_name: str
    day_count: int
    sufficient: bool
    insufficient_reason: str | None
    strategy_return: float | None
    strategy_drawdown: float | None
    strategy_volatility: float | None
    strategy_sharpe: float | None
    benchmark_return: float | None
    excess_return: float | None
    turnover: float | None
    costs: float | None
    coverage_pct: float | None

    def __post_init__(self) -> None:
        if not self.regime_name:
            raise EnvironmentSegmentedError("regime name must be non-empty.")
        if self.day_count < 0:
            raise EnvironmentSegmentedError("segment day count must be non-negative.")
        if self.sufficient:
            if self.insufficient_reason is not None:
                raise EnvironmentSegmentedError(
                    "a sufficient segment must not carry an insufficient-sample reason."
                )
        else:
            if not self.insufficient_reason:
                raise EnvironmentSegmentedError(
                    "an insufficient segment must carry an insufficient-sample reason."
                )
            if any(
                metric is not None
                for metric in (
                    self.strategy_return,
                    self.strategy_drawdown,
                    self.strategy_volatility,
                    self.strategy_sharpe,
                    self.benchmark_return,
                    self.excess_return,
                    self.turnover,
                    self.costs,
                    self.coverage_pct,
                )
            ):
                raise EnvironmentSegmentedError(
                    "an insufficient segment must not carry computed metrics."
                )
        if self.strategy_return is not None and self.benchmark_return is not None:
            if self.excess_return is None:
                raise EnvironmentSegmentedError(
                    "excess return is required when both strategy and benchmark "
                    "returns are present."
                )
            if not math.isclose(
                self.excess_return,
                self.strategy_return - self.benchmark_return,
                abs_tol=1e-9,
            ):
                raise EnvironmentSegmentedError(
                    "excess return must equal the strategy return minus the benchmark return."
                )
        elif self.excess_return is not None:
            raise EnvironmentSegmentedError(
                "excess return requires both strategy and benchmark returns."
            )
        if self.strategy_sharpe is not None and self.strategy_volatility is None:
            raise EnvironmentSegmentedError(
                "a strategy Sharpe ratio requires an annualized volatility."
            )
        if self.strategy_volatility is not None and self.strategy_volatility < 0:
            raise EnvironmentSegmentedError("strategy volatility must be non-negative.")

    def readable(self) -> str:
        """Render the segment as one line."""
        status = "sufficient" if self.sufficient else str(self.insufficient_reason)
        return (
            f"{self.dimension.value}/{self.regime_name}: {self.day_count} day(s) {status} "
            f"strategy {self.strategy_return} drawdown {self.strategy_drawdown} "
            f"benchmark {self.benchmark_return} turnover {self.turnover} "
            f"costs {self.costs} coverage {self.coverage_pct}"
        )


@dataclass(frozen=True)
class EnvironmentSegmentedPerformance:
    """The environment-segmented performance across all regimes (SP 3.50).

    ``segments`` cover every pre-registered SP 3.48 regime, ordered by
    ``(dimension.value, regime_name)``; ``definition_version`` /
    ``definition_fingerprint`` identify the regime set used, ``dataset_fingerprint``
    / ``code_version`` the frozen evaluation context, ``min_samples`` the
    insufficient-sample threshold and ``fingerprint`` the re-derivable SHA-256
    digest.
    """

    segments: tuple[EnvironmentSegmentPerformance, ...]
    definition_version: str
    definition_fingerprint: str
    dataset_fingerprint: str
    code_version: str
    min_samples: int
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.segments:
            raise EnvironmentSegmentedError(
                "an environment-segmented performance report requires at least one segment."
            )
        previous: tuple[str, str] | None = None
        for segment in self.segments:
            key = (segment.dimension.value, segment.regime_name)
            if previous is not None and key <= previous:
                raise EnvironmentSegmentedError(
                    "segments must be ordered by dimension then regime name."
                )
            previous = key
        if not self.definition_version:
            raise EnvironmentSegmentedError("definition version must be non-empty.")
        if not self.definition_fingerprint:
            raise EnvironmentSegmentedError("definition fingerprint must be non-empty.")
        if not self.dataset_fingerprint:
            raise EnvironmentSegmentedError("dataset fingerprint must be non-empty.")
        if not self.code_version:
            raise EnvironmentSegmentedError("code version must be non-empty.")
        if self.min_samples < 1:
            raise EnvironmentSegmentedError("min_samples must be positive.")
        if not self.fingerprint:
            raise EnvironmentSegmentedError(
                "environment-segmented performance fingerprint must be non-empty."
            )

    def __len__(self) -> int:
        return len(self.segments)

    def __iter__(self) -> Iterator[EnvironmentSegmentPerformance]:
        return iter(self.segments)

    def __getitem__(self, index: int) -> EnvironmentSegmentPerformance:
        return self.segments[index]

    def segment(
        self, dimension: EnvironmentDimension, regime_name: str
    ) -> EnvironmentSegmentPerformance | None:
        """Return the segment for one regime (None when not reported)."""
        for segment in self.segments:
            if segment.dimension is dimension and segment.regime_name == regime_name:
                return segment
        return None

    @property
    def day_count(self) -> int:
        """Total number of classified OOS days across all segments."""
        return sum(segment.day_count for segment in self.segments)

    @property
    def insufficient_count(self) -> int:
        """Number of segments with insufficient samples."""
        return sum(1 for segment in self.segments if not segment.sufficient)

    @property
    def sufficient_count(self) -> int:
        """Number of segments with sufficient samples."""
        return len(self.segments) - self.insufficient_count

    def readable(self) -> str:
        """Render the report as one line."""
        return (
            f"{len(self.segments)} segments, {self.day_count} OOS days, "
            f"{self.insufficient_count} insufficient (min {self.min_samples}) "
            f"env {self.definition_version} fp {self.fingerprint}"
        )


def _sample_std(values: Sequence[float]) -> float:
    """Return the sample standard deviation (ddof=1) of the values."""
    mean = sum(values) / len(values)
    squared = sum((value - mean) ** 2 for value in values)
    return math.sqrt(squared / (len(values) - 1))


def _strategy_metrics(
    days: Sequence[date],
    day_values: Mapping[date, NetValue],
    day_returns: Mapping[date, float],
    *,
    periods_per_year: float,
    risk_free_rate: float,
) -> tuple[float | None, float | None, float | None, float | None]:
    """Return (return, drawdown, volatility, sharpe) for one segment.

    The return and risk metrics are computed from the OOS path's day-over-day
    returns on the segment days; the drawdown reuses the SP 2.53 helper over
    the segment's own net-value sub-series. Uncomputable metrics (e.g. fewer
    than two observations) are ``None``, never fabricated.
    """
    returns = [day_returns[day] for day in days if day in day_returns]
    strategy_return: float | None = None
    if returns:
        product = 1.0
        for ret in returns:
            product *= 1.0 + ret
        strategy_return = product - 1.0
    strategy_volatility: float | None = None
    if len(returns) >= 2:
        strategy_volatility = _sample_std(returns) * math.sqrt(periods_per_year)
    strategy_sharpe: float | None = None
    if (
        strategy_return is not None
        and returns
        and strategy_volatility is not None
        and strategy_volatility > 0
    ):
        annualized = math.pow(1.0 + strategy_return, periods_per_year / len(returns)) - 1.0
        strategy_sharpe = (annualized - risk_free_rate) / strategy_volatility
    series = [day_values[day] for day in days if day in day_values]
    strategy_drawdown: float | None = None
    if len(series) >= 2:
        strategy_drawdown = max_drawdown(series)
    return strategy_return, strategy_drawdown, strategy_volatility, strategy_sharpe


def _aggregate_daily(
    days: Sequence[date],
    value_for: Callable[[date], float | None] | None,
    *,
    compounded: bool,
) -> float | None:
    """Aggregate an injected per-day value over the segment days.

    Compounded values (returns) multiply ``(1 + value)``; additive values
    (turnover, costs) sum. ``None`` when no day yields a value or the callable
    is not provided.
    """
    if value_for is None:
        return None
    collected: list[float] = []
    for day in days:
        value = value_for(day)
        if value is not None:
            collected.append(value)
    if not collected:
        return None
    if compounded:
        product = 1.0
        for value in collected:
            product *= 1.0 + value
        return product - 1.0
    return sum(collected)


def _coverage_mean(
    days: Sequence[date],
    value_for: Callable[[date], float | None] | None,
) -> float | None:
    """Return the mean injected coverage score over the segment days."""
    if value_for is None:
        return None
    collected: list[float] = []
    for day in days:
        value = value_for(day)
        if value is not None:
            collected.append(value)
    if not collected:
        return None
    return sum(collected) / len(collected)


def compute_environment_segments(
    attribution: EnvironmentAttributionReport,
    path: OosEquityPath,
    *,
    definition_set: EnvironmentDefinitionSet,
    min_samples: int = 20,
    periods_per_year: float = 252.0,
    risk_free_rate: float = 0.0,
    benchmark_return_for: Callable[[date], float | None] | None = None,
    turnover_for: Callable[[date], float | None] | None = None,
    cost_for: Callable[[date], float | None] | None = None,
    coverage_for: Callable[[date], float | None] | None = None,
) -> EnvironmentSegmentedPerformance:
    """Segment the OOS performance by environment regime (SP 3.50).

    Every pre-registered SP 3.48 regime gets one segment covering the OOS days
    the SP 3.49 attribution classified into it; a regime with fewer than
    ``min_samples`` days is explicitly marked insufficient with a recorded
    reason and no computed metrics (样本不足明确标注).

    Args:
        attribution: The SP 3.49 historical environment attribution.
        path: The SP 3.37 concatenated OOS equity path.
        definition_set: The pre-registered environment regimes (SP 3.48); must
            match the attribution's frozen definition fingerprint.
        min_samples: Minimum OOS days for a segment to be considered
            statistically sufficient.
        periods_per_year: Annualization factor for risk metrics.
        risk_free_rate: Annual risk-free rate for the segment Sharpe ratio.
        benchmark_return_for: Per-day benchmark return (fraction) on a day.
        turnover_for: Per-day turnover on a day.
        cost_for: Per-day costs on a day.
        coverage_for: Per-day coverage score (0-100) on a day.
    """
    if min_samples < 1:
        raise EnvironmentSegmentedError("min_samples must be positive.")
    if periods_per_year <= 0:
        raise EnvironmentSegmentedError("periods_per_year must be positive.")
    if definition_set.fingerprint != attribution.definition_fingerprint:
        raise EnvironmentSegmentedError(
            "the environment definition set does not match the attribution's "
            "frozen definition fingerprint."
        )
    if path.dataset_fingerprint != attribution.dataset_fingerprint:
        raise EnvironmentSegmentedError(
            "the OOS equity path does not match the attribution's frozen dataset."
        )

    day_values: dict[date, NetValue] = {}
    for net in path.net_values:
        day_values[net.as_of_date] = net
    day_returns: dict[date, float] = {}
    for earlier, later in zip(path.net_values, path.net_values[1:]):
        if earlier.total_value > 0:
            day_returns[later.as_of_date] = later.total_value / earlier.total_value - 1.0

    regime_days: dict[tuple[EnvironmentDimension, str], list[date]] = {}
    for fold in attribution.folds:
        for label in fold.labels:
            for name in label.regime_names:
                regime_days.setdefault((label.dimension, name), []).append(label.as_of)

    segments: list[EnvironmentSegmentPerformance] = []
    for regime in definition_set.regimes:
        days = tuple(sorted(set(regime_days.get((regime.dimension, regime.name), []))))
        day_count = len(days)
        if day_count < min_samples:
            segments.append(
                EnvironmentSegmentPerformance(
                    dimension=regime.dimension,
                    regime_name=regime.name,
                    day_count=day_count,
                    sufficient=False,
                    insufficient_reason=(
                        f"insufficient samples: {day_count} OOS day(s) below the "
                        f"{min_samples}-day minimum"
                    ),
                    strategy_return=None,
                    strategy_drawdown=None,
                    strategy_volatility=None,
                    strategy_sharpe=None,
                    benchmark_return=None,
                    excess_return=None,
                    turnover=None,
                    costs=None,
                    coverage_pct=None,
                )
            )
            continue
        strategy_return, strategy_drawdown, strategy_volatility, strategy_sharpe = (
            _strategy_metrics(
                days,
                day_values,
                day_returns,
                periods_per_year=periods_per_year,
                risk_free_rate=risk_free_rate,
            )
        )
        benchmark_return = _aggregate_daily(days, benchmark_return_for, compounded=True)
        turnover = _aggregate_daily(days, turnover_for, compounded=False)
        costs = _aggregate_daily(days, cost_for, compounded=False)
        coverage_pct = _coverage_mean(days, coverage_for)
        excess_return: float | None = None
        if strategy_return is not None and benchmark_return is not None:
            excess_return = strategy_return - benchmark_return
        segments.append(
            EnvironmentSegmentPerformance(
                dimension=regime.dimension,
                regime_name=regime.name,
                day_count=day_count,
                sufficient=True,
                insufficient_reason=None,
                strategy_return=strategy_return,
                strategy_drawdown=strategy_drawdown,
                strategy_volatility=strategy_volatility,
                strategy_sharpe=strategy_sharpe,
                benchmark_return=benchmark_return,
                excess_return=excess_return,
                turnover=turnover,
                costs=costs,
                coverage_pct=coverage_pct,
            )
        )

    ordered = tuple(sorted(segments, key=lambda s: (s.dimension.value, s.regime_name)))
    report = EnvironmentSegmentedPerformance(
        segments=ordered,
        definition_version=definition_set.version,
        definition_fingerprint=definition_set.fingerprint,
        dataset_fingerprint=attribution.dataset_fingerprint,
        code_version=attribution.code_version,
        min_samples=min_samples,
        fingerprint="unfingerprinted",
    )
    return replace(report, fingerprint=environment_segments_fingerprint(report))


def environment_segments_json(report: EnvironmentSegmentedPerformance) -> str:
    """Return a stable, key-sorted JSON serialization of segmented performance.

    The derived ``fingerprint`` field is intentionally excluded so the digest
    can be re-derived and compared against the recorded value (SP 3.7 style).
    """
    payload: dict[str, object] = {
        "definition_version": report.definition_version,
        "definition_fingerprint": report.definition_fingerprint,
        "dataset_fingerprint": report.dataset_fingerprint,
        "code_version": report.code_version,
        "min_samples": report.min_samples,
        "segments": [
            {
                "dimension": segment.dimension.value,
                "regime_name": segment.regime_name,
                "day_count": segment.day_count,
                "sufficient": segment.sufficient,
                "insufficient_reason": segment.insufficient_reason,
                "strategy_return": segment.strategy_return,
                "strategy_drawdown": segment.strategy_drawdown,
                "strategy_volatility": segment.strategy_volatility,
                "strategy_sharpe": segment.strategy_sharpe,
                "benchmark_return": segment.benchmark_return,
                "excess_return": segment.excess_return,
                "turnover": segment.turnover,
                "costs": segment.costs,
                "coverage_pct": segment.coverage_pct,
            }
            for segment in report.segments
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def environment_segments_fingerprint(report: EnvironmentSegmentedPerformance) -> str:
    """Return the stable SHA-256 fingerprint of segmented performance (SP 3.50)."""
    return hashlib.sha256(environment_segments_json(report).encode("utf-8")).hexdigest()


__all__: tuple[str, ...] = (
    "EnvironmentSegmentedError",
    "EnvironmentSegmentPerformance",
    "EnvironmentSegmentedPerformance",
    "compute_environment_segments",
    "environment_segments_fingerprint",
    "environment_segments_json",
)
