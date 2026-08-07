"""Emergent propositions — induce a candidate claim from evidence that has no home.

The claims in config answer "verify what I already suspect". The signals that matter
most are often the ones nobody thought to declare — "the datacenter bottleneck is
migrating from memory to optical/InP". Those are cross-layer and emergent: they belong
to no single layer and were never written down.

A fixed detector cannot cover them. Coding a "cross-layer tightness diff" catches
bottleneck migration and nothing else; the next signal might be a customer-mix shift or
a substitution running ahead of schedule. An open set cannot be enumerated.

So: the TRIGGER is deterministic (a pool of unmapped observations reaching substance),
the INDUCTION is the model's, and the OUTPUT is a card for a person — it never enters
the factor model, the Chief, or any recurring task. Only a human extends the axes,
which is what keeps the cross-section's history comparable.

Evidence-driven, not calendar-driven: nothing here runs on a cron. Time passing does
not produce propositions; accumulating unexplained facts does.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from ..schemas.chain import ClaimProposal

log = logging.getLogger("ats.chain.induction")

DEFAULTS = {
    "min_observations": 6,      # enough unexplained facts to be a pattern, not a blip
    "min_entities": 3,          # spread across companies, not one chatty filing
    "cooldown_days": 30,        # a rejected idea may not come back reworded next week
    "max_context_rows": 40,
}


def pool(store, *, limit: int = 500) -> list[dict]:
    """Observations we hold but could file under no declared dimension."""
    return store.unmapped_observations(limit=limit)


def should_induce(rows: list[dict], cfg: dict | None = None) -> tuple[bool, str]:
    """Deterministic gate. Returns (fire, reason) — the reason is logged either way.

    Purely mechanical on purpose: if the model decided when to look, "is there something
    here?" would be asked every run and answered yes often enough to manufacture themes.
    """
    c = {**DEFAULTS, **(cfg or {})}
    n = len(rows)
    entities = {(r.get("source_entity") or r.get("entity") or "").upper() for r in rows}
    entities.discard("")
    if n < c["min_observations"]:
        return False, f"未映射观测 {n} < {c['min_observations']}，尚不成形"
    if len(entities) < c["min_entities"]:
        return False, (f"仅 {len(entities)} 个来源（{'/'.join(sorted(entities))}），"
                       f"少于 {c['min_entities']}，可能只是一家的自说自话")
    return True, f"{n} 条未映射观测 · 来自 {len(entities)} 个来源 → 触发一次归纳"


def in_cooldown(store, signature: str, cfg: dict | None = None,
                now: datetime | None = None) -> bool:
    """A rejected candidate may not return under a new sentence during the cooldown.

    The signature fingerprints the EVIDENCE (entities + metrics), not the wording, so
    rephrasing does not evade it.
    """
    c = {**DEFAULTS, **(cfg or {})}
    prior = store.proposal_by_signature(signature)
    if not prior or prior.get("status") != "rejected":
        return False
    stamp = prior.get("reviewed_at") or prior.get("created_at")
    try:
        when = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return False
    now = now or datetime.now(timezone.utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (now - when) < timedelta(days=c["cooldown_days"])


def _stance_note(rows: list[dict], sector: str = "ai_hardware") -> str:
    """Report the witness mix, so a one-sided candidate is visibly one-sided."""
    from ..config import load_sector_config

    stance_of: dict[str, str] = {}
    try:
        cfg = load_sector_config(sector)
        for layer in cfg.layers:
            for claim in layer.claims:
                for w in claim.witnesses:
                    stance_of.setdefault(w.entity.upper(), w.stance)
    except Exception:  # noqa: BLE001
        pass
    counts: dict[str, int] = {}
    for r in rows:
        who = (r.get("source_entity") or r.get("entity") or "").upper()
        counts[stance_of.get(who, "未声明")] = counts.get(stance_of.get(who, "未声明"), 0) + 1
    note = " / ".join(f"{k}×{v}" for k, v in sorted(counts.items()))
    if len([k for k in counts if k != "未声明"]) < 2:
        note += "  ⚠️ 立场单一，交叉验证不足"
    return note


def induce(store, *, sector: str = "ai_hardware", cfg: dict | None = None,
           now: datetime | None = None) -> tuple[ClaimProposal | None, str]:
    """Run one induction pass. Returns (proposal, reason).

    Costs nothing when the gate does not fire — the model is only invoked once the
    deterministic conditions are met.
    """
    from ..agents.evidence.proposer import propose

    now = now or datetime.now(timezone.utc)
    c = {**DEFAULTS, **(cfg or {})}
    rows = pool(store)
    fire, reason = should_induce(rows, c)
    if not fire:
        return None, reason

    entities = [(r.get("source_entity") or r.get("entity") or "") for r in rows]
    metrics = [(r.get("metric") or "") for r in rows]
    signature = ClaimProposal.make_signature(entities, metrics)
    if in_cooldown(store, signature, c, now):
        return None, f"同证据指纹的候选处于冷却期（{c['cooldown_days']} 天内被拒过）"

    view = propose(rows[: c["max_context_rows"]], sector=sector)
    if view is None or not (view.statement or "").strip():
        return None, "归纳未产出可用命题"

    proposal = ClaimProposal(
        signature=signature, statement=view.statement.strip(),
        layer_hint=view.layer_hint or "", kind=view.kind or "common",
        subject=(view.subject or "").upper(),  # proposals stay free-form until adopted
        concepts=view.as_concepts(), witnesses=view.as_witnesses(),
        stance_note=_stance_note(rows, sector),
        observation_ids=[r["id"] for r in rows if r.get("id")],
        created_at=now)
    store.save_claim_proposal(proposal)
    # Freeze BEFORE anyone can adopt it: the material that made us notice this
    # proposition may never also serve as evidence that it is true.
    store.freeze_as_discovery(proposal.observation_ids)
    log.info("induction: proposed %r from %d observations", proposal.statement,
             len(proposal.observation_ids))
    return proposal, reason


def as_card(proposal: ClaimProposal, rows_by_id: dict[str, dict] | None = None) -> str:
    """Render the review card a person actually reads."""
    rows_by_id = rows_by_id or {}
    lines = [f"待确认命题（由 {len(proposal.observation_ids)} 条未映射观测积累触发）", "",
             f"  「{proposal.statement}」", ""]
    if proposal.layer_hint:
        lines.append(f"  涉及层：{proposal.layer_hint}")
    lines.append(f"  证人立场：{proposal.stance_note}")
    shown = [rows_by_id[i] for i in proposal.observation_ids if i in rows_by_id][:8]
    if shown:
        lines += ["", "  支撑观测（均可点回原文）"]
        for r in shown:
            who = r.get("source_entity") or r.get("entity") or ""
            lines.append(f"    {who:<11}{(r.get('evidence_span') or '')[:72]}")
    if proposal.concepts:
        lines += ["", "  建议的 claim 形态"]
        for c in proposal.concepts:
            lines.append(f"    {c.key}: {c.desc}")
    lines += ["", "  这批观测已冻结为 discovery evidence——采纳后它们不得用于印证该命题。",
              f"  → ats evidence review {proposal.id} --accept | --reject"]
    return "\n".join(lines)
