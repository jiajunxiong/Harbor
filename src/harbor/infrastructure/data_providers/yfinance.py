"""yfinance-backed market data providers for Harbor."""

import importlib
import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, cast

from harbor.config import MarketTarget
from harbor.core.interfaces import Capability, MarketDataProvider, ProviderCapabilities
from harbor.core.market_registry import get_market_config

_ALL_CAPABILITIES = frozenset(Capability)


def _column_value(
    columns: Mapping[str, Sequence[object]],
    name: str,
    index: int,
) -> object | None:
    """Return a column value at an index, or ``None`` when absent."""
    values = columns.get(name)
    if values is None or index >= len(values):
        return None
    return values[index]


def _optional_float(value: object) -> float | None:
    """Coerce a value to a finite float, or ``None`` when missing or invalid."""
    if value is None:
        return None
    if not isinstance(value, (int, float, str)) or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _as_date(value: date | datetime) -> date:
    """Normalize a timezone-aware timestamp to its calendar date."""
    if isinstance(value, datetime):
        return value.date()
    return value


def standardize_daily_quotes(
    market: MarketTarget,
    symbol: str,
    dates: Sequence[date | datetime],
    columns: Mapping[str, Sequence[object]],
    source: str,
) -> list[dict[str, Any]]:
    """Normalize yfinance-style OHLCV columns into daily quote rows.

    The ``Close``/``Adj Close`` pair follows yfinance's ``auto_adjust=False``
    output, so the split/dividend-adjusted close is preserved alongside the raw
    close. Rows with missing OHLC (suspended sessions) are dropped, and the
    adjusted close falls back to the raw close when no adjusted column is
    provided.
    """
    rows: list[dict[str, Any]] = []
    for index, day in enumerate(dates):
        open_price = _optional_float(_column_value(columns, "Open", index))
        high_price = _optional_float(_column_value(columns, "High", index))
        low_price = _optional_float(_column_value(columns, "Low", index))
        close_price = _optional_float(_column_value(columns, "Close", index))
        if open_price is None or high_price is None or low_price is None or close_price is None:
            continue
        adjusted_value = _column_value(columns, "Adj Close", index)
        adjusted_close = _optional_float(adjusted_value) or close_price
        volume_number = _optional_float(_column_value(columns, "Volume", index))
        volume = int(volume_number) if volume_number is not None else 0
        rows.append(
            {
                "market": market.value,
                "symbol": symbol,
                "date": _as_date(day),
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": volume,
                "adjusted_close": adjusted_close,
                "source": source,
            }
        )
    return rows


def _index_date(value: object) -> date | None:
    """Convert a yfinance index value (date/datetime/Timestamp) to a date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _in_range(value: object, start: date, end: date) -> bool:
    """Return whether an index value falls within a closed date range."""
    day = _index_date(value)
    return day is not None and start <= day <= end


def standardize_dividends(
    market: MarketTarget,
    symbol: str,
    dividends: Mapping[object, object],
    source: str,
) -> list[dict[str, Any]]:
    """Normalize yfinance dividend payouts into dividend rows.

    yfinance reports cash dividends as ex-date to per-share amount pairs. The
    record and payment dates are not available from yfinance, so they remain
    unset, and the type defaults to ``regular``.
    """
    rows: list[dict[str, Any]] = []
    for date_value, amount_value in sorted(
        dividends.items(),
        key=lambda item: _index_date(item[0]) or date.min,
    ):
        ex_date = _index_date(date_value)
        amount = _optional_float(amount_value)
        if ex_date is None or amount is None:
            continue
        rows.append(
            {
                "market": market.value,
                "symbol": symbol,
                "ex_date": ex_date,
                "record_date": None,
                "payment_date": None,
                "amount": amount,
                "type": "regular",
                "currency": get_market_config(market).currency,
            }
        )
    return rows


def standardize_splits(
    market: MarketTarget,
    symbol: str,
    splits: Mapping[object, object],
    source: str,
) -> list[dict[str, Any]]:
    """Normalize yfinance split factors into corporate action rows."""
    rows: list[dict[str, Any]] = []
    for index, (date_value, factor_value) in enumerate(
        sorted(splits.items(), key=lambda item: _index_date(item[0]) or date.min)
    ):
        factor = _optional_float(factor_value)
        ex_date = _index_date(date_value)
        if ex_date is None or factor is None or factor == 0:
            continue
        rows.append(
            {
                "market": market.value,
                "symbol": symbol,
                "action_id": f"{symbol}-split-{index + 1}",
                "announce_date": None,
                "ex_date": ex_date,
                "record_date": None,
                "effective_date": None,
                "action_type": "split",
                "status": "completed",
                "source": source,
            }
        )
    return rows


class YFinanceProvider(MarketDataProvider):
    """Base class for yfinance-backed providers.

    The ``yfinance`` package is imported lazily so that importing this module
    does not require the package to be installed.
    """

    def _normalize_symbol(self, symbol: str) -> str:
        """Normalize a raw symbol to the yfinance ticker form."""
        raise NotImplementedError

    def _ticker(self, symbol: str) -> Any:
        """Return a yfinance Ticker for a market-normalized symbol."""
        yfinance = cast(Any, importlib.import_module("yfinance"))
        return yfinance.Ticker(self._normalize_symbol(symbol))

    def _require_market(self, market: MarketTarget) -> None:
        """Reject a market the provider does not serve."""
        if market not in self.capabilities().markets():
            raise ValueError(f"{type(self).__name__} does not support {market.value!r}.")

    def fetch_daily_quotes(
        self,
        market: MarketTarget,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[Mapping[str, Any]]:
        """Fetch and standardize daily quotes for a symbol from yfinance."""
        self._require_market(market)
        if end < start:
            raise ValueError("end must not be earlier than start.")
        frame = self._ticker(symbol).history(start=start, end=end, auto_adjust=False)
        return standardize_daily_quotes(
            market,
            symbol,
            list(frame.index),
            dict(frame.to_dict("list")),
            source="yfinance",
        )

    def fetch_dividends(
        self,
        market: MarketTarget,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[Mapping[str, Any]]:
        """Fetch and standardize dividends for a symbol from yfinance."""
        self._require_market(market)
        if end < start:
            raise ValueError("end must not be earlier than start.")
        raw = dict(self._ticker(symbol).dividends)
        filtered = {
            date_value: amount
            for date_value, amount in raw.items()
            if _in_range(date_value, start, end)
        }
        return standardize_dividends(market, symbol, filtered, "yfinance")

    def fetch_corporate_actions(
        self,
        market: MarketTarget,
        symbol: str,
        start: date,
        end: date,
    ) -> Sequence[Mapping[str, Any]]:
        """Fetch and standardize split actions for a symbol from yfinance."""
        self._require_market(market)
        if end < start:
            raise ValueError("end must not be earlier than start.")
        raw = dict(self._ticker(symbol).splits)
        filtered = {
            date_value: factor
            for date_value, factor in raw.items()
            if _in_range(date_value, start, end)
        }
        return standardize_splits(market, symbol, filtered, "yfinance")


class HKYFinanceProvider(YFinanceProvider):
    """yfinance-backed provider for the Hong Kong market."""

    def capabilities(self) -> ProviderCapabilities:
        """Return the capabilities the provider offers for Hong Kong."""
        return ProviderCapabilities({MarketTarget.HK: _ALL_CAPABILITIES})

    def _normalize_symbol(self, symbol: str) -> str:
        """Return a 4-digit HK code with the ``.HK`` suffix."""
        return f"{symbol.removesuffix('.HK')}.HK"


class USYFinanceProvider(YFinanceProvider):
    """yfinance-backed provider for the United States market."""

    def capabilities(self) -> ProviderCapabilities:
        """Return the capabilities the provider offers for the United States."""
        return ProviderCapabilities({MarketTarget.US: _ALL_CAPABILITIES})

    def _normalize_symbol(self, symbol: str) -> str:
        """Return the ticker as given, trimmed and uppercased."""
        return symbol.strip().upper()
