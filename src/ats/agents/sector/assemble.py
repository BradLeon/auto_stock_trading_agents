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
    evidence_block: str = ""

    def as_context(self) -> str:
        parts = [
            f"Weekly sector review universe — {self.cfg.label} "
            f"(需求沿 L1→L6 传导; [PEAD] = 有活体档案的重点标的):",
        ]
        if self.macro_block:
            parts.append("## 宏观背景（自上而下：利率/风险偏好/板块倾斜 — 据此调整层与个股观点）\n"
                         + self.macro_block)
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
            "total_chars": len(self.as_context()),
        }


def build(cfg: SectorConfig, *, live_data: bool = True) -> SectorContext:
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
    _chain_evidence(sc, cfg)

    # Top-down cascade: prepend the latest macro regime/tilts if enabled.
    from ...config import load_pead_global

    mr = load_pead_global()["macro_review"]
    if mr.get("feed_sector", True):
        from ..macro import context as macro_context

        sc.macro_block = macro_context.sector_block(mr["name"])

    notes = industry.fetch_notes()
    sc.static_notes = industry.as_context(notes)[:int(cfg.review["static_notes_chars"])]
    return sc


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

        f = fundamentals.fetch_light(sym)
        time.sleep(sleep_s)

        cons_txt = ""
        if consensus_for == "all" or (consensus_for == "pead_targets" and sym in pead_syms):
            c = consensus_src.fetch(sym)
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
        out[sym] = " ".join(parts) + " | " + mom_txt + cons_txt
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


def _chain_evidence(sc: SectorContext, cfg: SectorConfig) -> None:
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
    from ...chain.corroborate import assess_layer
    from ...chain.factor_evidence import BASIS_CN, STANDING_CN
    from ...chain.sources import source_entities_for
    from ...memory import get_store

    store = get_store()
    ccfg = cfg.review.get("corroboration", {})
    demand_lines: list[str] = []
    pricing_lines: list[str] = []
    for layer in cfg.layers:
        if not layer.claims:
            continue
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
                    rows_by_entity[e] = store.observations(entity=e, limit=200)
        by_id = {c.id: c for c in layer.claims}
        for a in assess_layer(layer, rows_by_entity, cfg=ccfg):
            claim = by_id.get(a.claim_id)
            if claim is None:
                continue
            if claim.kind == "relative":
                pricing_lines.extend(_pricing_lines(layer, claim, a,
                                                    STANDING_CN, BASIS_CN))
            else:
                demand_lines.extend(_demand_lines(layer, claim, a))

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


def _demand_lines(layer, claim, a) -> list[str]:
    silent = f" · 未发声 {', '.join(a.silent_witnesses)}" if a.silent_witnesses else ""
    out = [f"- [{layer.key}] {claim.statement}\n"
           f"    结论 {a.verdict} · 证人覆盖 {a.coverage} · 独立证据簇 "
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
