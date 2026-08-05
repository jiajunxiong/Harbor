"""Storage-backed :class:`BacktestDataReader` (MVP 2 / SP 2.8).

Implements the :class:`harbor.core.backtest_interfaces.BacktestDataReader`
contract against the Harbor storage layer. Query building is delegated to the
market-scoped :class:`harbor.storage.repositories.Repository`; rows are mapped
to immutable domain records so the storage implementation is never leaked to
the engine.

The reader is point-in-time aware:

- :meth:`StorageBacktestDataReader.list_securities` returns only symbols that
  were listed and not yet delisted on the given date, so the universe is free
  of survivorship bias (SP 2.10). A security with an unknown listing date is
  excluded because its listing on that date cannot be confirmed.
- :meth:`StorageBacktestDataReader.fundamentals` applies the point-in-time
  availability rules (SP 2.9): a financial record is usable only when its
  ``disclosure_date`` is known and on or before the requested date. Records
  with an unknown disclosure date are refused rather than dated by guess.
"""

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from sqlalchemy import Connection, Select, or_, select

from harbor.core.adjustments import ActionTerms
from harbor.core.backtest_domain import Currency, Market, to_market_target
from harbor.core.backtest_interfaces import (
    AdjustmentFactor,
    BacktestDataReader,
    DailyQuote,
    Dividend,
    FundamentalRecord,
)
from harbor.core.equity import EntitlementEvent
from harbor.core.market_registry import CorporateActionType, get_market_config
from harbor.core.point_in_time import filter_available
from harbor.core.stock_pool import (
    StockPool,
    StockPoolMembership,
    evaluate_stock_pool,
)
from harbor.storage.models import (
    AdjustedFactor as AdjustedFactorModel,
)
from harbor.storage.models import (
    CorporateAction as CorporateActionModel,
)
from harbor.storage.models import (
    DailyQuote as DailyQuoteModel,
)
from harbor.storage.models import (
    Dividend as DividendModel,
)
from harbor.storage.models import (
    Financial as FinancialModel,
)
from harbor.storage.models import (
    Security as SecurityModel,
)
from harbor.storage.repositories import Repository


def _market(value: str) -> Market:
    """Map a stored market code to the backtest market enum."""
    return Market(value)


def _as_float(value: Any) -> float | None:
    """Return a numeric column value as ``float``, preserving ``None``."""
    if value is None:
        return None
    return float(value)


def _quote_from_row(row: Mapping[str, Any]) -> DailyQuote:
    """Map a daily quote row to the immutable :class:`DailyQuote` record."""
    return DailyQuote(
        market=_market(row["market"]),
        symbol=row["symbol"],
        day=row["date"],
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=row["volume"],
        adjusted_close=float(row["adjusted_close"]),
    )


def _dividend_from_row(row: Mapping[str, Any]) -> Dividend:
    """Map a dividend row to the immutable :class:`Dividend` record."""
    return Dividend(
        market=_market(row["market"]),
        symbol=row["symbol"],
        amount=float(row["amount"]),
        currency=Currency(row["currency"]),
        ex_date=row["ex_date"],
        record_date=row["record_date"],
        payment_date=row["payment_date"],
        is_special=(row["type"] == "special"),
    )


def _fundamental_from_row(row: Mapping[str, Any]) -> FundamentalRecord:
    """Map a financial row to an immutable :class:`FundamentalRecord`.

    ``available_on`` is the financial ``disclosure_date`` (SP 2.9); a missing
    disclosure date surfaces as ``None`` so the point-in-time filter refuses
    the record rather than guessing when it became knowable.
    """
    return FundamentalRecord(
        market=_market(row["market"]),
        symbol=row["symbol"],
        report_date=row["report_date"],
        fiscal_period=row["fiscal_period"],
        available_on=row.get("disclosure_date"),
        roe=_as_float(row.get("roe")),
        net_income=_as_float(row.get("net_income")),
        total_equity=_as_float(row.get("total_equity")),
        revenue=_as_float(row.get("revenue")),
    )


def _adjustment_factor_from_row(row: Mapping[str, Any]) -> AdjustmentFactor:
    """Map an adjusted factor row to an immutable :class:`AdjustmentFactor`."""
    return AdjustmentFactor(
        market=_market(row["market"]),
        symbol=row["symbol"],
        date=row["date"],
        cumulative_factor=float(row["cumulative_factor"]),
        daily_factor=float(row["daily_factor"]),
    )


def _entitlement_from_rows(
    action_row: Mapping[str, Any],
    term_rows: Sequence[Mapping[str, Any]],
) -> EntitlementEvent:
    """Map a corporate action row and its terms to an :class:`EntitlementEvent`.

    Raises:
        ValueError: If the stored action type is not a known corporate action
            type; a malformed stored type is surfaced rather than silently
            dropped.
    """
    ratio: float | None = None
    price: float | None = None
    for term in term_rows:
        if term["term_type"] == "ratio":
            ratio = float(term["value"])
        elif term["term_type"] == "price":
            price = float(term["value"])
    action_type_value = action_row["action_type"]
    try:
        action_type = CorporateActionType(action_type_value)
    except ValueError as exc:
        raise ValueError(
            f"Unknown corporate action type {action_type_value!r} for action "
            f"{action_row['action_id']!r}."
        ) from exc
    return EntitlementEvent(
        action_id=action_row["action_id"],
        action_type=action_type,
        terms=ActionTerms(ratio=ratio, price=price),
        record_date=action_row.get("record_date"),
        ex_date=action_row.get("ex_date"),
    )


def _entitlements_from_rows(
    action_rows: Sequence[Mapping[str, Any]],
    term_rows: Sequence[Mapping[str, Any]],
) -> tuple[EntitlementEvent, ...]:
    """Map corporate action rows and their terms to entitlement events."""
    terms_by_action: dict[str, list[Mapping[str, Any]]] = {}
    for term in term_rows:
        terms_by_action.setdefault(term["action_id"], []).append(term)
    return tuple(
        _entitlement_from_rows(row, terms_by_action.get(row["action_id"], ()))
        for row in action_rows
    )


def _securities_statement(market: Market, as_of: date) -> Select[Any]:
    """Return a survivorship-bias-free securities query for ``as_of``.

    A symbol qualifies when its listing date is known and on or before
    ``as_of`` and it is not yet delisted (delist date unknown or on/after
    ``as_of``).
    """
    return (
        select(SecurityModel)
        .where(
            SecurityModel.market == market.value,
            SecurityModel.list_date <= as_of,
            or_(
                SecurityModel.delist_date.is_(None),
                SecurityModel.delist_date >= as_of,
            ),
        )
        .order_by(SecurityModel.symbol)
    )


def _securities_rows_statement(market: Market) -> Select[Any]:
    """Return all securities for a market, ordered by symbol (for pool eval)."""
    return (
        select(SecurityModel)
        .where(SecurityModel.market == market.value)
        .order_by(SecurityModel.symbol)
    )


def _membership_from_row(
    row: Mapping[str, Any],
    source: str,
) -> StockPoolMembership:
    """Map a securities row to a :class:`StockPoolMembership`.

    The listing date becomes the inclusion (effective) date and the delisting
    date the expiry date (SP 2.10).
    """
    return StockPoolMembership(
        market=_market(row["market"]),
        symbol=row["symbol"],
        effective_date=row.get("list_date"),
        expiry_date=row.get("delist_date"),
        source=source,
    )


class StorageBacktestDataReader(BacktestDataReader):
    """BacktestDataReader backed by the Harbor storage repositories (SP 2.8)."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self._repository = Repository(connection)

    def _execute(self, statement: Select[Any]) -> Sequence[Mapping[str, Any]]:
        return [dict(row) for row in self._connection.execute(statement).mappings()]

    def list_securities(self, market: Market, as_of: date) -> Sequence[str]:
        statement = _securities_statement(market, as_of)
        return [row["symbol"] for row in self._execute(statement)]

    def stock_pool(
        self,
        market: Market,
        as_of: date,
        *,
        historical_known: bool,
    ) -> StockPool:
        """Return the market's historical stock pool evaluated on ``as_of``.

        Memberships are derived from the ``securities`` table (listing and
        delisting dates) and the market's configured stock pool source.
        ``historical_known`` declares whether the source provides historical
        constituents; when false, the pool is marked with survivorship-bias
        risk (SP 2.10).
        """
        source = get_market_config(to_market_target(market)).stock_pool_source
        statement = _securities_rows_statement(market)
        memberships = [_membership_from_row(row, source) for row in self._execute(statement)]
        return evaluate_stock_pool(
            market,
            as_of,
            memberships,
            source,
            historical_known=historical_known,
        )

    def daily_quotes(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[DailyQuote]:
        statement = self._repository.list_daily_quotes(market.value, symbol, start, end)
        statement = statement.order_by(DailyQuoteModel.date)
        return [_quote_from_row(row) for row in self._execute(statement)]

    def dividends(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[Dividend]:
        statement = self._repository.list_dividends(market.value, symbol)
        statement = statement.where(
            DividendModel.ex_date >= start,
            DividendModel.ex_date <= end,
        )
        statement = statement.order_by(DividendModel.ex_date)
        return [_dividend_from_row(row) for row in self._execute(statement)]

    def fundamentals(
        self,
        market: Market,
        symbol: str,
        as_of: date,
    ) -> Sequence[FundamentalRecord]:
        statement = self._repository.list_financials(market.value, symbol)
        statement = statement.order_by(FinancialModel.report_date)
        records = [_fundamental_from_row(row) for row in self._execute(statement)]
        return filter_available(records, as_of)

    def corporate_actions(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[EntitlementEvent]:
        statement = self._repository.list_corporate_actions(market.value, symbol)
        statement = statement.where(
            CorporateActionModel.ex_date >= start,
            CorporateActionModel.ex_date <= end,
        )
        statement = statement.order_by(
            CorporateActionModel.ex_date,
            CorporateActionModel.action_id,
        )
        action_rows = self._execute(statement)
        if not action_rows:
            return ()
        action_ids = [row["action_id"] for row in action_rows]
        term_statement = self._repository.list_action_terms(market.value, symbol, action_ids)
        term_rows = self._execute(term_statement)
        return _entitlements_from_rows(action_rows, term_rows)

    def adjustment_factors(
        self,
        market: Market,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[AdjustmentFactor]:
        statement = self._repository.list_adjusted_factors(market.value, symbol, start, end)
        statement = statement.order_by(AdjustedFactorModel.date)
        return [_adjustment_factor_from_row(row) for row in self._execute(statement)]
