import { useId, useMemo } from "react";

import { useBacktestRunFilters } from "../../api/hooks";
import { withSelection, type RunListSelection } from "../../app/route";

export interface RunFiltersProps {
  selection: RunListSelection;
  onChange: (next: RunListSelection) => void;
}

const EMPTY_CHOICE = "";

/**
 * Filter and sort controls for the run list (MVP 5 / SP 5.13).
 *
 * The status and strategy menus are populated from `GET /backtests/filters`,
 * i.e. from values that actually occur in the database. Offering a hardcoded
 * list would let a reader select a status nothing has ever had and conclude the
 * history is missing.
 */
export function RunFilters({ selection, onChange }: RunFiltersProps) {
  const ids = useId();
  const options = useBacktestRunFilters();

  const statuses = options.data?.statuses ?? [];
  const strategies = options.data?.strategies ?? [];
  const sortFields = options.data?.sort_fields ?? [];
  const sortOrders = options.data?.sort_orders ?? [];

  // While the option lists are loading the selects would be empty and would
  // silently reset the reader's choice; disabling them keeps the state honest.
  const optionsPending = options.isPending;

  const hint = useMemo(() => {
    if (options.isError) {
      return "筛选选项读取失败：仅可清除筛选，无法保证下拉选项完整。";
    }
    if (optionsPending) {
      return "正在读取可用筛选值…";
    }
    return `可选状态 ${statuses.length} 种 · 策略 ${strategies.length} 种（取自库内实际出现过的值）`;
  }, [options.isError, optionsPending, statuses.length, strategies.length]);

  return (
    <div className="toolbar" role="group" aria-label="运行筛选与排序">
      <div className="field">
        <label className="field__label" htmlFor={`${ids}-status`}>
          状态
        </label>
        <select
          id={`${ids}-status`}
          className="field__control"
          value={selection.status ?? EMPTY_CHOICE}
          disabled={optionsPending}
          onChange={(event) => {
            onChange(withSelection(selection, { status: event.target.value }));
          }}
        >
          <option value={EMPTY_CHOICE}>全部状态</option>
          {statuses.map((status) => (
            <option key={status} value={status}>
              {status}
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label className="field__label" htmlFor={`${ids}-strategy`}>
          策略
        </label>
        <select
          id={`${ids}-strategy`}
          className="field__control"
          value={selection.strategy ?? EMPTY_CHOICE}
          disabled={optionsPending}
          onChange={(event) => {
            onChange(withSelection(selection, { strategy: event.target.value }));
          }}
        >
          <option value={EMPTY_CHOICE}>全部策略</option>
          {strategies.map((strategy) => (
            <option key={strategy} value={strategy}>
              {strategy}
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label className="field__label" htmlFor={`${ids}-from`}>
          数据截点（起）
        </label>
        <input
          id={`${ids}-from`}
          className="field__control"
          type="date"
          value={selection.dataCutoffFrom ?? EMPTY_CHOICE}
          onChange={(event) => {
            onChange(withSelection(selection, { dataCutoffFrom: event.target.value }));
          }}
        />
      </div>

      <div className="field">
        <label className="field__label" htmlFor={`${ids}-to`}>
          数据截点（止）
        </label>
        <input
          id={`${ids}-to`}
          className="field__control"
          type="date"
          value={selection.dataCutoffTo ?? EMPTY_CHOICE}
          onChange={(event) => {
            onChange(withSelection(selection, { dataCutoffTo: event.target.value }));
          }}
        />
      </div>

      <div className="field">
        <label className="field__label" htmlFor={`${ids}-sort`}>
          排序字段
        </label>
        <select
          id={`${ids}-sort`}
          className="field__control"
          value={selection.sort ?? EMPTY_CHOICE}
          disabled={optionsPending}
          onChange={(event) => {
            onChange(withSelection(selection, { sort: event.target.value }));
          }}
        >
          <option value={EMPTY_CHOICE}>默认（开始时间）</option>
          {sortFields.map((field) => (
            <option key={field} value={field}>
              {field}
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label className="field__label" htmlFor={`${ids}-order`}>
          方向
        </label>
        <select
          id={`${ids}-order`}
          className="field__control"
          value={selection.order ?? EMPTY_CHOICE}
          disabled={optionsPending || sortOrders.length === 0}
          onChange={(event) => {
            onChange(withSelection(selection, { order: event.target.value as "asc" | "desc" }));
          }}
        >
          <option value={EMPTY_CHOICE}>默认（降序）</option>
          <option value="desc">降序</option>
          <option value="asc">升序</option>
        </select>
      </div>

      <button
        type="button"
        className="button"
        onClick={() => {
          onChange({ limit: selection.limit, offset: 0 });
        }}
      >
        清除筛选
      </button>

      <span className="card__hint" data-testid="run-filter-hint">
        {hint}
      </span>
    </div>
  );
}
