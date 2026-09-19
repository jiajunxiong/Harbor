import { useBacktestMetrics } from "../../api/hooks";
import { EmptyState, ErrorState, LoadingState } from "../../components/States";
import { formatPercent, formatRatio, formatSignedPercent } from "../../format";

export interface MetricCardsProps {
  runId: string;
}

interface MetricSpec {
  key: string;
  label: string;
  value: string;
  /** Why this figure carries a risk tone or a gain/loss tone. */
  tone?: "risk" | "gain" | "loss" | undefined;
  hint?: string;
}

/**
 * Return and risk metrics for a run (MVP 5 / SP 5.16).
 *
 * Two rules are load-bearing:
 *
 * 1. Metrics are computed by the API from the *persisted* net values using the
 *    same core functions the CLI report uses. Nothing is recomputed here, so
 *    the page and `harbor-cli backtest show` cannot disagree.
 * 2. When the server says `available: false` the reason is shown. A degenerate
 *    series has no Sharpe ratio, and printing `0.00` for it would state a
 *    measurement that was never made.
 */
export function MetricCards({ runId }: MetricCardsProps) {
  const query = useBacktestMetrics(runId);

  if (query.isPending) {
    return <LoadingState label="正在读取绩效指标…" />;
  }
  if (query.isError) {
    return (
      <ErrorState
        error={query.error}
        onRetry={() => {
          void query.refetch();
        }}
      />
    );
  }

  const payload = query.data;
  if (!payload.available || payload.metrics === null) {
    return (
      <EmptyState
        title="本运行没有可计算的绩效指标"
        hint={
          <>
            {payload.unavailable_reason ?? "服务端未说明原因。"}
            <br />
            指标由已落库的净值序列计算；净值缺失时不会以 0 代替。
          </>
        }
      />
    );
  }

  const metrics = payload.metrics;
  const currency = payload.currency ?? "";

  const specs: MetricSpec[] = [
    {
      key: "cumulative_return",
      label: "累计收益",
      value: formatSignedPercent(metrics.cumulative_return),
      tone:
        metrics.cumulative_return > 0
          ? "gain"
          : metrics.cumulative_return < 0
            ? "loss"
            : undefined,
      hint: `${metrics.start_date} 至 ${metrics.end_date}`,
    },
    {
      key: "annualized_return",
      label: "年化收益",
      value: formatSignedPercent(metrics.annualized_return),
      tone:
        metrics.annualized_return > 0 ? "gain" : metrics.annualized_return < 0 ? "loss" : undefined,
    },
    {
      key: "max_drawdown",
      label: "最大回撤",
      value: formatPercent(metrics.max_drawdown),
      tone: "risk",
      hint: "峰谷口径，按全量净值序列计算",
    },
    {
      key: "annualized_volatility",
      label: "年化波动率",
      value: formatPercent(metrics.annualized_volatility),
      tone: "risk",
    },
    {
      key: "sharpe_ratio",
      label: "Sharpe",
      value: formatRatio(metrics.sharpe_ratio),
    },
    {
      key: "calmar_ratio",
      label: "Calmar",
      value: formatRatio(metrics.calmar_ratio),
    },
    {
      key: "downside_deviation",
      label: "下行波动",
      value: formatPercent(metrics.downside_deviation),
      tone: "risk",
    },
    {
      key: "periods",
      label: "收益期数",
      value: `${metrics.periods}`,
      hint: "日频收益个数（净值点数 − 1）",
    },
  ];

  return (
    <>
      <div className="metric-strip">
        {specs.map((spec) => (
          <div
            key={spec.key}
            className={`metric${spec.tone === undefined ? "" : ` metric--${spec.tone}`}`}
            data-testid={`metric-${spec.key}`}
          >
            <span className="field__label">{spec.label}</span>
            <span className="metric__value">{spec.value}</span>
            {spec.hint !== undefined ? (
              <span className="card__hint">{spec.hint}</span>
            ) : null}
          </div>
        ))}
      </div>
      <p className="card__hint" data-testid="metrics-source">
        口径：{metrics.start_date} 至 {metrics.end_date} · 共 {metrics.periods} 期 · 币种
        {currency === "" ? "未记录" : ` ${currency}`} · 来源：已落库净值序列（与 CLI
        报告同源，前端不重算）
      </p>
    </>
  );
}
