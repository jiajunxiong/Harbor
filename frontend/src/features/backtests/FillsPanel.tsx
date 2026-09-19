import { useCallback, useState } from "react";

import { useFills } from "../../api/hooks";
import { DataTable, type Column } from "../../components/DataTable";
import { EmptyState, ErrorState, LoadingState } from "../../components/States";
import { EMPTY_VALUE, formatAmount, formatCount, formatDateOnly, formatQuantity } from "../../format";
import { paginationSummary } from "./paginationSummary";
import type { FillRow } from "../../api/types";

const COLUMNS: readonly Column<FillRow>[] = [
  {
    key: "trade_date",
    header: "交易日",
    render: (fill) => <span className="mono">{formatDateOnly(fill.trade_date)}</span>,
  },
  { key: "market", header: "市场", render: (fill) => fill.market },
  {
    key: "symbol",
    header: "代码",
    render: (fill) => <span className="mono">{fill.symbol}</span>,
  },
  { key: "side", header: "方向", render: (fill) => fill.side },
  {
    key: "quantity",
    header: "数量",
    numeric: true,
    render: (fill) => formatQuantity(fill.quantity),
  },
  {
    key: "price",
    header: "价格",
    numeric: true,
    render: (fill) => formatAmount(fill.price),
  },
  {
    key: "fee",
    header: "费用",
    numeric: true,
    render: (fill) => formatAmount(fill.fee),
  },
  { key: "currency", header: "币种", render: (fill) => fill.currency },
  {
    key: "order_ref",
    header: "委托号",
    render: (fill) => <span className="mono">{fill.order_ref}</span>,
  },
];

const PAGE_SIZE = 25;
const MARKET_CHOICES = ["", "HK", "US"] as const;

export interface FillsPanelProps {
  runId: string;
}

/**
 * Executed trades for one run (MVP 5 / SP 5.18).
 *
 * The market and symbol filters are sent to the server, and `total` therefore
 * describes the *filtered* set — so "共 N 笔" is a statement about what was
 * asked for, not about the whole run.
 */
export function FillsPanel({ runId }: FillsPanelProps) {
  const [market, setMarket] = useState<string>("");
  const [symbol, setSymbol] = useState("");
  const [offset, setOffset] = useState(0);

  // A new symbol only takes effect on submit: filtering per keystroke would
  // issue a request for every prefix of a ticker.
  const [appliedSymbol, setAppliedSymbol] = useState("");

  const query = useFills(runId, {
    limit: PAGE_SIZE,
    offset,
    market: market === "" ? undefined : market,
    symbol: appliedSymbol === "" ? undefined : appliedSymbol,
  });

  // Changing a filter returns to the first page. Done here rather than in an
  // effect: the reset is a consequence of the user's action, and keeping the
  // stale offset would show an empty page whose emptiness is not a fact about
  // the data.
  const changeMarket = useCallback((next: string) => {
    setMarket(next);
    setOffset(0);
  }, []);

  const applySymbol = useCallback((next: string) => {
    setAppliedSymbol(next);
    setOffset(0);
  }, []);

  const goPrevious = useCallback(() => {
    setOffset((current) => Math.max(0, current - PAGE_SIZE));
  }, []);

  const nextOffset = query.data?.next_offset ?? null;
  const goNext = useCallback(() => {
    if (nextOffset !== null) {
      setOffset(nextOffset);
    }
  }, [nextOffset]);

  const rows = query.data?.items ?? [];
  const total = query.data?.total ?? 0;
  const summary = query.data === undefined ? "读取中…" : `${formatCount(total)} 笔成交`;

  const marketLabel = market === "" ? "全部" : market;
  const symbolLabel = appliedSymbol === "" ? "全部" : appliedSymbol;
  const filtered = market !== "" || appliedSymbol !== "";

  // Two different empty situations, and conflating them would mislead: either
  // nothing matches the filter at all, or the page has run past the end of a
  // non-empty result set.
  const emptyHint =
    total === 0
      ? filtered
        ? `没有任何成交匹配当前筛选（市场 ${marketLabel} · 代码 ${symbolLabel}）；这不代表本运行没有成交，清除筛选即可查看全部。`
        : "本运行未落库任何成交记录。"
      : `已命中 ${formatCount(total)} 笔，但起始偏移 ${offset} 已越过末尾；请回到上一页。`;

  return (
    <>
      <div className="toolbar" role="group" aria-label="成交筛选">
        <div className="field">
          <label className="field__label" htmlFor="harbor-fill-market">
            市场
          </label>
          <select
            id="harbor-fill-market"
            className="field__control"
            value={market}
            onChange={(event) => {
              changeMarket(event.target.value);
            }}
          >
            {MARKET_CHOICES.map((choice) => (
              <option key={choice} value={choice}>
                {choice === "" ? "全部市场" : choice}
              </option>
            ))}
          </select>
        </div>

        <form
          className="field"
          onSubmit={(event) => {
            event.preventDefault();
            applySymbol(symbol.trim());
          }}
        >
          <label className="field__label" htmlFor="harbor-fill-symbol">
            代码（精确匹配）
          </label>
          <input
            id="harbor-fill-symbol"
            className="field__control"
            value={symbol}
            maxLength={32}
            placeholder="如 700.HK / AAPL"
            onChange={(event) => {
              setSymbol(event.target.value);
            }}
          />
          <button type="submit" className="button">
            应用
          </button>
        </form>

        <span className="card__hint">{summary}</span>
      </div>

      {query.isPending ? <LoadingState label="正在读取成交明细…" /> : null}

      {query.isError ? (
        <ErrorState
          error={query.error}
          onRetry={() => {
            void query.refetch();
          }}
        />
      ) : null}

      {query.isSuccess && rows.length === 0 ? (
        <EmptyState title={total === 0 ? "没有符合条件的成交" : "本页无数据"} hint={emptyHint} />
      ) : null}

      {rows.length > 0 ? (
        <>
          <DataTable
            columns={COLUMNS}
            rows={rows}
            rowKey={(fill) => `${fill.order_ref}-${fill.trade_date}-${fill.symbol}`}
            caption={`成交明细，本页 ${rows.length} 条`}
          />
          <div className="pagination">
            <span className="pagination__summary">
              {paginationSummary(total, offset, rows.length).replace("次运行", "笔成交")}
            </span>
            <button
              type="button"
              className="button"
              onClick={goPrevious}
              disabled={offset === 0 || query.isFetching}
            >
              上一页
            </button>
            <button
              type="button"
              className="button"
              onClick={goNext}
              disabled={nextOffset === null || query.isFetching}
            >
              下一页
            </button>
          </div>
        </>
      ) : null}

      {rows.length > 0 ? (
        <p className="card__hint">
          费用为落库值，未在展示层重新计算；币种不一致时不合并金额。委托号缺失显示为{" "}
          {EMPTY_VALUE}。
        </p>
      ) : null}
    </>
  );
}
