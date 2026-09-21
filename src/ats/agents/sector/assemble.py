"""Sector-review context assembly — pure code, no LLM.

Gathers per-layer/per-ticker light snapshots (one batched price call + paced
get_info + consensus for PEAD targets only), PEAD dossier conclusions, recent
insights/high-triage events, and the static industry notes into one prompt body.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ...schemas.sector import SectorConfig

log = logging.getLogger("ats.agents.sector.assemble")


@dataclass
class SectorContext:
    cfg: SectorConfig
    static_notes: str = ""
    layer_blocks: list[str] = field(default_factory=list)
    pead_blocks: list[str] = field(default_factory=list)
    insight_lines: list[str] = field(default_factory=list)
    event_lines: list[str] = field(default_factory=list)
    macro_block: str = ""
    regional_block: str = ""
    factset_block: str = ""
    kb_criteria: str = ""
    evidence_block: str = ""

    def as_context(self) -> str:
        parts = [
            f"Weekly sector review universe — {self.cfg.label} "
            f"(需求沿 L1→L6 传导; [PEAD] = 有活体档案的重点标的):",
        ]
        if self.macro_block:
            # Kept for callers that set it explicitly; the weekly path no longer does
            # (D16 — macro acts once, at the Chief).
            parts.append("## 宏观背景（自上而下：利率/风险偏好/板块倾斜 — 据此调整层与个股观点）\n"
                         + self.macro_block)
        if self.regional_block:
            parts.append("## 区域半导体需求（持久化月度序列）\n" + self.regional_block)
        if self.factset_block:
            parts.append(self.factset_block)
        if self.kb_criteria:
            # BEFORE the evidence on purpose, mirroring structure.assess and
            # graph/pead.py: the criteria say how to weigh a reading, the ledger says
            # what this quarter's reading was, and when they disagree the later block —
            # the evidence — is the one still in view as the model writes.
            parts.append(
                "## 子层判据（策展知识库 — 年度级，说的是**怎么判断**，不是**谁排第几**）\n"
                "> 排序由下面的产业链证据与本期读数决定；本节只提供判据与常见误判。\n"
                + self.kb_criteria)
        if self.evidence_block:
            parts.append(self.evidence_block)
        parts.append("\n\n".join(self.layer_blocks))
        if self.pead_blocks:
            parts.append("## PEAD 活体档案结论（最新叙事尾部 + 已出分的 Scorecard）\n"
                         + "\n\n".join(self.pead_blocks))
        if self.insight_lines:
            parts.append("## 近期研报 insight（newsletter 提取）\n" + "\n".join(self.insight_lines))
        if self.event_lines:
            parts.append("## 近期高分新闻事件（triage ≥ 阈值）\n" + "\n".join(self.event_lines))
        if self.static_notes:
            parts.append("## 行业静态背景（产业链框架/利润分布/周期护城河 — 稳定参考，可能滞后）\n"
                         + self.static_notes)
        return "\n\n".join(parts)

    def stats(self) -> dict:
        return {
            "layers": len(self.layer_blocks),
            "layer_chars": sum(len(b) for b in self.layer_blocks),
            "pead_blocks": len(self.pead_blocks),
            "insights": len(self.insight_lines),
            "events": len(self.event_lines),
            "static_chars": len(self.static_notes),
            "regional_chars": len(self.regional_block),
            "factset_chars": len(self.factset_block),
            "kb_chars": len(self.kb_criteria),
            "total_chars": len(self.as_context()),
        }


def build(cfg: SectorConfig, *, live_data: bool = True,
          allow_llm_evidence: bool = True) -> SectorContext:
    from ...config import is_pead_covered
    from ...data import industry

    sc = SectorContext(cfg=cfg)
    symbols = cfg.all_symbols()
    # Covered = tradable target OR evidence-only observe. This drives display/enrichment
    # (the [PEAD] tag, consensus fetch), not trading — so the observe names belong here.
    pead_syms = [s for s in symbols if is_pead_covered(s)]

    snapshots = _snapshots(cfg, symbols, pead_syms) if live_data else {}

    # Per-layer blocks: question + one line per ticker.
    for layer in cfg.layers:
        # Surface the exact layer key so the LLM echoes it verbatim (key=...).
        # Without it the model invents keys from the label (L1_ai_applications vs
        # L1_app) and the assessment gets dropped as unknown.
        lines = [f"### {layer.label}  [layer key（务必原样回填此 key）= {layer.key}]",
                 f"关键问题: {layer.question}" if layer.question else ""]
        for t in layer.tickers:
            tag = " [PEAD]" if t.symbol in pead_syms else ""
            note = f" ({t.note})" if t.note else ""
            snap = snapshots.get(t.symbol, "(offline)" if not live_data else "(n/a)")
            lines.append(f"- {t.symbol}{tag}{note}: {snap}")
        if layer.private:
            lines.append(f"- 非上市玩家: {', '.join(layer.private)}")
        sc.layer_blocks.append("\n".join(x for x in lines if x))

    _pead_conclusions(sc, pead_syms)
    _insights_and_events(sc, symbols, pead_syms)
    _chain_evidence(sc, cfg, allow_llm=allow_llm_evidence)

    if live_data:
        from ...data import regional
        try:
            sc.regional_block = regional.fetch(consumer="sector_agent").render()
        except Exception as exc:  # legacy regional sources must not stop the review
            log.warning("sector regional snapshot unavailable: %s", exc)
            sc.regional_block = "(区域月度数据不可用)"
        from ...data import factset
        try:
            sc.factset_block = factset.fetch_sector_context()
        except Exception as exc:  # optional top-down context never stops the review
            log.warning("sector FactSet snapshot unavailable: %s", exc)
            sc.factset_block = ""

    # ⚠️ 宏观**不再注入行业链路**（2026-08-20，design D16）。
    # 它在 Chief 已经有落点（chief/assemble.py 读宏观评审的 sector_tilts），行业这边再吃
    # 一遍会让同一个利率/风险偏好判断被计两次；更糟的是归因污染——层级结论变差时读的人
    # 分不清是产业景气变差还是宏观变差，而那两件事对仓位的含义相反（减这一层 vs 减总仓位）。
    # 层级分析师改用**产业证据**判周期位置：capex 指引、订单与交期、库存、产能投放。
    # `macro_block` 字段保留但不再填充，好让「谁在喂它」这件事在 diff 里看得见。
    # 开关 config/pead.yaml 的 macro_review.feed_sector 因此对本链路不再有效。

    _kb_criteria(sc, cfg)

    notes = industry.fetch_notes()
    sc.static_notes = industry.as_context(notes)[:int(cfg.review["static_notes_chars"])]
    return sc


def _kb_criteria(sc: SectorContext, cfg: SectorConfig) -> None:
    """The curated sub-layer notes — the same files the structure analyst reads.

    Until now these reached ONLY the cross-section's structure analyst, while the sector
    analyst got 36k of undistilled research and had to re-derive the criteria from it
    every week. The notes are the distilled form of that same research: what makes a
    moat in this sub-layer, and which readings are commonly misread. Giving it the
    criteria does not tell it the answer — the notes deliberately contain no ranking.
    """
    from ...data import industry

    paths: list[str] = []
    for layer in cfg.layers:
        for path in layer.structure_notes.values():
            if path not in paths:
                paths.append(path)
    if not paths:
        return
    kb = industry.fetch_named(paths)
    if kb:
        # 默认值的真源是 config.py 的 setdefault（那里保证这个键一定存在），这里的
        # 兜底只是防御性的——但两处必须一致，否则改了一处会以为改全了。
        cap = int(cfg.review.get("kb_criteria_chars", 32000))
        # 截断是静默的，且切的是拼接顺序最后一份笔记的尾部。真被切到时要留下痕迹，
        # 否则下游看到的是一份看起来完整、实则缺了结尾的知识库。
        joined = industry.as_context(kb)
        if len(joined) > cap:
            log.warning("kb criteria truncated: %d chars > cap %d — 末尾笔记的结尾已被切掉，"
                        "考虑调高 review.kb_criteria_chars", len(joined), cap)
        sc.kb_criteria = joined[:cap]


# --------------------------------------------------------------------------- #
# Per-ticker light snapshots (rate-limit aware)
# --------------------------------------------------------------------------- #
def _snapshots(cfg: SectorConfig, symbols: list[str], pead_syms: list[str]) -> dict[str, str]:
    from ...data import consensus as consensus_src, fundamentals, sector_snapshot

    days = cfg.snapshot["momentum_days"]
    sleep_s = float(cfg.snapshot["sleep_between_tickers"])
    consensus_for = cfg.snapshot["consensus_for"]

    prices = sector_snapshot.fetch_prices(symbols + [cfg.sector_etf])
    etf_mom = sector_snapshot.momentum(prices.get(cfg.sector_etf, []), days[0])

    out: dict[str, str] = {}
    for sym in symbols:
        closes = prices.get(sym, [])
        m1 = sector_snapshot.momentum(closes, days[0])
        m2 = sector_snapshot.momentum(closes, days[1]) if len(days) > 1 else None
        dh = sector_snapshot.dist_to_high(closes)

        f = fundamentals.fetch_constituent_financials(sym)
        time.sleep(sleep_s)

        cons_txt = ""
        if consensus_for == "all" or (consensus_for == "pead_targets" and sym in pead_syms):
            c = consensus_src.fetch(sym, consumer="sector_consensus")
            if c.get("target_mean") is not None:
                cons_txt = (f" | PT {_fmt(c.get('target_mean'))} vs px {_fmt(c.get('target_current'))}, "
                            f"SB{c.get('rating_strong_buy')}/B{c.get('rating_buy')}/"
                            f"H{c.get('rating_hold')}/S{c.get('rating_sell')}")

        mkt = f.get("market_cap")
        parts = [
            f"mkt{_cap(mkt)}" if mkt else "mkt n/a",
            f"PE{_fmt(f.get('pe'))}/fwd{_fmt(f.get('fwd_pe'))}",
            f"GM{_pct(f.get('gross_margin'))}",
            f"RevG{_pct(f.get('rev_growth'))}",
        ]
        mom_txt = (f"{days[0]}d {_signed(m1)}"
                   + (f" (vs {cfg.sector_etf} {_signed(_rel(m1, etf_mom))})" if m1 is not None and etf_mom is not None else "")
                   + (f" {days[1]}d {_signed(m2)}" if m2 is not None else "")
                   + (f" 距高{_signed(dh)}" if dh is not None else ""))
        accounting_note = "" if f.get("accounting_status") == "covered" else (
            f" | 财报{f.get('accounting_status', 'unavailable')}"
        )
        out[sym] = " ".join(parts) + " | " + mom_txt + cons_txt + accounting_note
    return out


def _rel(a, b):
    return round(a - b, 2) if a is not None and b is not None else None


def _signed(v) -> str:
    return f"{v:+.1f}%" if v is not None else "n/a"


def _fmt(v) -> str:
    return f"{v:.0f}" if isinstance(v, (int, float)) else "n/a"


def _pct(v) -> str:
    return f"{v * 100:.0f}%" if isinstance(v, (int, float)) else "n/a"


def _cap(v: float) -> str:
    if v >= 1e12:
        return f"${v / 1e12:.1f}T"
    if v >= 1e9:
        return f"${v / 1e9:.0f}B"
    return f"${v / 1e6:.0f}M"


# --------------------------------------------------------------------------- #
# PEAD conclusions + insights/events (store reads, no network)
# --------------------------------------------------------------------------- #
def _pead_conclusions(sc: SectorContext, pead_syms: list[str]) -> None:
    from ...config import load_pead_config
    from ...memory import get_store

    store = get_store()
    cap = int(sc.cfg.review["dossier_excerpt_chars"])
    for sym in pead_syms:
        try:
            pc = load_pead_config(sym)
        except Exception as exc:  # noqa: BLE001
            log.warning("sector: pead config failed for %s: %s", sym, exc)
            continue
        d = store.get_dossier(sym, pc.fiscal_label)
        if d and d.expectation_set and d.expectation_set.narrative:
            # Tail = freshest (monitor appends [update ...] blocks at the end).
            excerpt = d.expectation_set.narrative[-cap:]
            block = f"### {sym} ({pc.fiscal_label}, phase={d.phase})\n…{excerpt}"
            if d.scorecard:
                block += (f"\nScorecard: {d.scorecard.total:+.2f} "
                          f"(门槛 {d.scorecard.threshold:+.1f}) — {d.scorecard.band}")
        else:
            block = f"### {sym} ({pc.fiscal_label})\n(seed) {pc.narrative_seed[:200]}"
        sc.pead_blocks.append(block)


def _insights_and_events(sc: SectorContext, symbols: list[str], pead_syms: list[str]) -> None:
    from ...memory import get_store

    store = get_store()
    lookback = int(sc.cfg.review["events_lookback_days"])
    min_triage = float(sc.cfg.review["events_min_triage"])
    per_ticker = int(sc.cfg.review["insights_per_ticker"])
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback)).isoformat()

    for sym in symbols:
        for r in store.recent_insights(sym, limit=per_ticker):
            if (r.get("created_at") or "") >= cutoff:
                sc.insight_lines.append(
                    f"- [{r['direction']}/{r['impact_path']}] {r['ticker']} "
                    f"({r['confidence']:.2f}): {r['summary']}")

    for sym in pead_syms:
        for e in store.recent_events(sym, limit=30):
            score = e.get("triage_score")
            if (score is not None and score >= min_triage
                    and (e.get("published_at") or "") >= cutoff):
                sc.event_lines.append(
                    f"- [{e['published_at'][:10]} {score:.1f}] ({sym}) {e['headline'][:110]}")


def _chain_evidence(sc: SectorContext, cfg: SectorConfig, *, allow_llm: bool = True) -> None:
    """Claim verdicts from the evidence ledger, split to match the layer table's columns.

    BOTH kinds come here, and they are kept apart because the layer table already
    separates them:

      供需  <- `common`   claims (supply tightness, capacity, demand, throughput)
      定价权 <- `relative` claims (share, pricing power, customer qualification)

    `relative` used to be excluded on the grounds that it belonged to the cross-section
    factor. That was a routing mistake with a visible cost: the 定价权 cell was written
    from a static industry essay plus gross margins, while `hbm_share_and_pricing_power`
    — whose own dimension description reads "HBM 价格/ASP 与毛利率走向（定价权的直接
    读数）" — sat unread in the ledger. Two things named 定价权, in the same package,
    sharing no data.

    Feeding a factor and informing an analyst are not alternatives; a claim can do
    either, both, or neither (see ClaimDef.feeds_factor). The isolation that matters is
    unchanged: a common verdict still may not be read as "who is winning", which is why
    the two blocks stay separate rather than being merged into one list.
    """
    demand_lines: list[str] = []
    pricing_lines: list[str] = []
    for layer in cfg.layers:
        d, p = layer_claim_lines(cfg, layer, allow_llm=allow_llm)
        demand_lines += d
        pricing_lines += p

    blocks = []
    if demand_lines:
        blocks.append(
            "### 供需（共同需求命题 — 用于该层「供需」与景气打分）\n"
            "> 证据的筛选、去重、立场统计与记分由确定性引擎完成；每条 ＋/－ 后面是判读理由。\n"
            "> **不要重新判断这些结论对不对**，把它们当作证据基础。覆盖率低或立场单一时，"
            "说明证据还不足以下判断。\n" + "\n".join(demand_lines))
    if pricing_lines:
        blocks.append(
            "### 定价权（截面比较命题 — 用于该层「定价权」一列）\n"
            "> 逐家读数由判读器横向比较同期财报原文得出。**与静态行业笔记冲突时以此为准**："
            "笔记是稳定的结构背景，这里是本期实际发生的事。\n"
            "> 「仅自述」= 只有该公司自己说；「有交叉印证」= 客户或第三方也这么说。\n"
            + "\n".join(pricing_lines))
    if blocks:
        sc.evidence_block = "## 产业链证据（来自各家财报原文的命题印证）\n" + "\n\n".join(blocks)


def _no_llm_judge(_claim, clusters):
    """Keep `--no-llm` deterministic without silently treating an outage as evidence.

    A no-LLM review may still surface governed observations and coverage, but it must
    not make an unrecorded semantic decision about whether a cluster supports a claim.
    The explicit neutral reason reaches the rendered evidence block.
    """
    from ...schemas.chain import ClusterJudgement, EntityReading

    # ``relative`` claims are adjudicated as a cross-section and therefore pass a
    # mapping of entity -> clusters, not a flat cluster list.  Treating that mapping
    # as clusters made `sector review --no-llm` crash after all data inputs had been
    # assembled.  Explicitly return unknown readings: no-LLM means no comparative
    # conclusion, not a fabricated neutral ranking.
    if isinstance(clusters, dict):
        return [EntityReading(entity=str(entity).upper(), standing="unknown",
                              reason="未运行 LLM 判读（--no-llm）")
                for entity in clusters]

    return [ClusterJudgement(
        cluster_key=cluster.key, polarity="neutral", reason="未运行 LLM 判读（--no-llm）",
        speaker=cluster.speaker, concept=cluster.concept, stance=cluster.stance,
        primary=cluster.primary, observation_ids=cluster.observation_ids)
        for cluster in clusters]


def layer_assessments(cfg: SectorConfig, layer, *, as_of=None,
                      allow_llm: bool = True) -> list:
    """This layer's claim verdicts as OBJECTS, before any formatting.

    The formatted variants below feed the prompt; the report needs the same verdicts to
    render the evidence chain (coverage, clusters, silent witnesses, per-cluster
    judgements, per-entity readings). Running the engine twice would be both wasteful
    and — because it re-reads the ledger — capable of disagreeing with itself, so both
    consumers come through here.

    `as_of` should be the REVIEW's timestamp, not wall-clock (mirrors chain/report.py's
    `render`): every layer in one run shares it, so the eight `ClaimAssessment` rows a
    single weekly run produces all snapshot to the same moment rather than eight
    microseconds apart. `save_claim_assessment` truncates to the day regardless, but
    passing the real moment through keeps this path honest with the one that already
    does — and the day-only truncation is an implementation detail this call site
    should not have to know about to behave correctly.
    """
    from ...chain.corroborate import assess_layer
    from ...chain.sources import source_entities_for
    from ...data.products import get_unstructured_read_router

    if not layer.claims:
        return []
    reader = get_unstructured_read_router(consumer="sector_agent")
    try:
        ccfg = cfg.review.get("corroboration", {})
        rows_by_entity: dict[str, list[dict]] = {}
        for claim in layer.claims:
            # Third-party sources are never NAMED in a claim — they bind by dimension —
            # so iterating declared witnesses alone silently drops every non-company
            # witness, which is exactly the `regulator` stance this block most needs.
            for e in (claim.expected_witnesses()
                      | {w.entity.upper() for w in claim.witnesses}
                      | set(claim.entities)
                      | source_entities_for(claim)):
                if e not in rows_by_entity:
                    rows_by_entity[e] = reader.observations(entity=e, limit=200)
        return list(assess_layer(
            layer, rows_by_entity, cfg=ccfg, as_of=as_of,
            judge=None if allow_llm else _no_llm_judge,
        ))
    finally:
        reader.close()


def layer_claim_lines(cfg: SectorConfig, layer, assessments=None, *,
                      allow_llm: bool = True) -> tuple[list[str], list[str]]:
    """One layer's claim verdicts, already split by kind: (common lines, relative lines).

    Split by kind rather than pooled, because the two answer different questions and
    have different consumers (see docs/CHAIN_EVIDENCE.md):

      common   -> 这一层该给多少钱   the layer analyst's allocation call
      relative -> 层内怎么排序/选谁  the structure factor, and the per-name rationale

    Keeping them apart is the isolation invariant, not a formatting choice: a common
    verdict may never be read as "who is winning" (competitor stronger != we are
    weaker), which is exactly what pooling them into one list invites.
    """
    from ...chain.factor_evidence import BASIS_CN, STANDING_CN

    assessments = assessments if assessments is not None else layer_assessments(
        cfg, layer, allow_llm=allow_llm)
    if not assessments:
        return [], []
    by_id = {c.id: c for c in layer.claims}
    demand_lines: list[str] = []
    pricing_lines: list[str] = []
    for a in assessments:
        claim = by_id.get(a.claim_id)
        if claim is None:
            continue
        if claim.kind == "relative":
            pricing_lines.extend(_pricing_lines(layer, claim, a, STANDING_CN, BASIS_CN))
        else:
            demand_lines.extend(_demand_lines(layer, claim, a))
    return demand_lines, pricing_lines


COMMON_BLOCK_HEADER = (
    "## 共同需求议题（common 命题 — **这一层该给多少钱**的依据）\n"
    "> 证据的筛选、去重、立场统计与记分由确定性引擎完成；每条 ＋/－ 后面是判读理由。\n"
    "> **不要重新判断这些结论对不对**，把它们当作证据基础。覆盖率低或立场单一时，"
    "说明证据还不足以下判断。\n"
    "> ⚠️ 这些是**行业共同需求**的结论，**不得**读成「谁在赢」——竞争者变强不等于我方变弱。\n")

RELATIVE_BLOCK_HEADER = (
    "## 截面比较议题（relative 命题 — **层内选谁**的依据）\n"
    "> 逐家读数由判读器横向比较同期财报原文得出。**与静态判据笔记冲突时以此为准**："
    "笔记是稳定的结构背景，这里是本期实际发生的事。\n"
    "> 「仅自述」= 只有该公司自己说；「有交叉印证」= 客户或第三方也这么说。\n")


def layer_evidence_blocks(cfg: SectorConfig, layer, assessments=None) -> tuple[str, str]:
    """(common block, relative block) for ONE layer — the layer analyst's two inputs.

    Returns empty strings where the layer has nothing of that kind; the caller decides
    how to say so, because "no claims at all" and "claims that said nothing this
    quarter" must not be phrased the same way.
    """
    demand, pricing = layer_claim_lines(cfg, layer, assessments)
    common = (COMMON_BLOCK_HEADER + "\n".join(demand)) if demand else ""
    relative = (RELATIVE_BLOCK_HEADER + "\n".join(pricing)) if pricing else ""
    return common, relative


def _demand_lines(layer, claim, a) -> list[str]:
    silent = f" · 未发声 {', '.join(a.silent_witnesses)}" if a.silent_witnesses else ""
    # `basis` travels with the verdict, or the analyst cannot tell a reading that two
    # vantage points confirmed from one that only the interested parties asserted —
    # they warrant different confidence and the verdict word alone hides the difference.
    basis = {"self_reported": "（仅自述）", "thin": "（证据薄）"}.get(a.basis, "")
    out = [f"- [{layer.key}] {claim.statement}\n"
           f"    结论 {a.verdict}{basis} · 证人覆盖 {a.coverage} · 独立证据簇 "
           f"{a.evidence_clusters} · 立场 {a.stance_classes} 类 · "
           f"支持 {a.support_score:.0f}/反驳 {a.refute_score:.0f}{silent}"]
    # The reasons travel with the verdict. Without them the analyst is asked to accept a
    # number on authority — and the reasons are the only thing that makes the polarity
    # call checkable rather than merely asserted.
    mark = {"support": "＋", "refute": "－", "neutral": "・"}
    for j in a.judgements:
        if j.polarity == "neutral" and not j.reason:
            continue
        out.append(f"      {mark.get(j.polarity, '・')} {j.speaker} [{j.concept}] {j.reason}")
    return out


def _pricing_lines(layer, claim, a, standing_cn, basis_cn) -> list[str]:
    """One comparison table per claim — never N independent per-name assertions.

    The question is how a position is distributed across a cohort, and a reader can only
    answer that by seeing the names side by side.
    """
    if not a.entity_readings:
        return []
    out = [f"- [{layer.key}] {claim.statement}",
           "", "  | 公司 | 位置 | 依据强度 | 说话人 | 判读理由 |", "  |---|---|---|---|---|"]
    for r in a.entity_readings:
        out.append(f"  | **{r.entity}** | {standing_cn.get(r.standing, r.standing)} "
                   f"| {basis_cn.get(r.basis, r.basis)} | {', '.join(r.speakers) or '—'} "
                   f"| {r.reason or '—'} |")
    graded = [r for r in a.entity_readings if r.standing != "unknown"]
    if graded and all(r.basis == "self_reported" for r in graded):
        out.append("  > ⚠️ 全部读数均为各家自述，无客户或第三方交叉印证——可比性来自横向"
                   "对照，而非单条的可信度。")
    out.append("")
    return out
