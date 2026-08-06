"""Claim proposer — turn unexplained observations into ONE candidate proposition.

Invoked only after the deterministic gate in chain/induction.py fires, so this never
runs as a daily "think of something" pass. It proposes; a person decides. Its output
reaches no factor, no Chief context and no recurring task.
"""

from __future__ import annotations

import logging

from ...schemas.chain import Concept, Witness
from ..base import run_structured
from .outputs import ClaimProposalView

log = logging.getLogger("ats.agents.evidence.proposer")


def _rows_block(rows: list[dict]) -> str:
    out = []
    for r in rows:
        who = r.get("source_entity") or r.get("entity") or "?"
        about = r.get("entity") or ""
        tag = f"{who}" + (f"→{about}" if about and about != who else "")
        out.append(f"  [{tag}] {r.get('metric', '')} {r.get('direction', '')} · "
                   f"{(r.get('evidence_span') or '')[:150]}")
    return "\n".join(out)


def propose(rows: list[dict], *, sector: str = "ai_hardware") -> ClaimProposalView | None:
    """Induce one candidate proposition. Returns None on failure (never raises)."""
    from ...config import load_sector_config

    declared = []
    try:
        cfg = load_sector_config(sector)
        for layer in cfg.layers:
            for claim in layer.claims:
                declared.append(f"  [{layer.key}] {claim.statement or claim.id}")
    except Exception as exc:  # noqa: BLE001
        log.warning("proposer: sector config unavailable: %s", exc)

    ctx = (
        "下面是系统已经掌握、但**归不到任何已声明命题**的事实观测。\n"
        "请判断它们是否在共同指向某个尚未被声明的经济命题；如果是，提出**一条**。\n\n"
        + ("已声明的命题（不要重复提出，也不要只是换个说法）：\n"
           + "\n".join(declared) + "\n\n" if declared else "")
        + "未归属观测：\n" + _rows_block(rows) + "\n\n"
        "要求：\n"
        "- 命题要**可证伪**，写清机制（谁的什么变化导致什么），不要写「AI 利好」这种方向词\n"
        "- 允许跨层（例如瓶颈从某层迁移到另一层）——这类往往正是最有价值的\n"
        "- 只在这些观测**确实共同指向**一件事时才提；否则 statement 留空\n"
        "- 你只是提议，人来决定是否采纳；**不要**给买卖建议、目标价或仓位"
    )
    try:
        return run_structured("claim_proposer", ClaimProposalView, ctx,
                              skill_slug="claim-proposer")
    except Exception as exc:  # noqa: BLE001 - induction is best-effort
        log.warning("claim proposer failed: %s", exc)
        return None


def to_concepts(view: ClaimProposalView) -> list[Concept]:
    return [Concept(key=c.key, desc=c.desc, supports_when=c.supports_when,
                    expect_from=c.expect_from, direct=c.direct) for c in view.concepts]


def to_witnesses(view: ClaimProposalView) -> list[Witness]:
    out = []
    for w in view.witnesses:
        if w.stance in {"customer", "supplier", "competitor", "incumbent", "regulator"}:
            out.append(Witness(entity=w.entity.upper(), stance=w.stance))
    return out
