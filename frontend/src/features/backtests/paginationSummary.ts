/**
 * Pagination wording (MVP 5 / SP 5.6).
 *
 * Kept pure so the "showing N–M of T" claim can be tested directly: it is a
 * factual statement about what the screen contains, and it must stay true for
 * an empty first page as well as a full last one.
 */

import { formatCount } from "../../format";

export function paginationSummary(total: number, offset: number, shown: number): string {
  if (total === 0) {
    return "共 0 次运行";
  }
  if (shown === 0) {
    return `共 ${formatCount(total)} 次运行 · 本页无数据`;
  }
  const from = offset + 1;
  const to = offset + shown;
  return `共 ${formatCount(total)} 次运行 · 当前显示第 ${from}–${to} 条`;
}
