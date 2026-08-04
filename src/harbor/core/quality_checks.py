"""Daily quote quality checks for Harbor (per market).

These checks detect duplicate records and missing trading days for the
``(market, symbol, date)`` composite key. A trading day is a weekday
(Monday-Friday), matching the exchange calendar used across the pipeline.
Findings reuse the :class:`~harbor.core.validation.QualityFinding` vocabulary
and are applied identically to Hong Kong (1.86) and United States (1.87) data.
"""

import math
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any

from harbor.config import MarketTarget
from harbor.core.market_registry import CorporateActionType
from harbor.core.validation import QualityFinding


def _as_date(value: object) -> date | None:
    """Return a value as a date, or ``None`` when it is not a date."""
    return value if isinstance(value, date) else None


def find_duplicate_daily_quotes(
    market: MarketTarget,
    rows: Sequence[Mapping[str, Any]],
) -> list[QualityFinding]:
    """Detect duplicate ``(symbol, date)`` records in a daily quote batch.

    Args:
        market: The market the rows belong to.
        rows: Daily quote rows with ``symbol`` and ``date`` keys.

    Returns:
        One ``daily_quote_duplicate`` finding (severity ``error``) per
        duplicated ``(symbol, date)`` key, reporting the record count.
    """
    key_count: dict[tuple[str, date], int] = {}
    for row in rows:
        symbol = str(row.get("symbol", ""))
        day = _as_date(row.get("date"))
        if day is None:
            continue
        key = (symbol, day)
        key_count[key] = key_count.get(key, 0) + 1

    findings: list[QualityFinding] = []
    for (symbol, day), count in sorted(key_count.items()):
        if count > 1:
            findings.append(
                QualityFinding(
                    "daily_quote_duplicate",
                    "error",
                    symbol,
                    f"{count} records for {day.isoformat()}.",
                )
            )
    return findings


def _weekdays_between(start: date, end: date) -> list[date]:
    """Return the weekdays strictly between two dates (exclusive)."""
    days: list[date] = []
    day = start + timedelta(days=1)
    while day < end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def find_daily_quote_gaps(
    market: MarketTarget,
    rows: Sequence[Mapping[str, Any]],
) -> list[QualityFinding]:
    """Detect missing trading days between the first and last quote per symbol.

    Args:
        market: The market the rows belong to.
        rows: Daily quote rows with ``symbol`` and ``date`` keys.

    Returns:
        One ``daily_quote_gap`` finding (severity ``warning``) per symbol that
        is missing weekday quotes, reporting the count and a sample of dates.
    """
    by_symbol: dict[str, set[date]] = {}
    for row in rows:
        symbol = str(row.get("symbol", ""))
        day = _as_date(row.get("date"))
        if day is None:
            continue
        by_symbol.setdefault(symbol, set()).add(day)

    findings: list[QualityFinding] = []
    for symbol, days in sorted(by_symbol.items()):
        ordered = sorted(days)
        if len(ordered) < 2:
            continue
        present = set(ordered)
        missing: list[date] = []
        for earlier, later in zip(ordered, ordered[1:]):
            for day in _weekdays_between(earlier, later):
                if day not in present:
                    missing.append(day)
        if missing:
            sample = ", ".join(day.isoformat() for day in missing[:10])
            if len(missing) > 10:
                sample += ", ..."
            findings.append(
                QualityFinding(
                    "daily_quote_gap",
                    "warning",
                    symbol,
                    f"Missing {len(missing)} trading days between {ordered[0]} "
                    f"and {ordered[-1]}: {sample}",
                )
            )
    return findings


def check_daily_quotes(
    market: MarketTarget,
    rows: Sequence[Mapping[str, Any]],
) -> list[QualityFinding]:
    """Run the duplicate and gap checks over a daily quote batch."""
    return find_duplicate_daily_quotes(market, rows) + find_daily_quote_gaps(market, rows)


def _to_float(value: object) -> float | None:
    """Return a value as a float, or ``None`` when it is not numeric."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return float(value)


def _ohlc_numbers(row: Mapping[str, Any]) -> tuple[float, float, float, float] | None:
    """Return the open/high/low/close as floats when all are numeric."""
    open_price = _to_float(row.get("open"))
    high = _to_float(row.get("high"))
    low = _to_float(row.get("low"))
    close = _to_float(row.get("close"))
    if open_price is None or high is None or low is None or close is None:
        return None
    return open_price, high, low, close


def find_illegal_ohlc(
    market: MarketTarget,
    rows: Sequence[Mapping[str, Any]],
) -> list[QualityFinding]:
    """Detect rows whose OHLC prices are internally inconsistent.

    Args:
        market: The market the rows belong to.
        rows: Daily quote rows with ``symbol`` and OHLC keys.

    Returns:
        ``ohlc_invalid`` findings (severity ``error``) for rows where ``high``
        is below ``low``, an extreme lies outside the open/close range, or any
        price is non-positive.
    """
    findings: list[QualityFinding] = []
    for row in rows:
        symbol = str(row.get("symbol", ""))
        prices = _ohlc_numbers(row)
        if prices is None:
            continue
        open_price, high, low, close = prices
        if high < low:
            findings.append(QualityFinding("ohlc_invalid", "error", symbol, "high is below low."))
        if high < open_price or high < close:
            findings.append(
                QualityFinding("ohlc_invalid", "error", symbol, "high is below open or close.")
            )
        if low > open_price or low > close:
            findings.append(
                QualityFinding("ohlc_invalid", "error", symbol, "low is above open or close.")
            )
        if min(open_price, high, low, close) <= 0:
            findings.append(
                QualityFinding("ohlc_invalid", "error", symbol, "OHLC prices must be positive.")
            )
    return findings


def find_abnormal_moves(
    market: MarketTarget,
    rows: Sequence[Mapping[str, Any]],
    threshold: float = 0.5,
) -> list[QualityFinding]:
    """Detect single-day close-to-close moves at or beyond a threshold.

    Args:
        market: The market the rows belong to.
        rows: Daily quote rows with ``symbol``, ``date``, and ``close`` keys.
        threshold: The absolute daily move (as a fraction) that is considered
            abnormal; defaults to ``0.5`` (50%).

    Returns:
        ``abnormal_price_move`` findings (severity ``warning``) for each
        consecutive close-to-close move whose magnitude reaches the threshold.
    """
    by_symbol: dict[str, list[tuple[date, float]]] = {}
    for row in rows:
        symbol = str(row.get("symbol", ""))
        day = _as_date(row.get("date"))
        close = row.get("close")
        if day is None or not isinstance(close, (int, float)) or isinstance(close, bool):
            continue
        by_symbol.setdefault(symbol, []).append((day, float(close)))

    findings: list[QualityFinding] = []
    for symbol, quotes in sorted(by_symbol.items()):
        quotes.sort(key=lambda item: item[0])
        for (_, previous), (day, close) in zip(quotes, quotes[1:]):
            if previous <= 0:
                continue
            move = abs(close / previous - 1.0)
            if move >= threshold:
                findings.append(
                    QualityFinding(
                        "abnormal_price_move",
                        "warning",
                        symbol,
                        f"{move * 100.0:.1f}% move on {day.isoformat()} "
                        f"(close {close:g} vs {previous:g}).",
                    )
                )
    return findings


def find_coverage_gaps(
    market: MarketTarget,
    expected_symbols: Sequence[str],
    covered_symbols: Sequence[str],
) -> list[QualityFinding]:
    """Detect expected securities that have no daily quotes.

    Args:
        market: The market the securities belong to.
        expected_symbols: The securities universe expected to carry quotes.
        covered_symbols: The symbols that have at least one daily quote.

    Returns:
        A ``coverage_gap`` finding (severity ``error``) per expected symbol
        that has no daily quotes.
    """
    covered = set(covered_symbols)
    missing = sorted(set(expected_symbols) - covered)
    findings: list[QualityFinding] = []
    for symbol in missing:
        findings.append(
            QualityFinding(
                "coverage_gap",
                "error",
                symbol,
                "No daily quotes found for the security.",
            )
        )
    return findings


def find_stale_quotes(
    market: MarketTarget,
    rows: Sequence[Mapping[str, Any]],
    as_of: date,
    max_age_days: int = 5,
) -> list[QualityFinding]:
    """Detect symbols whose latest quote is older than the expected window.

    Args:
        market: The market the rows belong to.
        rows: Daily quote rows with ``symbol`` and ``date`` keys.
        as_of: The reference date the data freshness is measured against.
        max_age_days: The maximum allowed age, in calendar days, before a
            symbol's latest quote is considered stale; defaults to 5.

    Returns:
        A ``stale_quote`` finding (severity ``warning``) per symbol whose
        latest quote predates ``as_of - max_age_days``.
    """
    latest_by_symbol: dict[str, date] = {}
    for row in rows:
        symbol = str(row.get("symbol", ""))
        day = _as_date(row.get("date"))
        if day is None:
            continue
        if day > latest_by_symbol.get(symbol, date.min):
            latest_by_symbol[symbol] = day

    findings: list[QualityFinding] = []
    for symbol, latest in sorted(latest_by_symbol.items()):
        age = (as_of - latest).days
        if 0 <= age > max_age_days:
            findings.append(
                QualityFinding(
                    "stale_quote",
                    "warning",
                    symbol,
                    f"Latest quote on {latest.isoformat()} is {age} days older "
                    f"than {as_of.isoformat()}.",
                )
            )
    return findings


_DIVIDEND_RULES: dict[MarketTarget, tuple[float, float]] = {
    # (maximum regular per-share amount, minimum special per-share amount)
    MarketTarget.HK: (3.0, 0.5),
    MarketTarget.US: (1.5, 0.1),
}


def _date_order_findings(
    row: Mapping[str, Any],
    symbol: str,
) -> list[QualityFinding]:
    """Flag dividend rows whose dates are out of chronological order."""
    announce_date = _as_date(row.get("announce_date"))
    ex_date = _as_date(row.get("ex_date"))
    record_date = _as_date(row.get("record_date"))
    payment_date = _as_date(row.get("payment_date"))
    findings: list[QualityFinding] = []
    if announce_date is not None and ex_date is not None and ex_date <= announce_date:
        findings.append(
            QualityFinding(
                "dividend_date_invalid",
                "error",
                symbol,
                "Ex-date must be after the announcement date.",
            )
        )
    if ex_date is not None and record_date is not None and record_date < ex_date:
        findings.append(
            QualityFinding(
                "dividend_date_invalid",
                "error",
                symbol,
                "Record date must not be before the ex-date.",
            )
        )
    if ex_date is not None and payment_date is not None and payment_date < ex_date:
        findings.append(
            QualityFinding(
                "dividend_date_invalid",
                "error",
                symbol,
                "Payment date must not be before the ex-date.",
            )
        )
    if record_date is not None and payment_date is not None and payment_date < record_date:
        findings.append(
            QualityFinding(
                "dividend_date_invalid",
                "error",
                symbol,
                "Payment date must not be before the record date.",
            )
        )
    return findings


def find_inconsistent_dividends(
    market: MarketTarget,
    rows: Sequence[Mapping[str, Any]],
) -> list[QualityFinding]:
    """Detect inconsistent dividend timing, amounts, and special flags.

    Args:
        market: The market the rows belong to.
        rows: Dividend rows with ``ex_date``, ``record_date``, ``payment_date``,
            ``amount``, and ``type`` keys.

    Returns:
        ``dividend_amount_invalid`` and ``dividend_date_invalid`` findings
        (severity ``error``) for non-positive amounts and out-of-order dates,
        plus ``special_flag_unreasonable`` findings (severity ``warning``) when
        a special dividend is too small or a regular dividend is too large for
        the market's rules.
    """
    regular_max, special_min = _DIVIDEND_RULES[market]
    findings: list[QualityFinding] = []
    for row in rows:
        symbol = str(row.get("symbol", ""))
        amount = row.get("amount")
        if not isinstance(amount, (int, float)) or isinstance(amount, bool):
            continue
        if amount <= 0:
            findings.append(
                QualityFinding(
                    "dividend_amount_invalid",
                    "error",
                    symbol,
                    "Dividend amount must be positive.",
                )
            )
            continue
        findings.extend(_date_order_findings(row, symbol))
        dividend_type = row.get("type")
        if dividend_type == "special" and amount < special_min:
            findings.append(
                QualityFinding(
                    "special_flag_unreasonable",
                    "warning",
                    symbol,
                    f"Special dividend amount {amount:g} is below the "
                    f"{market.value} minimum {special_min:g}.",
                )
            )
        elif dividend_type == "regular" and amount > regular_max:
            findings.append(
                QualityFinding(
                    "special_flag_unreasonable",
                    "warning",
                    symbol,
                    f"Regular dividend amount {amount:g} exceeds the "
                    f"{market.value} ceiling {regular_max:g}; possibly a special dividend.",
                )
            )
    return findings


_KEY_METRICS = ("roe", "net_income", "total_equity", "revenue")


def find_incomplete_financials(
    market: MarketTarget,
    rows: Sequence[Mapping[str, Any]],
) -> list[QualityFinding]:
    """Detect financial rows missing key metric fields.

    Args:
        market: The market the rows belong to.
        rows: Financial rows with ``symbol`` and the key metric keys.

    Returns:
        A ``financials_incomplete`` finding per row that is missing at least one
        key metric: severity ``error`` when all metrics are missing, otherwise
        ``warning`` listing the missing fields.
    """
    findings: list[QualityFinding] = []
    for row in rows:
        symbol = str(row.get("symbol", ""))
        missing = [field for field in _KEY_METRICS if row.get(field) is None]
        if not missing:
            continue
        if len(missing) == len(_KEY_METRICS):
            findings.append(
                QualityFinding(
                    "financials_incomplete",
                    "error",
                    symbol,
                    "All key metrics are missing.",
                )
            )
        else:
            findings.append(
                QualityFinding(
                    "financials_incomplete",
                    "warning",
                    symbol,
                    f"Missing metrics: {', '.join(missing)}.",
                )
            )
    return findings


def find_unreasonable_report_dates(
    market: MarketTarget,
    rows: Sequence[Mapping[str, Any]],
    as_of: date | None = None,
) -> list[QualityFinding]:
    """Detect financial rows with implausible report dates.

    Args:
        market: The market the rows belong to.
        rows: Financial rows with ``report_date`` and ``fiscal_period`` keys.
        as_of: The reference date; defaults to today. Report dates after this
            date are considered future-dated.

    Returns:
        A ``report_date_unreasonable`` finding (severity ``warning``) per row
        whose report date is in the future or whose fiscal period does not match
        the report year.
    """
    reference = as_of if as_of is not None else date.today()
    findings: list[QualityFinding] = []
    for row in rows:
        symbol = str(row.get("symbol", ""))
        report_date = _as_date(row.get("report_date"))
        if report_date is None:
            continue
        if report_date > reference:
            findings.append(
                QualityFinding(
                    "report_date_unreasonable",
                    "warning",
                    symbol,
                    f"Report date {report_date.isoformat()} is in the future.",
                )
            )
        fiscal_period = row.get("fiscal_period")
        if fiscal_period is not None and str(fiscal_period) != str(report_date.year):
            findings.append(
                QualityFinding(
                    "report_date_unreasonable",
                    "warning",
                    symbol,
                    f"Fiscal period {fiscal_period!r} does not match report "
                    f"year {report_date.year}.",
                )
            )
    return findings


_LIFECYCLE_DATES = ("announce_date", "ex_date", "record_date", "effective_date")
_RATIO_ACTIONS = frozenset(
    {
        CorporateActionType.SPLIT,
        CorporateActionType.CONSOLIDATION,
        CorporateActionType.RIGHTS_ISSUE,
        CorporateActionType.MERGER,
        CorporateActionType.SPIN_OFF,
    }
)
_PRICE_ACTIONS = frozenset({CorporateActionType.DIVIDEND, CorporateActionType.TENDER_OFFER})


def _corporate_action_terms(
    action_type: CorporateActionType,
    row: Mapping[str, Any],
) -> list[str]:
    """Return the term names an action requires but the row lacks."""
    missing: list[str] = []
    ratio = _to_float(row.get("ratio"))
    if action_type in _RATIO_ACTIONS and (ratio is None or ratio <= 0):
        missing.append("ratio")
    price = _to_float(row.get("price"))
    if action_type is CorporateActionType.RIGHTS_ISSUE and price is None:
        missing.append("price")
    if action_type in _PRICE_ACTIONS and price is None:
        missing.append("price")
    return missing


def find_incomplete_corporate_actions(
    market: MarketTarget,
    rows: Sequence[Mapping[str, Any]],
) -> list[QualityFinding]:
    """Detect corporate actions with incomplete lifecycles or terms.

    Args:
        market: The market the rows belong to.
        rows: Corporate action rows with lifecycle date keys, ``action_type``,
            ``ratio``, and ``price`` keys.

    Returns:
        ``corporate_action_date_missing`` and ``corporate_action_terms_incomplete``
        findings (severity ``warning``) for absent dates/terms, plus
        ``corporate_action_date_order`` findings (severity ``error``) when the
        lifecycle dates are out of order.
    """
    findings: list[QualityFinding] = []
    for row in rows:
        symbol = str(row.get("symbol", ""))
        action_id = str(row.get("action_id", ""))

        missing_dates = [field for field in _LIFECYCLE_DATES if _as_date(row.get(field)) is None]
        if missing_dates:
            findings.append(
                QualityFinding(
                    "corporate_action_date_missing",
                    "warning",
                    symbol,
                    f"Action {action_id} is missing dates: {', '.join(missing_dates)}.",
                )
            )

        announce_date = _as_date(row.get("announce_date"))
        ex_date = _as_date(row.get("ex_date"))
        record_date = _as_date(row.get("record_date"))
        effective_date = _as_date(row.get("effective_date"))
        if announce_date is not None and ex_date is not None and ex_date < announce_date:
            findings.append(
                QualityFinding(
                    "corporate_action_date_order",
                    "error",
                    symbol,
                    f"Action {action_id}: ex-date is before the announcement date.",
                )
            )
        if ex_date is not None and record_date is not None and record_date < ex_date:
            findings.append(
                QualityFinding(
                    "corporate_action_date_order",
                    "error",
                    symbol,
                    f"Action {action_id}: record date is before the ex-date.",
                )
            )
        if record_date is not None and effective_date is not None and effective_date < record_date:
            findings.append(
                QualityFinding(
                    "corporate_action_date_order",
                    "error",
                    symbol,
                    f"Action {action_id}: effective date is before the record date.",
                )
            )

        action_type_value = row.get("action_type")
        if isinstance(action_type_value, str):
            try:
                action_type = CorporateActionType(action_type_value)
            except ValueError:
                continue
            missing_terms = _corporate_action_terms(action_type, row)
            if missing_terms:
                findings.append(
                    QualityFinding(
                        "corporate_action_terms_incomplete",
                        "warning",
                        symbol,
                        f"Action {action_id} is missing terms: {', '.join(missing_terms)}.",
                    )
                )
    return findings


def _close(a: object, b: object, rel_tol: float) -> bool:
    """Return whether two numeric values agree within a relative tolerance."""
    if (
        not isinstance(a, (int, float))
        or isinstance(a, bool)
        or not isinstance(b, (int, float))
        or isinstance(b, bool)
    ):
        return False
    return math.isclose(float(a), float(b), rel_tol=rel_tol)


def reconcile_adjusted_factors(
    market: MarketTarget,
    computed_rows: Sequence[Mapping[str, Any]],
    reference_rows: Sequence[Mapping[str, Any]],
    rel_tol: float = 1e-6,
) -> list[QualityFinding]:
    """Reconcile computed adjusted factors against reference values.

    Args:
        market: The market the rows belong to.
        computed_rows: Factor rows produced by the calculation.
        reference_rows: Factor rows from a manual calculation or an
            authoritative source.
        rel_tol: The relative tolerance for factor agreement.

    Returns:
        ``adjusted_factor_mismatch`` findings (severity ``error``) for factor
        values that disagree, plus ``adjusted_factor_unreconciled`` and
        ``adjusted_factor_missing`` findings (severity ``warning``) for rows
        present on only one side.
    """
    reference: dict[tuple[str, date], Mapping[str, Any]] = {}
    for row in reference_rows:
        symbol = str(row.get("symbol", ""))
        day = _as_date(row.get("date"))
        if day is not None:
            reference[(symbol, day)] = row

    findings: list[QualityFinding] = []
    for row in computed_rows:
        symbol = str(row.get("symbol", ""))
        day = _as_date(row.get("date"))
        if day is None:
            continue
        reference_row = reference.get((symbol, day))
        if reference_row is None:
            findings.append(
                QualityFinding(
                    "adjusted_factor_unreconciled",
                    "warning",
                    symbol,
                    f"No reference factor for {day.isoformat()}.",
                )
            )
            continue
        if not _close(
            row.get("daily_factor"), reference_row.get("daily_factor"), rel_tol
        ) or not _close(
            row.get("cumulative_factor"), reference_row.get("cumulative_factor"), rel_tol
        ):
            findings.append(
                QualityFinding(
                    "adjusted_factor_mismatch",
                    "error",
                    symbol,
                    f"Factor mismatch on {day.isoformat()} (computed "
                    f"{row.get('cumulative_factor')!r} vs reference "
                    f"{reference_row.get('cumulative_factor')!r}).",
                )
            )

    computed_keys = {
        (str(row.get("symbol", "")), _as_date(row.get("date"))) for row in computed_rows
    }
    for (symbol, day), _ in reference.items():
        if (symbol, day) not in computed_keys:
            findings.append(
                QualityFinding(
                    "adjusted_factor_missing",
                    "warning",
                    symbol,
                    f"No computed factor for {day.isoformat()}.",
                )
            )
    return findings


def reconcile_equity_events(
    market: MarketTarget,
    computed_rows: Sequence[Mapping[str, Any]],
    reference_rows: Sequence[Mapping[str, Any]],
    rel_tol: float = 1e-6,
) -> list[QualityFinding]:
    """Reconcile computed equity entitlements against reference values.

    Args:
        market: The market the rows belong to.
        computed_rows: Entitlement rows produced by the calculation.
        reference_rows: Entitlement rows from a manual calculation or an
            authoritative source.
        rel_tol: The relative tolerance for entitlement agreement.

    Returns:
        ``equity_event_mismatch`` findings (severity ``error``) for entitlements
        that disagree, plus ``equity_event_unreconciled`` and
        ``equity_event_missing`` findings (severity ``warning``) for rows
        present on only one side.
    """
    reference: dict[tuple[str, str, date | None], Mapping[str, Any]] = {}
    for row in reference_rows:
        key = (
            str(row.get("symbol", "")),
            str(row.get("action_id", "")),
            _as_date(row.get("position_date")),
        )
        reference[key] = row

    findings: list[QualityFinding] = []
    for row in computed_rows:
        symbol = str(row.get("symbol", ""))
        action_id = str(row.get("action_id", ""))
        position_date = _as_date(row.get("position_date"))
        reference_row = reference.get((symbol, action_id, position_date))
        if reference_row is None:
            findings.append(
                QualityFinding(
                    "equity_event_unreconciled",
                    "warning",
                    symbol,
                    f"No reference entitlement for action {action_id}.",
                )
            )
            continue
        if not _close(
            row.get("entitled_quantity"), reference_row.get("entitled_quantity"), rel_tol
        ) or not _close(row.get("cash_amount"), reference_row.get("cash_amount"), rel_tol):
            findings.append(
                QualityFinding(
                    "equity_event_mismatch",
                    "error",
                    symbol,
                    f"Entitlement mismatch for action {action_id}.",
                )
            )

    computed_keys = {
        (
            str(row.get("symbol", "")),
            str(row.get("action_id", "")),
            _as_date(row.get("position_date")),
        )
        for row in computed_rows
    }
    for (symbol, action_id, position_date), _ in reference.items():
        if (symbol, action_id, position_date) not in computed_keys:
            findings.append(
                QualityFinding(
                    "equity_event_missing",
                    "warning",
                    symbol,
                    f"No computed entitlement for action {action_id}.",
                )
            )
    return findings
