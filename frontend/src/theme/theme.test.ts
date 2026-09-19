import { describe, expect, it } from "vitest";

import { DARK_CHART_PALETTE, LIGHT_CHART_PALETTE, chartPalette, seriesColor } from "./chartPalette";
import { parseThemePreference, resolveTheme } from "./theme";

const HEX = /^#[0-9a-f]{6}$/;

describe("theme resolution", () => {
  it("narrows an unknown stored value to the system default", () => {
    expect(parseThemePreference("dark")).toBe("dark");
    expect(parseThemePreference("light")).toBe("light");
    expect(parseThemePreference("system")).toBe("system");
    expect(parseThemePreference("solarized")).toBe("system");
    expect(parseThemePreference(null)).toBe("system");
  });

  it("follows the OS only while the preference is 'system'", () => {
    expect(resolveTheme("system", true)).toBe("dark");
    expect(resolveTheme("system", false)).toBe("light");
    expect(resolveTheme("light", true)).toBe("light");
    expect(resolveTheme("dark", false)).toBe("dark");
  });
});

describe("chart palette", () => {
  it("defines a complete palette for every theme", () => {
    for (const [name, palette] of Object.entries({ light: LIGHT_CHART_PALETTE, dark: DARK_CHART_PALETTE })) {
      expect(Object.keys(palette).sort(), `${name} palette keys`).toEqual([
        "axisLabel",
        "border",
        "grid",
        "series",
        "surface",
        "text",
      ]);
      expect(palette.series).toHaveLength(4);
      for (const colour of [palette.grid, palette.axisLabel, palette.text, palette.surface, palette.border, ...palette.series]) {
        expect(colour, `${name} palette colour`).toMatch(HEX);
      }
      expect(new Set(palette.series).size, `${name} series must be distinguishable`).toBe(4);
    }
  });

  it("resolves a palette for either resolved theme", () => {
    expect(chartPalette("light")).toBe(LIGHT_CHART_PALETTE);
    expect(chartPalette("dark")).toBe(DARK_CHART_PALETTE);
  });

  it("cycles series colours and stays in range", () => {
    expect(seriesColor(LIGHT_CHART_PALETTE, 0)).toBe(LIGHT_CHART_PALETTE.series[0]);
    expect(seriesColor(LIGHT_CHART_PALETTE, 4)).toBe(LIGHT_CHART_PALETTE.series[0]);
    expect(seriesColor(LIGHT_CHART_PALETTE, 5)).toBe(LIGHT_CHART_PALETTE.series[1]);
  });
});
