"""Input field validation for Harbor (per market).

Every ingested dataset is validated for required fields, value types, and value
ranges, with rules that distinguish Hong Kong and United States data formats:
HK symbols follow ``\\d{4,5}.HK`` while US symbols are plain uppercase tickers,
dividend currency must match the market (HKD/USD), and corporate action types
must be valid for the target market. Findings are returned as quality-issue
records compatible with the ``quality_issues`` table.
"""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from harbor.config import MarketTarget
from harbor.core.market_registry import CorporateActionType, get_market_config

_HK_SYMBOL_PATTERN = re.compile(r"^\d{4,5}\.HK$")
_US_SYMBOL_PATTERN = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_CURRENCY_BY_MARKET: dict[MarketTarget, str] = {
    MarketTarget.HK: "HKD",
    MarketTarget.US: "USD",
}
_DIVIDEND_TYPES = frozenset({"regular", "special"})
_ACTION_STATUSES = frozenset({"announced", "pending", "completed", "cancelled"})
_ACTION_TYPES = frozenset(action.value for action in CorporateActionType)


@dataclass(frozen=True)
class QualityFinding:
    """A single data-quality finding for an ingested row."""

    check_name: str
    severity: str
    symbol: str | None = None
    details: str | None = None


def _is_number(value: object) -> bool:
    """Return whether a value is a real number (excluding booleans)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _require(
    fields: Sequence[str],
    row: Mapping[str, Any],
    symbol: str,
) -> list[QualityFinding]:
    """Flag required fields that are missing from a row."""
    findings: list[QualityFinding] = []
    for field in fields:
        if row.get(field) is None:
            findings.append(
                QualityFinding("required_field_missing", "error", symbol, f"Missing {field!r}.")
            )
    return findings


def _check_number(field: str, value: object, symbol: str) -> QualityFinding | None:
    """Flag a numeric field whose value is not numeric."""
    if value is not None and not _is_number(value):
        return QualityFinding("field_type_invalid", "error", symbol, f"{field!r} must be numeric.")
    return None


def _non_positive(field: str, value: object, symbol: str) -> QualityFinding | None:
    """Flag a numeric field whose value is not strictly positive."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value <= 0:
        return QualityFinding("field_out_of_range", "error", symbol, f"{field!r} must be positive.")
    return None


def _negative(field: str, value: object, symbol: str) -> QualityFinding | None:
    """Flag a numeric field whose value is negative."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0:
        return QualityFinding(
            "field_out_of_range", "error", symbol, f"{field!r} must be non-negative."
        )
    return None


def _validate_symbol_format(market: MarketTarget, symbol: str) -> QualityFinding | None:
    """Flag a symbol that does not match the market's expected format."""
    pattern = _HK_SYMBOL_PATTERN if market is MarketTarget.HK else _US_SYMBOL_PATTERN
    if pattern.fullmatch(symbol) is None:
        return QualityFinding(
            "symbol_format_invalid",
            "error",
            symbol,
            f"Symbol {symbol!r} does not match the {market.value} format.",
        )
    return None


def validate_securities(
    market: MarketTarget, rows: Sequence[Mapping[str, Any]]
) -> list[QualityFinding]:
    """Validate securities rows for a market."""
    findings: list[QualityFinding] = []
    for row in rows:
        symbol = row.get("symbol")
        symbol_text = "" if symbol is None else str(symbol)
        findings.extend(
            _require(("symbol", "name", "exchange", "list_date", "is_active"), row, symbol_text)
        )
        if symbol is not None:
            finding = _validate_symbol_format(market, symbol_text)
            if finding is not None:
                findings.append(finding)
        is_active = row.get("is_active")
        if is_active is not None and not isinstance(is_active, bool):
            findings.append(
                QualityFinding(
                    "field_type_invalid", "error", symbol_text, "'is_active' must be a boolean."
                )
            )
    return findings


_PRICE_FIELDS = ("open", "high", "low", "close", "adjusted_close")
_REQUIRED_DAILY = ("symbol", "date", *(_PRICE_FIELDS), "volume", "source")


def validate_daily_quotes(
    market: MarketTarget, rows: Sequence[Mapping[str, Any]]
) -> list[QualityFinding]:
    """Validate daily quote rows for a market."""
    findings: list[QualityFinding] = []
    for row in rows:
        symbol = row.get("symbol")
        symbol_text = "" if symbol is None else str(symbol)
        findings.extend(_require(_REQUIRED_DAILY, row, symbol_text))
        if symbol is not None:
            finding = _validate_symbol_format(market, symbol_text)
            if finding is not None:
                findings.append(finding)
        for field in _PRICE_FIELDS:
            finding = _check_number(field, row.get(field), symbol_text)
            if finding is not None:
                findings.append(finding)
            else:
                finding = _non_positive(field, row.get(field), symbol_text)
                if finding is not None:
                    findings.append(finding)
        finding = _negative("volume", row.get("volume"), symbol_text)
        if finding is not None:
            findings.append(finding)
        high = row.get("high")
        low = row.get("low")
        if (
            isinstance(high, (int, float))
            and not isinstance(high, bool)
            and isinstance(low, (int, float))
            and not isinstance(low, bool)
            and high < low
        ):
            findings.append(
                QualityFinding("ohlc_inconsistent", "error", symbol_text, "'high' is below 'low'.")
            )
        quote_date = row.get("date")
        if quote_date is not None and not isinstance(quote_date, date):
            findings.append(
                QualityFinding("field_type_invalid", "error", symbol_text, "'date' must be a date.")
            )
    return findings


def validate_dividends(
    market: MarketTarget, rows: Sequence[Mapping[str, Any]]
) -> list[QualityFinding]:
    """Validate dividend rows for a market, including the currency format."""
    findings: list[QualityFinding] = []
    expected_currency = _CURRENCY_BY_MARKET[market]
    for row in rows:
        symbol = row.get("symbol")
        symbol_text = "" if symbol is None else str(symbol)
        findings.extend(
            _require(("symbol", "ex_date", "amount", "type", "currency"), row, symbol_text)
        )
        if symbol is not None:
            finding = _validate_symbol_format(market, symbol_text)
            if finding is not None:
                findings.append(finding)
        finding = _non_positive("amount", row.get("amount"), symbol_text)
        if finding is not None:
            findings.append(finding)
        dividend_type = row.get("type")
        if dividend_type is not None and dividend_type not in _DIVIDEND_TYPES:
            findings.append(
                QualityFinding(
                    "field_value_invalid",
                    "error",
                    symbol_text,
                    f"'type' must be one of {sorted(_DIVIDEND_TYPES)}.",
                )
            )
        currency = row.get("currency")
        if currency is not None and currency != expected_currency:
            message = (
                f"Expected currency {expected_currency!r} for {market.value}, got {currency!r}."
            )
            findings.append(QualityFinding("currency_mismatch", "error", symbol_text, message))
    return findings


def validate_financials(
    market: MarketTarget, rows: Sequence[Mapping[str, Any]]
) -> list[QualityFinding]:
    """Validate financial indicator rows for a market."""
    findings: list[QualityFinding] = []
    for row in rows:
        symbol = row.get("symbol")
        symbol_text = "" if symbol is None else str(symbol)
        findings.extend(_require(("symbol", "report_date", "fiscal_period"), row, symbol_text))
        if symbol is not None:
            finding = _validate_symbol_format(market, symbol_text)
            if finding is not None:
                findings.append(finding)
        for field in ("roe", "net_income", "total_equity", "revenue"):
            finding = _check_number(field, row.get(field), symbol_text)
            if finding is not None:
                findings.append(finding)
    return findings


def validate_fundamentals(
    market: MarketTarget, rows: Sequence[Mapping[str, Any]]
) -> list[QualityFinding]:
    """Validate fundamental metric rows for a market."""
    findings: list[QualityFinding] = []
    for row in rows:
        symbol = row.get("symbol")
        symbol_text = "" if symbol is None else str(symbol)
        findings.extend(_require(("symbol", "date"), row, symbol_text))
        if symbol is not None:
            finding = _validate_symbol_format(market, symbol_text)
            if finding is not None:
                findings.append(finding)
        finding = _negative("dividend_yield", row.get("dividend_yield"), symbol_text)
        if finding is not None:
            findings.append(finding)
    return findings


def validate_corporate_actions(
    market: MarketTarget, rows: Sequence[Mapping[str, Any]]
) -> list[QualityFinding]:
    """Validate corporate action rows for a market, including allowed types."""
    findings: list[QualityFinding] = []
    allowed = {action.value for action in get_market_config(market).corporate_action_types}
    for row in rows:
        symbol = row.get("symbol")
        symbol_text = "" if symbol is None else str(symbol)
        findings.extend(
            _require(("symbol", "action_id", "action_type", "status", "source"), row, symbol_text)
        )
        if symbol is not None:
            finding = _validate_symbol_format(market, symbol_text)
            if finding is not None:
                findings.append(finding)
        action_type = row.get("action_type")
        if action_type is not None:
            if action_type not in _ACTION_TYPES:
                findings.append(
                    QualityFinding(
                        "action_type_invalid",
                        "error",
                        symbol_text,
                        f"Unknown corporate action type {action_type!r}.",
                    )
                )
            elif action_type not in allowed:
                findings.append(
                    QualityFinding(
                        "action_type_not_supported",
                        "error",
                        symbol_text,
                        f"Action type {action_type!r} is not supported for {market.value}.",
                    )
                )
        status = row.get("status")
        if status is not None and status not in _ACTION_STATUSES:
            findings.append(
                QualityFinding(
                    "field_value_invalid",
                    "error",
                    symbol_text,
                    f"'status' must be one of {sorted(_ACTION_STATUSES)}.",
                )
            )
    return findings


Validator = Callable[[MarketTarget, Sequence[Mapping[str, Any]]], list[QualityFinding]]

_VALIDATORS: dict[str, Validator] = {
    "securities": validate_securities,
    "daily_quotes": validate_daily_quotes,
    "dividends": validate_dividends,
    "financials": validate_financials,
    "fundamentals": validate_fundamentals,
    "corporate_actions": validate_corporate_actions,
}


def validate_dataset(
    market: MarketTarget,
    dataset: str,
    rows: Sequence[Mapping[str, Any]],
) -> list[QualityFinding]:
    """Validate a named dataset for a market.

    Args:
        market: The market the rows belong to.
        dataset: One of ``securities``, ``daily_quotes``, ``dividends``,
            ``financials``, ``fundamentals``, or ``corporate_actions``.
        rows: The ingested rows to validate.

    Returns:
        The data-quality findings for the dataset.

    Raises:
        ValueError: If the dataset name is unknown.
    """
    try:
        validator = _VALIDATORS[dataset]
    except KeyError as error:
        raise ValueError(f"Unknown dataset: {dataset!r}") from error
    return validator(market, rows)


def to_quality_records(
    market: MarketTarget,
    run_id: str,
    findings: Sequence[QualityFinding],
) -> list[dict[str, object]]:
    """Convert findings into records compatible with the quality-issues table."""
    return [
        {
            "run_id": run_id,
            "market": market.value,
            "symbol": finding.symbol,
            "check_name": finding.check_name,
            "severity": finding.severity,
            "details": finding.details,
            "resolved": False,
        }
        for finding in findings
    ]
