"""What a task produced, rendered for the run page: a summary line first, the content on click.

One renderer per analyst role shows the fields a reader acts on; any other role falls back to a field list,
never to nothing. Every projection also carries its as_of, its data vintages (the same wording as the Chief
report's 依据清单) and the raw payload behind a further click. Large payloads are cut, and say so.
"""

from __future__ import annotations

import json
from html import escape
from typing import Any

ROLE_CN = {
    "information_brief": "信息简报", "fundamental_expectation_update": "预期更新",
    "fundamental_risk_register": "风险登记", "fundamental_event_review": "财报事件评审",
    "fundamental_material_review": "重大事项评审", "technical_review": "技术面", "layer_analysis": "层分析",
    "macro_review": "宏观", "sector_allocation": "行业配置",
}
SIGNAL_CN = {"bullish": "看多", "bearish": "看空", "neutral": "中性", "unavailable": "无读数"}
VERDICT_CN = {"supportive": "支持", "refuting": "反驳", "contested": "有争议", "unresolved": "未定论",
              "insufficient": "证据不足"}
STATUS_CN = {"expanding": "扩张", "steady": "平稳", "contracting": "收缩", "peaking": "见顶", "troughing": "触底"}
STANCE_CN = {"overweight": "超配", "underweight": "低配", "neutral": "标配"}
RAW_LIMIT = 20_000
LIST_LIMIT = 8


def _e(value: Any, limit: int = 0) -> str:
    text = "" if value is None else str(value)
    if limit and len(text) > limit:
        text = text[:limit] + "…"
    return escape(text)


def _pct(value) -> str:
    try:
        return f"{float(value):.0%}"
    except (TypeError, ValueError):
        return "—"


def _num(value, digits: int = 2) -> str:
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return "—"


def _short(at) -> str:
    return f"{at:%m-%d %H:%M}" if at else "—"


def _when(value) -> str:
    """A stored ISO time as 11-16 (date-only) or 11-16 13:30 UTC; blank stays a dash."""
    from ...agents.provenance import parse_time

    at = parse_time(value)
    if at is None:
        return "—"
    return f"{at:%Y-%m-%d}" if (at.hour, at.minute, at.second) == (0, 0, 0) else f"{at:%m-%d %H:%M} UTC"


def _bullets(items, limit: int = LIST_LIMIT, chars: int = 240) -> str:
    items = [i for i in items or [] if i not in (None, "")]
    if not items:
        return ""
    shown = "".join(f"<li>{_e(i if isinstance(i, str) else json.dumps(i, ensure_ascii=False), chars)}</li>"
                    for i in items[:limit])
    more = f'<li class="muted">…另 {len(items) - limit} 条</li>' if len(items) > limit else ""
    return f'<ul style="margin:4px 0;padding-left:18px">{shown}{more}</ul>'


def _kv(pairs) -> str:
    rows = "".join(f'<tr><td class="muted" style="white-space:nowrap">{_e(k)}</td><td class="wrap">{v}</td></tr>'
                   for k, v in pairs if v not in (None, "", "—"))
    return f'<table class="inner">{rows}</table>' if rows else ""


# --------------------------------------------------------------------------- #
# one renderer per role: (summary line, body html)
# --------------------------------------------------------------------------- #
def _expectation_updates(outputs) -> tuple[str, str]:
    counts: dict[str, int] = {}
    rows = []
    with_values = any(o["payload"].get("new_value") not in (None, "") for o in outputs)
    for o in outputs:
        p = o["payload"]
        cls = str(p.get("change_class") or "—")
        counts[cls] = counts.get(cls, 0) + 1
        values = ""
        if p.get("new_value") not in (None, ""):
            values = f'{_e(p.get("previous_value") if p.get("previous_value") is not None else "—")} → {_e(p.get("new_value"))}'
        rows.append(f'<tr><td><span class="pill {"green" if cls == "确认" else "grey"}">{_e(cls)}</span></td>'
                    f'<td class="wrap">{_e(p.get("explanation") or p.get("metric"), 120)}</td>'
                    f'<td class="wrap">{_e(p.get("driver"), 260)}</td>'
                    + (f'<td>{values}</td>' if with_values else "") + "</tr>")
    summary = f"{len(outputs)} 条预期更新 · " + " / ".join(f"{k} {v}" for k, v in sorted(counts.items()))
    period = next((o["payload"].get("period") for o in outputs if o["payload"].get("period")), "")
    head = f'<div class="muted">期间 {_e(period)}</div>' if period else ""
    return summary, (head + '<table class="inner"><tr><th>归类</th><th>要点</th><th>驱动（来源）</th>'
                     + ("<th>新旧值</th>" if with_values else "") + "</tr>" + "".join(rows) + "</table>")


def _risk_register(o) -> tuple[str, str]:
    items = o["payload"].get("items") or []
    rows = "".join(f'<tr><td>{_e(i.get("kind"))}</td><td class="mono">{_e(i.get("key"))}</td>'
                   f'<td class="wrap">{_e(i.get("statement") or i.get("risk") or i.get("description"), 200)}</td>'
                   f'<td class="wrap">{_e(i.get("falsifier"), 200)}</td></tr>'
                   for i in items[:LIST_LIMIT] if isinstance(i, dict))
    more = f'<div class="muted">…另 {len(items) - LIST_LIMIT} 条</div>' if len(items) > LIST_LIMIT else ""
    return (f"{len(items)} 条风险",
            f'<table class="inner"><tr><th>类别</th><th>键</th><th>风险</th><th>证伪条件</th></tr>{rows}</table>{more}')


def _event_review(o) -> tuple[str, str]:
    p = o["payload"]
    direction = {1: "正向", -1: "负向", 0: "中性"}.get(p.get("direction"), str(p.get("direction")))
    summary = f'{p.get("period", "")} 财报 · 预期差{direction} · 幅度 {_num(p.get("magnitude"))}'
    metrics = (p.get("actuals") or {}).get("metrics") or []
    mrows = "".join(f'<tr><td>{_e(m.get("metric"))}</td><td class="wrap">{_e(m.get("actual"), 160)}</td>'
                    f'<td class="wrap">{_e(m.get("vs_expected"), 120)}</td></tr>' for m in metrics[:LIST_LIMIT])
    body = _kv([("结论", _e(p.get("notes"), 400)), ("指引", _e(p.get("guidance"), 500)),
                ("叙事", _e(p.get("narrative"), 600)), ("基线截至", _when(p.get("baseline_as_of")))])
    if mrows:
        body += f'<table class="inner"><tr><th>指标</th><th>实际</th><th>对比预期</th></tr>{mrows}</table>'
    return summary, body


def _information_brief(o) -> tuple[str, str]:
    p = o["payload"]
    summary = f'{p.get("headline") or "信息简报"} · 相关度 {p.get("relevance", "—")} · 来源 {p.get("source_count", 0)} 个'
    body = _kv([("事件时间", _when(p.get("event_time"))), ("发布时间", _when(p.get("published_at"))),
                ("新鲜度", _e(p.get("freshness"))), ("置信", _e(p.get("confidence"))),
                ("来源", _e("、".join(map(str, p.get("sources") or []))))])
    body += _bullets(p.get("fact_changes"))
    if p.get("unverified"):
        body += '<div class="muted">未核实：</div>' + _bullets(p.get("unverified"), limit=3)
    return summary, body


def _technical(o) -> tuple[str, str]:
    p = o["payload"]
    levels = p.get("levels") or {}
    summary = (f'{SIGNAL_CN.get(p.get("signal"), p.get("signal") or "—")} · 评分 {p.get("score", "—")}/7 · '
               f'建议敞口上限 {_pct(p.get("suggested_exposure"))}（前 {_pct(p.get("previous_exposure"))}）')
    body = _kv([("趋势", _e(p.get("trend_state"))),
                ("价位", " · ".join(f"{k} {_num(levels.get(k))}" for k in ("close", "sma20", "sma50", "sma200")
                                    if levels.get(k) is not None)),
                ("VIX", f'{_num(p.get("vix"))} / VIX3M {_num(p.get("vix3m"))}' if p.get("vix") is not None else ""),
                ("数据状态", _e(p.get("data_status")))])
    if p.get("invalidation"):
        body += '<div class="muted">失效条件：</div>' + _bullets(p.get("invalidation"), limit=5)
    return summary, body


def _layer(o) -> tuple[str, str]:
    p = o["payload"]
    summary = (f'{p.get("layer", "")} · {STATUS_CN.get(p.get("status"), p.get("status") or "—")} · '
               f'置信 {_num(p.get("confidence"))}')
    claims = "".join(f'<tr><td class="wrap">{_e(c.get("statement") or c.get("claim_id"), 160)}</td>'
                     f'<td>{_e(VERDICT_CN.get(c.get("verdict"), c.get("verdict")))}</td><td>{_e(c.get("coverage"))}</td>'
                     f'<td>{_e(c.get("evidence_clusters"))}</td></tr>'
                     for c in p.get("common_evidence") or [] if isinstance(c, dict))
    body = _kv([("结论", _e(p.get("summary"), 800)), ("周期位置", _e(p.get("cycle_position"), 500))])
    if claims:
        body += ('<table class="inner"><tr><th>命题</th><th>裁决</th><th>证人覆盖</th><th>证据簇</th></tr>'
                 f'{claims}</table>')
    if p.get("gaps"):
        body += '<div class="muted">缺口：</div>' + _bullets(p.get("gaps"), limit=5)
    if p.get("reversal_triggers"):
        body += '<div class="muted">反转条件：</div>' + _bullets(p.get("reversal_triggers"), limit=5)
    return summary, body


def _macro(o) -> tuple[str, str]:
    p = o["payload"]
    summary = f'{p.get("regime", "—")} · 象限 {p.get("quadrant", "—")}（{p.get("quadrant_state", "")}）'
    tilts = "".join(f'<tr><td class="mono">{_e(t.get("etf"))}</td><td class="wrap">{_e(t.get("rationale"), 240)}</td></tr>'
                    for t in p.get("sector_tilts") or [] if isinstance(t, dict))
    body = _kv([("结论", _e(p.get("summary"), 800)), ("叙事 regime", _e(p.get("narrative_regime"), 300)),
                ("利率路径", _e(p.get("rate_path"), 400)), ("资产含义", _e(p.get("asset_implications"), 400))])
    if tilts:
        body += f'<table class="inner"><tr><th>行业倾向</th><th>理由</th></tr>{tilts}</table>'
    return summary, body


def _sector(o) -> tuple[str, str]:
    p = o["payload"]
    summary = f'{p.get("sector", "")} · {STANCE_CN.get(p.get("stance"), p.get("stance") or "—")} · 目标权重 {_pct(p.get("target_weight"))}'
    layers = "".join(f'<tr><td class="mono">{_e(x.get("layer"))}</td><td>{_e(x.get("allocation"))}</td>'
                     f'<td>{_pct(x.get("budget"))}</td></tr>' for x in p.get("layers") or [] if isinstance(x, dict))
    body = _kv([("理由", _e(p.get("rationale"), 800)), ("轮动建议", _e(p.get("rotation_advice"), 500))])
    if layers:
        body += f'<table class="inner"><tr><th>层</th><th>配置</th><th>预算</th></tr>{layers}</table>'
    return summary, body


def _generic(o) -> tuple[str, str]:
    p = o["payload"]
    pairs = []
    for k, v in p.items():
        if k in ("schema_name", "schema_version"):
            continue
        if isinstance(v, (list, dict)):
            pairs.append((k, f'<span class="muted">{len(v)} 项</span>'))
        else:
            pairs.append((k, _e(v, 300)))
    return str(p.get("summary") or p.get("headline") or f"{len(pairs)} 个字段")[:120], _kv(pairs)


_SINGLE = {"fundamental_risk_register": _risk_register, "fundamental_event_review": _event_review,
           "fundamental_material_review": _event_review, "information_brief": _information_brief,
           "technical_review": _technical, "layer_analysis": _layer, "macro_review": _macro,
           "sector_allocation": _sector}


def _raw(o) -> str:
    payload = o["payload"] if isinstance(o, dict) and "payload" in o else [x["payload"] for x in o]
    text = json.dumps(payload, ensure_ascii=False, indent=1)
    cut = f'<div class="muted">（原始内容 {len(text):,} 字，只显示前 {RAW_LIMIT:,} 字）</div>' if len(text) > RAW_LIMIT else ""
    return (f'<details class="raw"><summary>查看完整内容（原始字段）</summary>{cut}'
            f'<pre class="raw">{escape(text[:RAW_LIMIT])}</pre></details>')


def _meta(outputs) -> str:
    o = outputs[0]
    reused = [x for x in outputs if x.get("reused")]
    tag = (f' <span class="pill grey">复用</span> 沿用 {_short(reused[0]["created_at"])} UTC 生成的产出，本次未重新生成'
           if reused and len(reused) == len(outputs) else
           f' <span class="pill grey">部分复用 {len(reused)}/{len(outputs)}</span>' if reused else "")
    vintages = sorted({v for x in outputs for v in x.get("vintages") or []})
    dep = f'<div class="muted">依据：{_e("；".join(vintages[:6]))}{"…" if len(vintages) > 6 else ""}</div>' if vintages else ""
    return f'<div class="muted">截至 {_short(o.get("as_of"))} UTC{tag}</div>{dep}'


def task_outputs(task: dict) -> str:
    """The 「产出」 cell of one task row."""
    outputs = [o for o in task.get("outputs") or [] if not o.get("missing")]
    missing = len(task.get("outputs") or []) - len(outputs)
    if not outputs:
        return '<span class="muted">—</span>' if not missing else f'<span class="muted">{missing} 份产出已不在库中</span>'
    blocks = []
    by_role: dict[str, list[dict]] = {}
    for o in outputs:
        by_role.setdefault(o["role"], []).append(o)
    for role, group in by_role.items():
        if role == "fundamental_expectation_update":
            summary, body = _expectation_updates(group)
            blocks.append((role, summary, _meta(group) + body + _raw(group)))
            continue
        render = _SINGLE.get(role, _generic)
        for o in group:
            summary, body = render(o)
            blocks.append((role, summary, _meta([o]) + body + _raw(o)))
    out = []
    for role, summary, body in blocks:
        label = ROLE_CN.get(role, role)
        out.append(f'<details class="out">'
                   f'<summary><span class="pill grey">{_e(label)}</span> {_e(summary, 160)}</summary>'
                   f'<div class="outbox">{body}</div></details>')
    if missing:
        out.append(f'<div class="muted">另有 {missing} 份产出已不在库中</div>')
    return "".join(out)
