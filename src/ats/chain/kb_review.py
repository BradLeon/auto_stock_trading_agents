"""Knowledge-base review — surface what the static knowledge is missing.

`config/knowledge/*.md` and `signal_chain` are annual-tenor knowledge: they change
slowly, which is exactly why nothing ever prompts a person to revisit them. A stale
anchor is worse than a missing one — the structure analyst does not doubt the KB, it
copies it.

So the detection is done by the data, not by a calendar reminder to re-read six files.
Six signals, all computed from rows we already store:

  ① 盲区标记   the structure analyst already writes "KB 未覆盖" and nobody collects it
  ② 未映射聚集 a theme recurring across companies AND quarters with no home
  ③ 未声明关系 an observation about a company absent from the speaker's signal_chain
  ④ 归因失败   a source judged neutral over and over for "无法归因"
  ⑤ 陌生实体   a name we have evidence on that appears in no config
  ⑥ 久未复核   a KB file untouched while its cohort accumulated evidence

Everything here is deterministic — no model call. ② uses token overlap on metric names,
which is a proxy for meaning, not meaning itself; it is deliberately crude so that what
it flags can be checked by eye.

The output is a TODO list for a person. Nothing here edits config, and nothing here
feeds a score: agents propose, only a human writes knowledge down (DESIGN.md §98,226).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("ats.chain.kb_review")

# Two different ways the structure analyst can fail to score a name, and they need
# different fixes. Once the knowledge base stopped carrying company-level facts (it
# gives criteria now, not rankings), "no company facts in the KB" became the EXPECTED
# state — collecting it as a KB gap would flag the design decision itself on every
# layer, every week. What is missing in that case is evidence, not knowledge.
BLIND_MARKERS = ("KB 未覆盖", "KB未覆盖", "知识库未覆盖", "知识库未涵盖", "无 KB", "未被知识库")
THIN_EVIDENCE_MARKERS = ("缺本期证据", "无本期证据", "缺公司级证据", "未提供.*公司级")
# Reasons the adjudicator gives when a source cannot be tied to the claim's subject.
# Used as a NOTE, never as a precondition: making a detector depend on the model's
# phrasing means it goes quiet the moment the model rewords, which is how the Taiwan
# binding survived four rounds unnoticed in the first place.
UNATTRIBUTABLE = ("无法归因", "难以归因", "不能归因", "无法对应", "口径", "无法区分")
# Metric-name tokens carrying no topic. Two groups, and the second is the load-bearing
# one: the unmapped pool is overwhelmingly GAAP line items lifted off earnings releases,
# so without banning the income statement's own vocabulary this detector reports
# "margin, cash, income" every week forever. Topical words that happen to appear in a
# financial line (`data_center_revenue`, `inventory`) are deliberately NOT banned — a
# recurring segment nobody declared is exactly what we are looking for.
STOPWORDS = {
    "yoy", "mom", "qoq", "growth", "rate", "ratio", "total", "value", "change",
    "guidance", "outlook", "commentary", "count", "level", "index", "usd",
    "pct", "percent", "avg", "the", "and", "for", "per", "adjusted",
    # income statement / balance sheet / cash flow boilerplate
    "revenue", "revenues", "sales", "margin", "margins", "gross", "operating", "opex",
    "income", "earnings", "eps", "net", "cash", "flow", "free", "diluted", "basic",
    "gaap", "share", "shares", "repurchase", "buyback", "dividend", "capital",
    "returns", "debt", "loans", "receivable", "payable", "investments", "investment",
    "funds", "equity", "fair", "term", "long", "short", "annual", "deferred", "assets",
    "liabilities", "expense", "expenses", "tax", "profit", "loss", "balance", "sheet",
    "quarter", "quarterly", "fiscal", "year", "sequential", "amount", "million",
    "billion", "dollars", "consolidated", "reported", "non",
}
# Two tokens of one phrase (`data` / `center`) cluster the same rows and would be
# reported as two findings. Above this Jaccard overlap the smaller one is dropped.
CLUSTER_OVERLAP = 0.7

DEFAULTS = {
    "min_cluster_entities": 2,     # a theme one company keeps repeating is that company's
    "min_cluster_periods": 2,      # ...and a one-quarter spike is news, not knowledge
    "min_cluster_rows": 3,
    "min_speaker_judgements": 4,   # below this, "always neutral" is not yet a pattern
    "speaker_neutral_share": 0.8,
    "min_concept_judgements": 6,   # a dimension needs more rounds before we call it inert
    "concept_neutral_share": 0.9,
    "stale_days": 90,              # a quarter untouched is when a re-read is worth it
    "stale_min_observations": 30,  # ...but only if evidence actually accumulated
    "max_per_signal": 8,
}


@dataclass
class Finding:
    """One thing a person should look at, and what it would mean if true."""

    signal: str            # ① .. ⑥ — which detector fired
    subject: str           # the name / theme / file the finding is about
    detail: str            # what was observed, with counts
    action: str            # what updating it would look like
    evidence: list[str] = field(default_factory=list)   # quotable spans, ≤3


def _canon(sym: str) -> str:
    from ..config import canonical_entity

    return canonical_entity((sym or "").upper())


def _tokens(metric: str) -> set[str]:
    parts = re.split(r"[^a-z0-9一-鿿]+", (metric or "").lower())
    return {p for p in parts if len(p) > 2 and p not in STOPWORDS and not p.isdigit()}


# --- ① blind spots ------------------------------------------------------- #
def blind_spots(cfg, store) -> list[Finding]:
    """The structure analyst's own "I could not score this" admissions.

    Already written into every basket row's rationale and read by nobody — the cheapest
    signal in the set, and the only one that is the analyst telling us directly.

    Split by which thing was missing. A KB gap is fixed by writing criteria; an evidence
    gap is fixed by declaring a claim or a witness, and no amount of KB editing will
    clear it. Reporting them together sends the reader to the wrong file.
    """
    label_of = {ly.key: ly.label for ly in cfg.layers}
    blind: dict[str, list[str]] = {}
    thin: dict[str, list[str]] = {}
    seen_layers: set[str] = set()
    # Newest-first, first basket per layer wins: a weekly run usually scores ONE layer,
    # so the current view of L3 may sit three reviews back. Reading only the latest
    # review reports "no blind spots" for every layer that simply did not run.
    for review in store.sector_review_history(cfg.name, limit=8):
        for basket in review.baskets:
            if basket.layer_key in seen_layers:
                continue
            seen_layers.add(basket.layer_key)
            for row in basket.rows:
                why = row.rationale or ""
                if any(m in why for m in BLIND_MARKERS):
                    blind.setdefault(basket.layer_key, []).append(row.symbol)
                elif any(re.search(m, why) for m in THIN_EVIDENCE_MARKERS):
                    thin.setdefault(basket.layer_key, []).append(row.symbol)
    out = []
    for key, names in sorted(blind.items()):
        layer = next((ly for ly in cfg.layers if ly.key == key), None)
        has_kb = bool(layer and layer.structure_notes)
        out.append(Finding(
            signal="① 盲区标记",
            subject=f"{label_of.get(key, key)}：{', '.join(sorted(set(names)))}",
            detail=f"结构分析师自报 {len(set(names))} 个标的**没有可用判据**"
                   + ("（该层已有 KB，但未覆盖这些名字所属的环节）" if has_kb
                      else "（该层没有任何 KB）"),
            action=("补写这些名字所属子层的知识库" if has_kb
                    else f"为 {key} 新建 config/knowledge/*.md 并在 structure_notes 里挂上")))
    for key, names in sorted(thin.items()):
        out.append(Finding(
            signal="① 盲区标记",
            subject=f"{label_of.get(key, key)}：{', '.join(sorted(set(names)))}（缺证据）",
            detail=f"{len(set(names))} 个标的**有判据但没有本期读数**可套——"
                   f"判据管怎么权衡，排序要靠台账里的实际表述",
            action="**这不是知识库的问题**：给该层立命题、补 witness_roster，"
                   "或等下一次财报把读数补上"))
    return out[: DEFAULTS["max_per_signal"]]


# --- ② unmapped clustering ----------------------------------------------- #
def unmapped_clusters(store, cfg_over: dict | None = None) -> list[Finding]:
    """Themes the system keeps meeting and has nowhere to put.

    Induction reads the same pool but only ever proposes a CLAIM. Much of what recurs
    there is not falsifiable — "CoWoS is on every HBM shipment's path" is topology, and
    topology belongs in the KB. The routing test is in the action line, not automated:
    falsifiable + nameable witnesses -> claim; descriptive -> KB.
    """
    c = {**DEFAULTS, **(cfg_over or {})}
    rows = store.unmapped_observations(limit=500)
    by_token: dict[str, list[dict]] = {}
    for r in rows:
        for tok in _tokens(r.get("metric") or ""):
            by_token.setdefault(tok, []).append(r)
    scored = []
    for tok, group in by_token.items():
        ents = {_canon(r.get("source_entity") or r.get("entity") or "") for r in group}
        ents.discard("")
        periods = {r.get("period") for r in group if r.get("period")}
        if (len(group) < c["min_cluster_rows"] or len(ents) < c["min_cluster_entities"]
                or len(periods) < c["min_cluster_periods"]):
            continue
        scored.append(((len(ents), len(periods), len(group)),
                       {r["id"] for r in group if r.get("id")}, Finding(
            signal="② 未映射聚集",
            subject=tok,
            detail=f"{len(group)} 条未映射观测 · 跨 {len(ents)} 家（{', '.join(sorted(ents))}）"
                   f" · 跨 {len(periods)} 个期间",
            action="可证伪且能点名证人 → 立命题；描述性拓扑/机制 → 写进知识库",
            evidence=[(r.get("evidence_span") or "")[:100] for r in group[:3]])))
    # Spread across companies first, then across quarters: a theme three companies each
    # mentioned once is knowledge; thirty rows from one filing is one filing.
    scored.sort(key=lambda kv: kv[0], reverse=True)
    out, taken = [], []
    for _, ids, finding in scored:
        if any(ids & prior and len(ids & prior) / len(ids | prior) >= CLUSTER_OVERLAP
               for prior in taken):
            continue
        taken.append(ids)
        out.append(finding)
    return out[: c["max_per_signal"]]


# --- ③ undeclared relations ---------------------------------------------- #
def undeclared_relations(cfg, store) -> list[Finding]:
    """A speaker talked about a company its own signal_chain does not list.

    This is how new topology arrives: `AMD → ANTHROPIC` was found this way, and Anthropic
    was in no chain at all. Annual-tenor by nature, so it is the highest-value increment
    the detectors produce.
    """
    from ..config import _config_dir, _load_yaml

    known: dict[str, set[str] | None] = {}

    def chain_of(sym: str) -> set[str] | None:
        """Curated counterparties, or None when nobody wrote a chain for this speaker.

        Reads the raw YAML rather than `load_pead_config`, which back-fills a peer list
        from the sector layer when the file declares none (`config.py:_derive_signal_chain`).
        That fallback keeps the PEAD report from rendering empty, but it carries no role
        and no trailing comment — and the comment is the whole point here: `relation_hint`
        is what lets 「我们最大的内存合作伙伴」 resolve to a company. Treating the
        fallback as a declaration would make this detector permanently silent.
        """
        if sym not in known:
            raw = _load_yaml(_config_dir() / "pead" / f"{sym.upper()}.yaml")
            chain = raw.get("signal_chain") or []
            known[sym] = {_canon(x.get("symbol", "")) for x in chain} or None
        return known[sym]

    pairs: dict[tuple[str, str], list[dict]] = {}
    chainless: dict[str, set[str]] = {}
    for r in store.observations(limit=2000):
        speaker = _canon(r.get("source_entity") or "")
        about = _canon(r.get("entity") or "")
        if not speaker or not about or speaker == about:
            continue
        chain = chain_of(speaker)
        if chain is None:
            # One finding for the speaker, not one per counterparty: "AMD never
            # declared a chain" is a single thing to fix, and listing its six
            # counterparties as six missing links hides that.
            chainless.setdefault(speaker, set()).add(about)
            continue
        if about not in chain:
            pairs.setdefault((speaker, about), []).append(r)

    out = []
    for speaker, mentioned in sorted(chainless.items(), key=lambda kv: -len(kv[1])):
        out.append(Finding(
            signal="③ 未声明关系",
            subject=f"{speaker}（无 signal_chain）",
            detail=f"{speaker} 谈到了 {', '.join(sorted(mentioned))}，"
                   f"但没人给它写过 signal_chain（只有同层同业的自动兜底，无 role 无注释）",
            action=f"建 config/pead/{speaker}.yaml 的 signal_chain（带区分性行尾注释）；"
                   f"注释是 relation_hint 区分「上游 HBM 主供」与「上游 EUV」的唯一依据"))
    for (speaker, about), group in sorted(pairs.items(), key=lambda kv: -len(kv[1])):
        out.append(Finding(
            signal="③ 未声明关系",
            subject=f"{speaker} → {about}",
            detail=f"{len(group)} 条观测里 {speaker} 谈到 {about}，"
                   f"但 {about} 不在 {speaker} 的 signal_chain 中",
            action=f"若关系成立：补 config/pead/{speaker}.yaml 的 signal_chain"
                   f"（带区分性行尾注释），必要时写进该层知识库的「价值链分工」",
            evidence=[(r.get("evidence_span") or "")[:100] for r in group[:2]]))
    return out[: DEFAULTS["max_per_signal"]]


# --- ④ attribution failure ------------------------------------------------ #
def attribution_failures(store, cfg_over: dict | None = None) -> list[Finding]:
    """Witnesses and dimensions that never move a verdict.

    Precedent: 台湾 IC 出口 was called 「整体口径无法归因到 HBM」 four times running — the
    source was bound to the wrong concept, and a person had to notice by eye.

    Measured two ways, because they mean different things. A SPEAKER that is always
    neutral is bound to the wrong claim, or has nothing to say about it. A DIMENSION that
    is always neutral is worse: it collects evidence every quarter and has never once
    changed an answer, which means it is not asking a question the evidence can settle.
    """
    c = {**DEFAULTS, **(cfg_over or {})}
    by_speaker: dict[str, dict] = {}
    by_concept: dict[str, dict] = {}
    for row in store.latest_claim_assessments(limit=100):
        try:
            payload = json.loads(row.get("payload") or "{}")
        except (ValueError, TypeError):
            continue
        for j in payload.get("judgements") or []:
            neutral = j.get("polarity") == "neutral"
            reason = j.get("reason") or ""
            for bucket, key in ((by_speaker, j.get("speaker") or "—"),
                                (by_concept, j.get("concept") or "—")):
                t = bucket.setdefault(key, {"n": 0, "neutral": 0, "reasons": [],
                                            "unattributable": 0})
                t["n"] += 1
                if neutral:
                    t["neutral"] += 1
                    t["reasons"].append(reason)
                    if any(m in reason for m in UNATTRIBUTABLE):
                        t["unattributable"] += 1

    out = []
    for concept, t in sorted(by_concept.items(), key=lambda kv: -kv[1]["neutral"]):
        if (t["n"] < c["min_concept_judgements"]
                or t["neutral"] / t["n"] < c["concept_neutral_share"]):
            continue
        out.append(Finding(
            signal="④ 归因失败",
            subject=f"维度 {concept}",
            detail=f"{t['n']} 次判读全部/几乎全部中性（{t['neutral']}/{t['n']}）——"
                   f"这个维度收了一整轮证据，一次也没改变过结论",
            action="它问的问题证据答不了：把维度改成可判读的形态（例如"
                   "「扩产」→「产能增速已跑到需求前面」），或在 Concept.desc 里"
                   "写明什么读数才算反证",
            evidence=[r[:100] for r in t["reasons"][:2]]))
    for speaker, t in sorted(by_speaker.items(), key=lambda kv: -kv[1]["neutral"]):
        if (t["n"] < c["min_speaker_judgements"]
                or t["neutral"] / t["n"] < c["speaker_neutral_share"]):
            continue
        note = (f"，其中 {t['unattributable']} 次明说口径对不上"
                if t["unattributable"] else "")
        out.append(Finding(
            signal="④ 归因失败",
            subject=f"证人 {speaker}",
            detail=f"{t['n']} 次判读里 {t['neutral']} 次中性{note}",
            action="要么改这条源/命题的 concepts 绑定，要么修知识库里对该环节机制的描述",
            evidence=[r[:100] for r in t["reasons"][:2]]))
    return out[: c["max_per_signal"]]


# --- ⑤ unfamiliar entities ------------------------------------------------ #
def unfamiliar_entities(cfg, store) -> list[Finding]:
    """Evidence about a name that appears in no config anywhere.

    Must be read by a person, never acted on: in the first real run `NEM`/`AAPL` came
    from mis-sourced documents (since fixed) while `INTC` was genuine — an ASML release
    quoting an Intel executive. The signal cannot tell those apart, and should not try.
    """
    from ..config import load_entities
    from .sources import load_sources

    known = {_canon(s) for s in load_entities()}
    # Third-party series declare their own entity id in config/sources.yaml and appear
    # in no ticker list by design — TW_IC_EXPORT is a customs bureau, not a company.
    known |= {_canon(s.entity) for s in load_sources()}
    for layer in cfg.layers:
        for t in layer.tickers:
            known.add(_canon(t.symbol))
        for s in list(layer.cohort_extra) + list(layer.private):
            known.add(_canon(s))
        for names in layer.witness_roster.values():
            known.update(_canon(s) for s in names)
        for claim in layer.claims:
            known.update(_canon(s) for s in claim.entities)
            known.update(_canon(w.entity) for w in claim.witnesses)
    seen: dict[str, list[dict]] = {}
    for r in store.observations(limit=2000):
        ent = _canon(r.get("entity") or "")
        if ent and ent not in known:
            seen.setdefault(ent, []).append(r)
    out = []
    for ent, group in sorted(seen.items(), key=lambda kv: -len(kv[1])):
        speakers = {_canon(r.get("source_entity") or "") for r in group}
        out.append(Finding(
            signal="⑤ 陌生实体",
            subject=ent,
            detail=f"{len(group)} 条观测提到它 · 说话人 {', '.join(sorted(speakers - {''}))}"
                   f" · 不在 entities.yaml，也不在任何层的名单里",
            action="**先人工判断真伪**：抓错文档 → 查取数；真有其人 → 进 entities.yaml"
                   "／层名单／知识库",
            evidence=[(r.get("evidence_span") or "")[:100] for r in group[:2]]))
    return out[: DEFAULTS["max_per_signal"]]


# --- ⑥ never revisited ---------------------------------------------------- #
def stale_notes(cfg, store, *, now: datetime, cfg_over: dict | None = None) -> list[Finding]:
    """A KB file untouched while its cohort kept producing evidence.

    Not an error — nothing here says the file is wrong. It says nobody has checked it
    against what has come in since, which for annual-tenor knowledge is the only way
    it goes stale without anyone noticing.
    """
    c = {**DEFAULTS, **(cfg_over or {})}
    from ..config import REPO_ROOT

    out = []
    for layer in cfg.layers:
        cohort = {_canon(t.symbol) for t in layer.tickers}
        cohort |= {_canon(s) for s in layer.cohort_extra}
        for name, rel in sorted(layer.structure_notes.items()):
            path = Path(rel)
            if not path.is_absolute():
                path = REPO_ROOT / rel
            if not path.exists():
                out.append(Finding(
                    signal="⑥ 久未复核", subject=f"{name}（{rel}）",
                    detail="structure_notes 指向的文件不存在",
                    action="修 config/sectors 里的路径，或补写这份知识库"))
                continue
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            age = (now - mtime).days
            if age < c["stale_days"]:
                continue
            fresh = sum(len(store.observations(entity=e, since=mtime, limit=200))
                        for e in cohort)
            if fresh < c["stale_min_observations"]:
                continue
            out.append(Finding(
                signal="⑥ 久未复核", subject=f"{name}（{rel}）",
                detail=f"{age} 天未改动，期间该层新增 {fresh} 条观测",
                action="扫一眼判据是否还站得住——不是说它错了，是说没人对过账"))
    return out[: c["max_per_signal"]]


# --- assembly ------------------------------------------------------------- #
def review(cfg, store, *, now: datetime | None = None,
           cfg_over: dict | None = None) -> list[Finding]:
    """Run all six detectors. Each is isolated: one failing must not silence the rest."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:      # callers pass the report's as_of, which may be naive
        now = now.replace(tzinfo=timezone.utc)
    runners = [
        ("blind_spots", lambda: blind_spots(cfg, store)),
        ("unmapped_clusters", lambda: unmapped_clusters(store, cfg_over)),
        ("undeclared_relations", lambda: undeclared_relations(cfg, store)),
        ("attribution_failures", lambda: attribution_failures(store, cfg_over)),
        ("unfamiliar_entities", lambda: unfamiliar_entities(cfg, store)),
        ("stale_notes", lambda: stale_notes(cfg, store, now=now, cfg_over=cfg_over)),
    ]
    out: list[Finding] = []
    for name, run in runners:
        try:
            out += run()
        except Exception as exc:  # noqa: BLE001
            log.warning("kb_review: %s skipped: %s", name, exc)
    return out


def as_section(findings: list[Finding]) -> list[str]:
    """Markdown for the weekly chain-evidence report. Empty findings is a real result."""
    lines = ["## 知识库复核", "",
             "> 静态知识（`config/knowledge/*.md` 与 `signal_chain`）是年度级的，"
             "所以没人会主动想起去看它。下面由数据指出**哪里可能该更新了**——"
             "全部确定性汇总，不含模型判断。\n"
             "> **系统不自动改配置**：这是一张待办清单，改不改由你决定。", ""]
    if not findings:
        lines += ["（本期无待复核项）", ""]
        return lines
    by_signal: dict[str, list[Finding]] = {}
    for f in findings:
        by_signal.setdefault(f.signal, []).append(f)
    for signal in sorted(by_signal):
        lines += [f"### {signal}", ""]
        for f in by_signal[signal]:
            lines += [f"**{f.subject}**", "", f"- {f.detail}", f"- → {f.action}"]
            for span in f.evidence[:3]:
                if span:
                    lines.append(f"  - 「{span}」")
            lines.append("")
    return lines
