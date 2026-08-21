"""One `SectorReview` -> a single self-contained, click-through-traceable HTML dashboard.

Design: `~/.claude/plans/elegant-petting-nygaard.md`. The markdown reports
(`report.py`) answer "what does each layer conclude"; this answers "why", letting a
reader drill from a layer's allocation call down to the verbatim sentence it rests on
without leaving the page. Single file, embedded JSON, client-side rendering — no
server, no build step (see the plan's "关键设计决策").

Two ways to build a bundle, same shape either way:
  * live  — `_run_layered` already holds `assessments_by_layer` in memory for the run
    it just did; no extra DB reads beyond the observation/document batch fetch.
  * offline — `ats sector html <sector> --date` has no in-memory review. It reads a
    stored `SectorReview` and calls `store.claim_assessments_on()` for the same day.
    Read-only, never re-runs analysis (mirrors `runtime/digest.py`'s discipline).

`build_bundle()` never calls an LLM and never writes to the store — it only reads
what a review already produced and computed. A failure anywhere in here must not cost
the run its markdown reports; callers wrap this in a best-effort try/except.
"""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from ...chain.factor_evidence import BASIS_CN, STANDING_CN
from ...chain.report import VERDICT_MARK
from ..evidence.observer import source_rank
from .cross_section import utilization_for
from .viz_assets import CSS, JS

log = logging.getLogger("ats.agents.sector.viz")

ALLOC_CLASS = {"超配": "over", "标配": "flat", "低配": "under", "清仓": "exit"}
# 立场 -> 证据/溯源页的分栏标签与配色分组，复用 dataviz 的 cat-1..5 记号。
STANCE_CN = {"supplier": "供给方", "customer": "客户方", "regulator": "第三方",
            "competitor": "同业", "incumbent": "当事方"}
STANCE_ORDER = ["supplier", "customer", "regulator", "competitor", "incumbent"]
# observer.source_rank() 的整数分级 -> 溯源页的来源层级徽章。
TIER_CN = {3: "manual", 2: "keyed", 1: "public", 0: "search"}
POLARITY_TEXT = {"support": "支持", "refute": "反驳", "neutral": "中性"}
STANCE_PILL_CLASS = {"增持": "in", "持有": "hold", "减持": "out"}
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"
MAX_CHAINMAP_EDGES = 90
# Config labels are written "L6 存储（HBM/DRAM/NAND/HDD）" — the short code is already
# baked in. The die and chain-map lane tag show the code and the name as two visually
# separate pieces (code in mono, name in bold), so they need the name ALONE; the scope
# bar and everywhere else use the full label as config wrote it.
_LAYER_PREFIX_RE = re.compile(r"^L\d+[_\s]*")


def _short_label(label: str) -> str:
    return _LAYER_PREFIX_RE.sub("", label or "").strip() or label


def build_bundle(cfg, review, *, assessments_by_layer: dict, store) -> dict:
    """`cfg`: SectorConfig. `review`: the SectorReview this bundle renders (its
    `baskets`/`layer_verdicts` must be from the SAME run as `assessments_by_layer` —
    the live caller already guarantees this; the offline CLI must pick a review whose
    `as_of` date matches the requested date, not just "latest"). `store`: TradingMemory.
    """
    pead = _pead_symbols(cfg)
    basket_by_key = {b.layer_key: b for b in review.baskets}
    verdict_by_key = {v.layer_key: v for v in review.layer_verdicts}

    # Every observation/document this run actually cites, fetched ONCE — eight layers
    # share one batch instead of each re-querying the ledger (see store.observations_by_id
    # / documents_by_id docstrings for why this must not over-fetch).
    all_obs_ids: set[str] = set()
    for assessments in assessments_by_layer.values():
        for a in assessments:
            all_obs_ids.update(a.observation_ids)
            for j in a.judgements:
                all_obs_ids.update(j.observation_ids)
            for r in a.entity_readings:
                all_obs_ids.update(r.key_observation_ids)
    observations = store.observations_by_id(list(all_obs_ids))
    doc_ids = {o["document_id"] for o in observations.values() if o.get("document_id")}
    documents = store.documents_by_id(list(doc_ids))

    layers = [_layer_bundle(ly, verdict_by_key.get(ly.key), basket_by_key.get(ly.key),
                            assessments_by_layer.get(ly.key) or [],
                            observations, documents, pead)
             for ly in cfg.layers]
    return {"meta": _meta(cfg, review, pead, verdict_by_key),
            "chainmap": _chainmap(cfg), "layers": layers}


# --------------------------------------------------------------------------- #
# top-of-page
# --------------------------------------------------------------------------- #
def _pead_symbols(cfg) -> set[str]:
    from ...config import is_pead_covered

    return {s for s in cfg.all_symbols() if is_pead_covered(s)}


def _meta(cfg, review, pead, verdict_by_key) -> dict:
    missing = [ly.label for ly in cfg.layers if ly.key not in verdict_by_key]
    return {
        "sector": cfg.name, "label": cfg.label or cfg.name,
        "as_of": review.as_of.isoformat(), "as_of_display": f"{review.as_of:%Y-%m-%d}",
        "regime": review.regime, "rotation_advice": review.rotation_advice,
        "top_risks": list(review.top_risks), "missing_layers": missing,
        "universe_count": len(cfg.all_symbols()), "pead_symbols": sorted(pead),
    }


def _chainmap(cfg) -> dict:
    """Illustrative overview only — the UI caption says so. Edges are inferred from
    each claim's declared witness STANCE (customer/supplier), not a separately
    maintained dependency graph: this repo has no such data source, and inventing one
    would assert more than is actually known. A `customer` witness points INTO the
    layer (it orders from it); a `supplier` witness is pointed to BY the layer (the
    layer sources from it). Capped and deduped for legibility, not a complete graph.
    """
    lanes: list[dict] = []
    node_id_of: dict[str, str] = {}
    for ly in cfg.layers:
        nodes = []
        for t in ly.tickers:
            if not t.symbol:
                continue
            nid = f"{ly.key}::{t.symbol}"
            nodes.append({"id": nid, "symbol": t.symbol})
            node_id_of.setdefault(t.symbol.upper(), nid)
        if nodes:
            lanes.append({"key": ly.key, "short": ly.key.split("_")[0],
                         "label": ly.label, "short_label": _short_label(ly.label),
                         "nodes": nodes})

    edges: set[tuple[str, str]] = set()
    for ly in cfg.layers:
        layer_nodes = [node_id_of[t.symbol.upper()] for t in ly.tickers
                       if t.symbol and t.symbol.upper() in node_id_of]
        for claim in ly.claims:
            for w in claim.witnesses:
                wid = node_id_of.get(w.entity.upper())
                if not wid:
                    continue
                for ln in layer_nodes:
                    if wid == ln:
                        continue
                    if w.stance == "customer":
                        edges.add((wid, ln))
                    elif w.stance == "supplier":
                        edges.add((ln, wid))
        if len(edges) >= MAX_CHAINMAP_EDGES:
            break
    return {"lanes": lanes, "edges": [list(e) for e in list(edges)[:MAX_CHAINMAP_EDGES]]}


# --------------------------------------------------------------------------- #
# one layer
# --------------------------------------------------------------------------- #
def _layer_bundle(layer, verdict, basket, assessments, observations, documents, pead) -> dict:
    by_claim = {c.id: c for c in layer.claims}
    common = [a for a in assessments if by_claim.get(a.claim_id)
              and by_claim[a.claim_id].kind != "relative"]
    budget_line = _budget_line(verdict, layer, basket)
    return {
        "key": layer.key, "short": layer.key.split("_")[0], "label": layer.label,
        "short_label": _short_label(layer.label),
        "die": _die(layer, verdict, budget_line),
        "budget_formula": budget_line["formula"],
        "confidence": round(verdict.confidence, 2) if verdict else None,
        "cycle_position": (verdict.cycle_position if verdict else "") or "—",
        "cycle_why": _cycle_why(verdict),
        "has_claims": bool(verdict.has_claims) if verdict else bool(layer.claims),
        "cross_section_applicable": (bool(verdict.cross_section_applicable)
                                    if verdict else True),
        "name_calls": _name_calls(verdict, basket, pead),
        "reversal_triggers": [{"text": t} for t in (verdict.reversal_triggers if verdict else [])],
        "rationale": (verdict.rationale if verdict else "") or "",
        "claims": [_claim_dict(a, by_claim.get(a.claim_id)) for a in assessments],
        "witness_matrix": _witness_matrix(common, by_claim),
        "evidence": _evidence_view(assessments, by_claim, observations),
        "trace": _trace_view(assessments, by_claim, observations, documents),
        "xsection": _xsection_view(basket),
        "candidate_claims": [{"statement": c.statement, "witnesses": list(c.witnesses),
                              "falsifier": c.falsifier, "why_now": c.why_now}
                             for c in (verdict.candidate_claims if verdict else [])],
    }


def _budget_line(verdict, layer, basket) -> dict:
    """`层上限 × 使用率` — same derivation as `report.py::_budget_derivation`, reused
    (not re-invented) so the HTML and the markdown report can never disagree about this
    number. `basket.layer_cap` is the ACTUAL post-group-rescale budget when a basket
    ran this round; the formula recomputation is only a fallback for when it didn't.
    """
    cap = layer.weight_cap or 0.0
    if verdict is None:
        return {"amount": 0.0, "cap": cap, "formula": "—（本轮未产出结论）", "squeezed": False}
    budget = basket.layer_cap if basket is not None else None
    util = utilization_for(verdict.allocation)
    if budget is None:
        amount = cap * util
        return {"amount": amount, "cap": cap, "squeezed": False,
                "formula": f"{amount:.1%} NAV = 层上限 {cap:.0%} × 使用率 {util:.0%}"
                          f"（{verdict.allocation}，本轮无截面预算，按公式估算）"}
    expected = cap * util
    squeezed = budget < expected * 0.999
    formula = f"{budget:.1%} NAV = 层上限 {cap:.0%} × 使用率 {util:.0%}（{verdict.allocation}）"
    if squeezed:
        formula += f" ⚠ 实得低于算式值（{expected:.1%}），被跨层组上限按比例压到 {budget:.1%}"
    return {"amount": budget, "cap": cap, "formula": formula, "squeezed": squeezed}


def _die(layer, verdict, budget_line) -> dict:
    cap = layer.weight_cap or 0.0
    label = _short_label(layer.label)
    if verdict is None:
        return {"key": layer.key, "short": layer.key.split("_")[0], "label": label,
                "allocation": "—", "alloc_class": "flat", "confidence": 0.0, "budget": 0.0,
                "cap": cap, "budget_pct_of_cap": 0, "flags": ["本轮未产出结论"]}
    flags = []
    if not verdict.has_claims:
        flags.append("无命题")
    if not verdict.cross_section_applicable:
        flags.append("截面不适用")
    pct = round(100 * budget_line["amount"] / cap) if cap else 0
    return {"key": layer.key, "short": layer.key.split("_")[0], "label": label,
            "allocation": verdict.allocation,
            "alloc_class": ALLOC_CLASS.get(verdict.allocation, "flat"),
            "confidence": round(verdict.confidence, 2), "budget": budget_line["amount"],
            "cap": cap, "budget_pct_of_cap": max(0, min(100, pct)), "flags": flags}


def _cycle_why(verdict) -> list[str]:
    """The 「依据」 disclosure: one line per common claim this layer's sizing rested on
    (`claim_attributions`, already written for exactly this purpose), plus the layer
    analyst's own synthesis (`rationale`) as the closing line. No new field — both
    already exist on `LayerVerdict` and this is what they are for.
    """
    if verdict is None:
        return []
    lines = [f"{CIRCLED[i] if i < len(CIRCLED) else i + 1} {t}"
             for i, t in enumerate(verdict.claim_attributions)]
    if verdict.rationale:
        lines.append(f"→ {verdict.rationale}")
    return lines


def _name_calls(verdict, basket, pead) -> list[dict]:
    if verdict is None:
        return []
    weights = {r.symbol: r for r in (basket.rows if basket is not None else [])}
    called = {c.symbol: c for c in verdict.name_calls}
    out = []
    for sym in list(called) + [s for s in weights if s not in called]:
        c, r = called.get(sym), weights.get(sym)
        stance = c.stance if c else "—"
        out.append({
            "symbol": sym, "is_pead": sym in pead,
            "subgroup": (c.subgroup if c and c.subgroup else (r.subgroup if r else "")) or "",
            "stance": stance, "stance_class": STANCE_PILL_CLASS.get(stance, "hold"),
            "self_reported_only": bool(c and c.self_reported_only),
            "weight": r.weight if r else None,
            "rank": (r.rank if r and verdict.cross_section_applicable else None),
            "rationale": c.rationale if c else "",
        })
    return out


# --------------------------------------------------------------------------- #
# ② 命题
# --------------------------------------------------------------------------- #
def _claim_dict(a, claim) -> dict:
    kind = "relative" if (claim and claim.kind == "relative") else "common"
    return {
        "claim_id": a.claim_id,
        "statement": (claim.statement if claim else "") or a.claim_id,
        "kind": kind, "verdict": a.verdict, "verdict_mark": VERDICT_MARK.get(a.verdict, a.verdict),
        "basis": a.basis, "basis_cn": BASIS_CN.get(a.basis, a.basis),
        "coverage": a.coverage, "evidence_clusters": a.evidence_clusters,
        "stance_classes": a.stance_classes,
        "support_score": a.support_score, "refute_score": a.refute_score,
        "silent_witnesses": list(a.silent_witnesses), "dissenters": list(a.dissenters),
        "unresolved_reason": a.unresolved_reason,
        "entity_readings": [
            {"entity": r.entity, "standing": r.standing,
             "standing_cn": STANDING_CN.get(r.standing, r.standing),
             "basis": r.basis, "basis_cn": BASIS_CN.get(r.basis, r.basis),
             "evidence_clusters": r.evidence_clusters, "stance_classes": r.stance_classes,
             "speakers": list(r.speakers), "reason": r.reason}
            for r in a.entity_readings
        ],
    }


def _witness_matrix(common: list, by_claim: dict) -> dict:
    """Rows = common claims only (a `relative` claim has no single true/false to plot
    on a support/refute matrix — its per-entity standing already IS the answer, shown
    in its own table). Columns = every witness this layer's common claims ever named,
    in first-seen order."""
    witnesses: list[str] = []
    seen: set[str] = set()
    rows = []
    for a in common:
        claim = by_claim.get(a.claim_id)
        expected = claim.expected_witnesses() if claim else set()
        cols = (expected | {(j.speaker or "").upper() for j in a.judgements if j.speaker}
                | set(a.silent_witnesses))
        cells: dict[str, str] = {}
        for w in cols:
            if not w:
                continue
            if w not in seen:
                seen.add(w)
                witnesses.append(w)
            js = [j for j in a.judgements if (j.speaker or "").upper() == w]
            if any(j.polarity == "refute" for j in js):
                cells[w] = "ref"
            elif any(j.polarity == "support" for j in js):
                cells[w] = "sup"
            elif w in a.silent_witnesses:
                cells[w] = "silent"
            elif js:
                cells[w] = "neu"
            else:
                cells[w] = "silent" if w in expected else "neu"
        rows.append({"claim_id": a.claim_id,
                     "statement": (claim.statement if claim else a.claim_id),
                     "cells": cells})
    return {"witnesses": witnesses, "rows": rows}


# --------------------------------------------------------------------------- #
# ③ 证据 / ④ 溯源 — shared helpers
# --------------------------------------------------------------------------- #
def _period_span(periods: list[str]) -> str:
    if not periods:
        return ""
    return periods[0] if len(periods) == 1 else f"{periods[0]}–{periods[-1]}"


def _standing_polarity(standing: str) -> str:
    """A `relative` claim's per-entity reading has no support/refute call of its own
    (see `EntityReading` — it grades a POSITION, not a proposition). For the shared
    evidence-card visual language this maps `strong` to the same color as `support`
    and `weak` to `refute`; `neutral`/`unknown` render as neutral, same as elsewhere."""
    return {"strong": "support", "weak": "refute"}.get(standing, "neutral")


def _evidence_view(assessments, by_claim, observations) -> dict:
    stance_groups: dict[str, list] = defaultdict(list)
    silents = []
    for a in assessments:
        claim = by_claim.get(a.claim_id)
        statement = claim.statement if claim else a.claim_id
        # Cross-validation, computed from the judgements actually on hand rather than a
        # stored flag: does this polarity, within THIS claim, rest on >=2 distinct
        # witness stance classes? That is corroboration's own definition.
        by_polarity: dict[str, set] = defaultdict(set)
        for j in a.judgements:
            if j.stance:
                by_polarity[j.polarity].add(j.stance)
        for j in a.judgements:
            stance = j.stance or "incumbent"
            spans = [observations[i] for i in j.observation_ids if i in observations]
            periods = sorted({s.get("period") for s in spans if s.get("period")})
            stance_groups[stance].append({
                "cluster_key": j.cluster_key, "claim_id": a.claim_id, "statement": statement,
                "speaker": j.speaker, "polarity": j.polarity, "concept": j.concept,
                "reason": j.reason, "n_observations": len(j.observation_ids),
                "period_span": _period_span(periods),
                "cross": len(by_polarity.get(j.polarity, ())) >= 2,
                "has_trace": bool(spans), "is_relative": False,
            })
        for w in a.silent_witnesses:
            silents.append({"speaker": w, "claim_id": a.claim_id, "statement": statement})
        # `relative` claims: one card per entity reading — there is no per-cluster
        # polarity to show (the comparison itself is the answer), so the card carries
        # the entity's standing instead.
        for r in a.entity_readings:
            stance = (claim.stance_of(r.entity) if claim else "") or "competitor"
            spans = [observations[i] for i in r.key_observation_ids if i in observations]
            periods = sorted({s.get("period") for s in spans if s.get("period")})
            stance_groups[stance].append({
                "cluster_key": f"reading|{a.claim_id}|{r.entity}", "claim_id": a.claim_id,
                "statement": statement, "speaker": r.entity,
                "polarity": _standing_polarity(r.standing), "concept": "截面读数",
                "reason": r.reason, "n_observations": len(r.key_observation_ids),
                "period_span": _period_span(periods), "cross": r.basis == "corroborated",
                "has_trace": bool(spans), "is_relative": True,
                "standing": r.standing, "standing_cn": STANDING_CN.get(r.standing, r.standing),
            })
    return {
        "stances": [{"key": s, "label": STANCE_CN.get(s, s), "clusters": stance_groups[s]}
                    for s in STANCE_ORDER if stance_groups.get(s)],
        "silent": silents,
    }


def _quotes(obs_ids: list[str], observations: dict, documents: dict) -> list[dict]:
    out = []
    for oid in obs_ids:
        o = observations.get(oid)
        if not o or not o.get("evidence_span"):
            continue
        doc = documents.get(o.get("document_id")) or {}
        rank = source_rank(doc.get("source") or "")
        out.append({
            "text": o["evidence_span"], "confidence": o.get("extraction_confidence") or 1.0,
            "entity": o.get("source_entity") or o.get("entity") or "",
            "period": o.get("period") or "", "source": doc.get("source") or "",
            "tier": TIER_CN.get(rank, "search"),
            "source_url": doc.get("source_url") or o.get("source_url") or "",
            "local_path": doc.get("local_path") or "", "sha256": doc.get("sha256") or "",
            "fetched_at": doc.get("fetched_at") or "",
        })
    return out


def _judgement_verdict_text(j) -> str:
    lab = POLARITY_TEXT.get(j.polarity, j.polarity)
    return f"{lab} —— {j.reason}" if j.reason else lab


def _trace_view(assessments, by_claim, observations, documents) -> dict:
    clusters = []
    seen: set[str] = set()
    for a in assessments:
        claim = by_claim.get(a.claim_id)
        for j in a.judgements:
            if j.cluster_key in seen or not j.observation_ids:
                continue
            quotes = _quotes(j.observation_ids, observations, documents)
            if not quotes:
                continue
            seen.add(j.cluster_key)
            clusters.append({
                "cluster_key": j.cluster_key, "title": f"{j.speaker} · {j.concept or a.claim_id}",
                "stance": j.stance or "incumbent", "verdict_class": j.polarity,
                "verdict_text": _judgement_verdict_text(j), "quotes": quotes,
            })
        for r in a.entity_readings:
            key = f"reading|{a.claim_id}|{r.entity}"
            if key in seen or not r.key_observation_ids:
                continue
            quotes = _quotes(r.key_observation_ids, observations, documents)
            if not quotes:
                continue
            seen.add(key)
            stance = (claim.stance_of(r.entity) if claim else "") or "competitor"
            standing_cn = STANDING_CN.get(r.standing, r.standing)
            text = f"位置 {standing_cn} —— {r.reason}" if r.reason else f"位置 {standing_cn}"
            clusters.append({
                "cluster_key": key, "title": f"{r.entity} · 截面读数", "stance": stance,
                "verdict_class": _standing_polarity(r.standing), "verdict_text": text,
                "quotes": quotes,
            })
    return {"clusters": clusters}


# --------------------------------------------------------------------------- #
# ⑤ 截面
# --------------------------------------------------------------------------- #
def _xsection_view(basket) -> dict:
    if basket is None or not basket.rows:
        return {"rows": [], "applicable": False, "structural": False}
    rows = []
    for r in sorted(basket.rows, key=lambda x: x.rank):
        m = r.metrics or {}
        rows.append({
            "symbol": r.symbol, "subgroup": r.subgroup, "data_ok": r.data_ok,
            "rank": r.rank, "composite": round(r.composite, 2), "weight": r.weight,
            "rev_growth": m.get("rev_growth"), "gross_margin": m.get("gross_margin"),
            "peg": m.get("peg"), "mom_60d": m.get("mom_60d"),
            "factors": {k: round(v, 2) for k, v in (r.factors or {}).items()},
        })
    return {"rows": rows, "applicable": basket.cross_section_applicable,
            "structural": basket.structural}


# --------------------------------------------------------------------------- #
# render / write
# --------------------------------------------------------------------------- #
_HTML_SKELETON = """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<div class="topbar">
  <div class="topbar-inner">
    <div class="topbar-row1">
      <div class="topbar-title"><h1 id="pageTitle"></h1></div>
      <div style="display:flex; align-items:center; gap:14px;">
        <span class="topbar-meta" id="asOfMeta"></span>
        <div class="search">
          <span style="font-size:13px;">⌕</span>
          <input type="text" id="searchInput" placeholder="搜索公司 / 命题 / 原文关键词，回车跳转…">
        </div>
      </div>
    </div>
    <div class="topbar-signals" id="topbarSignals"></div>
  </div>
</div>

<div class="shell">

  <div class="chainmap-wrap" data-open="true" id="chainmapWrap">
    <div class="chainmap-head">
      <div class="chainmap-title-group">
        <span class="rail-label">产业链全景</span>
        <span class="chainmap-sub">需求沿层级传导；箭头示意订单/资金流向（简化示意，据证据源里声明的证人立场推导，非独立维护的完整依赖图）</span>
      </div>
      <button class="toggle-btn" id="chainmapToggle" aria-expanded="true"><span class="car">▾</span> 收起</button>
    </div>
    <div class="chainmap" id="chainmap">
      <svg class="cm-svg" id="cmSvg">
        <defs>
          <marker id="cmArrow" viewBox="0 0 8 8" refX="6.5" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0,0 L8,4 L0,8 z"></path>
          </marker>
        </defs>
      </svg>
      <div class="cm-lanes" id="cmLanes"></div>
      <div class="cm-foot">从这里开始：先看链条全貌，再点下方任意一层下钻到该层的配置结论、命题证据与溯源。</div>
    </div>
  </div>

  <div class="rail-wrap">
    <span class="rail-label">各层配置 · 点击切换下钻范围</span>
    <div class="rail" id="rail" role="tablist" aria-label="产业链层"></div>
  </div>

  <div class="scope-bar">
    <div class="scope-label">当前范围：<strong id="scopeLabel"></strong></div>
    <details class="cycle-why">
      <summary><span class="cw-closed">依据 ▾</span><span class="cw-open">收起依据 ▴</span></summary>
      <div class="cycle-why-body" id="cycleWhyBody"></div>
    </details>
  </div>

  <div class="tabs" role="tablist">
    <button class="tab" data-tab="decision" aria-selected="true">① 决策</button>
    <button class="tab" data-tab="claims" aria-selected="false">② 命题 <span class="n">0</span></button>
    <button class="tab" data-tab="evidence" aria-selected="false">③ 证据 <span class="n">0</span></button>
    <button class="tab" data-tab="trace" aria-selected="false">④ 溯源</button>
    <button class="tab" data-tab="xsection" aria-selected="false">⑤ 截面</button>
  </div>

  <div class="filters" data-for="claims">
    <button class="filter-chip" data-kind="supportive" aria-pressed="false"><span class="dot"></span>只看印证</button>
    <button class="filter-chip" data-kind="contradicted" aria-pressed="false"><span class="dot"></span>只看反驳</button>
    <button class="filter-chip" data-kind="insufficient" aria-pressed="false"><span class="dot"></span>结论不足</button>
  </div>
  <div class="filters" data-for="evidence">
    <button class="filter-chip" data-kind="cross" aria-pressed="false"><span class="dot"></span>只看交叉验证</button>
    <button class="filter-chip" data-kind="refute" aria-pressed="false"><span class="dot"></span>只看反驳</button>
    <button class="filter-chip" data-kind="silent" aria-pressed="false"><span class="dot"></span>只看沉默</button>
  </div>

  <div class="focus-bar" id="focusBar">
    <span id="focusText"></span>
    <button class="focus-clear" id="focusClear">× 清除聚焦</button>
  </div>

  <div class="panel" data-tab="decision" data-active="true"></div>
  <div class="panel" data-tab="claims"></div>
  <div class="panel" data-tab="evidence"></div>
  <div class="panel" data-tab="trace"></div>
  <div class="panel" data-tab="xsection"></div>

  <div class="footnote">{footnote}</div>
</div>

<div class="popover" id="popover" role="tooltip"></div>

<script type="application/json" id="bundleData">{bundle_json}</script>
<script>{js}</script>
</body>
</html>
"""


def render_html(bundle: dict) -> str:
    """The bundle -> a complete, self-contained HTML document (string). No network
    calls, no external assets — everything a browser needs is in this one file
    (see the plan's "关键设计决策": offline-openable, syncable, forwardable).
    """
    meta = bundle["meta"]
    title = f"{meta['label']} 产业链 · 层级分析看板"
    footnote = (f"{title} · 生成于 {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · "
               f"数据截至 {meta['as_of_display']} · universe {meta['universe_count']} 家"
               f"（PEAD 覆盖 {len(meta['pead_symbols'])} 家）· "
               f"行情/因子值的溯源只到 basket 快照，不到供应商侧的不可变留痕")
    bundle_json = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))
    # A script tag's content ends at the first literal "</" the HTML parser sees,
    # REGARDLESS of its type attribute — this is an HTML tokenizer rule, not a JS one.
    # LLM-authored report text is the payload here, so it must be defended against
    # deliberately, not just assumed clean. `\/` is a legal JSON escape for `/`.
    bundle_json = bundle_json.replace("</", "<\\/")
    return _HTML_SKELETON.format(title=title, css=CSS, footnote=footnote,
                                 bundle_json=bundle_json, js=JS)


def write_html(bundle: dict, folder: str | Path) -> Path | None:
    """Write `层分析-<sector>-<date>.html` into `folder`. Same-day reruns overwrite —
    mirrors every other report writer in this package (`report.py`, `chain/report.py`).
    Returns None (does not raise) when `folder` is unset/missing, so a caller can
    treat this the same best-effort way as the markdown writers.
    """
    if not folder:
        log.info("sector viz html: output_dir unset — skipped")
        return None
    folder = Path(folder)
    if not folder.is_dir():
        log.warning("sector viz html: output_dir missing — skipped: %s", folder)
        return None
    meta = bundle["meta"]
    path = folder / f"层分析-{meta['label']}-{meta['as_of_display']}.html"
    path.write_text(render_html(bundle), encoding="utf-8")
    return path
