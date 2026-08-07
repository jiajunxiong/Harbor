"""Benchmark definition and resolution (MVP 2 / SP 2.52).

Resolves the configured benchmark (SP 2.4 :class:`~harbor.core.backtest_config.BenchmarkConfig`)
into a day-by-day level series over the backtest's trading days, so the
portfolio can later be compared against a fixed, replayable reference. Three
kinds are supported:

- cash (现金): a flat reference with no market data — every day's level is 1.0
  (a zero return), independent of any index provider;
- single market index (单市场指数): the level of one configured index (e.g. a
  HK or US index symbol) supplied by an index-level provider;
- configurable blended benchmark (配置化混合基准): a weighted mix of cash and
  one or more market-index legs, whose weights sum to 1.0 (validated in the
  config, SP 2.4).

Reliable benchmark data is a hard requirement: when an index leg has no
available level for a day, the resolver raises :class:`BenchmarkDataError`
rather than fabricating a value (缺少可靠基准数据时禁止虚构超额收益). Because a
missing index leg refuses the whole series, any excess-return calculation over
that benchmark is likewise forbidden rather than silently wrong.

Pure core logic: depends only on the config and the backtest domain types;
never touches storage or CLI code.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date

from harbor.core.backtest_config import BenchmarkConfig, BenchmarkKind
from harbor.core.backtest_domain import Market


class BenchmarkDataError(ValueError):
    """Raised when reliable benchmark data is missing (SP 2.52)."""


@dataclass(frozen=True)
class BenchmarkLevel:
    """One benchmark level on one day (SP 2.52).

    Cash levels are fixed at 1.0; index levels are the provider's raw index
    level. The series' total return is ``last / first - 1``.
    """

    as_of: date
    level: float
    kind: BenchmarkKind


@dataclass(frozen=True)
class BenchmarkSeries:
    """A resolved benchmark level series over the backtest's trading days."""

    kind: BenchmarkKind
    levels: tuple[BenchmarkLevel, ...]

    def __post_init__(self) -> None:
        if not self.levels:
            raise ValueError("A benchmark series requires at least one level.")
        if any(level.level <= 0 for level in self.levels):
            raise ValueError("Benchmark levels must be positive.")

    @property
    def start_date(self) -> date:
        """Return the first day of the series."""
        return self.levels[0].as_of

    @property
    def end_date(self) -> date:
        """Return the last day of the series."""
        return self.levels[-1].as_of

    def total_return(self) -> float:
        """Return the total benchmark return over the series.

        ``last / first - 1``; a single-day series returns ``0.0``.
        """
        first = self.levels[0].level
        last = self.levels[-1].level
        return last / first - 1.0

    def returns(self) -> tuple[float, ...]:
        """Return the period-by-period returns (empty for a single day)."""
        if len(self.levels) < 2:
            return ()
        return tuple(
            later.level / earlier.level - 1.0
            for earlier, later in zip(self.levels, self.levels[1:])
        )

    def readable(self) -> str:
        """Render the benchmark series as a human-readable summary."""
        lines = [
            f"Benchmark ({self.kind.value}) from {self.start_date.isoformat()} to "
            f"{self.end_date.isoformat()}: total return {self.total_return():.4%}"
        ]
        for level in self.levels:
            lines.append(f"  {level.as_of.isoformat()}: {level.level:.4f}")
        return "\n".join(lines)


def resolve_benchmark_series(
    *,
    config: BenchmarkConfig,
    days: Sequence[date],
    index_level: Callable[[Market, str, date], float | None],
) -> BenchmarkSeries:
    """Resolve the configured benchmark into a day-by-day level series (SP 2.52).

    Args:
        config: The benchmark definition (SP 2.4).
        days: The trading days the series must cover, in order.
        index_level: Returns the reliable index level for ``(market, symbol)``
            on a day, or ``None`` when the data is missing.

    Returns:
        A :class:`BenchmarkSeries` with one level per day.

    Raises:
        BenchmarkDataError: If an index leg has no reliable level for any day
            (missing benchmark data is refused, never fabricated).
    """
    levels: list[BenchmarkLevel] = []
    for day in days:
        if config.kind is BenchmarkKind.CASH:
            levels.append(BenchmarkLevel(as_of=day, level=1.0, kind=BenchmarkKind.CASH))
        elif config.kind is BenchmarkKind.MARKET_INDEX:
            assert config.market is not None and config.symbol
            level = index_level(config.market, config.symbol, day)
            if level is None or level <= 0:
                raise BenchmarkDataError(
                    f"Missing reliable level for {config.symbol} on "
                    f"{day.isoformat()}; refusing to fabricate a benchmark return."
                )
            levels.append(BenchmarkLevel(as_of=day, level=level, kind=BenchmarkKind.MARKET_INDEX))
        else:  # BLENDED
            level = _blended_level(config, day, index_level)
            levels.append(BenchmarkLevel(as_of=day, level=level, kind=BenchmarkKind.BLENDED))
    return BenchmarkSeries(kind=config.kind, levels=tuple(levels))


def _blended_level(
    config: BenchmarkConfig,
    day: date,
    index_level: Callable[[Market, str, date], float | None],
) -> float:
    """Compute the blended benchmark level for one day (SP 2.52).

    Cash legs contribute their weight times a level of 1.0; each market-index
    leg contributes its weight times the provider's level. A missing index leg
    refuses the whole blended level rather than fabricating a value.
    """
    total = config.cash_weight
    for component in config.components:
        level = index_level(component.market, component.symbol, day)
        if level is None or level <= 0:
            raise BenchmarkDataError(
                f"Missing reliable level for {component.symbol} on "
                f"{day.isoformat()}; refusing to fabricate a blended benchmark return."
            )
        total += component.weight * level
    return total


def excess_return(*, portfolio_return: float, benchmark_return: float) -> float:
    """Return the portfolio return minus the benchmark return (SP 2.52).

    The benchmark return must come from a resolved :class:`BenchmarkSeries`;
    when reliable benchmark data is missing the resolver raises
    :class:`BenchmarkDataError` before this function can be reached, so an
    excess return is never fabricated.
    """
    return portfolio_return - benchmark_return
