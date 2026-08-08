"""Cross-section verdicts -> an evidence pack for whichever structural factor a claim
declares it informs.

The cross-section carries two structural factors — `tech_tenor` (position on the
secular technology curve) and `moat_pricing` (vertical integration, share, bottleneck
pricing power, customer concentration), 20% each. A claim says which one it informs via
`ClaimDef.feeds_factor`; omitting it means the claim informs neither, which is the
common case. Nothing here is specific to `moat_pricing` — that this module used to be
named after it was the artefact of a framework written around its first application.

What the structure analyst needs, and what this therefore produces:

  * A COMPARISON, not a stack of assertions. It must output differentiated -2..+2
    scores across a cohort, so "SK strong, Samsung strong" tells it nothing. One claim
    renders as one table with every declared entity in it, silent ones included.
  * Evidence that justifies what is claimed. The pack used to print the first four of
    everything an entity had said: SK hynix was labelled `strong` above one `down`
    reading ("DRAM ASP growth fell below market expectations") and two `flat` ones.
    Now it prints the clusters the adjudicator cited, and summarises the rest by
    dimension rather than passing them off as support.
  * The DIMENSION each reading rests on. `moat_pricing` is itself decomposed into
    share / pricing power / customer concentration, so collapsing a share verdict and a
    pricing verdict into one +/- discards exactly the part that maps onto the factor.
  * How much to trust it. Three companies each describing their own position is
    comparable — the bias is common-mode — but it is not the same as a customer
    confirming, and the pack says which it is.

`unknown` readings produce no score suggestion, so the factor stays NULL (cohort-
neutral) rather than 0. "Not looked at" and "looked at, judged neutral" are different
states and must remain distinguishable.
"""

from __future__ import annotations

import logging
from collections import defaultdict

log = logging.getLogger("ats.chain.factor_evidence")

MAX_SPANS = 3                 # per entity; the ledger holds the full set
SPAN_CHARS = 150

STANDING_CN = {"strong": "强", "neutral": "中", "weak": "弱", "unknown": "无读数"}
BASIS_CN = {"corroborated": "有交叉印证", "self_reported": "仅自述", "thin": "证据薄"}


class FactorEvidence:
    """One claim's cross-section reading, ready for the structure analyst.

    Deliberately per-CLAIM rather than per-entity: the analyst is scoring a cohort
    relative to itself, and N independent per-name assertions cannot express an
    ordering.
    """

    def __init__(self, claim_id: str, statement: str, factor: str,
                 readings: list, spans_by_entity: dict, silent: list[str] | None = None):
        self.claim_id = claim_id
        self.statement = statement
        self.factor = factor              # which structural factor this informs
        self.readings = readings          # list[EntityReading], declared order
        self.spans_by_entity = spans_by_entity
        self.silent = silent or []

    @property
    def subjects(self) -> list[str]:
        return [r.entity for r in self.readings if r.standing != "unknown"]

    def as_text(self) -> str:
        out = [f"### {self.statement}",
               f"（本条只影响 `{self.factor}`；比较对象：{', '.join(r.entity for r in self.readings)}）",
               "",
               "| 公司 | 位置 | 依据强度 | 说话人 | 判读理由 |",
               "|---|---|---|---|---|"]
        for r in self.readings:
            out.append(f"| **{r.entity}** | {STANDING_CN.get(r.standing, r.standing)} "
                       f"| {BASIS_CN.get(r.basis, r.basis)} | {', '.join(r.speakers) or '—'} "
                       f"| {r.reason or '—'} |")

        graded = [r for r in self.readings if r.standing != "unknown"]
        if graded and all(r.basis == "self_reported" for r in graded):
            # Material to how far the analyst should move the factor, so it is stated
            # rather than left to be inferred from a column.
            out += ["", "> ⚠️ 全部读数均为**各家自述**，无客户或第三方交叉印证。"
                        "各家都会把自己说得不错，可比性来自横向对照而非单条可信度。"]

        for r in self.readings:
            spans = self.spans_by_entity.get(r.entity) or {}
            if not spans:
                continue
            out += ["", f"**{r.entity} 的依据**（判读器实际引用的原文）"]
            for concept, items in spans.items():
                for who, span in items[:MAX_SPANS]:
                    out.append(f"- [{concept}] {who}：{span}")
        return "\n".join(out)


def build_packs(assessments, claims, observations_by_id) -> list[FactorEvidence]:
    """Turn cross-section readings into one pack per claim that declares a factor.

    Routing is by `claim.feeds_factor`, not by `claim.kind`. `kind` selects how a claim
    is aggregated (support/refute scoring vs per-entity readings); where the answer goes
    is a separate, declared choice, and conflating the two is what made `moat_pricing`
    look like the framework's purpose.
    """
    by_id = {c.id: c for c in claims}
    out: list[FactorEvidence] = []
    for a in assessments:
        claim = by_id.get(a.claim_id)
        if claim is None or not claim.feeds_factor:
            continue                      # declares no factor -> informs no factor
        if claim.kind != "relative" or a.verdict != "resolved":
            # A comparison of one is not a comparison: when a single name in the cohort
            # disclosed anything, scoring it would be the single-name self-report the
            # cross-section exists to escape. The factor stays null for everyone.
            continue

        spans_by_entity: dict[str, dict[str, list]] = {}
        for reading in a.entity_readings:
            # ONLY the cited clusters. Falling back to "the first few of everything" is
            # what printed contradictory quotes under a stated conclusion.
            by_concept: dict[str, list] = defaultdict(list)
            for oid in reading.key_observation_ids:
                row = observations_by_id.get(oid)
                if not row or not row.get("evidence_span"):
                    continue
                who = row.get("source_entity") or row.get("entity") or ""
                by_concept[row.get("concept") or "—"].append(
                    (who, row["evidence_span"][:SPAN_CHARS]))
            if by_concept:
                spans_by_entity[reading.entity] = dict(by_concept)
            elif reading.standing != "unknown":
                log.info("factor_evidence: %s/%s graded %s but cited no evidence",
                         claim.id, reading.entity, reading.standing)

        out.append(FactorEvidence(
            claim_id=claim.id, statement=claim.statement or claim.id,
            factor=claim.feeds_factor, readings=list(a.entity_readings),
            spans_by_entity=spans_by_entity, silent=a.silent_witnesses))
    return out


def packs_for_layer(layer, store, *, cfg: dict | None = None) -> list[FactorEvidence]:
    """Assess a layer's claims from stored observations and build its evidence packs."""
    from .corroborate import assess_layer
    from .sources import source_entities_for as _source_entities

    if not getattr(layer, "claims", None):
        return []
    rows_by_entity: dict[str, list[dict]] = {}
    for claim in layer.claims:
        entities = claim.expected_witnesses() | {w.entity.upper() for w in claim.witnesses}
        entities |= set(claim.entities)
        entities |= _source_entities(claim)
        for e in entities:
            if e not in rows_by_entity:
                rows_by_entity[e] = store.observations(entity=e, limit=200)
    assessments = assess_layer(layer, rows_by_entity, cfg=cfg)
    by_id = {r["id"]: r for rows in rows_by_entity.values() for r in rows}
    return build_packs(assessments, layer.claims, by_id)


def packs_for_factor(packs: list[FactorEvidence], factor: str) -> list[FactorEvidence]:
    return [p for p in packs if p.factor == factor]


def as_context(packs: list[FactorEvidence]) -> str:
    """Render packs for the structure analyst's prompt."""
    if not packs:
        return ""
    factors = sorted({p.factor for p in packs})
    return ("## 竞争位置证据（各家财报原文，已过独立性/立场/隔离三道闸）\n"
            f"下列读数由确定性引擎筛选去重、由判读器横向比较得出，**影响的因子："
            f"{'、'.join(f'`{f}`' for f in factors)}**。\n"
            "请据此校正相应标的的因子分；**没有出现在下面的标的，本期没有可用的竞争位置\n"
            "证据——保持空值，不要凭印象打分**。知识库笔记与这里冲突时，以这里为准：\n"
            "笔记是稳定的结构背景，这里是本期实际发生的事。\n\n"
            + "\n\n".join(p.as_text() for p in packs))
