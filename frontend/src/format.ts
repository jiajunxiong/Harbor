/**
 * Display formatting shared by the tables (MVP 5 / SP 5.12).
 *
 * The guiding rule is that a research tool must never silently change a value:
 * an unparseable timestamp is shown exactly as it arrived rather than
 * reformatted into a confident-looking wrong instant.
 */

const TIMESTAMP_FORMAT = new Intl.DateTimeFormat("zh-CN", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  timeZoneName: "short",
});

const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

/** Placeholder for a missing value, kept visually distinct from real data. */
export const EMPTY_VALUE = "—";

/** Render an ISO timestamp in the viewer's locale and timezone. */
export function formatTimestamp(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") {
    return EMPTY_VALUE;
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return TIMESTAMP_FORMAT.format(parsed);
}

/**
 * Render a date-only value (such as a data cutoff) without any timezone
 * conversion: `2026-01-02` is a calendar date, and parsing it as an instant
 * would shift it to the previous day for a viewer west of UTC.
 */
export function formatDateOnly(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") {
    return EMPTY_VALUE;
  }
  return DATE_ONLY.test(value) ? value : value;
}

/** Render a count with thousands separators. */
export function formatCount(value: number): string {
  return value.toLocaleString("zh-CN");
}

/** Shorten an identifier for display while keeping it recognizable. */
export function shortenId(value: string, keep = 10): string {
  return value.length <= keep ? value : `${value.slice(0, keep)}…`;
}

const AMOUNT_FORMAT = new Intl.NumberFormat("zh-CN", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const QUANTITY_FORMAT = new Intl.NumberFormat("zh-CN", {
  minimumFractionDigits: 0,
  maximumFractionDigits: 4,
});

/** Render a fraction as a percentage, or the placeholder when it is not a number. */
export function formatPercent(value: number | null | undefined, places = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return EMPTY_VALUE;
  }
  return `${(value * 100).toFixed(places)}%`;
}

/**
 * Render a fraction as a signed percentage.
 *
 * The sign is explicit so a table column of returns cannot be misread: a value
 * that merely lost its minus sign is the classic way a loss reads as a gain.
 */
export function formatSignedPercent(value: number | null | undefined, places = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return EMPTY_VALUE;
  }
  const rendered = formatPercent(Math.abs(value), places);
  if (value > 0) {
    return `+${rendered}`;
  }
  if (value < 0) {
    return `-${rendered}`;
  }
  return rendered;
}

/** Render a dimensionless ratio (Sharpe, Calmar) to a fixed number of places. */
export function formatRatio(value: number | null | undefined, places = 2): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return EMPTY_VALUE;
  }
  return value.toFixed(places);
}

/** Render a money amount with thousands separators. */
export function formatAmount(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return EMPTY_VALUE;
  }
  return AMOUNT_FORMAT.format(value);
}

/**
 * Render a traded quantity.
 *
 * Kept apart from `formatAmount` because a quantity is not money and must not be
 * forced to two decimals — rounding 0.5 shares to 1 would misreport a fill.
 */
export function formatQuantity(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return EMPTY_VALUE;
  }
  return QUANTITY_FORMAT.format(value);
}
