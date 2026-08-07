"""Relative-share verdicts -> evidence packs for the cross-section's `moat_pricing`.

The last mile of docs/CHAIN_EVIDENCE.md. The cross-section already carries a
`moat_pricing` factor (-2..+2, 20% weight in the blended composite) that is exactly
"share / pricing power / customer concentration" — but its only input was a static KB
note, and layers without a KB skipped the overlay entirely. This module hands the
structure analyst event-level evidence instead: what the filings actually said, with
verbatim spans, per subject.

Two rules the caller must not bypass:

  * only `relative` (cross-section) claims produce packs. A `common` verdict is about
    the industry, not about who wins inside it — routing it here would re-introduce
    exactly the "competitor expands => our holding lost share" inference gate 3 blocks;
  * only `strong` / `weak` readings produce packs. `neutral` and `unknown` produce
    NOTHING, so `moat_pricing` stays null (cohort-neutral) rather than 0. "Not looked
    at" and "looked at, judged neutral" are different states and must stay
    distinguishable in the report.
"""

from __future__ import annotations

import logging

log = logging.getLogger("ats.chain.moat")

MAX_SPANS = 4                 # keep the pack readable; the ledger holds the full set


class MoatEvidence:
    """Evidence about one subject's competitive position, ready for the analyst."""

    def __init__(self, subject: str, direction: str, claim_id: str, statement: str,
                 verdict: str, coverage: str, spans: list[str],
                 silent: list[str] | None = None):
        self.subject = subject
        self.direction = direction          # "positive" | "negative"
        self.claim_id = claim_id
        self.statement = statement
        self.verdict = verdict
        self.coverage = coverage
        self.spans = spans
        self.silent = silent or []

    def as_text(self) -> str:
        head = (f"- {self.subject}｜{self.statement}\n"
                f"  结论: {self.verdict}（{'支持' if self.direction == 'positive' else '反证'}）"
                f" · 证人覆盖 {self.coverage}")
        if self.silent:
            head += f" · 本期未发声: {', '.join(self.silent)}"
        for s in self.spans:
            head += f"\n    · {s}"
        return head


def build_packs(assessments, claims, observations_by_id) -> list[MoatEvidence]:
    """Turn cross-section readings into one evidence pack per company.

    `observations_by_id` maps observation id -> row, so each pack can carry the actual
    source sentences: the analyst is being asked to score a competitive position, and
    it should score the evidence, not our summary of it.

    One pack per COMPANY, not per claim. That is the whole change: the previous shape
    produced a single verdict about one subject, so a rival's own share disclosure could
    never reach the factor for the rival — only 20% of the cohort could ever be scored
    from evidence, and it happened to be the name we hold.
    """
    by_id = {c.id: c for c in claims}
    out: list[MoatEvidence] = []
    for a in assessments:
        claim = by_id.get(a.claim_id)
        if claim is None or claim.kind != "relative":
            continue
        if a.verdict != "resolved":
            # A comparison of one is not a comparison. When only a single name in the
            # cohort disclosed anything, scoring it "strong" would be exactly the
            # single-name self-report the cross-section exists to escape — so the whole
            # assessment yields nothing and moat_pricing stays null for everyone.
            continue
        for reading in a.entity_readings:
            if reading.standing == "strong":
                direction = "positive"
            elif reading.standing == "weak":
                direction = "negative"
            else:
                continue      # neutral / unknown -> no pack -> moat_pricing stays null
            spans = []
            for oid in reading.observation_ids[:MAX_SPANS]:
                row = observations_by_id.get(oid)
                if row and row.get("evidence_span"):
                    who = row.get("source_entity") or row.get("entity") or ""
                    spans.append(f"[{who}] {row['evidence_span'][:160]}")
            out.append(MoatEvidence(
                subject=reading.entity, direction=direction, claim_id=claim.id,
                statement=claim.statement or claim.id,
                verdict=f"{reading.standing}（{reading.basis}）"
                        + (f" — {reading.reason}" if reading.reason else ""),
                coverage=f"{reading.evidence_clusters} 簇/{reading.stance_classes} 类立场",
                spans=spans, silent=a.silent_witnesses))
    return out


def packs_for_layer(layer, store, *, cfg: dict | None = None) -> list[MoatEvidence]:
    """Assess a layer's claims from stored observations and build its evidence packs."""
    from .corroborate import assess_layer

    if not getattr(layer, "claims", None):
        return []
    rows_by_entity: dict[str, list[dict]] = {}
    for claim in layer.claims:
        entities = claim.expected_witnesses() | {w.entity.upper() for w in claim.witnesses}
        entities |= set(claim.entities)
        for e in entities:
            if e not in rows_by_entity:
                rows_by_entity[e] = store.observations(entity=e, limit=200)
    assessments = assess_layer(layer, rows_by_entity, cfg=cfg)
    by_id = {r["id"]: r for rows in rows_by_entity.values() for r in rows}
    return build_packs(assessments, layer.claims, by_id)


def as_context(packs: list[MoatEvidence]) -> str:
    """Render packs for the structure analyst's prompt."""
    if not packs:
        return ""
    return ("## 竞争位置证据（来自各家财报的事实观测，已过独立性/立场/隔离三道闸）\n"
            "下列结论由确定性引擎算出，**不要重新判断它们对不对**；请据此校正相关标的的\n"
            "moat_pricing。没有出现在下面的标的，说明本期没有可用的竞争位置证据——\n"
            "**保持空值，不要凭印象打分**。\n"
            + "\n".join(p.as_text() for p in packs))
