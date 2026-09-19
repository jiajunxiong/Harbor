import { createContext, useContext } from "react";

import type { ChartPalette } from "./chartPalette";
import type { ResolvedTheme, ThemePreference } from "./theme";

export interface ThemeContextValue {
  /** What the user selected, including `"system"`. */
  preference: ThemePreference;
  /** What the document renders after resolving `"system"`. */
  resolved: ResolvedTheme;
  /** Theme-matched colours for ECharts, kept in step with `resolved`. */
  palette: ChartPalette;
  setPreference: (next: ThemePreference) => void;
}

export const ThemeContext = createContext<ThemeContextValue | null>(null);

/** Read the active theme, or `null` when no provider is mounted. */
export function useTheme(): ThemeContextValue | null {
  return useContext(ThemeContext);
}
