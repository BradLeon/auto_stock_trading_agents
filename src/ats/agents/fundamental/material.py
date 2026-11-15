"""Company risk register and material-event review (event-impact-routing group 4, 2026-11-15).

Two Fundamental outputs that sit beside the earnings-anchored view:

* `fundamental_risk_register` — company-specific risks (key person, governance, customer concentration, a regulatory
  case ...) with falsifiers, kept all year. A thesis baseline exists only around earnings, so the register is its
  own projection: initialised once per target, refreshed whenever a new baseline is built.
* `fundamental_material_review` — what a material, non-earnings event means for ONE company: which thesis line,
  risk item or falsifier it touches, the thesis status, whether quarterly numbers or only the risk premium move.
  It is read by the Chief ALONGSIDE the Routine update / earnings review, never instead of it, and judges only the
  company-specific part: the layer-wide part belongs to the Layer analyst. Valid 14 days.

Neither output carries an action, a size or a risk verdict (the payload schemas refuse them).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field

from ...agent.task_projection import ProjectionScope, build_envelope

log = __import__("logging").getLogger("ats.agents.fundamental.material")

REVIEW_ROLE, REVIEW_SKILL = "fundamental_material", "fundamental-material-review"
REGISTER_ROLE, REGISTER_SKILL = "fundamental_risk_register", "fundamental-risk-register"
VALIDITY = timedelta(days=14)
NO_BASELINE_CONFIDENCE_CAP = 0.3


def _now() -> datetime:
    from ...workflow.evaluation_clock import now as evaluation_now

    return evaluation_now(timezone.utc)


# --------------------------------------------------------------------------- risk register


class RiskItemView(BaseModel):
    key: str = Field(min_length=1, description="snake_case key, e.g. key_person, customer_concentration")
    label: str = Field(min_length=1, description="Chinese short label")
    kind: str = Field(default="", description="key_person / governance / customer_concentration / regulatory_case / supply_dependency / other")
    falsifier: str = Field(min_length=1, description="Chinese: the observable development that would show the risk has materialised")
    rationale: str = Field(default="", description="Chinese: why this is a company-specific risk")


class RiskRegisterView(BaseModel):
    items: list[RiskItemView] = Field(min_length=1, max_length=8)


def latest_register(store, symbol: str) -> dict | None:
    rows = store.task_projection_envelopes(agent_role="fundamental_risk_register", scope_kind="entity",
                                           scope_id=symbol.upper(), limit=1)
    return rows[0] if rows else None


def register_context(symbol: str, baseline: dict, previous: dict | None) -> str:
    prior = "\n".join(f"- {i.get('key')}: {i.get('label')} — 证伪：{i.get('falsifier')}"
                      for i in ((previous or {}).get("payload") or {}).get("items") or []) or "(none)"
    return (f"Company: {symbol}\n\nCurrent thesis narrative (may be empty):\n{baseline.get('narrative') or '(none)'}\n\n"
            f"Tracked expectation dimensions: {', '.join(baseline.get('dim_keys') or []) or '(none)'}\n\n"
            f"Previous risk register:\n{prior}\n\n"
            "List the company-specific risks an analyst should watch, each with an observable falsifier.")


def build_register(store, symbol: str, *, rate=None, refreshed_with: str = "") -> str:
    """Publish a new risk register for `symbol`. Returns the projection id."""
    from ..base import execution_versions, run_structured
    from .routine import load_baseline

    symbol = symbol.upper()
    baseline = load_baseline(store, symbol)
    previous = latest_register(store, symbol)
    context = register_context(symbol, baseline, previous)
    rate = rate or (lambda ctx: run_structured(REGISTER_ROLE, RiskRegisterView, ctx, skill_slug=REGISTER_SKILL))
    view = rate(context)
    payload = {"entity": symbol, "items": [item.model_dump() for item in view.items],
               "refreshed_with": refreshed_with or ",".join(baseline.get("baseline_refs") or []),
               "previous_ref": (previous or {}).get("projection_id", "")}
    envelope = build_envelope(
        role="fundamental_risk_register", payload=payload, scope=ProjectionScope(kind="entity", id=symbol),
        as_of=_now().isoformat(), input_refs=[*(baseline.get("baseline_refs") or []),
                                              *([previous["projection_id"]] if previous else [])],
        supersedes_projection_id=(previous or {}).get("projection_id", ""),
        **execution_versions(REGISTER_ROLE, REGISTER_SKILL, template_source=__file__))
    store.save_task_projection_envelope(envelope)
    return envelope.projection_id


# --------------------------------------------------------------------------- material review


class MaterialReviewView(BaseModel):
    thesis_status: Literal["stable", "pressured", "falsified"]
    hit_items: list[str] = Field(default_factory=list, description="Risk-item keys, thesis lines or falsifiers this touches")
    expectation_effect: Literal["quarterly_numbers", "risk_premium", "none"]
    direction: Literal[-1, 0, 1]
    magnitude: float = Field(ge=0.0, le=3.0, description="0 none … 3 thesis-changing")
    confidence: float = Field(ge=0.0, le=1.0)
    narrative: str = Field(default="", description="Chinese: the company-specific reading; no actions or sizes")
    unverified: list[str] = Field(default_factory=list)
    materiality_review: str = Field(default="", description="Start with agree / higher / lower, then the reason")


def components_for(brief_payload: dict, component_ids: list[str]) -> list[dict]:
    wanted = set(component_ids)
    return [c for c in brief_payload.get("event_components") or [] if c.get("component_id") in wanted]


def review_context(symbol: str, components: list[dict], facts: list[str], baseline: dict,
                   register: dict | None) -> str:
    comp_lines = "\n".join(
        f"- [{c.get('component_id')}] {c.get('content')}（重要性 {c.get('materiality')}，方向 {c.get('direction')}，"
        f"影响面 {c.get('scope')}；{c.get('rationale', '')}）" for c in components)
    cited = sorted({i for c in components for i in c.get("fact_indices") or [] if 0 <= i < len(facts)})
    fact_lines = "\n".join(f"[{i}] {facts[i]}" for i in cited) or "(no indexed facts)"
    items = "\n".join(f"- {i.get('key')}: {i.get('label')} — 证伪：{i.get('falsifier')}"
                      for i in ((register or {}).get("payload") or {}).get("items") or []) or "(no risk register)"
    return (f"Company: {symbol}\n\nMaterial event components to review:\n{comp_lines}\n\n"
            f"Facts they cite:\n{fact_lines}\n\n"
            f"Thesis narrative ({baseline.get('fiscal_label') or 'no baseline'}):\n{baseline.get('narrative') or '(none)'}\n"
            f"Tracked expectation dimensions: {', '.join(baseline.get('dim_keys') or []) or '(none)'}\n\n"
            f"Company risk register:\n{items}\n\n"
            "Judge only the company-specific part; the layer-wide part is the Layer analyst's.")


def run_material_pass(request, *, store=None, rate=None) -> dict:
    """One material review for `request.symbol` from the components named in `request.extra['component_refs']`."""
    from ...memory import get_store
    from ...workflow.read_scope import read_projection
    from ..base import execution_versions, run_structured
    from .routine import load_baseline

    store = store or get_store()
    symbol = request.symbol
    refs = [str(r) for r in request.extra.get("component_refs") or []]
    if not refs:
        raise ValueError("material review requires component_refs")
    brief_ids = {r.split("#", 1)[0] for r in refs}
    brief = read_projection(store, consumer="fundamental", role="information_brief",
                            scope=ProjectionScope(kind="entity", id=symbol))
    if not brief or brief.get("projection_id") not in brief_ids:
        raise ValueError("the brief that carries the components is not the readable one for this company")
    payload = brief.get("payload") or {}
    components = components_for(payload, [r.split("#", 1)[1] for r in refs if "#" in r])
    if not components:
        raise ValueError("none of the referenced components is in the brief")
    baseline = load_baseline(store, symbol)
    register = latest_register(store, symbol)
    no_baseline = not (baseline.get("narrative") or baseline.get("dim_keys"))
    context = review_context(symbol, components, list(payload.get("fact_changes") or []), baseline, register)
    rate = rate or (lambda ctx: run_structured(REVIEW_ROLE, MaterialReviewView, ctx, skill_slug=REVIEW_SKILL))
    view = rate(context)
    now = _now()
    confidence = min(view.confidence, NO_BASELINE_CONFIDENCE_CAP) if no_baseline else view.confidence
    review = {"entity": symbol, "event": components[0].get("cluster_key", ""), "component_refs": refs,
              "thesis_status": view.thesis_status, "hit_items": view.hit_items,
              "expectation_effect": view.expectation_effect, "direction": view.direction,
              "magnitude": view.magnitude, "confidence": confidence, "narrative": view.narrative,
              "unverified": [*view.unverified, *(["无论点基线：信心上限 0.3"] if no_baseline else [])],
              "valid_until": (now + VALIDITY).isoformat(), "materiality_review": view.materiality_review,
              "no_baseline": no_baseline}
    from ..information.briefs import assert_no_advice

    assert_no_advice([view.narrative])
    envelope = build_envelope(
        role="fundamental_material_review", payload=review, scope=ProjectionScope(kind="entity", id=symbol),
        as_of=now.isoformat(), valid_until=review["valid_until"],
        input_refs=[*request.extra.get("input_refs", []), brief["projection_id"],
                    *(baseline.get("baseline_refs") or []), *([register["projection_id"]] if register else [])],
        **execution_versions(REVIEW_ROLE, REVIEW_SKILL, template_source=__file__))
    store.save_task_projection_envelope(envelope)
    return {"mode": "material", "symbol": symbol, "projection_id": envelope.projection_id,
            "summary": f"material review {view.thesis_status}, direction {view.direction}"}


def init_registers(store, symbols: list[str], *, apply: bool = False) -> dict:
    """One-time initialisation (tasks 4.1): a register for every target that has none. Dry-run by default."""
    missing = [s.upper() for s in symbols if latest_register(store, s) is None]
    out = {"targets": len(symbols), "missing": missing, "requests": len(missing), "built": {}, "failed": {}}
    if not apply:
        return out
    for symbol in missing:
        try:
            out["built"][symbol] = build_register(store, symbol)
        except Exception as exc:  # noqa: BLE001 - one company failing does not stop the others
            out["failed"][symbol] = f"{type(exc).__name__}: {exc}"[:200]
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    from ...config import load_pead_global
    from ...memory import get_store

    parser = argparse.ArgumentParser(prog="python -m ats.agents.fundamental.material",
                                     description="初始化公司风险项（event-impact-routing 4.1）；默认只列出要建的标的与请求数")
    parser.add_argument("--init", action="store_true", help="为没有风险项的 PEAD target 建立风险项")
    parser.add_argument("--apply", action="store_true", help="真正调用模型并发布；不加只做 dry-run")
    args = parser.parse_args(argv)
    if not args.init:
        parser.print_help()
        return 2
    targets = [str(s) for s in load_pead_global().get("targets", [])]
    print(json.dumps(init_registers(get_store(), targets, apply=args.apply), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
