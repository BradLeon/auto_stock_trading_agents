"""Event components and risk claims for one layer analysis (event-impact-routing group 5, 2026-11-16).

What the layer analyst gets on top of its claims, and what code checks in its answer:

  * INPUT — event components from information briefs of the last `validity_days`:
      - every component whose scope is this layer, or a cross-layer component that names it (these are also
        the ones that TRIGGER a layer review once routing is on);
      - the safety net (D6): every component of a MEMBER company rated at or above `major`, including ones
        tagged company-only, so a mis-scoped event is not lost at the layer.
    The `kind: risk` claims of the layer with the evidence engine's ledger half, and last round's shocks.
  * OUTPUT CHECKS — the model judges, code keeps the arithmetic and the direction:
      - a risk claim may only be "pressured"/"realized" with >= 2 independent sources, >= 1 authoritative,
        counted over the components it cites plus the ledger's supporting clusters; otherwise "emerging";
      - an off-thesis shock needs a readable component and materiality >= moderate; it expires 14 days after
        its newest component, and an expired one is background only;
      - utilization advice is down-only and must cite a pressured/realized risk claim or a live major-and-
        negative shock, or it is cleared.

Nothing here decides what an event means for the layer. The layer analysis judges only the layer-wide part
and the relative position inside the layer; the company-specific part stays with Fundamental.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ...agent.task_projection import MATERIALITY_ORDER

log = logging.getLogger("ats.agents.layer.event_inputs")

SETTINGS_PATH = Path(__file__).resolve().parents[4] / "config" / "workflow" / "event_impact.yaml"
RISK_STATUSES = ("dormant", "emerging", "pressured", "realized", "resolved")
STATUS_CN = {"dormant": "休眠", "emerging": "出现", "pressured": "承压", "realized": "兑现", "resolved": "解除"}
MATERIALITY_CN = {"minor": "轻微", "moderate": "中等", "major": "重大", "critical": "极重大"}
_GATED = {"pressured", "realized"}


def settings(path: Path | None = None) -> dict[str, Any]:
    import yaml

    try:
        doc = yaml.safe_load((path or SETTINGS_PATH).read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        doc = {}
    layer = doc.get("layer") or {}
    routing = doc.get("routing") or {}
    return {"enabled": bool(layer.get("enabled", False)),
            "max_components": int(layer.get("max_components", 30)),
            "safety_min": str(layer.get("safety_net_min_materiality", "major")),
            "validity_days": int(routing.get("validity_days", 14))}


def _when(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


# --------------------------------------------------------------------------- inputs


def read_components(store, layer, *, now: datetime, conf: dict[str, Any] | None = None) -> list[dict]:
    """This layer's event components, newest first, one row per (entity, cluster, content)."""
    from ...chain import risk_sources

    conf = conf or settings()
    cutoff = now - timedelta(days=conf["validity_days"])
    members = {t.symbol.upper() for t in layer.tickers}
    safety_min = MATERIALITY_ORDER.get(conf["safety_min"], MATERIALITY_ORDER["major"])
    rows = store.task_projection_envelopes(agent_role="information_brief", limit=3000)
    seen: set[tuple] = set()
    out: list[dict] = []
    for row in sorted(rows, key=lambda r: str(r.get("as_of") or ""), reverse=True):
        when = _when(row.get("as_of") or "")
        if when is None or when < cutoff or when > now:
            continue
        payload = row.get("payload") or {}
        entity = str(row.get("scope_id") or payload.get("entity") or "").upper()
        for comp in payload.get("event_components") or []:
            scope = comp.get("scope")
            on_layer = scope in ("layer", "cross_layer") and layer.key in (comp.get("layers") or [])
            materiality = comp.get("materiality") or "minor"
            safety = (not on_layer and entity in members
                      and MATERIALITY_ORDER.get(materiality, 0) >= safety_min)
            if not (on_layer or safety):
                continue
            key = (entity, comp.get("cluster_key"), comp.get("content"))
            if key in seen:
                continue                 # an older republication of the same component
            seen.add(key)
            sources = risk_sources.component_sources(payload, comp)
            out.append({"ref": f"{row['projection_id']}#{comp.get('component_id')}", "as_of": when.isoformat(),
                        "entity": entity, "route": "layer" if on_layer else "safety_net",
                        "scope": scope, "layers": list(comp.get("layers") or []),
                        "materiality": materiality, "direction": comp.get("direction") or "unclear",
                        "status": comp.get("status") or "ongoing", "novelty": comp.get("novelty") or "first",
                        "cluster_key": comp.get("cluster_key") or "", "content": comp.get("content") or "",
                        "sources": [{"key": s.key, "authoritative": s.authoritative} for s in sources]})
    return out[: conf["max_components"]]


def prior_shocks(store, layer) -> list[dict]:
    rows = store.task_projection_envelopes(agent_role="layer_analysis", scope_id=layer.key, limit=1)
    return list(((rows[0].get("payload") or {}).get("off_thesis_shocks") or [])) if rows else []


def risk_claim_defs(layer) -> list:
    return [c for c in layer.claims if c.kind == "risk"]


def render_block(layer, *, assessments, components: list[dict], prior: list[dict], now: datetime) -> str:
    """The context section the layer analyst reads; the output rules live here, next to the inputs."""
    risks = risk_claim_defs(layer)
    engine = {a.claim_id: a for a in assessments or []}
    lines = ["## 事件成分与风险命题（event-impact-routing）",
             "> 你只评估**整层共同**的影响与**层内相对位置**；公司特有的部分（治理、人事、单家诉讼）留给基本面，"
             "不要在这里定价。",
             "> 规则：",
             "> 1. 每个「本层」或「跨层」成分必须有归宿：归入一条经营命题（在 claim_attributions 里写明成分 ref），"
             "或归入一条风险命题（risk_claims 的 component_refs），或——都归不进且重要性不低于中等——列为"
             "论点外冲击（off_thesis_shocks）。",
             "> 2. 标为「安全网」的成分是成员公司的本公司事件。只有存在**整层传导**时才写 cross_scope_claims"
             "并写明传导路径；没有传导就不列，也**不得**据此建议下调。",
             "> 3. 每个被你认领的成分都要附重要性复核（agree / higher / lower + 理由）。",
             "> 4. risk_claims 必填：风险命题逐条给状态（含休眠），引用的成分写进 component_refs。承压/兑现须有至少 2 个独立来源、其中至少 1 个一手或权威——"
             "同一出版方或同一原发的转载只算一个。代码会核对，不达标会降为「出现」。",
             "> 5. utilization_advice 只能向下：理由必须引用承压/兑现的风险命题 id，或重大及以上、负面、"
             "持续的冲击编号。没有这些就给 none。"]
    if risks:
        lines.append("\n### 风险命题")
        for claim in risks:
            a = engine.get(claim.id)
            ledger = (f"台账：{a.verdict}（{a.note}）" if a is not None else "台账：本轮无评估")
            lines.append(f"- `{claim.id}` {claim.statement}\n    {ledger}")
            if claim.falsifiers:
                lines.append("    解除条件：" + "；".join(claim.falsifiers))
    else:
        lines.append("\n### 风险命题\n本层没有声明风险命题；risk_claims 留空。")
    lines.append("\n### 事件成分（近 14 天，新到旧）")
    if not components:
        lines.append("本期没有涉及本层的事件成分。")
    for c in components:
        route = "本层/跨层" if c["route"] == "layer" else f"安全网·{c['entity']} 本公司"
        srcs = "、".join(s["key"] + ("★" if s["authoritative"] else "") for s in c["sources"]) or "无可追溯来源"
        lines.append(f"- ref `{c['ref']}` · {c['as_of'][:10]} · {route} · "
                     f"{MATERIALITY_CN.get(c['materiality'], c['materiality'])}/{c['direction']} · "
                     f"{c['status']}/{c['novelty']}\n    {c['content']}\n    来源（★=一手或权威）：{srcs}")
    live, expired = [], []
    for s in prior or []:
        (expired if (_when(s.get("expires_at") or "") or now) < now else live).append(s)
    if live:
        lines.append("\n### 上一轮的论点外冲击（仍在有效期；延续时沿用原编号）")
        lines += [f"- `{s.get('shock_id')}` {s.get('summary')}（至 {str(s.get('expires_at'))[:10]}）" for s in live]
    if expired:
        lines.append("\n### 已过期的冲击（仅作背景，**不得**作为下调依据）")
        lines += [f"- `{s.get('shock_id')}` {s.get('summary')}" for s in expired]
    return "\n".join(lines)


# --------------------------------------------------------------------------- output checks


def _norm(value: str, allowed: tuple[str, ...] | set[str], default: str) -> str:
    v = str(value or "").strip().lower()
    return v if v in allowed else default


def apply(layer, view, *, assessments, components: list[dict], now: datetime,
          conf: dict[str, Any] | None = None) -> tuple[dict[str, Any], list[str]]:
    """Check the model's event fields; returns (projection fields, gap notes)."""
    from ...chain import risk_sources

    conf = conf or settings()
    gaps: list[str] = []
    by_ref = {c["ref"]: c for c in components}
    engine = {a.claim_id: a for a in assessments or []}
    validity = timedelta(days=conf["validity_days"])

    # --- risk claims: every declared one gets a state; the gate is code's ---
    given = {str(r.claim_id).strip(): r for r in getattr(view, "risk_claims", []) or []}
    risk_rows = []
    for claim in risk_claim_defs(layer):
        row = given.pop(claim.id, None)
        a = engine.get(claim.id)
        status = _norm(row.status if row else "", RISK_STATUSES,
                       "emerging" if a is not None and a.verdict == "supportive" else "dormant")
        refs = [r for r in (row.component_refs if row else []) if r in by_ref]
        sources = [risk_sources.Source(key=k, authoritative=k in (a.authoritative_sources if a else []))
                   for k in (a.independent_sources if a else [])]
        sources += [risk_sources.Source(key=s["key"], authoritative=s["authoritative"])
                    for r in refs for s in by_ref[r]["sources"]]
        counted = risk_sources.gate(sources)
        rationale = (row.rationale if row else "") or ("本轮模型未给出该命题的状态" if row is None else "")
        if status in _GATED and not counted["ok"]:
            rationale = (f"【代码核对：独立来源 {len(counted['independent'])} 个、一手或权威 "
                         f"{len(counted['authoritative'])} 个，未达门槛，{STATUS_CN[status]}→出现】{rationale}")
            status = "emerging"
        risk_rows.append({"claim_id": claim.id, "status": status,
                          "independent_sources": len(counted["independent"]),
                          "authoritative_sources": len(counted["authoritative"]),
                          "sources": counted["independent"][:12], "component_refs": refs,
                          "materiality_review": (row.materiality_review if row else ""), "rationale": rationale})
    for stray in given:
        gaps.append(f"模型给出了本层未声明的风险命题 {stray}，已忽略")

    # --- off-thesis shocks ---
    shocks, used_ids = [], set()
    for i, s in enumerate(getattr(view, "off_thesis_shocks", []) or [], start=1):
        refs = [r for r in s.component_refs if r in by_ref]
        materiality = _norm(s.materiality, tuple(MATERIALITY_ORDER), "moderate")
        if not refs or not (s.summary or "").strip():
            gaps.append(f"论点外冲击「{(s.summary or s.shock_id)[:40]}」没有可读的成分引用，已丢弃")
            continue
        if MATERIALITY_ORDER[materiality] < MATERIALITY_ORDER["moderate"]:
            continue
        newest = max(_when(by_ref[r]["as_of"]) for r in refs)
        expires = newest + validity
        sid = (s.shock_id or "").strip() or f"{layer.key}-shock-{i}"
        if sid in used_ids:
            sid = f"{sid}-{i}"
        used_ids.add(sid)
        if expires < now:
            gaps.append(f"冲击 {sid} 已过有效期（最后进展 {newest:%Y-%m-%d}），降为背景")
            continue
        shocks.append({"shock_id": sid, "component_refs": refs, "summary": s.summary.strip(),
                       "materiality": materiality,
                       "direction": _norm(s.direction, ("positive", "negative", "mixed", "unclear"), "unclear"),
                       "role": "direct", "status": _norm(s.status, ("ongoing", "concluded"), "ongoing"),
                       "touched_claims": list(s.touched_claims)[:6], "proposed_claim": s.proposed_claim,
                       "expires_at": expires.isoformat(timespec="seconds"),
                       "materiality_review": s.materiality_review})

    # --- cross-scope claims: company-only components the analyst says reach the layer ---
    cross = []
    for c in getattr(view, "cross_scope_claims", []) or []:
        comp = by_ref.get((c.component_ref or "").strip())
        if comp is None or not (c.transmission or "").strip():
            gaps.append(f"补认领 {c.component_ref} 不是本次输入里的成分或缺传导理由，已丢弃")
            continue
        cross.append({"component_ref": comp["ref"], "from_entity": comp["entity"],
                      "transmission": c.transmission.strip(), "materiality_review": c.materiality_review})

    # --- utilization advice: down only, and only on a live risk or a live major negative shock ---
    valid = ({r["claim_id"] for r in risk_rows if r["status"] in _GATED}
             | {s["shock_id"] for s in shocks
                if MATERIALITY_ORDER[s["materiality"]] >= MATERIALITY_ORDER["major"]
                and s["direction"] == "negative" and s["status"] == "ongoing"})
    raw = getattr(view, "utilization_advice", None)
    advice = {"direction": "none", "notches": 0, "reasons": [], "rationale": ""}
    if raw is not None:
        reasons = [r for r in raw.reasons if r in valid]
        notches = max(0, min(2, int(raw.notches or 0)))
        if str(raw.direction).strip().lower() == "down" and notches and reasons:
            advice = {"direction": "down", "notches": notches, "reasons": reasons, "rationale": raw.rationale}
        elif str(raw.direction).strip().lower() not in ("", "none") or notches:
            gaps.append("下调建议没有引用承压/兑现的风险命题或有效的重大负面冲击（或方向不是向下），已清除")

    # --- every layer-level component of moderate or above needs a destination (spec: 层次分析承接层面成分) ---
    claimed = ({r for row in risk_rows for r in row["component_refs"]}
               | {r for sh in shocks for r in sh["component_refs"]} | {c["component_ref"] for c in cross})
    attributions = "\n".join(getattr(view, "claim_attributions", []) or [])
    for comp in components:
        if comp["route"] != "layer" or MATERIALITY_ORDER.get(comp["materiality"], 0) < MATERIALITY_ORDER["moderate"]:
            continue
        component_id = comp["ref"].partition("#")[2]
        if comp["ref"] in claimed or comp["ref"] in attributions or (component_id and component_id in attributions):
            continue
        gaps.append(f"成分 {comp['ref']}（{comp['materiality']}：{comp['content'][:40]}）未归宿："
                    "既未归入经营或风险命题，也未列为论点外冲击")

    fields: dict[str, Any] = {}
    if risk_rows:
        fields["risk_claims"] = risk_rows
    if shocks:
        fields["off_thesis_shocks"] = shocks
    if cross:
        fields["cross_scope_claims"] = cross
    if risk_rows or shocks or components:
        fields["utilization_advice"] = advice
    return fields, gaps
