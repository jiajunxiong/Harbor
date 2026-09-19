/**
 * Chart palette, one entry per theme (MVP 5 / SP 5.10).
 *
 * These colours live in TypeScript rather than in `tokens.css` for a concrete
 * reason: ECharts draws into a `<canvas>`, which cannot read CSS custom
 * properties, and a render-time DOM read would make the chart depend on when
 * the stylesheet happened to load. Keeping the palette a pure function of the
 * resolved theme makes the chart deterministic and trivially testable.
 *
 * The values are deliberately parallel to the `--color-*` tokens in
 * `tokens.css`; `chartPalette.test.ts` pins the shape of both palettes so a
 * half-finished theme cannot ship. UI (CSS) and chart (canvas) are two
 * rendering surfaces, each reading its own declaration of the same design.
 */

import type { ResolvedTheme } from "./theme";

export interface ChartPalette {
  /** Categorical series colours, in assignment order. */
  series: readonly [string, string, string, string];
  /** Axis line, tick and split colours. */
  grid: string;
  axisLabel: string;
  /** Primary text, used by the tooltip. */
  text: string;
  /** Tooltip background. */
  surface: string;
  border: string;
}

export const LIGHT_CHART_PALETTE: ChartPalette = {
  series: ["#1d4ed8", "#0f766e", "#b45309", "#6d28d9"],
  grid: "#dfe3ea",
  axisLabel: "#5a6473",
  text: "#131722",
  surface: "#ffffff",
  border: "#d8dce3",
};

export const DARK_CHART_PALETTE: ChartPalette = {
  series: ["#7aa2f7", "#5eead4", "#fbbf24", "#c4b5fd"],
  grid: "#2c3644",
  axisLabel: "#9aa5b5",
  text: "#e7ebf1",
  surface: "#1c2531",
  border: "#2c3644",
};

export const CHART_PALETTES: Record<ResolvedTheme, ChartPalette> = {
  light: LIGHT_CHART_PALETTE,
  dark: DARK_CHART_PALETTE,
};

/** The palette for a resolved theme. */
export function chartPalette(theme: ResolvedTheme): ChartPalette {
  return CHART_PALETTES[theme];
}

/** Pick a series colour by position, cycling through the palette. */
export function seriesColor(palette: ChartPalette, index: number): string {
  const colours = palette.series;
  const slot = ((index % colours.length) + colours.length) % colours.length;
  return colours[slot] ?? colours[0];
}
