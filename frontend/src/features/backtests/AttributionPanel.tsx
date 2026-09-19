import { EmptyState } from "../../components/States";
import type { BacktestRunDetail } from "../../api/types";

export interface AttributionPanelProps {
  run: BacktestRunDetail;
}

/** The CLI command that does produce this data, quoted so the reader can act. */
const CLI_HINT = "harbor-cli backtest show <run_id>";

/**
 * Positions and attribution — honestly empty (MVP 5 / SP 5.19, SP 5.20).
 *
 * This panel does not pretend to be a feature that is merely switched off. The
 * persistence layer only writes net values, fills and rejected trades for a
 * backtest run: `backtest_positions`, `backtest_metrics` and
 * `backtest_factor_snapshots` are never populated, and dividends and corporate
 * actions have no per-run table at all. So the panel states that fact, names the
 * counts it is reporting, and points at the surface that does carry the
 * information.
 *
 * Building a chart here from the persisted counts would be worse than showing
 * nothing, because a reader would reasonably conclude that zero positions means
 * the strategy held nothing.
 */
export function AttributionPanel({ run }: AttributionPanelProps) {
  const positions = run.counts.positions;
  const metricsRows = run.counts.metrics;

  if (positions > 0) {
    // Defensive: if persistence ever starts writing positions, say what exists
    // rather than silently continuing to claim nothing is stored.
    return (
      <EmptyState
        title={`本运行已持久化 ${positions} 条持仓快照`}
        hint={
          <>
            持仓明细的读取接口尚未实现，因此本页面暂无明细视图；数据本身已落库。
            需要立即查看请使用 <span className="mono">{CLI_HINT}</span>。
          </>
        }
      />
    );
  }

  return (
    <>
      <div className="notice notice--info" data-testid="attribution-unavailable">
        <strong>本运行未持久化持仓与归因数据。</strong>
        <br />
        落库事实：持仓快照 <span className="mono">{positions}</span> 条，绩效行{" "}
        <span className="mono">{metricsRows}</span> 条。回测落库目前只写入净值序列、成交与拒单，
        不写入逐日持仓、因子快照或分红/公司行动归因。
      </div>

      <EmptyState
        title="无可展示的持仓明细"
        hint={
          <>
            这不是「持仓为零」，而是「未记录持仓」。把 0 条渲染成一张空图会让人误读为策略空仓，
            因此这里不提供图表。
          </>
        }
      />

      <div className="card">
        <div className="card__header">
          <span className="card__title">本阶段口径说明</span>
          <span className="card__hint">MVP 5 / SP 5.19、SP 5.20 · 部分完成</span>
        </div>
        <div className="card__body">
          <ul className="summary-list">
            <li>
              <span className="mono">backtest_positions</span> 无行：逐日持仓无法拆分到个股、权重与费用。
            </li>
            <li>
              <span className="mono">backtest_metrics</span> 无行：绩效指标改为由已落库净值序列实时计算
              （见「绩效」页），口径与 CLI 同源。
            </li>
            <li>
              分红与公司行动没有按运行存储的表，无法在运行级归因中呈现；相关一致性由 CLI
              与测试套件保证。
            </li>
            <li>
              需要完整归因时以{" "}
              <span className="mono">{CLI_HINT}</span> 的报告为准；看板不重算这些口径。
            </li>
          </ul>
        </div>
      </div>
    </>
  );
}
