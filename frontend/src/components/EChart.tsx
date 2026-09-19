/**
 * Thin React wrapper around ECharts (MVP 5 / SP 5.9, SP 5.10).
 *
 * Three behaviours matter more than features here:
 *
 * * **A chart failure never takes down the page.** Canvas support is probed
 *   during render, so a headless environment renders a note instead of throwing,
 *   and the surrounding table still carries every number.
 * * **The chart instance lives in a ref, not in state.** Initialising and
 *   disposing an imperative library is exactly the "sync with an external
 *   system" case, and keeping it out of state avoids cascading renders.
 * * **Resize is observed, not guessed.** A `ResizeObserver` keeps the chart
 *   correct in a responsive layout, and is skipped where it does not exist.
 */

import { BarChart } from "echarts/charts";
import { GridComponent, LegendComponent, TooltipComponent } from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { useEffect, useMemo, useRef } from "react";

import type { ChartOption } from "./chartTypes";

echarts.use([BarChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer]);

/** Whether this environment can actually paint to a canvas. */
function isCanvasAvailable(): boolean {
  if (typeof document === "undefined") {
    return false;
  }
  try {
    return document.createElement("canvas").getContext("2d") !== null;
  } catch {
    return false;
  }
}

export interface EChartProps {
  option: ChartOption;
  /** Accessible description of what the chart shows. */
  ariaLabel: string;
  /** Height in pixels. */
  height?: number;
  testId?: string;
}

export function EChart({ option, ariaLabel, height = 260, testId }: EChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<echarts.EChartsType | null>(null);
  const canRender = useMemo(() => isCanvasAvailable(), []);

  useEffect(() => {
    const container = containerRef.current;
    if (!canRender || container === null) {
      return undefined;
    }

    try {
      chartRef.current = echarts.init(container, undefined, { renderer: "canvas" });
    } catch (cause) {
      console.warn("Harbor 看板：ECharts 初始化失败，图表不显示，数据仍以表格呈现。", cause);
      return undefined;
    }

    let observer: ResizeObserver | null = null;
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(() => {
        try {
          chartRef.current?.resize();
        } catch {
          // A resize failure is cosmetic; the current frame stays valid.
        }
      });
      observer.observe(container);
    }

    return () => {
      observer?.disconnect();
      chartRef.current?.dispose();
      chartRef.current = null;
    };
  }, [canRender]);

  useEffect(() => {
    if (!canRender) {
      return;
    }
    try {
      chartRef.current?.setOption(option, true);
    } catch (cause) {
      console.warn("Harbor 看板：图表配置无效，已保留上一帧。", cause);
    }
  }, [canRender, option]);

  return (
    <figure className="chart" data-testid={testId}>
      <div
        ref={containerRef}
        className="chart__canvas"
        style={{ height }}
        role="img"
        aria-label={ariaLabel}
      />
      {canRender ? null : (
        <figcaption className="state__meta">
          当前环境不支持 Canvas 渲染；同一数据仍以上方数值与表格完整呈现。
        </figcaption>
      )}
    </figure>
  );
}
