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
