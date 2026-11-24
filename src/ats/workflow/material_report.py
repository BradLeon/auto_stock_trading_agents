# -*- coding: utf-8 -*-
"""Readable report for one material-event review (event-impact-routing, 2026-11-24).

The review lives as a `fundamental_material_review` projection; this writes a human-readable copy next to the other
analyst reports (`重大事件评审-<代码>-<日期>.md`, `-v2`… on the same day). It reads only what the projection and the
briefs it cites already hold — no model call.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

STATUS_CN = {"intact": "完好", "stable": "稳定", "pressured": "承压", "falsified": "证伪", "strengthened": "增强"}
EFFECT_CN = {"quarterly_numbers": "季度数字", "risk_premium": "风险溢价", "none": "无"}
MATERIALITY_CN = {"minor": "轻微", "moderate": "中等", "major": "重大", "critical": "极重大"}


def _payload(row: dict) -> dict:
    p = row.get("payload") or {}
    return json.loads(p) if isinstance(p, str) else p


def _components(store, refs: list[str]) -> list[dict[str, Any]]:
    out = []
    for ref in refs:
        brief_id, _, cid = str(ref).partition("#")
        row = store.get_task_projection(brief_id) or {}
        payload = _payload(row)
        comp = next((c for c in payload.get("event_components") or [] if c.get("component_id") == cid), {})
        evidence = [e for e in payload.get("source_evidence") or [] if e.get("fact_index") in (comp.get("fact_indices") or [])]
        out.append({"ref": ref, "brief_as_of": row.get("as_of", ""), "comp": comp,
                    "sources": sorted({str(e.get("source_actor") or "") for e in evidence if e.get("source_actor")})})
    return out


def render(row: dict, components: list[dict[str, Any]]) -> str:
    p = _payload(row)
    sym = p.get("entity") or row.get("scope_id")
    lines = [f"# 🤖 重大事件评审 — {sym}（{str(row.get('as_of'))[:16]} UTC）", "",
             f"- 事件簇：`{p.get('event')}`",
             f"- 论点状态：**{STATUS_CN.get(p.get('thesis_status'), p.get('thesis_status'))}** · 影响落在："
             f"{EFFECT_CN.get(p.get('expectation_effect'), p.get('expectation_effect'))} · 方向 {p.get('direction'):+d} · "
             f"幅度 {p.get('magnitude')} · 信心 {p.get('confidence')}"
             + ("（无论点基线，信心上限 0.3）" if p.get("no_baseline") else ""),
             f"- 有效至：{str(p.get('valid_until'))[:16]} UTC（过期后不再进入主理人快照）",
             f"- 投影：`{row.get('projection_id')}`", "",
             "> 研究输入，不是交易指令。只评公司特有部分；整层共同的部分由层次分析评估，主理人按事件簇对齐、不重复计算。", "",
             "## 触发的事件成分"]
    for c in components:
        comp = c["comp"]
        lines.append(f"- **{MATERIALITY_CN.get(comp.get('materiality'), comp.get('materiality'))}/"
                     f"{comp.get('direction')}** · {comp.get('novelty')} · {comp.get('content') or '（成分已不可读）'}")
        lines.append(f"  - 来源：{'、'.join(c['sources']) or '—'} · 简报 {str(c['brief_as_of'])[:10]} · `{c['ref']}`")
    lines += ["", "## 评审结论", str(p.get("narrative") or ""), "", "## 命中的论点、风险项与证伪条件"]
    lines += [f"- {h}" for h in p.get("hit_items") or []] or ["- （无）"]
    lines += ["", "## 对重要性评级的复核", str(p.get("materiality_review") or "—"), "", "## 待核实"]
    lines += [f"- {u}" for u in p.get("unverified") or []] or ["- （无）"]
    return "\n".join(lines) + "\n"


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    n = 2
    while (candidate := path.with_name(f"{path.stem}-v{n}{path.suffix}")).exists():
        n += 1
    return candidate


def write(store, projection_ids: list[str], *, output_dir: str | Path) -> list[str]:
    folder = Path(output_dir)
    if not folder.is_dir():
        return []
    paths = []
    for pid in projection_ids:
        row = store.get_task_projection(pid)
        if not row or row.get("agent_role") != "fundamental_material_review":
            continue
        p = _payload(row)
        day = datetime.fromisoformat(str(row.get("as_of"))).date().isoformat()
        path = _unique(folder / f"重大事件评审-{p.get('entity') or row.get('scope_id')}-{day}.md")
        path.write_text(render(row, _components(store, list(p.get("component_refs") or []))), encoding="utf-8")
        paths.append(str(path))
    return paths
