import { useMemo } from "react";

import { EChart } from "../../components/EChart";
import { StatusBadge } from "../../components/StatusBadge";
import { LIGHT_CHART_PALETTE } from "../../theme/chartPalette";
import { useTheme } from "../../theme/ThemeContext";
import { buildStatusChartOption, type StatusCount } from "./statusChart";

export interface BacktestStatusChartProps {
  counts: readonly StatusCount[];
}

/**
 * Bar chart of the current page's runs by status, plus the same numbers as
 * text (SP 5.12).
 *
 * The textual summary is not decoration: it is what makes the card readable
 * when the canvas is unavailable, and it is what a test can assert on.
 */
export function BacktestStatusChart({ counts }: BacktestStatusChartProps) {
  const theme = useTheme();
  const palette = theme?.palette ?? LIGHT_CHART_PALETTE;
  const option = useMemo(() => buildStatusChartOption(counts, palette), [counts, palette]);
  const pageTotal = counts.reduce((total, entry) => total + entry.count, 0);

  return (
    <>
      <EChart
        option={option}
        testId="backtest-status-chart"
        ariaLabel={`当前页 ${pageTotal} 次回测运行的状态分布`}
      />
      <ul className="summary-list" data-testid="backtest-status-summary">
        {counts.map((entry) => (
          <li key={entry.status}>
            <StatusBadge status={entry.status} />
            <span className="mono">{entry.count}</span>
          </li>
        ))}
      </ul>
    </>
  );
}
