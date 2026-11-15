"""Weekly calibration of event-component ratings (event-impact-routing 3.3). Pure code: no model call.

What it answers, for the week before `now`:
  * how the information analyst rated (materiality x direction x scope);
  * where downstream disagreed — the Fundamental material review and the Layer analysis each record a
    `materiality_review`; anything that does not start with agreement counts as a disagreement;
  * how often the Layer analyst claimed a layer-wide transmission for a component tagged company-only
    (`cross_scope_claims`: the information analyst's scope misses);
  * whether materiality orders the market's reaction: abnormal return of each named company over 1–3 sessions,
    against the equal-weight basket of its layer's members. Used to calibrate, never to rewrite a rating;
  * a short list of the week's most material components to read by hand.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Callable

log = logging.getLogger("ats.agents.information.calibration")

ORDER = {"minor": 0, "moderate": 1, "major": 2, "critical": 3}
LABEL = {"minor": "轻微", "moderate": "中等", "major": "重大", "critical": "极重大"}
WINDOWS = (1, 2, 3)
AGREE_PREFIXES = ("agree", "一致", "同意", "认同")


def _rows(store, role: str, start: datetime, end: datetime) -> list[dict]:
    out = []
    for row in store.task_projection_envelopes(agent_role=role, limit=5000):
        try:
            as_of = datetime.fromisoformat(str(row.get("as_of")))
        except ValueError:
            continue
        if as_of.tzinfo is not None and start <= as_of <= end:
            out.append({**row, "_as_of": as_of})
    return out


def components_in(store, *, start: datetime, end: datetime) -> list[dict]:
    out = []
    for row in _rows(store, "information_brief", start, end):
        for comp in (row.get("payload") or {}).get("event_components") or []:
            out.append({**comp, "brief": row["projection_id"], "entity": row.get("scope_id"),
                        "as_of": row["_as_of"]})
    return out


def _disagrees(review: str) -> bool:
    text = (review or "").strip().lower()
    return bool(text) and not text.startswith(AGREE_PREFIXES)


def downstream_reviews(store, *, start: datetime, end: datetime) -> dict[str, list[dict]]:
    reviews, cross_scope = [], []
    for row in _rows(store, "fundamental_material_review", start, end):
        p = row.get("payload") or {}
        reviews.append({"by": "fundamental", "scope": row.get("scope_id"), "refs": p.get("component_refs") or [],
                        "review": p.get("materiality_review", "")})
    for row in _rows(store, "layer_analysis", start, end):
        p = row.get("payload") or {}
        for shock in p.get("off_thesis_shocks") or []:
            reviews.append({"by": "layer", "scope": row.get("scope_id"), "refs": shock.get("component_refs") or [],
                            "review": shock.get("materiality_review", "")})
        for claim in p.get("cross_scope_claims") or []:
            cross_scope.append({"layer": row.get("scope_id"), **claim})
            reviews.append({"by": "layer", "scope": row.get("scope_id"), "refs": [claim.get("component_ref", "")],
                            "review": claim.get("materiality_review", "")})
    return {"reviews": reviews, "disagreements": [r for r in reviews if _disagrees(r["review"])],
            "cross_scope": cross_scope}


def _returns(closes: list[tuple[date, float]], event_day: date, k: int) -> float | None:
    """Close-to-close return from the last session at or before the event day to k sessions later."""
    base = [i for i, (d, _) in enumerate(closes) if d <= event_day]
    if not base:
        return None
    i = base[-1]
    if i + k >= len(closes) or not closes[i][1]:
        return None
    return closes[i + k][1] / closes[i][1] - 1


def abnormal_returns(components: list[dict], *, layers: dict[str, set[str]],
                     history: Callable[[list[str]], dict[str, list[tuple[date, float]]]]) -> list[dict]:
    """Per (component, named company): return over 1–3 sessions minus its layer's equal-weight basket."""
    member_of = {sym: key for key, members in layers.items() for sym in members}
    wanted = {e for c in components for e in (c.get("entities") or [c.get("entity")]) if e}
    baskets = {key: members for key, members in layers.items()}
    symbols = sorted(wanted | {s for key in {member_of.get(e) for e in wanted} if key for s in baskets[key]})
    series = history(symbols) if symbols else {}
    out = []
    for comp in components:
        day = comp["as_of"].date()
        for entity in comp.get("entities") or [comp.get("entity")]:
            layer = member_of.get(entity)
            row = {"component_id": comp.get("component_id"), "entity": entity, "layer": layer,
                   "materiality": comp.get("materiality"), "direction": comp.get("direction", "unclear")}
            for k in WINDOWS:
                own = _returns(series.get(entity, []), day, k)
                peers = [r for s in (baskets.get(layer) or set()) if s != entity
                         for r in [_returns(series.get(s, []), day, k)] if r is not None]
                row[f"ar{k}"] = (own - mean(peers)) if own is not None and peers else None
            out.append(row)
    return out


def summarize(ar_rows: list[dict]) -> list[dict]:
    """Mean |abnormal return| per materiality: should rise with materiality if the ratings carry information."""
    out = []
    for level in ORDER:
        rows = [r for r in ar_rows if r["materiality"] == level]
        line = {"materiality": level, "n": len(rows)}
        for k in WINDOWS:
            vals = [abs(r[f"ar{k}"]) for r in rows if r.get(f"ar{k}") is not None]
            line[f"mean_abs_ar{k}"] = mean(vals) if vals else None
        out.append(line)
    return out


def _market_history(symbols: list[str]) -> dict[str, list[tuple[date, float]]]:
    """Dated closes through the runtime market-data downloader (its session carries the Yahoo TLS fix, 3896f69)."""
    from ...data.runtime.market_data import _download_close_frame, _frame_columns

    frame, _normalized, _failed = _download_close_frame(symbols, "3mo")
    return {sym: [(idx.date(), float(v)) for idx, v in series.dropna().items()]
            for sym, series in _frame_columns(frame, symbols).items()}


def build(store, *, now: datetime | None = None, days: int = 7, history=None) -> dict[str, Any]:
    from .event_components import chain_scope

    now = now or datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    comps = components_in(store, start=start, end=now)
    downstream = downstream_reviews(store, start=start, end=now)
    report: dict[str, Any] = {"start": start, "end": now, "components": comps, **downstream,
                              "abnormal_returns": [], "by_materiality": [], "market_note": ""}
    if comps:
        layers = {key: v["members"] for key, v in chain_scope().items()}
        try:
            report["abnormal_returns"] = abnormal_returns(comps, layers=layers, history=history or _market_history)
            report["by_materiality"] = summarize(report["abnormal_returns"])
        except Exception as exc:  # noqa: BLE001 - market data is a calibration aid; its absence is stated
            report["market_note"] = f"行情未取到（{type(exc).__name__}），本周不含超额收益对照"
    major = sorted(comps, key=lambda c: (-ORDER.get(c.get("materiality"), 0), str(c.get("as_of"))))
    report["sample"] = major[:8]
    return report


def render(report: dict[str, Any]) -> str:
    comps = report["components"]
    lines = [f"# 事件评级校准 {report['start']:%Y-%m-%d} – {report['end']:%Y-%m-%d}", ""]
    if not comps:
        lines += ["本周没有事件成分（开关未打开或无材料）。", ""]
        return "\n".join(lines)
    count = {lvl: sum(1 for c in comps if c.get("materiality") == lvl) for lvl in ORDER}
    lines += ["## 评级分布", "", "| 重要性 | 成分数 |", "|---|---|"]
    lines += [f"| {LABEL[lvl]} | {count[lvl]} |" for lvl in ORDER] + [""]
    lines += ["## 下游复核分歧", "",
              f"共 {len(report['reviews'])} 条复核，其中与简报评级不一致 {len(report['disagreements'])} 条。", ""]
    for r in report["disagreements"][:20]:
        lines.append(f"- {r['by']}（{r['scope']}）：{r['review']}　← {', '.join(r['refs'])}")
    lines += ["", "## 层面补认领（影响面漏判）", "", f"共 {len(report['cross_scope'])} 条。", ""]
    for c in report["cross_scope"][:20]:
        lines.append(f"- {c.get('layer')}：{c.get('component_ref')}（{c.get('from_entity', '')}）— {c.get('transmission', '')}")
    lines += ["", "## 事后对照：重要性 × 1–3 个交易日的超额收益（相对层篮子等权）", ""]
    if report.get("market_note"):
        lines.append(report["market_note"])
    else:
        lines += ["| 重要性 | 样本 | 平均|超额|·1日 | ·2日 | ·3日 |", "|---|---|---|---|---|"]
        for s in report["by_materiality"]:
            cells = [f"{s[f'mean_abs_ar{k}']:.2%}" if s[f"mean_abs_ar{k}"] is not None else "—" for k in WINDOWS]
            lines.append(f"| {LABEL[s['materiality']]} | {s['n']} | " + " | ".join(cells) + " |")
        lines += ["", "只用来校准评级，不改写当时的评级：事前评级不该用事后价格来定。"]
    lines += ["", "## 本周抽检清单（最高重要性）", ""]
    for c in report["sample"]:
        lines.append(f"- {LABEL.get(c.get('materiality'), c.get('materiality'))}·{c.get('direction', '')}·"
                     f"{c.get('scope')}｜{c.get('entity')}｜{c.get('content')}（{c.get('component_id')}）")
    return "\n".join(lines) + "\n"


def write(store, *, output_dir: str | Path, now: datetime | None = None, history=None) -> Path | None:
    from ..report_files import unique_path

    report = build(store, now=now, history=history)
    if not report["components"]:
        return None
    folder = Path(output_dir)
    if not folder.is_dir():
        log.warning("event calibration report: output_dir missing — skipped: %s", folder)
        return None
    path = unique_path(folder / f"事件评级校准-{report['end']:%Y-%m-%d}.md")
    path.write_text(render(report), encoding="utf-8")
    return path
