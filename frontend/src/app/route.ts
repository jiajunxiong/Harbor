/**
 * Hash-based routing for the read-only dashboard (MVP 5 / SP 5.24 deep links).
 *
 * Small on purpose. A research dashboard needs *shareable, reloadable* URLs — a
 * reviewer should be able to paste a link to one run's drawdown view — but it
 * does not need a routing framework, and hash routing works when the static
 * bundle is served by anything at all (including `file://`), which matters for
 * the container delivery in SP 5.61.
 *
 * Everything here is pure so the URL contract can be unit-tested without a DOM.
 */

import type { RunQuery, SortOrder } from "../api/types";

/** The detail page's panels; each has its own link. */
export const RUN_TABS = ["overview", "performance", "trades", "attribution", "replay"] as const;
export type RunTab = (typeof RUN_TABS)[number];

export const DEFAULT_RUN_TAB: RunTab = "overview";

/**
 * The validation detail page's panels (SP 5.26–SP 5.35).
 *
 * Fewer tabs than there are specification points on purpose: the conclusion
 * belongs beside the run's status and its notices (SP 5.32 / SP 5.35) and the
 * coverage warnings belong beside the coverage they explain (SP 5.30 / SP 5.33),
 * so the pipeline artifacts that the pipeline has not written yet sit together
 * in one place instead of in three empty tabs (SP 5.27 / SP 5.29 / SP 5.31).
 */
export const VALIDATION_TABS = ["overview", "split", "coverage", "pipeline", "report"] as const;
export type ValidationTab = (typeof VALIDATION_TABS)[number];

export const DEFAULT_VALIDATION_TAB: ValidationTab = "overview";

/** The literal path segment that selects the validation dashboard. */
export const VALIDATIONS_SEGMENT = "validations";

/** The run list's shareable state (SP 5.13 filters plus paging). */
export interface RunListSelection {
  limit: number;
  offset: number;
  status?: string;
  strategy?: string;
  dataCutoffFrom?: string;
  dataCutoffTo?: string;
  sort?: string;
  order?: SortOrder;
}

export const DEFAULT_PAGE_SIZE = 25;

/** How many runs one comparison link may carry, matching the server's bound. */
export const MAX_COMPARISON_RUNS = 5;

/** The literal path segment that selects the comparison view. */
export const COMPARISON_SEGMENT = "compare";

export type Route =
  | { kind: "runs"; selection: RunListSelection }
  | { kind: "run"; runId: string; tab: RunTab }
  | { kind: "comparison"; runIds: string[] }
  | { kind: "validations"; limit: number; offset: number }
  | { kind: "validation"; runId: string; tab: ValidationTab };

export const DEFAULT_ROUTE: Route = {
  kind: "runs",
  selection: { limit: DEFAULT_PAGE_SIZE, offset: 0 },
};

function asSortOrder(value: string | null): SortOrder | undefined {
  return value === "asc" || value === "desc" ? value : undefined;
}

function positiveInt(value: string | null, fallback: number): number {
  if (value === null) {
    return fallback;
  }
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback;
}

function clean(value: string | null): string | undefined {
  const trimmed = value?.trim();
  return trimmed ? trimmed : undefined;
}

/** Read the run-list filter state out of a query string. */
export function parseRunListSelection(params: URLSearchParams): RunListSelection {
  const selection: RunListSelection = {
    limit: positiveInt(params.get("limit"), DEFAULT_PAGE_SIZE),
    offset: positiveInt(params.get("offset"), 0),
  };
  const status = clean(params.get("status"));
  const strategy = clean(params.get("strategy"));
  const from = clean(params.get("data_cutoff_from"));
  const to = clean(params.get("data_cutoff_to"));
  const sort = clean(params.get("sort"));
  const order = asSortOrder(params.get("order"));
  if (status !== undefined) selection.status = status;
  if (strategy !== undefined) selection.strategy = strategy;
  if (from !== undefined) selection.dataCutoffFrom = from;
  if (to !== undefined) selection.dataCutoffTo = to;
  if (sort !== undefined) selection.sort = sort;
  if (order !== undefined) selection.order = order;
  return selection;
}

/** Render the run-list filter state as a query string (without the `?`). */
export function buildRunListSearch(selection: RunListSelection): string {
  const params = new URLSearchParams();
  if (selection.limit !== DEFAULT_PAGE_SIZE) {
    params.set("limit", String(selection.limit));
  }
  if (selection.offset > 0) {
    params.set("offset", String(selection.offset));
  }
  for (const [key, value] of [
    ["status", selection.status],
    ["strategy", selection.strategy],
    ["data_cutoff_from", selection.dataCutoffFrom],
    ["data_cutoff_to", selection.dataCutoffTo],
    ["sort", selection.sort],
    ["order", selection.order],
  ] as const) {
    if (value !== undefined) {
      params.set(key, value);
    }
  }
  return params.toString();
}

/** Convert the shareable selection into the API query (SP 5.13). */
export function selectionToQuery(selection: RunListSelection): RunQuery {
  const query: RunQuery = { limit: selection.limit, offset: selection.offset };
  if (selection.status !== undefined) query.status = selection.status;
  if (selection.strategy !== undefined) query.strategy = selection.strategy;
  if (selection.dataCutoffFrom !== undefined) query.data_cutoff_from = selection.dataCutoffFrom;
  if (selection.dataCutoffTo !== undefined) query.data_cutoff_to = selection.dataCutoffTo;
  if (selection.sort !== undefined) query.sort = selection.sort;
  if (selection.order !== undefined) query.order = selection.order;
  return query;
}

/** Whether the selection narrows the history at all (drives the "clear" button). */
export function hasActiveFilters(selection: RunListSelection): boolean {
  return (
    selection.status !== undefined ||
    selection.strategy !== undefined ||
    selection.dataCutoffFrom !== undefined ||
    selection.dataCutoffTo !== undefined
  );
}

function nonEmpty(value: string | undefined): string | undefined {
  return value === undefined || value === "" ? undefined : value;
}

/**
 * Apply a change to the selection.
 *
 * An explicit empty string clears a filter (the URL then omits the key, so the
 * server applies its own default rather than receiving `status=`). Changing
 * anything other than the offset returns to the first page: keeping a stale
 * offset would show an empty page after the result set shrank, which looks like
 * "no matching runs" even though matches exist.
 */
export function withSelection(
  selection: RunListSelection,
  changes: Partial<RunListSelection>,
): RunListSelection {
  const pick = <T,>(next: T | undefined, current: T | undefined): T | undefined =>
    next === undefined ? current : next;

  const next: RunListSelection = {
    limit: pick(changes.limit, selection.limit) ?? DEFAULT_PAGE_SIZE,
    offset: pick(changes.offset, 0) ?? 0,
  };
  const status = nonEmpty(pick(changes.status, selection.status));
  const strategy = nonEmpty(pick(changes.strategy, selection.strategy));
  const from = nonEmpty(pick(changes.dataCutoffFrom, selection.dataCutoffFrom));
  const to = nonEmpty(pick(changes.dataCutoffTo, selection.dataCutoffTo));
  const sort = nonEmpty(pick(changes.sort, selection.sort));
  const order = pick(changes.order, selection.order);
  if (status !== undefined) next.status = status;
  if (strategy !== undefined) next.strategy = strategy;
  if (from !== undefined) next.dataCutoffFrom = from;
  if (to !== undefined) next.dataCutoffTo = to;
  if (sort !== undefined) next.sort = sort;
  if (order !== undefined) next.order = order;
  return next;
}

function asTab(value: string | null): RunTab {
  return RUN_TABS.includes(value as RunTab) ? (value as RunTab) : DEFAULT_RUN_TAB;
}

function asValidationTab(value: string | null): ValidationTab {
  return VALIDATION_TABS.includes(value as ValidationTab)
    ? (value as ValidationTab)
    : DEFAULT_VALIDATION_TAB;
}

/** Decode a fragment, tolerating malformed input rather than throwing. */
function safeDecode(value: string): string {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

/**
 * Split a fragment into its path and query parts.
 *
 * A proxy may percent-encode the whole fragment — a forwarded localhost port
 * turns `#/runs?tab=trades` into `#%2Fruns%3Ftab%3Dtrades` — and such a fragment
 * has no path separator left in it, which is the signal used here. A fragment
 * that still has real separators is left untouched: decoding it would promote an
 * escaped `?` inside a run id into a query separator and change the route.
 */
function splitFragment(fragment: string): [string, string] {
  const canonical = fragment.includes("/") ? fragment : safeDecode(fragment);
  const [path = "", search = ""] = canonical.split("?");
  return [path, search];
}

/**
 * Parse a location hash into a route.
 *
 * An unrecognised hash falls back to the run list rather than to an error page:
 * a stale bookmark should still land somewhere useful.
 */
export function parseHash(hash: string): Route {
  const raw = hash.startsWith("#") ? hash.slice(1) : hash;
  const [pathPart, searchPart] = splitFragment(raw);
  const segments = pathPart.split("/").filter((segment) => segment !== "");

  if (segments[0] === VALIDATIONS_SEGMENT) {
    if (segments.length === 1) {
      const params = new URLSearchParams(searchPart);
      return {
        kind: "validations",
        limit: positiveInt(params.get("limit"), DEFAULT_PAGE_SIZE),
        offset: positiveInt(params.get("offset"), 0),
      };
    }
    return {
      kind: "validation",
      runId: safeDecode(segments[1] ?? ""),
      tab: asValidationTab(new URLSearchParams(searchPart).get("tab")),
    };
  }
  if (segments[0] !== "runs") {
    return DEFAULT_ROUTE;
  }
  if (segments.length === 1) {
    return { kind: "runs", selection: parseRunListSelection(new URLSearchParams(searchPart)) };
  }
  // The comparison view is a literal segment rather than a run id, so it must be
  // recognised before the general `#/runs/<id>` shape — the same precedence rule
  // the API applies to `/backtests/compare`.
  if (segments[1] === COMPARISON_SEGMENT) {
    return { kind: "comparison", runIds: parseRunIds(new URLSearchParams(searchPart)) };
  }
  return {
    kind: "run",
    runId: safeDecode(segments[1] ?? ""),
    tab: asTab(new URLSearchParams(searchPart).get("tab")),
  };
}

/** Read the compared run ids out of a query string, de-duplicated and ordered. */
export function parseRunIds(params: URLSearchParams): string[] {
  const raw = params.get("ids") ?? "";
  const ids: string[] = [];
  for (const part of raw.split(",")) {
    const id = part.trim();
    // A repeated id would make the API refuse the whole comparison, so it is
    // dropped here rather than surfaced as an error a reader cannot act on.
    if (id !== "" && !ids.includes(id) && ids.length < MAX_COMPARISON_RUNS) {
      ids.push(id);
    }
  }
  return ids;
}

/** Render a route as a location hash (including the leading `#`). */
export function buildHash(route: Route): string {
  if (route.kind === "runs") {
    const search = buildRunListSearch(route.selection);
    return search === "" ? "#/runs" : `#/runs?${search}`;
  }
  if (route.kind === "validations") {
    const params = new URLSearchParams();
    if (route.limit !== DEFAULT_PAGE_SIZE) {
      params.set("limit", String(route.limit));
    }
    if (route.offset > 0) {
      params.set("offset", String(route.offset));
    }
    const search = params.toString();
    return search === "" ? `#/${VALIDATIONS_SEGMENT}` : `#/${VALIDATIONS_SEGMENT}?${search}`;
  }
  if (route.kind === "validation") {
    const encoded = encodeURIComponent(route.runId);
    const path = `#/${VALIDATIONS_SEGMENT}/${encoded}`;
    return route.tab === DEFAULT_VALIDATION_TAB ? path : `${path}?tab=${route.tab}`;
  }
  if (route.kind === "comparison") {
    const ids = route.runIds.slice(0, MAX_COMPARISON_RUNS).join(",");
    return `#/runs/${COMPARISON_SEGMENT}?ids=${encodeURIComponent(ids)}`;
  }
  const encoded = encodeURIComponent(route.runId);
  return route.tab === DEFAULT_RUN_TAB ? `#/runs/${encoded}` : `#/runs/${encoded}?tab=${route.tab}`;
}
