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
                "falsified": "⛔ 已证伪", "mixed": "⚠️ 分歧", "unknown": "· 未定",
                "resolved": "📊 已比较"}
# `mixed` covers two states that mean opposite things. Only one of them is a
# disagreement; the other is one-sided evidence that every witness happens to share a
# vantage point on, which the engine declines to confirm. Labelling both 「分歧」 told
# the reader the evidence conflicted when it did not.
UNRESOLVED_MARK = {"single_stance": "◐ 未确认（立场单一）", "dissent": "⚠️ 分歧"}


def verdict_mark(a) -> str:
    """The headline label for one assessment, disambiguating the two kinds of `mixed`."""
    if a.verdict == "mixed":
        return UNRESOLVED_MARK.get(getattr(a, "unresolved_reason", ""), "⚠️ 分歧")
    return VERDICT_MARK.get(a.verdict, a.verdict)
STANDING_MARK = {"strong": "强", "neutral": "中", "weak": "弱", "unknown": "—"}
BASIS_CN = {"corroborated": "有交叉印证", "self_reported": "仅自述", "thin": "证据薄"}
TYPE_CN = {"reported_actual": "已实现", "guidance": "指引", "counterparty": "对手方",
           "regulatory": "监管", "research": "研究", "media": "媒体", "market": "市场"}
STANCE_CN = {"customer": "客户", "supplier": "供应商", "competitor": "竞对",
            "incumbent": "当事方", "regulator": "监管/统计机构"}


def _claim_section(cfg, store, assessments_by_layer, rows_by_id) -> list[str]:
    lines = ["## 命题印证", "",
             "> 证据的筛选、去重、立场统计与记分由确定性引擎完成；每条判读都附理由，"
             "**结论应当对着理由争论，而不是对着数字**。\n"
             "> **竞争者扩产不等于我方失份额**——扩产不是 direct 维度，进不了截面比较。"
             "截面命题不断言真假，只回答「这个位置在同业之间怎么分布」。", ""]
    any_claim = False
    for layer in cfg.layers:
        assessments = assessments_by_layer.get(layer.key) or []
        if not assessments:
            continue
        any_claim = True
        lines += [f"### {layer.label}", ""]
        for a in assessments:
            claim = next((c for c in layer.claims if c.id == a.claim_id), None)
            kind = "截面比较" if (claim and claim.kind == "relative") else "共同需求"
            subj = (f" · 比较 {', '.join(claim.entities)}"
                    if claim and claim.entities else "")
            lines += [
                f"**{verdict_mark(a)}｜{claim.statement if claim else a.claim_id}**",
                "",
                f"- 类型 {kind}{subj} · 证人覆盖 **{a.coverage}** · 独立证据簇 "
                f"{a.evidence_clusters} · 立场 {a.stance_classes} 类",
            ]
            if not a.entity_readings:
                # A cross-section has no aggregate support/refute — printing "0 / 0"
                # under a table that clearly says otherwise reads as a bug.
                lines.append(
                    f"- 支持 {a.support_score:.0f} / 反驳 {a.refute_score:.0f}"
                    + (f" · 异议方 {', '.join(a.dissenters)}" if a.dissenters else ""))
            if a.silent_witnesses:
                # Silence is a gap, not neutrality: a witness who said nothing is the
                # difference between "checked and fine" and "never checked".
                lines.append(f"- ⚠️ 本期未发声：{', '.join(a.silent_witnesses)}")
            if a.note:
                # The engine's note repeats the silent-witness list for CLI readers;
                # the report already gives it its own line.
                lines.append(f"- {a.note.split(' · 未发声：')[0]}")
            if a.entity_readings:
                # A cross-section's answer IS this table. Basis travels with each row:
                # a self-reported "strong" must not read like a corroborated one.
                lines += ["", "  | 公司 | 位置 | 依据强度 | 证据 | 说话人 | 理由 |",
                          "  |---|---|---|---|---|---|"]
                for r in a.entity_readings:
                    lines.append(
                        f"  | **{r.entity}** | {STANDING_MARK.get(r.standing, r.standing)} "
                        f"| {BASIS_CN.get(r.basis, r.basis)} | {r.evidence_clusters} 簇/"
                        f"{r.stance_classes} 类 | {', '.join(r.speakers) or '—'} "
                        f"| {r.reason or '—'} |")
            if a.judgements:
                # Every polarity call with its reason. This is the section a reader
                # actually argues with: the verdict is arithmetic over these, so
                # disagreeing with the conclusion means disagreeing with a line here.
                mark = {"support": "＋支持", "refute": "－反驳", "neutral": "・中性"}
                lines += ["", "  | 判读 | 说话人 | 维度 | 理由 |", "  |---|---|---|---|"]
                for j in a.judgements:
                    lines.append(f"  | {mark.get(j.polarity, j.polarity)} | {j.speaker} | "
                                 f"{j.concept or '—'} | {j.reason or '—'} |")
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


def _sources_section(store) -> list[str]:
    """Independent (non-self-reported) data feeds behind the claims above.

    Placed right after the claims because it is their grounding: a company saying its
    own ASP rose is one interested party; a customs bureau or a price-reporting agency
    saying the same is a different witness stance, and it is the only way some claims
    (`hbm_pricing_still_expanding`) clear the stance-diversity gate at all.

    Deliberately reads the LEDGER, not a live fetch — `render()` has never made a
    network call, and it must not start now. The claims in this same report were
    computed against stored data; showing a live peek that might have since revised
    would let the report disagree with itself. Freshness is answered honestly instead:
    each source's most recent stored reading, and whether its last collection attempt
    (`ats evidence collect`) succeeded.
    """
    from . import sources as chain_sources

    declared = chain_sources.load_sources()
    if not declared:
        return []
    lines = ["## 第三方数据源", "",
             "> 非自述、独立于任何一家公司的读数——这是部分命题唯一能拿到跨立场佐证的"
             "来源。取不到数据记成缺口，不会悄悄变成沉默（`ats evidence collect` / "
             "`chain/sources.py`）。下面是**台账里已存的**读数，不是现抓的——"
             "本报告不发网络请求，且上面的判读正是对着这批存量数据算出来的。", ""]
    for s in declared:
        lines += [f"### {s.label or s.id}（`{s.id}`）", ""]
        # Pulled from the adapter's own module docstring so this rationale can never
        # drift from the code that actually justifies the source being on-mechanism.
        try:
            import importlib

            mod = importlib.import_module(f"..data.sources.{s.adapter}", __package__)
            doc = (mod.__doc__ or "").strip()
            rationale = doc.split("\n\n")[0].replace("\n", " ") if doc else ""
        except Exception:  # noqa: BLE001 — a missing/broken adapter must not break this
            rationale = ""
        if rationale:
            lines += [rationale, ""]
        lines += [f"- 适配器 `{s.adapter}` · 立场 **{STANCE_CN.get(s.stance, s.stance)}** "
                  f"· 类型 {TYPE_CN.get(s.observation_type, s.observation_type)} "
                  f"· 更新频率 {s.cadence}",
                  f"- 绑定维度：{', '.join(f'`{c}`' for c in s.concepts) or '（无）'}", ""]

        # collect() saves one physical row PER declared concept — the same print filed
        # under `supply_tightness` and again under `hbm_demand` is two DB rows so both
        # claims can find it, but showing "what this source said" twice reads like a
        # duplication bug. Fetch generously, then dedupe on the reading itself.
        raw = store.observations(entity=s.entity, limit=60)
        seen: set[tuple] = set()
        rows = []
        for r in raw:
            key = (r.get("period"), r.get("metric"), r.get("direction"), r.get("evidence_span"))
            if key in seen:
                continue
            seen.add(key)
            rows.append(r)
            if len(rows) >= 12:
                break
        # `collect()` only ever writes a `source_documents` row here on FAILURE (a
        # successful fetch has nothing analogous to save — its output is the
        # observations themselves), so the latest row, if any, is always an attempt
        # that came back empty.
        attempts = store.documents(entity=s.entity, ok_only=False, limit=1)
        last_fail = attempts[0] if attempts else None
        if not rows:
            note = f"（最近一次尝试 {last_fail['fetched_at']}：{last_fail['note']}）" if last_fail else ""
            lines += [f"⚠️ 台账里还没有这个源的观测{note}", ""]
            continue
        if last_fail and last_fail["fetched_at"] > raw[0]["observed_at"]:
            lines += [f"⚠️ 最近一次抓取失败（{last_fail['fetched_at']}：{last_fail['note']}）"
                      f"——以下是上一次成功抓到的读数", ""]
        lines += ["| 期间 | 读数 | 方向 | 原文 |", "|---|---|---|---|"]
        for r in rows:
            lines.append(f"| {r.get('period') or '—'} | {r.get('metric') or '—'} "
                         f"| {r.get('direction') or '—'} | {(r.get('evidence_span') or '')[:110]} |")
        lines.append("")
    lines += _article_sources_section(store)
    return lines


def _article_sources_section(store) -> list[str]:
    """Article sources — the prose half of the same grounding, same no-network rule.

    Shows what could NOT be read as prominently as what could. A publisher's paywall
    widening and a publisher going quiet look identical in a success-only listing, and
    they call for opposite responses: one means find another source, the other means the
    story really has stopped moving.
    """
    from . import articles as chain_articles

    declared = chain_articles.load_article_sources()
    if not declared:
        return []
    lines = ["### 文章类第三方源", "",
             "> 与上面的数列源同为非自述读数，区别只在形态：数列靠公式变成观测，"
             "文章要被读。**取不到正文的文章单列**——付费墙扩大与「这家最近没什么可说的」"
             "在只报成功的清单里长得一模一样，但该做的应对正好相反。", ""]
    for s in declared:
        lines += [f"#### {s.label or s.id}（`{s.id}`）", ""]
        try:
            import importlib

            mod = importlib.import_module(f"..data.articles.{s.adapter}", __package__)
            doc = (mod.__doc__ or "").strip()
            rationale = doc.split("\n\n")[0].replace("\n", " ") if doc else ""
        except Exception:  # noqa: BLE001 — a missing/broken adapter must not break this
            rationale = ""
        if rationale:
            lines += [rationale, ""]
        lines += [f"- 适配器 `{s.adapter}` · 立场 **{STANCE_CN.get(s.stance, s.stance)}** "
                  f"· 更新频率 {s.cadence}", ""]

        docs = store.documents(entity=s.entity, ok_only=False, limit=200)
        ok = [d for d in docs if d.get("ok") and d.get("doc_type") == s.doc_type]
        bad = [d for d in docs if not d.get("ok") and d.get("period")]
        if not ok and not bad:
            lines += ["⚠️ 台账里还没有这个源的文章（`ats evidence articles` 尚未跑过）", ""]
            continue
        if ok:
            lines += ["| 抓取时间 | 文章 | 原文 |", "|---|---|---|"]
            for d in ok[:12]:
                lines.append(f"| {(d.get('fetched_at') or '')[:10]} "
                             f"| {(d.get('period') or '—')[:76]} "
                             f"| [链接]({d.get('source_url') or ''}) |")
            lines.append("")
        if bad:
            lines += [f"🚫 **{len(bad)} 篇取不到正文**（付费或模板变更）——记成缺口，不是沉默：", ""]
            for d in bad[:8]:
                lines.append(f"- `{(d.get('period') or '—')[:70]}` · {d.get('note') or ''}")
            lines.append("")
    return lines


def _kb_section(cfg, store, *, as_of) -> list[str]:
    """What the static knowledge may be missing (chain/kb_review.py).

    Placed after the proposals because it is the same kind of output — a list only a
    person may act on — and before the failures, which are plumbing.
    """
    from . import kb_review

    try:
        findings = kb_review.review(cfg, store, now=as_of)
    except Exception as exc:  # noqa: BLE001 — a detector must never break the report
        log.warning("kb review section skipped: %s", exc)
        return []
    return kb_review.as_section(findings)


def _kb_audit_section(cfg, store) -> list[str]:
    """Whether the knowledge base was USED correctly (chain/kb_audit.py).

    Sits right after the review section because they are the two halves of one
    question: is the knowledge right, and is it being applied.
    """
    from . import kb_audit

    try:
        return kb_audit.as_section(kb_audit.audit(cfg, store))
    except Exception as exc:  # noqa: BLE001 — an audit must never break the report
        log.warning("kb audit section skipped: %s", exc)
        return []


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
    from .sources import source_entities_for as _source_entities

    ccfg = cfg.review.get("corroboration", {})
    assessments_by_layer, rows_by_id = {}, {}
    for layer in cfg.layers:
        if not layer.claims:
            continue
        rows_by_entity = {}
        for claim in layer.claims:
            ents = claim.expected_witnesses() | {w.entity.upper() for w in claim.witnesses}
            ents |= set(claim.entities)
            ents |= _source_entities(claim)
            for e in ents:
                if e not in rows_by_entity:
                    rows_by_entity[e] = store.observations(entity=e, limit=200)
        # Stamp the snapshot with the REPORT's as_of, not wall-clock: otherwise
        # re-rendering the same report writes a second row instead of replacing
        # one, and the history reads as change when nothing changed.
        assessments = assess_layer(layer, rows_by_entity, cfg=ccfg, as_of=as_of)
        assessments_by_layer[layer.key] = assessments
        # Snapshot the verdicts. Versioned by (claim_id, as_of) so the table answers
        # "when did this turn from mixed to contradicted" — the only question a verdict
        # history exists for. Best-effort: the report must still render if it fails.
        for a in assessments:
            try:
                store.save_claim_assessment(a)
            except Exception as exc:  # noqa: BLE001
                log.warning("claim assessment persist skipped for %s: %s", a.claim_id, exc)
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
    lines += _sources_section(store)
    lines += _pool_section(store, ind_cfg or {})
    lines += _proposal_section(store)
    lines += _kb_section(cfg, store, as_of=as_of)
    lines += _kb_audit_section(cfg, store)
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
