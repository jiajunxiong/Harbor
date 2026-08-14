"""OOS HTML research report (MVP 3 / SP 3.68).

Renders the SP 3.66 OOS export document as a self-contained HTML research
report containing the split diagram (切分图), the OOS net-value chart (OOS 净值),
the fold dispersion (折叠离散度), the environment / stress performance (环境/压
力表现), the coverage scores (覆盖评分), the limitations (限制) and the
conclusion (结论), with a prominent research-only banner (显著展示研究性质).

The split, stress differences and conclusion are read from the SP 3.66 export;
the net values, fold dispersion (SP 3.39), environment segments (SP 3.50) and
coverage (SP 3.9) are passed in when available. The report is research-only and
never implies a return promise: the prominent banner and the conclusion note
state that the output is not investment advice and contains no promise of
future returns (结论不含收益承诺). Dynamic text is HTML-escaped and the embedded
chart JSON cannot break out of its ``<script>`` block.

Pure core layer: only stdlib (html, json, datetime) and the SP 3.39 / 3.50 /
3.9 types; never touches storage, services or CLI.
"""

import html
import json
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from harbor.core.coverage_scoring import MarketCoverage
from harbor.core.environment_segmented import EnvironmentSegmentedPerformance
from harbor.core.oos_dispersion import OosDispersionReport


class OosReportError(ValueError):
    """Raised when an OOS HTML report cannot be rendered (SP 3.68)."""


_CSS = """\
body { font-family: -apple-system, "Segoe UI", "Helvetica Neue", Arial, sans-serif;
       margin: 2rem auto; max-width: 900px; color: #222; line-height: 1.5; }
h1 { font-size: 1.5rem; }
h2 { font-size: 1.1rem; border-bottom: 1px solid #ddd; padding-bottom: .2rem; }
table { border-collapse: collapse; width: 100%; margin: .5rem 0 1rem; }
th, td { border: 1px solid #ddd; padding: .35rem .5rem; text-align: left;
         font-size: .9rem; vertical-align: top; }
th { background: #f5f5f5; }
.research { background: #fff3cd; border: 2px solid #f0ad4e; border-radius: .3rem;
            padding: .7rem .9rem; font-weight: bold; }
.note { color: #666; font-size: .85rem; }
.run-id { color: #555; }
.split-bar { position: relative; height: 2.2rem; margin: .5rem 0 1rem; }
.split-seg { position: absolute; top: 0; bottom: 0; border-radius: .2rem;
             overflow: hidden; font-size: .7rem; color: #fff; text-align: center;
             padding-top: .55rem; }
"""

_RESEARCH_BANNER = (
    "本报告仅用于研究，不构成投资建议，也不表示未来收益或回撤 "
    "(research only; not investment advice; no promise of future returns)."
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


def build_report_data(export_dict: Mapping[str, Any]) -> dict[str, Any]:
    """Extract the structured report data from an SP 3.66 OOS export.

    Raises:
        OosReportError: If the artifact is not an SP 3.66 OOS export document.
    """
    if (
        "run" not in export_dict
        or "frozen_config" not in export_dict
        or "conclusion" not in export_dict
    ):
        raise OosReportError(
            "Expected an SP 3.66 OOS export with 'run', 'frozen_config' and 'conclusion'."
        )
    return {
        "run_id": export_dict["run"]["run_id"],
        "split": export_dict["frozen_config"]["split"],
        "stress_results": export_dict.get("stress_results", {}),
        "conclusion": export_dict["conclusion"],
        "audit_count": len(export_dict.get("audit_events", ())),
    }


def _header_section(data: dict[str, Any]) -> str:
    """The header with the prominent research-only banner (显著展示研究性质)."""
    return (
        "<header>"
        "<h1>样本外验证研究报告 "
        '<span class="note">(Out-of-Sample Research Report)</span></h1>'
        f'<p class="run-id">validation run {_esc(data["run_id"])} · '
        f"审计事件 {data['audit_count']} (audit events)</p>"
        f'<p class="research">{_esc(_RESEARCH_BANNER)}</p>'
        "</header>"
    )


def _split_section(split: Mapping[str, Any]) -> str:
    """The split diagram (切分图) and interval table."""
    train_start = split["train_start"]
    train_end = split["train_end"]
    validation_start = split["validation_start"]
    validation_end = split["validation_end"]
    test_start = split["test_start"]
    test_end = split["test_end"]
    days = (date.fromisoformat(test_end) - date.fromisoformat(train_start)).days
    if days <= 0:
        days = 1

    def span(start: str, end: str) -> float:
        return (date.fromisoformat(end) - date.fromisoformat(start)).days / days * 100.0

    segments = [
        ("#4c78a8", train_start, train_end, "训练 train"),
        ("#f58518", validation_start, validation_end, "验证 validation"),
        ("#54a24b", test_start, test_end, "测试 test (OOS)"),
    ]
    left = 0.0
    parts: list[str] = []
    for color, start, end, label in segments:
        width = span(start, end)
        style = f"left:{left:.1f}%;width:{width:.1f}%;background:{color};"
        parts.append(
            f'<div class="split-seg" style="{style}">{_esc(label)} {_esc(start)}→{_esc(end)}</div>'
        )
        left += width
    bar = f'<div class="split-bar">{"".join(parts)}</div>'
    rows = [
        ("训练区间 (train)", f"{_esc(train_start)} → {_esc(train_end)}"),
        ("验证区间 (validation)", f"{_esc(validation_start)} → {_esc(validation_end)}"),
        ("测试区间 (test, OOS)", f"{_esc(test_start)} → {_esc(test_end)}"),
    ]
    table = "".join(f"<tr><th>{_esc(key)}</th><td>{_esc(value)}</td></tr>" for key, value in rows)
    return f'<section id="split"><h2>切分 (Split)</h2>{bar}<table>{table}</table></section>'


def _svg_net_value_chart(series: Sequence[Mapping[str, Any]]) -> str:
    """Render the OOS net-value series as a simple inline SVG line chart."""
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


def _net_value_section(net_values: Sequence[Mapping[str, Any]]) -> str:
    """The OOS net-value section (OOS 净值)."""
    return (
        '<section id="net-values"><h2>OOS 净值 (OOS Net Values)</h2>'
        f"{_svg_net_value_chart(net_values)}"
        '<p class="note">图表数据以 JSON 嵌入本页 '
        "<code>window.REPORT_DATA</code>.</p>"
        "</section>"
    )


def _dispersion_section(dispersion: OosDispersionReport) -> str:
    """The fold-dispersion section (折叠离散度)."""
    worst = "—" if dispersion.worst_fold_index is None else str(dispersion.worst_fold_index)
    rows = [
        ("平均收益 (average return)", _pct(dispersion.average_return)),
        ("收益离散度 (return spread)", _pct(dispersion.return_spread)),
        ("最差折叠 (worst fold)", worst),
        ("折叠数 (folds)", str(len(dispersion.folds))),
    ]
    table = "".join(f"<tr><th>{_esc(key)}</th><td>{_esc(value)}</td></tr>" for key, value in rows)
    parts: list[str] = [
        f'<section id="dispersion"><h2>折叠离散度 (Fold Dispersion)</h2><table>{table}</table>'
    ]
    failures = dispersion.failure_distribution
    if failures:
        parts.append("<h3>失败分布 (Failure Distribution)</h3><ul>")
        for reason, count in failures:
            parts.append(f"<li>{_esc(reason)}: {count}</li>")
        parts.append("</ul>")
    else:
        parts.append('<p class="note">无失败折叠 (no failed folds).</p>')
    parts.append("</section>")
    return "\n".join(parts)


def _stress_section(stress_results: Mapping[str, Any]) -> str:
    """The stress-performance section (压力表现)."""
    registrations = stress_results.get("registrations", ())
    if not registrations:
        return (
            '<section id="stress"><h2>压力表现 (Stress Performance)</h2>'
            '<p class="note">无压力情景登记 (no registered stress scenarios).</p></section>'
        )
    rows: list[str] = []
    for registration in registrations:
        difference = registration.get("baseline_difference")
        summary = registration.get("difference_summary")
        difference_text = "" if difference is None else str(difference)
        summary_text = "" if summary is None else str(summary)
        rows.append(
            "<tr>"
            f"<td>{_esc(registration.get('category'))}</td>"
            f"<td>{_esc(registration.get('scenario_id'))}</td>"
            f"<td>{_esc(registration.get('market'))}</td>"
            f"<td>{_esc(difference_text)}</td>"
            f"<td>{_esc(summary_text)}</td>"
            "</tr>"
        )
    return (
        '<section id="stress"><h2>压力表现 (Stress Performance)</h2>'
        "<table><tr><th>类别</th><th>情景</th><th>市场</th><th>与基线差异</th>"
        "<th>说明</th></tr>" + "".join(rows) + "</table></section>"
    )


def _environment_section(segments: EnvironmentSegmentedPerformance) -> str:
    """The environment-performance section (环境表现)."""
    parts: list[str] = ['<section id="environment"><h2>环境表现 (Environment Performance)</h2>']
    if not segments.segments:
        parts.append('<p class="note">无环境分段 (no environment segments).</p>')
    else:
        parts.append(
            "<table><tr><th>维度</th><th>环境</th><th>天数</th><th>样本</th>"
            "<th>策略收益</th><th>基准收益</th><th>超额</th><th>覆盖</th></tr>"
        )
        for segment in segments.segments:
            sufficient = "足够" if segment.sufficient else f"不足 ({segment.insufficient_reason})"
            coverage = "—" if segment.coverage_pct is None else _num(segment.coverage_pct)
            parts.append(
                "<tr>"
                f"<td>{_esc(segment.dimension.value)}</td>"
                f"<td>{_esc(segment.regime_name)}</td>"
                f"<td>{segment.day_count}</td>"
                f"<td>{_esc(sufficient)}</td>"
                f"<td>{_pct(segment.strategy_return)}</td>"
                f"<td>{_pct(segment.benchmark_return)}</td>"
                f"<td>{_pct(segment.excess_return)}</td>"
                f"<td>{coverage}</td>"
                "</tr>"
            )
        parts.append("</table>")
    parts.append("</section>")
    return "\n".join(parts)


def _coverage_section(coverage: MarketCoverage) -> str:
    """The coverage-scores section (覆盖评分)."""
    rows: list[str] = []
    for score in coverage.scores:
        rows.append(
            "<tr>"
            f"<td>{_esc(score.market.value)}</td>"
            f"<td>{_esc(score.item.value)}</td>"
            f"<td>{_num(score.coverage_pct)}</td>"
            f"<td>{_esc(score.measurement.gap or '—')}</td>"
            "</tr>"
        )
    return (
        '<section id="coverage"><h2>覆盖评分 (Coverage Scores)</h2>'
        "<table><tr><th>市场</th><th>项目</th><th>覆盖</th><th>缺口</th></tr>"
        + "".join(rows)
        + "</table></section>"
    )


def _limitations_section(limitations: Sequence[str]) -> str:
    """The limitations section (限制)."""
    if not limitations:
        return (
            '<section id="limitations"><h2>限制 (Limitations)</h2>'
            '<p class="note">无未解决限制 (no unresolved limitations).</p></section>'
        )
    items = "".join(f"<li>{_esc(limitation)}</li>" for limitation in limitations)
    return f'<section id="limitations"><h2>限制 (Limitations)</h2><ul>{items}</ul></section>'


def _conclusion_section(data: dict[str, Any]) -> str:
    """The conclusion section (结论) with the no-return-promise note."""
    conclusion = data["conclusion"]
    rows = [
        ("结论 (conclusion)", _esc(conclusion.get("overall"))),
        ("测试集版本 (test set version)", _esc(conclusion.get("test_set_version"))),
        (
            "数据集指纹 (dataset fingerprint)",
            _esc(conclusion.get("dataset_fingerprint")),
        ),
        ("代码版本 (code version)", _esc(conclusion.get("code_version"))),
        (
            "结论指纹 (conclusion fingerprint)",
            _esc(conclusion.get("conclusion_fingerprint")),
        ),
    ]
    table = "".join(f"<tr><th>{_esc(key)}</th><td>{_esc(value)}</td></tr>" for key, value in rows)
    return (
        f'<section id="conclusion"><h2>结论 (Conclusion)</h2><table>{table}</table>'
        '<p class="note">本结论仅基于历史样本外表现，不包含任何收益承诺 '
        "(no promise of future returns).</p></section>"
    )


def render_oos_report(
    export_dict: Mapping[str, Any],
    *,
    title: str | None = None,
    net_values: Sequence[Mapping[str, Any]] | None = None,
    dispersion: OosDispersionReport | None = None,
    environment_segments: EnvironmentSegmentedPerformance | None = None,
    coverage: MarketCoverage | None = None,
    limitations: Sequence[str] = (),
) -> str:
    """Render an SP 3.66 OOS export as a self-contained HTML report (SP 3.68).

    Args:
        export_dict: The SP 3.66 OOS JSON export document.
        title: Optional document title.
        net_values: The OOS net-value series (rendered as the 净值 chart).
        dispersion: The SP 3.39 fold-dispersion report (折叠离散度).
        environment_segments: The SP 3.50 environment segments (环境表现).
        coverage: The SP 3.9 per-market coverage (覆盖评分).
        limitations: The unresolved limitations (限制).

    Returns:
        The full HTML document, with the chart data embedded as JSON in
        ``window.REPORT_DATA``.
    """
    data = build_report_data(export_dict)
    title_text = title or f"OOS validation report {data['run_id']}"
    sections = [
        _header_section(data),
        _split_section(data["split"]),
    ]
    if net_values is not None:
        sections.append(_net_value_section(net_values))
    if dispersion is not None:
        sections.append(_dispersion_section(dispersion))
    sections.append(_stress_section(data["stress_results"]))
    if environment_segments is not None:
        sections.append(_environment_section(environment_segments))
    if coverage is not None:
        sections.append(_coverage_section(coverage))
    sections.append(_limitations_section(limitations))
    sections.append(_conclusion_section(data))
    body = "\n".join(sections)
    chart_json = json.dumps(
        {
            "run_id": data["run_id"],
            "net_values": list(net_values) if net_values is not None else [],
        },
        sort_keys=True,
    )
    safe_chart = chart_json.replace("</", "<\\/")
    return (
        '<!doctype html>\n<html lang="zh">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{_esc(title_text)}</title>\n<style>{_CSS}</style>\n</head>\n<body>\n"
        f"{body}\n"
        f"<script>\nwindow.REPORT_DATA = {safe_chart};\n</script>\n"
        "</body>\n</html>\n"
    )


__all__: tuple[str, ...] = (
    "OosReportError",
    "build_report_data",
    "render_oos_report",
)
