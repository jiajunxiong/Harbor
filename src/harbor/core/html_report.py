"""HTML research report (MVP 2 / SP 2.60).

Renders the SP 2.58 results artifact as a self-contained HTML research report
containing a summary (摘要), an embedded net-value chart (图表数据), the key
risks (主要风险, drawdown events and warnings), the data coverage (数据覆盖)
and the documented known assumptions (已知假设).

The report is research-only and never implies a return promise: a disclaimer
and the assumptions section state that the output is not investment advice and
does not represent future returns or drawdowns. Dynamic text is HTML-escaped,
and the embedded chart JSON is written so it cannot break out of its
``<script>`` block.

Pure core logic: only stdlib (html, json) and the SP 2.58 artifact; never
touches storage or CLI code.
"""

import html
import json
from typing import Any


class ReportError(ValueError):
    """Raised when an HTML report cannot be rendered (SP 2.60)."""


_CSS = """\
body { font-family: -apple-system, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
       margin: 2rem auto; max-width: 860px; color: #222; line-height: 1.5; }
h1 { font-size: 1.5rem; }
h2 { font-size: 1.1rem; border-bottom: 1px solid #ddd; padding-bottom: .2rem; }
table { border-collapse: collapse; width: 100%; margin: .5rem 0 1rem; }
th, td { border: 1px solid #ddd; padding: .35rem .5rem; text-align: left;
         font-size: .9rem; vertical-align: top; }
th { background: #f5f5f5; }
.disclaimer { background: #fff8e1; border-left: 4px solid #f0ad4e;
              padding: .6rem .8rem; }
.note { color: #666; font-size: .85rem; }
.run-id { color: #555; }
"""

_ASSUMPTIONS = (
    "本报告仅用于研究，不构成投资建议，也不表示未来收益或回撤 "
    "(research only, not investment advice, no promise of future returns).",
    "港股与美股分别使用各自的交易日、成本、手数/碎股、停牌与企业行动规则，不以统一逻辑近似。",
    "交易成本（佣金、印花税、监管费等）按市场模型计入；默认无滑点，除非配置明确设定。",
    "跨市场组合使用明确的基准币种与日度 FX；缺少 FX 数据时拒绝生成跨市场净值，不默认 1:1。",
    "停牌标的禁止新成交；持仓按最后可得价格估值并产生告警。",
    "股息按派息日计入账本；特别股息遵循策略配置。",
    "企业行动按 MVP 1 的两地专属规则处理。",
    "若历史成分不完整，结果可能受幸存者偏差影响（仅研究用途）。",
    "因子输入按可得日期使用时点数据，决策日后数据不参与。",
)


def _esc(value: Any) -> str:
    """HTML-escape a dynamic value for safe embedding in text nodes."""
    return html.escape("" if value is None else str(value))


def _pct(value: Any) -> str:
    """Format a fraction as a percentage, or a dash when missing."""
    if value is None:
        return "—"
    return f"{float(value):.2%}"


def _num(value: Any) -> str:
    """Format a number with thousands separators, or a dash when missing."""
    if value is None:
        return "—"
    return f"{float(value):,.2f}"


def build_report_data(artifact: dict[str, Any]) -> dict[str, Any]:
    """Extract the structured report data (摘要 + 图表数据) from an artifact.

    Raises:
        ReportError: If the artifact is not an SP 2.58 results artifact.
    """
    if "run" not in artifact or "net_values" not in artifact:
        raise ReportError("Expected an SP 2.58 results artifact with 'run' and 'net_values'.")
    run = artifact["run"]
    metrics = artifact["metrics"]
    return {
        "run_id": run["run_id"],
        "status": run["status"],
        "succeeded": run["succeeded"],
        "inputs": run["inputs"],
        "base_currency": run["base_currency"],
        "initial_capital": run["initial_capital"],
        "day_count": run["day_count"],
        "reconciliation_failures": run["reconciliation_failures"],
        "strategy": artifact["config"].get("strategy"),
        "markets": artifact["config"].get("markets", []),
        "performance": metrics.get("performance"),
        "drawdown": metrics.get("drawdown"),
        "net_values": [
            {"date": row["date"], "total_value": row["total_value"]}
            for row in artifact["net_values"]
        ],
    }


def _svg_net_value_chart(series: list[dict[str, Any]]) -> str:
    """Render the net-value series as a simple inline SVG line chart."""
    if len(series) < 2:
        return '<p class="note">不足两个净值点，无法绘制图表 (need at least two points).</p>'
    width, height = 760, 240
    pad_l, pad_r, pad_t, pad_b = 52, 16, 16, 30
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    values = [float(point["total_value"]) for point in series]
    low, high = min(values), max(values)
    span = high - low
    if span <= 0:
        span = 1.0
    low -= span * 0.05
    high += span * 0.05
    span = high - low
    count = len(series)

    def x_at(index: int) -> float:
        return pad_l + plot_w * index / (count - 1)

    def y_at(value: float) -> float:
        return pad_t + plot_h * (1.0 - (value - low) / span)

    grid: list[str] = []
    for k in range(5):
        value = low + span * k / 4
        yy = y_at(value)
        grid.append(
            f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{width - pad_r}" y2="{yy:.1f}" '
            'stroke="#e8e8e8"/>'
        )
        grid.append(
            f'<text x="{pad_l - 6}" y="{yy + 4:.1f}" text-anchor="end" font-size="10" '
            f'fill="#666">{value:,.0f}</text>'
        )
    points = " ".join(f"{x_at(i):.1f},{y_at(v):.1f}" for i, v in enumerate(values))
    return (
        f'<svg viewBox="0 0 {width} {height}" width="100%" '
        'xmlns="http://www.w3.org/2000/svg">'
        f"{''.join(grid)}"
        f'<polyline points="{points}" fill="none" stroke="#1f77b4" stroke-width="2"/>'
        f'<text x="{pad_l}" y="{height - 8}" font-size="10" fill="#666">'
        f"{_esc(series[0]['date'])}</text>"
        f'<text x="{width - pad_r}" y="{height - 8}" text-anchor="end" font-size="10" '
        f'fill="#666">{_esc(series[-1]["date"])}</text>'
        "</svg>"
    )


def _header_section(data: dict[str, Any]) -> str:
    return (
        "<header>"
        '<h1>回测研究报告 <span class="note">(Backtest Research Report)</span></h1>'
        f'<p class="run-id">run {_esc(data["run_id"])} · {_esc(data["status"])}</p>'
        '<p class="disclaimer">本报告仅用于研究，不构成投资建议，也不表示未来收益或回撤。'
        "Research only; not investment advice; no promise of future returns.</p>"
        "</header>"
    )


def _summary_section(data: dict[str, Any]) -> str:
    succeeded = "yes" if data["succeeded"] else "no"
    rows = [
        ("运行 ID (run id)", _esc(data["run_id"])),
        ("状态 (status)", _esc(data["status"])),
        ("是否成功 (succeeded)", succeeded),
        ("策略 (strategy)", _esc(data["strategy"] or "—")),
    ]
    table = "".join(f"<tr><th>{_esc(key)}</th><td>{_esc(value)}</td></tr>" for key, value in rows)
    return f'<section id="summary"><h2>摘要 (Summary)</h2><table>{table}</table></section>'


def _coverage_section(data: dict[str, Any]) -> str:
    inputs = data["inputs"]
    rows = [
        ("市场 (markets)", _esc(", ".join(data["markets"]))),
        ("基准币种 (base currency)", _esc(data["base_currency"])),
        (
            "初始资金 (initial capital)",
            f"{_num(data['initial_capital'])} {_esc(data['base_currency'])}",
        ),
        ("交易日数 (trading days)", str(data["day_count"])),
        (
            "数据区间 (data range)",
            f"{_esc(inputs['data_range_start'] or '—')} → {_esc(inputs['data_range_end'] or '—')}",
        ),
        ("代码版本 (code version)", _esc(inputs["code_version"])),
        ("配置哈希 (config hash)", _esc(inputs["config_hash"])),
        ("数据截止 (data cutoff)", _esc(inputs["data_cutoff"])),
    ]
    table = "".join(f"<tr><th>{_esc(key)}</th><td>{_esc(value)}</td></tr>" for key, value in rows)
    return (
        f'<section id="coverage"><h2>数据覆盖 (Data Coverage)</h2><table>{table}</table></section>'
    )


def _performance_section(artifact: dict[str, Any]) -> str:
    performance = artifact["metrics"].get("performance")
    if performance is None:
        return (
            '<section id="performance"><h2>绩效指标 (Performance)</h2>'
            '<p class="note">未计算绩效指标 (performance metrics not computed).</p></section>'
        )
    rows = [
        ("累计收益 (cumulative return)", _pct(performance.get("cumulative_return"))),
        ("年化收益 (annualized return)", _pct(performance.get("annualized_return"))),
        ("年化波动率 (annualized volatility)", _pct(performance.get("annualized_volatility"))),
        ("最大回撤 (max drawdown)", _pct(performance.get("max_drawdown"))),
        ("Sharpe 比率", _num(performance.get("sharpe_ratio"))),
        ("Calmar 比率", _num(performance.get("calmar_ratio"))),
        ("下行风险 (downside deviation)", _pct(performance.get("downside_deviation"))),
        ("观测期 (periods)", _esc(performance.get("periods"))),
    ]
    table = "".join(f"<tr><th>{_esc(key)}</th><td>{_esc(value)}</td></tr>" for key, value in rows)
    return (
        f'<section id="performance"><h2>绩效指标 (Performance)</h2><table>{table}</table></section>'
    )


def _risk_section(artifact: dict[str, Any]) -> str:
    parts: list[str] = ['<section id="risk"><h2>主要风险 (Key Risks)</h2>']
    drawdown = artifact["metrics"].get("drawdown")
    if drawdown is not None and drawdown.get("events"):
        parts.append(
            "<h3>回撤事件 (Drawdown Events)</h3><table><tr>"
            "<th>阈值</th><th>触发日</th><th>谷底日</th><th>深度</th><th>恢复</th>"
            "</tr>"
        )
        for event in drawdown["events"]:
            recovered = event.get("recovered_date") or "尚未恢复"
            parts.append(
                "<tr>"
                f"<td>{_pct(event.get('threshold'))}</td>"
                f"<td>{_esc(event.get('start_date'))}</td>"
                f"<td>{_esc(event.get('trough_date'))}</td>"
                f"<td>{_pct(event.get('depth'))}</td>"
                f"<td>{_esc(recovered)}</td>"
                "</tr>"
            )
        parts.append("</table>")
    else:
        parts.append('<p class="note">无阈值触发回撤事件 (no threshold drawdown events).</p>')

    warnings = artifact.get("warnings", [])
    if warnings:
        parts.append("<h3>告警 (Warnings)</h3><ul>")
        for warning in warnings:
            parts.append(f"<li>{_esc(warning['date'])}: {_esc(warning['message'])}</li>")
        parts.append("</ul>")
    else:
        parts.append('<p class="note">无告警 (no warnings).</p>')

    failures = artifact["run"].get("reconciliation_failures", [])
    if failures:
        parts.append("<h3>对账失败 (Reconciliation Failures)</h3><ul>")
        for failure in failures:
            parts.append(f"<li>{_esc(failure)}</li>")
        parts.append("</ul>")
    else:
        parts.append('<p class="note">账本对账通过 (ledger reconciliation passed).</p>')
    parts.append("</section>")
    return "\n".join(parts)


def _assumptions_section() -> str:
    items = "".join(f"<li>{_esc(assumption)}</li>" for assumption in _ASSUMPTIONS)
    return (
        '<section id="assumptions"><h2>已知假设 (Known Assumptions)</h2>'
        '<p class="note">这些默认处理是研究假设，不是市场事实。</p>'
        f"<ul>{items}</ul></section>"
    )


def _chart_section(data: dict[str, Any]) -> str:
    return (
        '<section id="chart"><h2>净值走势 (Net Value Chart)</h2>'
        f"{_svg_net_value_chart(data['net_values'])}"
        '<p class="note">图表数据以 JSON 嵌入本页 <code>window.REPORT_DATA</code>，'
        "可复用于外部工具。</p>"
        "</section>"
    )


def render_html_report(
    artifact: dict[str, Any],
    *,
    title: str | None = None,
) -> str:
    """Render an SP 2.58 artifact as a self-contained HTML report (SP 2.60).

    Args:
        artifact: The SP 2.58 results artifact.
        title: Optional document title; defaults to a run-based title.

    Returns:
        The full HTML document, with the chart data embedded as JSON in
        ``window.REPORT_DATA``.
    """
    data = build_report_data(artifact)
    title_text = title or f"Backtest report {data['run_id']}"
    body = "\n".join(
        [
            _header_section(data),
            _summary_section(data),
            _performance_section(artifact),
            _risk_section(artifact),
            _coverage_section(data),
            _assumptions_section(),
            _chart_section(data),
        ]
    )
    chart_json = json.dumps(
        {"run_id": data["run_id"], "net_values": data["net_values"]}, sort_keys=True
    )
    safe_chart = chart_json.replace("</", "<\\/")
    return (
        '<!doctype html>\n<html lang="zh">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{_esc(title_text)}</title>\n<style>{_CSS}</style>\n</head>\n<body>\n"
        f"{body}\n"
        f"<script>\nwindow.REPORT_DATA = {safe_chart};\n</script>\n"
        "</body>\n</html>\n"
    )
