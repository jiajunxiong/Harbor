import { useCallback, useEffect, useLayoutEffect, useMemo, useState, type ReactNode } from "react";

import { chartPalette } from "./chartPalette";
import { ThemeContext, type ThemeContextValue } from "./ThemeContext";
import {
  THEME_STORAGE_KEY,
  parseThemePreference,
  resolveTheme,
  type ResolvedTheme,
  type ThemePreference,
} from "./theme";

const DARK_QUERY = "(prefers-color-scheme: dark)";

function readStoredPreference(): ThemePreference {
  try {
    return parseThemePreference(window.localStorage.getItem(THEME_STORAGE_KEY));
  } catch {
    return "system";
  }
}

function systemPrefersDark(): boolean {
  try {
    return typeof window.matchMedia === "function" ? window.matchMedia(DARK_QUERY).matches : false;
  } catch {
    return false;
  }
}

export interface ThemeProviderProps {
  children: ReactNode;
  /** Initial preference; defaults to the stored value so a reload keeps the choice. */
  initialPreference?: ThemePreference;
}

/**
 * Applies the theme to `<html data-theme>` and publishes the chart palette
 * (MVP 5 / SP 5.10).
 *
 * The attribute is written in a layout effect so the document is themed before
 * the browser paints, and the palette is a pure function of the resolved theme
 * so the charts can never lag behind it by a frame.
 */
export function ThemeProvider({ children, initialPreference }: ThemeProviderProps) {
  const [preference, setPreferenceState] = useState<ThemePreference>(
    () => initialPreference ?? readStoredPreference(),
  );
  const [prefersDark, setPrefersDark] = useState<boolean>(systemPrefersDark);

  // Follow the OS while the preference is "system" (SP 5.10). The current value
  // is already the initial state, so only changes need to be subscribed to.
  useEffect(() => {
    if (typeof window.matchMedia !== "function") {
      return;
    }
    const media = window.matchMedia(DARK_QUERY);
    const onChange = (event: MediaQueryListEvent) => {
      setPrefersDark(event.matches);
    };
    media.addEventListener("change", onChange);
    return () => {
      media.removeEventListener("change", onChange);
    };
  }, []);

  const resolved: ResolvedTheme = resolveTheme(preference, prefersDark);
  const palette = useMemo(() => chartPalette(resolved), [resolved]);

  useLayoutEffect(() => {
    document.documentElement.dataset["theme"] = resolved;
  }, [resolved]);

  const setPreference = useCallback((next: ThemePreference) => {
    setPreferenceState(next);
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // A blocked localStorage must not break rendering.
    }
  }, []);

  const value = useMemo<ThemeContextValue>(
    () => ({ preference, resolved, palette, setPreference }),
    [preference, resolved, palette, setPreference],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}
