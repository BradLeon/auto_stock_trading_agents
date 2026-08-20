"""Did the structure analyst actually use the knowledge base, or just cite it?

`kb_review.py` asks whether the KB should be UPDATED. This asks whether it was USED —
a different question with a different failure mode. The dangerous one is not "the KB is
stale", it is "the model wrote a confident rationale that attributes to the KB something
the KB does not say", because that reads exactly like a well-grounded score.

The checks here are deterministic and cheap enough to run on every weekly report. They
rest on one property of the rewritten notes that makes the sharpest check exact:

    **the knowledge base contains no ticker and no company name.**

By construction — it gives criteria, not rankings. So a rationale that attributes a
statement ABOUT A COMPANY to the KB is fabricating, and we can say so without judging
content. Statements of ABSENCE ("KB 未覆盖 MRVL") are the one legitimate way a company
name and the KB appear together, and are excluded by the negation test.

What this cannot check: whether a criterion was applied in the right DIRECTION. That
needs either a labelled regression set or the perturbation test in `kb_perturb.py`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..data import industry

log = logging.getLogger("ats.chain.kb_audit")

# Verbs that make the KB the SOURCE of the following statement.
ATTRIBUTION = r"(?:KB|知识库|笔记)[^。；\n]{0,12}?(?:指出|明确|说明|表明|显示|认为|列为|将|把|所述|所称|称|写明|给出|定义)"
# ...unless the statement is about the KB NOT saying something, which is legitimate and
# is in fact what the analyst is instructed to write when it has no criteria to apply.
NEGATION = ("未覆盖", "未提供", "未给出", "没有给出", "未说明", "未涉及", "不涉及",
            "未列出", "缺", "无法", "未包含", "未写", "没写")
# Where a score says it came from. The sector review is a legitimate source — weaker
# than a filing but a real, auditable artefact — so it must not be reported as "no
# source". Telling them apart is the whole point: a layer whose scores all rest on the
# review rather than the ledger is a layer that needs claims, not a layer that is wrong.
SECTOR_VIEW = ("行业评审", "行业结论", "本周背景", "本周行业", "层评审", "周报结论")


def _criterion_keys(title: str) -> list[str]:
    """Substantive tokens of a criterion title, for loose citation matching.

    Titles read like 「软件生态的实际锁定深度」 while the rationale writes 「软件生态、
    系统级互联」. Requiring the full title matched almost nothing — L4 scored 0/5 while
    every rationale was visibly citing criteria, which makes the metric worse than
    absent: it reads as "the KB is being ignored" when the opposite is true.
    """
    body = re.sub(r"[（(].*?[)）]", "", title)
    parts = re.split(r"[的与和、/／\s]+", body)
    return [p for p in parts if len(p) >= 2]


@dataclass
class AuditFinding:
    kind: str          # fabricated | cross_layer | ungrounded | coverage
    layer: str
    symbol: str
    detail: str
    quote: str = ""


@dataclass
class AuditReport:
    layer: str
    rows: int = 0
    findings: list[AuditFinding] = field(default_factory=list)
    criteria_total: int = 0
    criteria_cited: int = 0
    uncited: list[str] = field(default_factory=list)
    grounding: dict = field(default_factory=dict)   # 出处 -> [symbol]

    @property
    def ok(self) -> bool:
        return not [f for f in self.findings if f.kind != "coverage"]


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"[。；\n]", text or "") if s.strip()]


def _company_tokens(cfg) -> set[str]:
    """Every ticker in the sector, canonical and as configured."""
    out: set[str] = set()
    for layer in cfg.layers:
        for t in layer.tickers:
            out.add(t.symbol.upper())
        out.update(s.upper() for s in layer.cohort_extra)
    return out


def _criteria_of(path: Path) -> list[str]:
    """The bolded titles of the numbered criteria in a note's criteria section.

    Parsed rather than configured because the note IS the specification: if a criterion
    is added to the file, it should start being counted without anyone remembering to
    register it somewhere else.

    The section is located by heading MEANING (`industry.criteria_spans`), not by section
    number — see the comment there for what the old `split("## 三")` silently did to the
    two notes that outgrew the four-section template.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return re.findall(r"^\s*\d+\.\s*\*\*(.+?)\*\*",
                      industry.criteria_text(text), flags=re.M)


def _criteria_gap(path: Path) -> str:
    """Why a mounted note contributed no criteria — "" when it contributed some.

    Silence here used to be indistinguishable from health, and that is the actual defect:
    zero criteria suppressed the coverage line, left `uncited` empty, and let the layer
    render 「无异常」. A coverage metric that cannot say "I measured nothing" is worse
    than no metric, because absence reads as a pass.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "读不出来"
    if not industry.criteria_spans(text):
        return "没有标题含「判据」的小节，审计无从知道这份笔记的判据是哪几条"
    if not _criteria_of(path):
        return "判据小节里没有「数字. **加粗标题**」形式的条目，抽不出判据"
    return ""


def audit_layer(cfg, layer, basket) -> AuditReport:
    """Check one layer's basket rows against the notes that layer was actually given."""
    from ..config import REPO_ROOT

    rep = AuditReport(layer=layer.key, rows=len(basket.rows))
    tickers = _company_tokens(cfg)
    own_paths = {Path(p) if Path(p).is_absolute() else REPO_ROOT / p
                 for p in layer.structure_notes.values()}
    own_stems = {p.stem for p in own_paths}
    # Sub-layer names from notes this layer was NOT given. Citing one is a sign the
    # model reached for knowledge it was not shown — usually its own prior.
    foreign_stems = {p.stem for p in (REPO_ROOT / "config" / "knowledge").glob("*.md")
                     } - own_stems

    criteria = [c for p in sorted(own_paths) for c in _criteria_of(p)]
    rep.criteria_total = len(criteria)
    for p in sorted(own_paths):
        gap = _criteria_gap(p)
        if gap:
            # A note defect, not an analyst defect — but it invalidates this layer's
            # coverage number, so it must not be reported as a clean run.
            rep.findings.append(AuditFinding(
                kind="no_criteria", layer=layer.key, symbol="",
                detail=f"知识库「{p.stem}」{gap}　→ 本层的判据引用率不可信"))
    cited: set[str] = set()

    for row in basket.rows:
        why = row.rationale or ""
        for sent in _sentences(why):
            if not re.search(ATTRIBUTION, sent):
                continue
            if any(n in sent for n in NEGATION):
                continue                       # "KB 未覆盖 X" — legitimate
            named = [t for t in tickers if re.search(rf"\b{re.escape(t)}\b", sent)]
            if named:
                # The notes name no company, so a positive KB-attributed statement
                # about one cannot have come from them.
                rep.findings.append(AuditFinding(
                    kind="fabricated", layer=layer.key, symbol=row.symbol,
                    detail=f"把关于 {', '.join(named)} 的说法归给了知识库，"
                           f"但知识库里没有任何公司名",
                    quote=sent.strip()[:120]))
        for stem in foreign_stems:
            if stem in why:
                rep.findings.append(AuditFinding(
                    kind="cross_layer", layer=layer.key, symbol=row.symbol,
                    detail=f"引用了本层没有拿到的笔记「{stem}」",
                    quote=why.strip()[:120]))
        for crit in criteria:
            if any(k in why for k in _criterion_keys(crit)):
                cited.add(crit)

    rep.criteria_cited = len(cited)
    rep.uncited = [c for c in criteria if c not in cited]
    return rep


def grounding_mix(layer, basket, store) -> tuple[dict[str, list[str]], list[AuditFinding]]:
    """Where each non-zero `moat_pricing` says it came from.

    Classified, not accused. `moat_pricing` is a statement about one company, and the
    notes deliberately contain no such statement — but that leaves three legitimate
    sources with very different strength, and only the last is a defect:

      台账读数   an observation exists for this company — first-party, strongest
      行业评审   the layer view from the review that ran minutes earlier — real but weaker
      仅判据     the KB alone, i.e. the score restates which sub-layer the name sits in

    A layer where every score is 仅判据 is not lying; it is telling you the overlay has
    nothing company-specific to work with, which is a claims/witness gap, not a KB gap.
    An earlier version reported all of these as "no source" — including rows whose
    rationale named the sector review in the very sentence being flagged.
    """
    from ..config import canonical_entity

    mix: dict[str, list[str]] = {"台账读数": [], "行业评审": [], "仅判据": [], "无说明": []}
    findings: list[AuditFinding] = []
    for row in basket.rows:
        if not row.moat_pricing:
            continue
        why = row.rationale or ""
        if store.observations(entity=canonical_entity(row.symbol), limit=1):
            mix["台账读数"].append(row.symbol)
        elif any(s in why for s in SECTOR_VIEW):
            mix["行业评审"].append(row.symbol)
        elif re.search(ATTRIBUTION, why) or "判据" in why:
            mix["仅判据"].append(row.symbol)
        else:
            mix["无说明"].append(row.symbol)
            findings.append(AuditFinding(
                kind="ungrounded", layer=layer.key, symbol=row.symbol,
                detail=f"moat_pricing={row.moat_pricing:+.1f}，但理由里既没引台账读数、"
                       f"也没引行业评审或判据——这个分数的出处不明",
                quote=why.strip()[:120]))
    return mix, findings


def audit(cfg, store, *, layer_key: str = "") -> list[AuditReport]:
    """Audit every layer whose most recent basket we can find."""
    reports: list[AuditReport] = []
    seen: set[str] = set()
    for review in store.sector_review_history(cfg.name, limit=8):
        for basket in review.baskets:
            if basket.layer_key in seen or (layer_key and basket.layer_key != layer_key):
                continue
            seen.add(basket.layer_key)
            # 历史 basket 可能带着拆分前的层键（L5_fab 等）。按当前键找不到就走
            # legacy_keys —— 否则重构当天，整段历史的判据审计会静默地什么都不报。
            # 拆分键会同时命中两半，用 basket 自己的标的挑对那一半：否则会拿代工层的
            # 判据去审存储层的 basket，而那种错**不报错**，只是审出一堆无意义的 finding。
            layer = cfg.layer_for_key_and_symbols(
                basket.layer_key, [r.symbol for r in basket.rows])
            if layer is None or not basket.structural:
                continue        # no overlay ran -> nothing to audit
            try:
                rep = audit_layer(cfg, layer, basket)
                rep.grounding, extra = grounding_mix(layer, basket, store)
                rep.findings += extra
                reports.append(rep)
            except Exception as exc:  # noqa: BLE001
                log.warning("kb audit skipped for %s: %s", basket.layer_key, exc)
    return sorted(reports, key=lambda r: r.layer)


def as_section(reports: list[AuditReport]) -> list[str]:
    """Markdown for the weekly report."""
    lines = ["## 知识库使用审计", "",
             "> 上一节问「知识库该不该更新」，这一节问「它有没有被用对」。\n"
             "> 依据一条构造性质：**新模板的知识库里零股票代码、零公司名**——"
             "所以把一条**关于某公司**的说法归给知识库，就是编造。\n"
             "> 确定性检查，无模型判断。查不出方向对错——那要靠扰动测试"
             "（`ats sector kbperturb`）。", ""]
    if not reports:
        lines += ["（本期没有跑过结构层的层）", ""]
        return lines
    for rep in reports:
        mark = "✅" if rep.ok else "⚠️"
        cover = (f" · 判据被引用 {rep.criteria_cited}/{rep.criteria_total}"
                 if rep.criteria_total else "")
        lines += [f"### {mark} {rep.layer}（{rep.rows} 个标的{cover}）", ""]
        for f in rep.findings:
            tag = {"fabricated": "🔴 编造引用", "cross_layer": "🟡 跨层串味",
                   "ungrounded": "🟡 无出处",
                   "no_criteria": "🟠 判据抽不出"}.get(f.kind, f.kind)
            # no_criteria is about a note, not about a holding — it carries no symbol.
            lines.append(f"- {tag} **{f.symbol}**：{f.detail}" if f.symbol
                         else f"- {tag}：{f.detail}")
            if f.quote:
                lines.append(f"  - 原文「{f.quote}」")
        shown = {k: v for k, v in (rep.grounding or {}).items() if v}
        if not shown:
            # Distinct from "sourced badly": every score is 0, so the overlay abstained
            # everywhere. Printing an empty 出处 line and then complaining that none of
            # them rest on the ledger reads as a bug.
            lines.append("- moat_pricing 全为 0——结构层在本层整体弃权，没有分数可追溯")
        else:
            lines.append("- moat_pricing 的出处：" + " · ".join(
                f"{k} {len(v)}（{', '.join(v)}）" for k, v in shown.items()))
            if not shown.get("台账读数"):
                # Not an error — a diagnosis. The overlay has nothing company-specific
                # to read, so it is restating sub-layer membership.
                lines.append("  - ⚠️ 本层没有一个分数建立在台账读数上"
                             "　→ 缺的是命题/证人，不是知识库")
        if rep.uncited:
            # Not a defect of the run — a defect of the note. A criterion nobody ever
            # cites is either unusable as written or never reached the context.
            lines.append(f"- ⬜ 从未被引用的判据：{'、'.join(rep.uncited)}"
                         f"　→ 要么写得没法用，要么根本没进上下文")
        if rep.ok and not rep.uncited:
            lines.append("- 无异常")
        lines.append("")
    return lines
