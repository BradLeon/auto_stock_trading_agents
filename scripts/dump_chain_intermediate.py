"""Dump every intermediate artefact of one sector layer's claims, for human inspection.

    PYTHONPATH=src .venv/bin/python scripts/dump_chain_intermediate.py L5_fab
    PYTHONPATH=src .venv/bin/python scripts/dump_chain_intermediate.py L2_cloud --sector ai_hardware
    PYTHONPATH=src .venv/bin/python scripts/dump_chain_intermediate.py L2_cloud --out /tmp/x.md

Writes a markdown file (default: the sector's `output_dir`) containing, in order:

    0. the claims as declared      — dimensions, witnesses, direct flags, bound sources
    1. every observation that reaches them, plus the unmapped rows that do not
    2. the persisted verdicts      — per-cluster judgements and per-entity readings
    3. the EXACT context each downstream agent receives, verbatim
    4. a causal trace              — which gate each claim is stuck behind, and why

This is a **human** artefact: nothing reads it back. It exists because the pipeline's
failure modes are silent — a claim with a `direct: false` dimension reports "no
applicable evidence" rather than an error, and single-stance evidence reports `mixed`
rather than "unconfirmed". Both look like an empty result until you read the trace.

Read-only against the ledger; the only write is the output file.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

MARK = {"support": "＋支持", "refute": "－反驳", "neutral": "・中性"}
STUCK = {
    "single_stance": "**闸2 立场**",
    "dissent": "**判读分歧**",
}


def esc(s) -> str:
    """Markdown-table-safe one-liner."""
    return (str(s or "")).replace("|", "\\|").replace("\n", " ").strip()


def main() -> int:
    from ats.agents.chief import assemble as chief_assemble
    from ats.agents.sector import assemble as sec_assemble
    from ats.chain import factor_evidence
    from ats.chain.sources import sources_for_concepts
    from ats.config import canonical_entity, load_sector_config
    from ats.memory import _db_path, get_store

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("layer", help="layer key, e.g. L2_cloud / L5_fab")
    ap.add_argument("--sector", default="ai_hardware")
    ap.add_argument("--out", default="", help="output path (default: sector output_dir)")
    ap.add_argument("--unmapped-cap", type=int, default=400)
    args = ap.parse_args()

    cfg = load_sector_config(args.sector)
    layer = next((ly for ly in cfg.layers if ly.key == args.layer), None)
    if layer is None:
        print(f"layer {args.layer!r} not in {args.sector}: "
              f"{[ly.key for ly in cfg.layers]}")
        return 2
    if not layer.claims:
        print(f"{args.layer} declares no claims — nothing to dump")
        return 1

    store = get_store()
    # Read-only handle: MemoryStore.__init__ runs ALTER TABLE migrations, and a dump
    # must never migrate the ledger it is inspecting.
    db = sqlite3.connect(f"file:{_db_path()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row

    out = Path(args.out) if args.out else Path(cfg.output_dir or ".") / (
        f"产业链证据-中间过程检查-{layer.key}-{datetime.now():%Y-%m-%d}.md")

    L: list[str] = []
    w = L.append

    # ── 0. claims as declared ──────────────────────────────────────────────
    w(f"# 产业链证据 · 中间过程检查（{layer.label}）")
    w("")
    w(f"生成于 {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · "
      f"行业 {cfg.label} · 层 {layer.label}")
    w("")
    w("> **中间过程的完整落盘，供人工核对——它不是给任何 agent 读的。**")
    w("> 存在的理由：这条链的失效大多是静默的。`direct: false` 的维度报的是"
      "「无适用证据」不是报错；单一立场的证据报的是 `mixed` 不是「未确认」。"
      "两者看起来都只是「空结果」，只有第 4 节的追踪能分开。")
    w("")
    w("---")
    w("")
    w(f"## 0. 本层的 {len(layer.claims)} 条命题")
    w("")
    concept_keys: set[str] = set()
    for c in layer.claims:
        kind = "共同需求（common）" if c.kind == "common" else "截面比较（relative）"
        w(f"### `{c.id}` — {kind}")
        w("")
        w(f"**{c.statement}**")
        w("")
        if c.horizon:
            w(f"- 时间跨度：{c.horizon.from_} ~ {c.horizon.to}")
        if c.entities:
            w(f"- 截面对象：{', '.join(c.entities)}")
        if c.feeds_factor:
            w(f"- 供给的结构因子：`{c.feeds_factor}`")
        w("")
        w("| 维度 | 说明 | 声明证人 | direct | 第三方源 |")
        w("|---|---|---|---|---|")
        for x in c.concepts:
            concept_keys.add(x.key)
            srcs = ", ".join(s.id for s in sources_for_concepts({x.key})) or "—"
            w(f"| `{x.key}` | {esc(x.desc)} | {', '.join(x.expect_from) or '—'} "
              f"| {'✓' if x.direct else ''} | {srcs} |")
        w("")
        w("| 证人 | 立场 |")
        w("|---|---|")
        for wit in c.witnesses:
            w(f"| {wit.entity} | {wit.stance} |")
        if c.falsifiers:
            w("")
            w("证伪条件：")
            for f in c.falsifiers:
                w(f"- {f}")
        w("")

    # ── 1. observations ────────────────────────────────────────────────────
    w("---")
    w("")
    w("## 1. `evidence_observations` — 本层命题够得着的部分")
    w("")
    rows = db.execute(
        "SELECT * FROM evidence_observations WHERE concept IN (%s) "
        "ORDER BY concept, entity" % ",".join("?" * len(concept_keys)),
        sorted(concept_keys)).fetchall()
    by_concept: dict[str, list] = defaultdict(list)
    for r in rows:
        by_concept[r["concept"]].append(r)
    total = db.execute("SELECT COUNT(*) FROM evidence_observations").fetchone()[0]
    unmapped_n = db.execute(
        "SELECT COUNT(*) FROM evidence_observations WHERE (concept IS NULL OR concept='')"
        " AND COALESCE(discovery_evidence,0)=0").fetchone()[0]
    w(f"全库 **{total}** 条 · 归到本层维度的 **{len(rows)}** 条 · "
      f"未映射池 **{unmapped_n}** 条")
    w("")
    w("> 只有下面这些进得了本层命题。未映射池里的任何命题都够不着——它是归纳引擎的原料。")
    w("")
    empty_concepts = sorted(concept_keys - set(by_concept))
    if empty_concepts:
        w(f"⚠️ **零证据的维度**：{', '.join('`%s`' % k for k in empty_concepts)}"
          f" —— 声明了但没有任何观测归进来。")
        w("")
    for key in sorted(by_concept):
        rs = by_concept[key]
        w(f"### `{key}` — {len(rs)} 条")
        w("")
        w("| 说话人 | 关于 | 方向 | 类型 | 期间 | 原文 |")
        w("|---|---|---|---|---|---|")
        for r in rs:
            w(f"| {esc(r['source_entity'])} | {esc(r['entity'])} | {esc(r['direction'])} "
              f"| {esc(r['observation_type'])} | {esc(r['period'])} "
              f"| {esc(r['evidence_span'])[:200]} |")
        w("")

    ents = {canonical_entity(t.symbol) for t in layer.tickers}
    ents |= {canonical_entity(s) for s in layer.cohort_extra}
    for c in layer.claims:
        ents |= {canonical_entity(x.entity) for x in c.witnesses}
        ents |= {canonical_entity(e) for e in c.entities}
    un = db.execute(
        "SELECT entity, source_entity, metric, evidence_span FROM evidence_observations"
        " WHERE (concept IS NULL OR concept='') AND COALESCE(discovery_evidence,0)=0"
        " AND entity IN (%s) ORDER BY entity LIMIT ?" % ",".join("?" * len(ents)),
        [*sorted(ents), args.unmapped_cap]).fetchall()
    w(f"### 未映射池里属于本层相关公司的 — {len(un)} 条"
      f"（上限 {args.unmapped_cap}）")
    w("")
    w("> 抽出来了，但归不到任何已声明维度。**它们对本层命题完全没有影响。**")
    w("")
    w("| 关于 | 说话人 | 指标 | 原文 |")
    w("|---|---|---|---|")
    for r in un:
        w(f"| {esc(r['entity'])} | {esc(r['source_entity'])} | {esc(r['metric'])} "
          f"| {esc(r['evidence_span'])[:160]} |")
    w("")

    # ── 2. verdicts ────────────────────────────────────────────────────────
    w("---")
    w("")
    w("## 2. `claim_assessments` — 结论快照")
    w("")
    w("> 按 `(claim_id, as_of)` 版本化，**每天一行**。只有 `ats evidence report` 会写，"
      "`ats evidence claims` 是只读的——所以用 claims 看到的结论可能还没落库。")
    w("")
    ids = [c.id for c in layer.claims]
    snaps = db.execute(
        "SELECT * FROM claim_assessments WHERE claim_id IN (%s) "
        "ORDER BY as_of DESC, claim_id" % ",".join("?" * len(ids)), ids).fetchall()
    w(f"共 {len(snaps)} 行。")
    w("")
    latest: dict[str, dict] = {}
    for s in snaps:
        p = json.loads(s["payload"])
        latest.setdefault(s["claim_id"], p)
        w(f"### `{s['claim_id']}` @ {s['as_of']}")
        w("")
        reason = p.get("unresolved_reason") or ""
        tag = {"single_stance": "（证据一边倒但立场单一）",
               "dissent": "（分歧未消解）"}.get(reason, "")
        w(f"- 结论 **{p['verdict']}**{tag} · 支持 {p['support_score']:.0f} / "
          f"反驳 {p['refute_score']:.0f} · 独立证据簇 {p['evidence_clusters']} · "
          f"立场 {p['stance_classes']} 类 · 证人覆盖 "
          f"{p['witnesses_reported']}/{p['witnesses_expected']}")
        w(f"- 引擎备注：{esc(p.get('note'))}")
        if p.get("dissenters"):
            w(f"- 异议方：{', '.join(p['dissenters'])}")
        if p.get("silent_witnesses"):
            w(f"- **本期未发声**：{', '.join(p['silent_witnesses'])}"
              f"　← 沉默记成缺口，不算中性")
        if p.get("entity_readings"):
            w("")
            w("**逐家读数**（截面命题的答案就是这张表）")
            w("")
            w("| 公司 | 位置 | 依据强度 | 证据簇 | 立场数 | 说话人 | 理由 |")
            w("|---|---|---|---|---|---|---|")
            for e in p["entity_readings"]:
                w(f"| **{e['entity']}** | {e['standing']} | {e['basis']} "
                  f"| {e['evidence_clusters']} | {e['stance_classes']} "
                  f"| {', '.join(e.get('speakers') or [])} | {esc(e.get('reason'))} |")
        if p.get("judgements"):
            w("")
            w("**逐簇判读**（结论是这张表的算术；要争论就争论其中某一行）")
            w("")
            w("| 判读 | 说话人 | 维度 | 理由 |")
            w("|---|---|---|---|")
            for j in p["judgements"]:
                w(f"| {MARK.get(j['polarity'], j['polarity'])} | {esc(j.get('speaker'))} "
                  f"| `{esc(j.get('concept'))}` | {esc(j.get('reason'))} |")
        w("")

    # ── 3. downstream context, verbatim ────────────────────────────────────
    w("---")
    w("")
    w("## 3. 下游消费者拿到的完整 context")
    w("")
    w("> 以下是**原样**喂给各 agent 的文本，一字未删。")
    w("")
    w("### 3.1 截面分析师 —— 因子证据包")
    w("")
    w("用途：校正命题**声明**的那个结构因子（`feeds_factor`）。省略声明的不产生包。")
    w("")
    packs = factor_evidence.packs_for_layer(
        layer, store, cfg=cfg.review.get("corroboration", {}))
    ctx = factor_evidence.as_context(packs)
    w("```text")
    w(ctx if ctx.strip() else "（本层没有声明 feeds_factor 的已解析命题 —— 不产生证据包）")
    w("```")
    w("")
    w("### 3.2 行业分析师 —— 周度评审上下文里的产业链证据块")
    w("")
    w("用途：分层表的「供需」与「定价权」两列。common 与 relative 分开呈现，"
      "因为共同命题不能被读成「谁在赢」。**这一块是全行业的，不只本层。**")
    w("")
    sc = sec_assemble.SectorContext(cfg=cfg)
    sec_assemble._chain_evidence(sc, cfg)
    w("```text")
    w(sc.evidence_block or "（无）")
    w("```")
    w("")
    w("### 3.3 Chief —— 行业评审块")
    w("")
    w("用途：日级决策。Chief 读的是**已保存的行业评审**（周度产出），不是实时重算——"
      "所以 3.1/3.2 里的新证据要等下一次周度作业才会进到这里。")
    w("")
    try:
        chief_block = chief_assemble._sector_block(set())
    except Exception as exc:  # noqa: BLE001 - a dump must not fail on one section
        chief_block = f"（取不到：{type(exc).__name__}: {exc}）"
    w("```text")
    w(chief_block or "（无）")
    w("```")
    w("")

    # ── 4. causal trace ────────────────────────────────────────────────────
    w("---")
    w("")
    w("## 4. 每一步怎么影响结论")
    w("")
    w("上面三节是**产出**，这一节是**因果**：一句财报原文要过多少道闸，才能改变一个"
      "agent 的结论。")
    w("")
    w("### 4.0 全链路")
    w("")
    w("```")
    w("财报原文 / 电话会纪要")
    w("   │  ① 抽取（LLM）   concept_menu = 该公司**是声明证人**的那些维度")
    w("   ↓                  归不上 → 未映射池，任何命题都够不着")
    w("evidence_observations")
    w("   │  ② 闸1 去重      按 (说话人, 事实, 方向, 一手/二手) 聚簇")
    w("   │                  同一家在纪要+财报稿+PPT 说三遍 = 1 簇")
    w("   │  ③ 闸3 隔离      relative 命题**只吃 direct 维度**、且只吃 cohort 内公司")
    w("   ↓")
    w("EvidenceCluster")
    w("   │  ④ 判读（LLM）   逐簇给 ＋/－/・ 与理由。**唯一需要理解语义的一步**")
    w("   ↓                  判读器看不到价格、持仓、上一次结论，也看不到知识库")
    w("   │  ⑤ 闸2 立场      只有 PRIMARY 类型贡献立场类；<2 类 → 不给确定结论")
    w("   ↓")
    w("ClaimAssessment ──┬─→ 因子证据包 → 截面结构分析师 → moat_pricing → 复合排名/权重")
    w("                  ├─→ 产业链证据块 → 行业分析师 → 层「供需」「定价权」+ 景气分")
    w("                  └─→ （经周报存档）→ Chief → 日级决策")
    w("```")
    w("")
    w("### 4.1 本轮各条命题卡在哪一步")
    w("")
    w("| 命题 | 结论 | 卡点 | 依据 |")
    w("|---|---|---|---|")
    for c in layer.claims:
        p = latest.get(c.id)
        if not p:
            w(f"| `{c.id}` | — | **未落库** | 跑一次 `ats evidence report` |")
            continue
        zero = [x.key for x in c.concepts if x.key in empty_concepts]
        blind = [x.key for x in c.concepts
                 if c.kind == "relative" and not x.direct]
        bits = []
        if p["verdict"] in ("supportive", "contradicted", "resolved"):
            bits.append("无")
        if p.get("unresolved_reason"):
            bits.append(STUCK.get(p["unresolved_reason"], p["unresolved_reason"]))
        if zero:
            bits.append(f"**①抽取无料**（{', '.join('`%s`' % k for k in zero)} 零条）")
        if blind:
            bits.append(f"**③隔离闸**（{', '.join('`%s`' % k for k in blind)} 非 direct）")
        if p["witnesses_expected"] and (
                p["witnesses_reported"] < p["witnesses_expected"]):
            bits.append(f"覆盖 {p['witnesses_reported']}/{p['witnesses_expected']}")
        # A cross-section has no aggregate support/refute — printing "0 / 0" next to a
        # table of per-entity readings reads as a bug, which is the same reason the
        # weekly report suppresses that line for relative claims.
        if c.kind == "relative":
            basis = f"{len(p.get('entity_readings') or [])} 家有读数"
            corro = sum(1 for e in (p.get('entity_readings') or [])
                        if e.get("basis") == "corroborated")
            if corro:
                basis += f"，其中 {corro} 家有交叉印证"
        else:
            basis = (f"支持 {p['support_score']:.0f} / 反驳 {p['refute_score']:.0f}")
        w(f"| `{c.id}` | {p['verdict']} | {' · '.join(bits) or '无'} "
          f"| {basis}，{p['stance_classes']} 类立场 |")
    w("")
    w("### 4.2 跨立场佐证发生在哪里")
    w("")
    w("> 一条读数的「依据强度」是 `corroborated`（有交叉印证）而不是 `self_reported`"
      "（仅自述）时，说明**有人替它作证**——那才是这套机制相对「读一家财报」的增量。")
    w("")
    found = False
    for c in layer.claims:
        p = latest.get(c.id) or {}
        for e in p.get("entity_readings") or []:
            if e.get("basis") == "corroborated":
                found = True
                others = [s for s in (e.get("speakers") or []) if s != e["entity"]]
                w(f"- **{e['entity']}**（`{c.id}`）：{e['standing']} · "
                  f"作证方 **{', '.join(others) or '—'}** · {e['stance_classes']} 类立场")
                w(f"  - {esc(e.get('reason'))}")
    if not found:
        w("（本层暂无跨立场佐证的读数——所有读数都是各家自述。）")
    w("")
    w("### 4.3 哪些东西**没有**影响结论")
    w("")
    w(f"- **未映射池的 {len(un)} 条**：抽出来了，归不到任何已声明维度 → 本层命题一条都"
      f"够不着。它们只是归纳引擎的原料。")
    w("- **知识库笔记**：`config/knowledge/*.md` 只到达截面结构分析师与行业分析师，"
      "**判读器看不到**——判读只能基于眼前的证据，不能被通用先验带着走。")
    w("- **持仓与价格**：判读器看不到，也看不到这条命题上一次的结论。")
    w("- **Chief 看到的是上一次周报的存档**：3.1/3.2 的新证据要等下一次周度作业。")
    w("")

    db.close()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"📝 {out}  ({len(L)} 行)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
