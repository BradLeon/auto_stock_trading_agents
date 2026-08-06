"""Obsidian markdown report for the chain-evidence ledger.

Always creates its own file (产业链证据-<label>-<date>.md) — never appends to the
user's own notes. Same-day reruns overwrite (idempotent). Unset/missing output dir
degrades to None. Mirrors agents/sector/report.py.

The report is deliberately organised around what a reader has to be able to check:
every verdict shows its coverage and who stayed silent, and every conclusion carries
the source sentences it rests on. A verdict you cannot trace back to a filing is not
usable — and a claim resting on one witness must look different from one resting on
four, or you cannot tell confidence from coverage.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("ats.chain.report")

VERDICT_MARK = {"supportive": "✅ 印证", "contradicted": "⛔ 反证",
                "falsified": "⛔ 已证伪", "mixed": "⚠️ 分歧", "unknown": "· 未定"}
TYPE_CN = {"reported_actual": "已实现", "guidance": "指引", "counterparty": "对手方",
           "regulatory": "监管", "research": "研究", "media": "媒体", "market": "市场"}


def _claim_section(cfg, store, assessments_by_layer, rows_by_id) -> list[str]:
    lines = ["## 命题印证", "",
             "> 结论由确定性引擎算出（去重 → 立场 → common/relative 隔离），"
             "LLM 只负责把事实归到维度上。**竞争者扩产不等于我方失份额**——"
             "没有关于本方的直接读数时，相对份额命题保持 unknown。", ""]
    any_claim = False
    for layer in cfg.layers:
        assessments = assessments_by_layer.get(layer.key) or []
        if not assessments:
            continue
        any_claim = True
        lines += [f"### {layer.label}", ""]
        for a in assessments:
            claim = next((c for c in layer.claims if c.id == a.claim_id), None)
            kind = "相对份额" if (claim and claim.kind == "relative") else "共同需求"
            subj = f" · 主体 {claim.subject}" if claim and claim.subject else ""
            lines += [
                f"**{VERDICT_MARK.get(a.verdict, a.verdict)}｜{claim.statement if claim else a.claim_id}**",
                "",
                f"- 类型 {kind}{subj} · 证人覆盖 **{a.coverage}** · 独立证据簇 "
                f"{a.evidence_clusters} · 立场 {a.stance_classes} 类",
                f"- 支持 {a.support_score:.0f} / 反驳 {a.refute_score:.0f}"
                + (f" · 异议方 {', '.join(a.dissenters)}" if a.dissenters else ""),
            ]
            if a.silent_witnesses:
                # Silence is a gap, not neutrality: a witness who said nothing is the
                # difference between "checked and fine" and "never checked".
                lines.append(f"- ⚠️ 本期未发声：{', '.join(a.silent_witnesses)}")
            if a.note:
                # The engine's note repeats the silent-witness list for CLI readers;
                # the report already gives it its own line.
                lines.append(f"- {a.note.split(' · 未发声：')[0]}")
            spans = [rows_by_id[i] for i in a.observation_ids if i in rows_by_id][:6]
            if spans:
                lines += ["", "  | 说话人 | 关于 | 维度 | 方向 | 类型 | 原文 |",
                          "  |---|---|---|---|---|---|"]
                for r in spans:
                    who = r.get("source_entity") or r.get("entity") or ""
                    about = r.get("entity") or ""
                    lines.append(
                        f"  | {who} | {about} | {r.get('concept') or '—'} | "
                        f"{r.get('direction') or '—'} | "
                        f"{TYPE_CN.get(r.get('observation_type'), r.get('observation_type') or '—')} | "
                        f"{(r.get('evidence_span') or '')[:90]} |")
            lines.append("")
    if not any_claim:
        lines += ["（该行业配置里还没有 claims —— 见 docs/CHAIN_EVIDENCE.md）", ""]
    return lines


def _pool_section(store, ind_cfg: dict) -> list[str]:
    from .induction import should_induce

    pool = store.unmapped_observations(limit=500)
    fire, reason = should_induce(pool, ind_cfg)
    lines = ["## 未映射池（涌现命题的原料）", "",
             "> 归不到任何已声明维度的观测。**留空是合法结果**——不硬套，"
             "攒够分量才触发一次归纳（无 cron，纯时间流逝不触发）。", "",
             f"- 当前 {len(pool)} 条 · 触发判定：{reason}", ""]
    if pool:
        lines += ["| 说话人 | 指标 | 方向 | 原文 |", "|---|---|---|---|"]
        for r in pool[:12]:
            who = r.get("source_entity") or r.get("entity") or ""
            lines.append(f"| {who} | {r.get('metric') or '—'} | {r.get('direction') or '—'} "
                         f"| {(r.get('evidence_span') or '')[:90]} |")
        lines.append("")
    return lines


def _proposal_section(store) -> list[str]:
    pending = store.claim_proposals(status="pending", limit=10)
    lines = ["## 待确认命题", ""]
    if not pending:
        lines += ["（无）", ""]
        return lines
    lines += ["> 系统提议，**只有你能把它写进配置**——坐标系只有人能扩，"
              "否则模型今天发明一个维度、明天换一个，截面因子的历史就不再可比。", ""]
    for p in pending:
        lines += [f"**{p['statement']}**", "",
                  f"- id `{p['id']}` · 类型 {p['kind']}"
                  + (f" · 涉及层 {p['layer_hint']}" if p.get("layer_hint") else ""),
                  f"- 采纳：`ats evidence review {p['id']} --accept`"
                  f" · 拒绝：`ats evidence review {p['id']} --note \"理由\"`",
                  "- 触发它的观测已冻结，采纳后不得用于印证它自己", ""]
    return lines


def _failure_section(store) -> list[str]:
    fails = store.observation_failures(limit=8)
    if not fails:
        return []
    return (["## 抽取失败（保留而非记成「零观测」）", "",
             "> 「读不到」和「没说」是两种状态，只有后者可以当作证据缺失。", "",
             "| 实体 | 文档 | 原因 |", "|---|---|---|"]
            + [f"| {f['entity']} | {f['document_id']} | {f['reason']} |" for f in fails]
            + [""])


def render(cfg, store, *, as_of, ind_cfg: dict | None = None) -> str:
    """Build the markdown. `cfg` is a SectorConfig; `store` the TradingMemory."""
    from .corroborate import assess_layer

    ccfg = cfg.review.get("corroboration", {})
    assessments_by_layer, rows_by_id = {}, {}
    for layer in cfg.layers:
        if not layer.claims:
            continue
        rows_by_entity = {}
        for claim in layer.claims:
            ents = claim.expected_witnesses() | {w.entity.upper() for w in claim.witnesses}
            if claim.subject:
                ents.add(claim.subject)
            for e in ents:
                if e not in rows_by_entity:
                    rows_by_entity[e] = store.observations(entity=e, limit=200)
        assessments_by_layer[layer.key] = assess_layer(layer, rows_by_entity, cfg=ccfg)
        for rows in rows_by_entity.values():
            for r in rows:
                rows_by_id[r["id"]] = r

    lines = [
        f"# 🤖 产业链证据 — {cfg.label}（{as_of:%Y-%m-%d}）",
        "",
        "> 由 `ats sector crosssection` / 周报自动生成。读**不持有**公司的财报，"
        "把它们变成可核对的观测——利润是流动的，HBM 的供需由三家共同决定，"
        "只读自己持有的那家等于用三分之一的证据下注。",
        "> 设计见 `docs/CHAIN_EVIDENCE.md`。",
        "",
    ]
    lines += _claim_section(cfg, store, assessments_by_layer, rows_by_id)
    lines += _pool_section(store, ind_cfg or {})
    lines += _proposal_section(store)
    lines += _failure_section(store)
    total = len(store.observations(limit=1000))
    lines += ["---", f"*观测总数 {total} · 命题 "
              f"{sum(len(v) for v in assessments_by_layer.values())} 条*", ""]
    return "\n".join(lines)


def write(cfg, store, *, as_of, ind_cfg: dict | None = None) -> Path | None:
    if not cfg.output_dir:
        log.info("chain evidence report: output_dir unset — skipped")
        return None
    folder = Path(cfg.output_dir)
    if not folder.is_dir():
        log.warning("chain evidence report: output_dir missing — skipped: %s", folder)
        return None
    path = folder / f"产业链证据-{cfg.label}-{as_of:%Y-%m-%d}.md"
    path.write_text(render(cfg, store, as_of=as_of, ind_cfg=ind_cfg), encoding="utf-8")
    return path
