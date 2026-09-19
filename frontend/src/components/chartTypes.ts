/**
 * The subset of the ECharts option type the dashboard actually uses.
 *
 * Composing only the needed series and components keeps the type narrow and
 * lets `echarts/core` tree-shake the rest of the library out of the bundle.
 */

import type { BarSeriesOption, LineSeriesOption } from "echarts/charts";
import type {
  DataZoomComponentOption,
  GridComponentOption,
  LegendComponentOption,
  MarkAreaComponentOption,
  MarkLineComponentOption,
  TooltipComponentOption,
} from "echarts/components";
import type { ComposeOption } from "echarts/core";

export type ChartOption = ComposeOption<
  | BarSeriesOption
  | LineSeriesOption
  | DataZoomComponentOption
  | GridComponentOption
  | LegendComponentOption
  | MarkAreaComponentOption
  | MarkLineComponentOption
  | TooltipComponentOption
>;
