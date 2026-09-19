/**
 * Theme vocabulary and resolution (MVP 5 / SP 5.10).
 *
 * The preference is what the user chose (including "system"); the resolved
 * theme is what the document actually renders. Keeping the two apart means a
 * user who picked "system" follows the OS when it changes, while an explicit
 * choice is never overridden.
 */

export type ThemePreference = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

export const THEME_STORAGE_KEY = "harbor.theme";

export const THEME_PREFERENCES: readonly ThemePreference[] = ["system", "light", "dark"];

export const THEME_LABELS: Record<ThemePreference, string> = {
  system: "跟随系统",
  light: "浅色",
  dark: "深色",
};

/** Narrow an arbitrary stored value to a preference. */
export function parseThemePreference(value: unknown): ThemePreference {
  return value === "light" || value === "dark" || value === "system" ? value : "system";
}

/** Resolve the preference against the current system setting. */
export function resolveTheme(preference: ThemePreference, prefersDark: boolean): ResolvedTheme {
  if (preference === "system") {
    return prefersDark ? "dark" : "light";
  }
  return preference;
}
