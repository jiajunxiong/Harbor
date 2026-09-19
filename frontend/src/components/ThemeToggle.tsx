import { useTheme } from "../theme/ThemeContext";
import { THEME_LABELS, THEME_PREFERENCES } from "../theme/theme";

/** Light/dark/system selector (MVP 5 / SP 5.10). */
export function ThemeToggle() {
  const theme = useTheme();
  if (theme === null) {
    return null;
  }

  return (
    <div className="theme-toggle">
      <label className="theme-toggle__label" htmlFor="harbor-theme">
        主题
      </label>
      <select
        id="harbor-theme"
        className="theme-toggle__select"
        value={theme.preference}
        onChange={(event) => {
          theme.setPreference(event.target.value as (typeof THEME_PREFERENCES)[number]);
        }}
      >
        {THEME_PREFERENCES.map((preference) => (
          <option key={preference} value={preference}>
            {THEME_LABELS[preference]}
          </option>
        ))}
      </select>
    </div>
  );
}
