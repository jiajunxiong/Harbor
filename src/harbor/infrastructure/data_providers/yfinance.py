"""yfinance-backed market data providers for Harbor."""

import importlib
import math
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from html.parser import HTMLParser
from typing import Any, cast

from harbor.config import MarketTarget
from harbor.core.interfaces import Capability, MarketDataProvider, ProviderCapabilities
from harbor.core.market_registry import get_market_config

_ALL_CAPABILITIES = frozenset(Capability)

# The current constituents of the Hang Seng Index (HSI) and the S&P 500 are
# parsed from their Wikipedia articles. Their symbols are consistent with
# Yahoo Finance, so they map directly onto the yfinance ticker forms used by
# ``fetch_daily_quotes``.
_WIKIPEDIA_USER_AGENT = "Mozilla/5.0 (Harbor research data tool)"
_CONSTITUENTS_EXCHANGE: dict[MarketTarget, str] = {
    MarketTarget.HK: "HKEX",
    MarketTarget.US: "US",
}


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


def _report_date(info: Mapping[str, object]) -> date:
    """Return the report date from a yfinance info snapshot, else today."""
    quarter = _index_date(info.get("mostRecentQuarter"))
    if quarter is not None:
        return quarter
    return date.today()


def standardize_financials(
    market: MarketTarget,
    symbol: str,
    info: Mapping[str, object],
    report_date: date,
) -> list[dict[str, Any]]:
    """Extract key financial metrics from a yfinance info snapshot.

    Missing metrics are retained as ``None`` (the financials columns are
    nullable); if no metric is present at all the snapshot yields no row.
    """
    roe = _optional_float(info.get("returnOnEquity"))
    net_income = _optional_float(info.get("netIncomeToCommon"))
    total_equity = _optional_float(info.get("totalStockholderEquity"))
    revenue = _optional_float(info.get("totalRevenue"))
    if all(value is None for value in (roe, net_income, total_equity, revenue)):
        return []
    return [
        {
            "market": market.value,
            "symbol": symbol,
            "report_date": report_date,
            "fiscal_period": str(report_date.year),
            "roe": roe,
            "net_income": net_income,
            "total_equity": total_equity,
            "revenue": revenue,
        }
    ]


def _hsi_symbol(code: str) -> str | None:
    """Convert a 5-digit HKEX code to the 4-digit yfinance form (00005 -> 0005.HK)."""
    return f"{int(code):04d}.HK" if code.isdigit() else None


def _us_symbol(code: str) -> str | None:
    """Return a US ticker as-is, trimmed and uppercased."""
    cleaned = code.strip().upper()
    return cleaned if cleaned else None


class _WikipediaConstituentParser(HTMLParser):
    """Extract index constituents from a Wikipedia table.

    The parser keeps only the first table whose header row contains
    ``header_marker`` and converts each data row's first cell via
    ``convert_symbol`` (returning ``None`` for rows that are not real
    constituents, e.g. section headers or totals); the second cell becomes the
    display name.
    """

    def __init__(
        self,
        header_marker: str,
        convert_symbol: Callable[[str], str | None],
    ) -> None:
        super().__init__()
        self._header_marker = header_marker
        self._convert_symbol = convert_symbol
        self._in_table = False
        self._is_target = False
        self._row: list[str] | None = None
        self._cell: str | None = None
        self._row_index = 0
        self.constituents: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._in_table = True
            self._is_target = False
            self._row_index = 0
        elif tag == "tr" and self._in_table:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = ""

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell += data

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th"):
            if self._row is not None and self._cell is not None:
                self._row.append(self._cell.strip())
            self._cell = None
        elif tag == "tr":
            if self._row is not None and self._in_table:
                self._row_index += 1
                row = self._row
                self._row = None
                if self._row_index == 1:
                    self._is_target = any(self._header_marker in cell for cell in row)
                elif self._is_target:
                    self._consume(row)
        elif tag == "table":
            self._in_table = False

    def _consume(self, row: Sequence[str]) -> None:
        if len(row) >= 2:
            symbol = self._convert_symbol(row[0])
            name = row[1]
            if symbol and name:
                self.constituents.append({"Symbol": symbol, "Name": name})


def _fetch_wikipedia_constituents(
    url: str,
    header_marker: str,
    convert_symbol: Callable[[str], str | None],
) -> Sequence[Mapping[str, Any]]:
    """Fetch a Wikipedia page and parse its index-constituents table.

    Raises:
        ValueError: If the page cannot be fetched.
    """
    request = urllib.request.Request(
        url,
        headers={"User-Agent": _WIKIPEDIA_USER_AGENT},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            html = response.read().decode("utf-8")
    except OSError as error:
        raise ValueError(f"Failed to fetch index constituents from {url}: {error}") from error
    parser = _WikipediaConstituentParser(header_marker, convert_symbol)
    parser.feed(html)
    return parser.constituents


def _fetch_hsi_wikipedia() -> Sequence[Mapping[str, Any]]:
    """Parse the current HSI constituents from the Chinese Wikipedia article."""
    url = "https://zh.wikipedia.org/zh/" + urllib.parse.quote("恒生指数")
    return _fetch_wikipedia_constituents(url, "股份代號", _hsi_symbol)


def _fetch_sp500_wikipedia() -> Sequence[Mapping[str, Any]]:
    """Parse the current S&P 500 constituents from the English Wikipedia article."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    return _fetch_wikipedia_constituents(url, "Symbol", _us_symbol)


def standardize_securities(
    market: MarketTarget,
    constituents: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize index constituents into securities rows (SP 1.33).

    The constituents carry a Yahoo-consistent symbol and a display name but no
    exchange or listing date; the exchange is a market-level default and
    ``list_date`` is a research placeholder (2000-01-03) so current index
    members are active throughout a backtest. This is a survivorship-bias
    limitation surfaced by the stock-pool evaluation (SP 2.10 / SP 2.30), not
    a silent assumption.
    """
    exchange = _CONSTITUENTS_EXCHANGE[market]
    rows: list[dict[str, Any]] = []
    for item in constituents:
        symbol = item.get("Symbol")
        name = item.get("Name")
        if not isinstance(symbol, str) or not symbol or not isinstance(name, str) or not name:
            continue
        rows.append(
            {
                "market": market.value,
                "symbol": symbol,
                "name": name,
                "exchange": exchange,
                "list_date": date(2000, 1, 3),
                "delist_date": None,
                "is_active": True,
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

    def fetch_financials(
        self,
        market: MarketTarget,
        symbol: str,
    ) -> Sequence[Mapping[str, Any]]:
        """Fetch and standardize financial metrics for a symbol from yfinance."""
        self._require_market(market)
        info = dict(self._ticker(symbol).info)
        return standardize_financials(market, symbol, info, _report_date(info))


class HKYFinanceProvider(YFinanceProvider):
    """yfinance-backed provider for the Hong Kong market."""

    def capabilities(self) -> ProviderCapabilities:
        """Return the capabilities the provider offers for Hong Kong."""
        return ProviderCapabilities({MarketTarget.HK: _ALL_CAPABILITIES})

    def _normalize_symbol(self, symbol: str) -> str:
        """Return a 4-digit HK code with the ``.HK`` suffix.

        Hong Kong codes are officially written with five digits on the HKEX
        (``00001``) but yfinance resolves them with four (``0001.HK``); a code
        is normalized to the four-digit form so both spellings fetch the same
        series.
        """
        digits = symbol.removesuffix(".HK").strip()
        return f"{int(digits):04d}.HK"

    def list_securities(self, market: MarketTarget) -> Sequence[Mapping[str, Any]]:
        """Return the current Hang Seng Index constituents (from Wikipedia).

        The Chinese Wikipedia article on the Hang Seng Index (恒生指数) is
        parsed for its ``股份代號 | 名稱`` table; five-digit HKEX codes are
        converted to the four-digit yfinance ticker forms (``00005`` ->
        ``0005.HK``).
        """
        self._require_market(market)
        return standardize_securities(market, _fetch_hsi_wikipedia())


class USYFinanceProvider(YFinanceProvider):
    """yfinance-backed provider for the United States market."""

    def capabilities(self) -> ProviderCapabilities:
        """Return the capabilities the provider offers for the United States."""
        return ProviderCapabilities({MarketTarget.US: _ALL_CAPABILITIES})

    def _normalize_symbol(self, symbol: str) -> str:
        """Return the ticker as given, trimmed and uppercased."""
        return symbol.strip().upper()

    def list_securities(self, market: MarketTarget) -> Sequence[Mapping[str, Any]]:
        """Return the current S&P 500 constituents (from Wikipedia).

        The English Wikipedia "List of S&P 500 companies" article is parsed
        for its ``Symbol | Security`` table; tickers are used as-is.
        """
        self._require_market(market)
        return standardize_securities(market, _fetch_sp500_wikipedia())
