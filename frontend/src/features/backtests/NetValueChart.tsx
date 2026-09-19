import { useId, useMemo, useState } from "react";

import { useDrawdowns, useNetValues } from "../../api/hooks";
import { EChart } from "../../components/EChart";
import { EmptyState, ErrorState, LoadingState } from "../../components/States";
import { formatAmount } from "../../format";
import { LIGHT_CHART_PALETTE } from "../../theme/chartPalette";
import { useTheme } from "../../theme/ThemeContext";
import {
  buildNetValueChartOption,
  subsampleNote,
  thresholdLabel,
  toDrawdownBands,
} from "./netValueChart";

export interface NetValueChartProps {
  runId: string;
  /**
   * The threshold whose intervals are shaded, or `null` to let the chart pick the
   * deepest one. Lifted to the page so the drawdown table and the curve annotate
   * the *same* intervals (SP 5.24 chart/table linkage).
   */
  bandThreshold?: number | null;
  onThresholdChange?: (threshold: number) => void;
}

/**
 * How many points to request for drawing.
 *
 * The server caps this at 5000 and downsamples by LTTB, keeping the first and
 * last point, so the curve on screen has the right endpoints and shape. The
 * metrics and drawdowns are computed server-side from the *full* series
 * regardless of this number, and the caption says so.
 */
const MAX_CHART_POINTS = 1200;

/** The equity curve with its drawdown intervals shaded (MVP 5 / SP 5.15, SP 5.17). */
export function NetValueChart({ runId, bandThreshold, onThresholdChange }: NetValueChartProps) {
  const theme = useTheme();
  const palette = theme?.palette ?? LIGHT_CHART_PALETTE;
  const ids = useId();

  const netValues = useNetValues(runId, MAX_CHART_POINTS);
  const drawdowns = useDrawdowns(runId);
  const [ownThreshold, setThreshold] = useState<number | null>(null);

  const bands = useMemo(
    () => toDrawdownBands(drawdowns.data?.events ?? []),
    [drawdowns.data],
  );
  const thresholds = drawdowns.data?.thresholds ?? [];

  // Shade the deepest threshold by default: shading every overlapping interval
  // would stack translucent rectangles over the same dates.
  const activeThreshold =
    bandThreshold ?? ownThreshold ?? (thresholds.length > 0 ? Math.max(...thresholds) : undefined);

  const series = netValues.data ?? null;
  const option = useMemo(
    () =>
      series === null
        ? null
        : buildNetValueChartOption({
            series,
            bands,
            palette,
            bandThreshold: activeThreshold,
          }),
    [series, bands, palette, activeThreshold],
  );

  if (netValues.isPending) {
    return <LoadingState label="正在读取净值序列…" />;
  }
  if (netValues.isError) {
    return (
      <ErrorState
        error={netValues.error}
        onRetry={() => {
          void netValues.refetch();
        }}
      />
    );
  }

  if (series === null || series.points.length === 0) {
    return (
      <EmptyState
        title="本运行没有净值序列"
        hint="该运行未落库任何净值点位（通常是失败或中断的运行），因此没有可绘制的曲线，也没有可标注的回撤。"
      />
    );
  }

  const first = series.points[0];
  const last = series.points[series.points.length - 1];

  return (
    <>
      <div className="chart-toolbar">
        <label className="field__label" htmlFor={`${ids}-threshold`}>
          回撤标注阈值
        </label>
        <select
          id={`${ids}-threshold`}
          className="field__control"
          value={activeThreshold === undefined ? "" : String(activeThreshold)}
          disabled={thresholds.length === 0}
          onChange={(event) => {
            const next = event.target.value === "" ? null : Number.parseFloat(event.target.value);
            setThreshold(next);
            if (next !== null) {
              onThresholdChange?.(next);
            }
          }}
        >
          {thresholds.length === 0 ? <option value="">无可用阈值</option> : null}
          {thresholds.map((value) => (
            <option key={value} value={String(value)}>
              {thresholdLabel(value)}
            </option>
          ))}
        </select>
        <span className="card__hint" data-testid="net-value-note">
          {subsampleNote(series)}
        </span>
      </div>

      {option !== null ? <EChart option={option} height={360} ariaLabel="净值曲线" /> : null}

      <div className="card__hint" data-testid="net-value-range">
        {series.currency ?? "币种未记录"} · 首个点位 {first?.as_of_date ?? "—"}（
        {formatAmount((first?.cash ?? 0) + (first?.securities_value ?? 0))}）· 末个点位{" "}
        {last?.as_of_date ?? "—"}（
        {formatAmount((last?.cash ?? 0) + (last?.securities_value ?? 0))}）
      </div>

      {drawdowns.isError ? (
        <p className="state__meta" data-testid="drawdown-unavailable">
          回撤事件读取失败，曲线未标注回撤区间；错误详情：请刷新重试。
        </p>
      ) : null}
      {drawdowns.isSuccess && bands.length === 0 ? (
        <p className="state__meta" data-testid="drawdown-none">
          本运行没有任何回撤事件触及给定阈值（{thresholds.map(thresholdLabel).join(" / ") || "—"}）。
        </p>
      ) : null}
    </>
  );
}
