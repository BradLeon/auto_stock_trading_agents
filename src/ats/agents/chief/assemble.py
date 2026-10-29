"""Chief context assembly — read-only rendering of the research snapshot and ledger state.

Research is rendered from published projections (each line carries its projection id and
content hash); account, trade history and performance come from Internal State with their
completeness stated. No analysis table is read. Pure code, no LLM.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ...workflow.runtime_reads import scoped_read  # noqa: E402

log = logging.getLogger("ats.agents.chief.assemble")


@dataclass
class ChiefContext:
    as_of: datetime
    net_liquidation: float = 0.0
    blocks: dict[str, str] = field(default_factory=dict)   # ordered by insertion

    def as_context(self) -> str:
        parts = [f"Chief decision context @ {self.as_of:%Y-%m-%d %H:%M} UTC. "
                 f"Book size ${self.net_liquidation:,.0f}."]
        for name, text in self.blocks.items():
            if text:
                parts.append(f"## {name}\n{text}")
        return "\n\n".join(parts)

    def stats(self) -> dict:
        out = {name: len(text) for name, text in self.blocks.items()}
        out["total_chars"] = len(self.as_context())
        return out


@scoped_read("chief")
def build(*, projection_manifest=None, query_plan=None) -> ChiefContext:
    """The Chief's decision context, rendered only from governed inputs.

    Research comes from the published research snapshot (task projection envelopes);
    account, trade history and performance come from the Internal State API through
    the declared PORTFOLIO_DATA / HISTORY_DATA inputs. No analysis table is read here,
    and a missing or partial input is stated in the context, never filled in.
    """
    from ...data.consumer_api import read_input
    from ...memory import get_store
    from ...workflow.cutover_routing import RouteUnavailable
    from ...workflow.evaluation_clock import now as evaluation_now
    from ...workflow.runtime_reads import current_read_context

    ctx = ChiefContext(as_of=evaluation_now(timezone.utc))
    read = current_read_context()
    store = get_store()
    portfolio = read_input("chief", "PORTFOLIO_DATA", scope={"kind": "portfolio"}, store=store)
    history = read_input("chief", "HISTORY_DATA", scope={"kind": "portfolio"}, store=store)
    ctx.net_liquidation = (portfolio.payload or {}).get("net_liquidation", 0)
    ctx.blocks["组合账本状态"] = _ledger_block(portfolio, history)
    ctx.blocks["风控状态"] = _risk_posture_block((history.payload or {}).get("risk_posture"), ctx.as_of)
    snapshot, detail = build_chief_snapshot(
        store, at=read.cutoff if read else ctx.as_of, projection_manifest=projection_manifest,
        query_plan=query_plan)
    if not snapshot.complete:
        raise RouteUnavailable("required Chief projections unavailable: " + gap_report(snapshot, detail))
    ctx.blocks["研究快照（六类投影）"] = projection_context_block(detail)
    ctx.blocks["战绩反馈"] = _track_record_block(store, history)
    return ctx


def _ledger_block(portfolio, history) -> str:
    """Account state with its as-of and completeness; an unreconciled ledger is called out."""
    lines = [f"{portfolio.payload} · {portfolio.status} · as_of={portfolio.source_as_of}"]
    gaps = sorted({*portfolio.gaps, *history.gaps})
    if portfolio.status != "complete" or history.status != "complete":
        lines.append("⚠ 账本未完整对账，以下持仓、成交与绩效不得当作完整事实："
                     + (("；".join(gaps)) if gaps else "来源缺失或部分可用"))
    return "\n".join(lines)


def _risk_posture_block(posture, now) -> str:
    """The risk role's latest posture, with its age: a stale posture is shown as stale, not dropped."""
    if not posture:
        return "风控尚无评审记录；本轮订单将直接经风控闸门裁决。"
    try:
        age = (now - datetime.fromisoformat(posture["as_of"])).days
    except (KeyError, ValueError, TypeError):
        age = None
    stale = f"\n⚠ 该风控评审已 {age} 天前，姿态可能已变化，以风控闸门实时裁决为准。" if age and age >= 2 else ""
    return posture["block"] + stale


def _track_record_block(store, history) -> str:
    """Latest performance, recent decisions with what became of them, and recent fills.

    Trade history and performance arrive through HISTORY_DATA (Internal State). A decision
    is marked executed only when a trade row carries its cycle and symbol; otherwise it was
    proposed and never reached the broker, which is context, not a commitment.
    """
    payload = history.payload or {}
    lines = []
    performance = payload.get("performance")
    if performance:
        lines.append(f"绩效统计: {performance}")
    lines.append(f"内部状态: {history.status} · {history.source_as_of}")
    trades = payload.get("trades") or []
    sent = {(t.get("cycle_id") or "", t.get("symbol")) for t in trades}
    for d in store.recent_decisions(limit=8):
        outcome = "已下单" if (d.get("cycle_id") or "", d["symbol"]) in sent else "未下单"
        lines.append(f"  近期决策: {d['action']} {d['symbol']} "
                     f"${d.get('notional_usd') or 0:,.0f}（{outcome}）— {(d.get('rationale') or '')[:50]}")
    for held in payload.get("approved_not_executed") or []:
        lines.append(f"  已批准未执行（未下单，不计入成交与绩效）: {held['cycle_id']} — {held.get('final_outcome') or ''}")
    for f in payload.get("fills") or []:
        rp = f" realized ${f['realized_pnl']:,.0f}" if f.get("realized_pnl") is not None else ""
        lines.append(f"  近期成交: {f['side']} {f['symbol']} {f['shares']:.0f}@{f['price']:.2f}{rp}")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Projection read path + research snapshot (Phase D Group 7, tasks 7.1/7.4-7.9)
# --------------------------------------------------------------------------- #

# The chief is the ONLY role allowed to aggregate every analyst's view; this
# module is where that aggregation lives. Each decision-required task maps to
# the projection scopes the chief must be able to read: multi-scope categories
# (one projection per layer / per target) are satisfied only when EVERY scope
# has a usable projection — a subset is a partial answer, not a complete one.
def projection_query_plan() -> dict[str, dict]:
    """task_id -> {role, scopes} the snapshot builder must cover."""

    from ...agent.task_projection import ProjectionScope
    from ...config import load_pead_global, load_sector_config

    g = load_pead_global()
    targets = [str(t).upper() for t in g.get("targets", [])]
    sectors = [str(s) for s in g.get("sector_review", {}).get("sectors", [])]
    layer_keys: list[str] = []
    for name in sectors:
        try:
            cfg = load_sector_config(name)
        except Exception:  # noqa: BLE001 - config problems surface in the gap report
            continue
        layer_keys += [layer.key for layer in cfg.layers]
    entity = [ProjectionScope(kind="entity", id=t) for t in targets]
    return {
        "layer_analysis": {
            "role": "layer_analysis",
            "scopes": [ProjectionScope(kind="layer", id=k) for k in layer_keys]},
        "information_brief": {"role": "information_brief", "scopes": entity},
        "sector_allocation": {
            "role": "sector_allocation",
            "scopes": [ProjectionScope(kind="sector", id=s) for s in sectors]},
        "fundamental_expectation_update": {
            "role": "fundamental_expectation_update", "scopes": entity},
        "fundamental_event_review": {
            "role": "fundamental_event_review", "scopes": entity},
        "macro_review": {"role": "macro_review",
                         "scopes": [ProjectionScope(kind="portfolio")]},
        "technical_review": {"role": "technical_review", "scopes": entity},
    }


def _envelope_from_row(row: dict, scope) -> Any:
    """Rebuild a TaskProjectionEnvelope from a decoded envelopes-table row."""
    from ...agent.task_projection import TaskProjectionEnvelope

    return TaskProjectionEnvelope(
        projection_id=row["projection_id"],
        workflow_run_id=row.get("workflow_run_id") or "",
        agent_run_id=row.get("agent_run_id") or "",
        agent_role=row["agent_role"], scope=scope, as_of=row["as_of"],
        valid_until=row.get("valid_until") or "",
        schema_name=row.get("schema_name") or "",
        schema_version=row.get("schema_version") or "v1",
        input_refs=row.get("input_refs") or [],
        data_vintage_refs=row.get("data_vintage_refs") or [],
        model_version=row.get("model_version") or "",
        prompt_version=row.get("prompt_version") or "",
        payload=row.get("payload") or {},
        content_hash=row.get("content_hash") or "",
        status=row.get("status") or "published",
        created_at=row.get("created_at") or "",
        supersedes_projection_id=row.get("supersedes_projection_id") or "")


def _latest_row(store, *, role: str, scope, projection_id: str = "",
                content_hash: str = "") -> dict | None:
    from ...workflow.consumer_reads import read_projection

    return read_projection(store, consumer="chief", role=role, scope=scope,
                           projection_id=projection_id, content_hash=content_hash,
                           require_usable=False)


def scan_projection_scopes(store, *, at=None,
                           projection_manifest: list[dict] | None = None,
                           query_plan=None) -> list[dict]:
    """Per (task, scope) projection state — the fine-grained gap detail.

    `status` speaks the snapshot vocabulary: `fresh` (usable), `missing`,
    `expired`, `scope_mismatch`, `schema_mismatch`… the reason strings come
    straight from Phase A's `reuse_decision` so the report never forks terms.
    """
    from ...agent.task_projection import reuse_decision
    from ...workflow.decision_requirements import routine_producer_refs, validate_routine_value_contract
    from ...workflow.cutover_routing import RouteUnavailable

    manifest = None
    if projection_manifest is not None:
        manifest = {}
        for item in projection_manifest:
            manifest.setdefault((item["role"], item["scope_kind"], item["scope_id"]), []).append(item)
    detail: list[dict] = []
    for task_id, plan in (query_plan if query_plan is not None else projection_query_plan()).items():
        for scope in plan["scopes"]:
            references = (manifest.get((plan["role"], scope.kind, scope.id), [])
                          if manifest is not None else [None])
            if manifest is None and plan["role"] == "fundamental_expectation_update":
                latest = _latest_row(store, role=plan["role"], scope=scope)
                if latest and latest.get("agent_run_id"):
                    refs = routine_producer_refs(store, latest, scope)
                    valid = bool(refs)
                    if not valid:
                        detail.append({"task_id": task_id, "role": plan["role"],
                            "scope": scope.key, "status": "producer_unresolved",
                            "projection_id": latest["projection_id"], "content_hash": latest["content_hash"],
                            "as_of": latest["as_of"], "reason": "complete terminal Routine producer required"})
                        continue
                    references = [{"projection_id": ref, "producer_attempt": latest["agent_run_id"],
                                   "producer_run": latest["workflow_run_id"]} for ref in refs]
            for selected in references or [None]:
                projection_id = str(selected.get("projection_id", "")) if selected else ""
                row = (_latest_row(store, role=plan["role"], scope=scope,
                                   projection_id=projection_id,
                                   content_hash=str(selected.get("content_hash", "")
                                                    if selected else ""))
                       if manifest is None or selected else None)
                if row is None:
                    detail.append({"task_id": task_id, "role": plan["role"],
                                   "scope": scope.key, "status": "missing",
                                   "projection_id": "", "content_hash": "",
                                   "as_of": "", "reason": "missing"})
                    continue
                envelope = _envelope_from_row(row, scope)
                if plan["role"] == "fundamental_expectation_update":
                    try:
                        validate_routine_value_contract(envelope.payload)
                    except RouteUnavailable as exc:
                        detail.append({"task_id":task_id, "role":plan["role"], "scope":scope.key,
                            "status":"value_unconfirmed", "projection_id":envelope.projection_id,
                            "content_hash":envelope.content_hash, "as_of":envelope.as_of,
                            "reason":str(exc)})
                        continue
                if (manifest is None and selected and selected.get("producer_attempt")
                        and (envelope.agent_run_id != selected["producer_attempt"]
                             or envelope.workflow_run_id != selected["producer_run"])):
                    detail.append({"task_id": task_id, "role": plan["role"], "scope": scope.key,
                        "status": "producer_mismatch", "projection_id": envelope.projection_id,
                        "content_hash": envelope.content_hash, "as_of": envelope.as_of,
                        "reason": "Routine member differs from complete terminal producer"})
                    continue
                reusable, reason = reuse_decision(envelope, scope=scope, at=at)
                detail.append({"task_id": task_id, "role": plan["role"],
                               "scope": scope.key,
                               "status": "fresh" if reusable else reason,
                               "projection_id": envelope.projection_id,
                               "as_of": envelope.as_of, "reason": reason,
                               "content_hash": envelope.content_hash,
                               "payload": envelope.payload})
    return detail


def build_chief_snapshot(store, *, at=None,
                         projection_manifest: list[dict] | None = None,
                         query_plan=None) -> tuple[Any, list[dict]]:
    """The chief's research snapshot (7.1) plus its per-scope detail.

    Multi-scope categories are satisfied only when every scope is fresh; the
    representative item records the freshest usable envelope of the category.
    The fundamental category's two modes compete per target: either mode's
    envelope may cover a target, and the winning task id is what the snapshot
    records (per the `decision/research-snapshot` contract).
    """
    from ...agent.task_projection import ProjectionScope
    from ...decision.snapshot import build_research_snapshot
    from ...workflow.run_contracts import default_registry

    def scope_from_key(key: str) -> ProjectionScope:
        kind, _, ident = key.partition(":")
        return ProjectionScope(kind=kind, id=ident)

    plan = query_plan if query_plan is not None else projection_query_plan()
    detail = scan_projection_scopes(store, at=at,
                                    projection_manifest=projection_manifest, query_plan=plan)
    envelopes: dict[str, Any] = {}
    required_scopes: dict[str, ProjectionScope] = {}

    def category_covered(task_id: str) -> tuple[dict | None, ProjectionScope | None]:
        """Representative fresh row when EVERY scope of the task is fresh."""
        rows = [d for d in detail if d["task_id"] == task_id]
        fresh = [d for d in rows if d["status"] == "fresh"]
        if not rows or len(fresh) != len(rows):
            return None, None
        best = max(fresh, key=lambda d: d["as_of"])
        scope = scope_from_key(best["scope"])
        row = _latest_row(store, role=plan[task_id]["role"], scope=scope,
                          projection_id=best["projection_id"],
                          content_hash=best["content_hash"])
        return (best, scope) if row is not None else (None, None)

    # Fundamental: per target, EITHER mode may cover the target; a category is
    # satisfied only when every target is covered by exactly one winner.
    entity_count = len(plan.get("fundamental_expectation_update", plan.get(
        "fundamental_event_review", {"scopes": []}))["scopes"])
    fund_winners: dict[str, tuple[str, dict]] = {}
    for d in detail:
        if d["task_id"] not in ("fundamental_expectation_update",
                                "fundamental_event_review"):
            continue
        current = fund_winners.get(d["scope"])
        fresh, cur_fresh = d["status"] == "fresh", (current or (None, {"status": ""}))[1]["status"] == "fresh"
        if (current is None
                or (fresh and not cur_fresh)
                or (fresh and cur_fresh and d["as_of"] > current[1]["as_of"])):
            fund_winners[d["scope"]] = (d["task_id"], d)
    for task_id in ("fundamental_expectation_update", "fundamental_event_review"):
        winners = [(scope_key, entry) for scope_key, entry in fund_winners.items()
                   if entry[0] == task_id and entry[1]["status"] == "fresh"]
        if winners and len(winners) == entity_count:
            best_key, best_entry = max(winners, key=lambda pair: pair[1][1]["as_of"])
            scope = scope_from_key(best_key)
            row = _latest_row(store, role=plan[task_id]["role"], scope=scope,
                              projection_id=best_entry[1]["projection_id"],
                              content_hash=best_entry[1]["content_hash"])
            if row is not None:
                envelopes[task_id] = _envelope_from_row(row, scope)
                required_scopes[task_id] = scope

    for task_id in ("layer_analysis", "information_brief", "sector_allocation",
                    "macro_review", "technical_review"):
        best, scope = category_covered(task_id)
        if best is not None and scope is not None:
            row = _latest_row(store, role=plan[task_id]["role"], scope=scope,
                              projection_id=best["projection_id"],
                              content_hash=best["content_hash"])
            envelopes[task_id] = _envelope_from_row(row, scope)
            required_scopes[task_id] = scope

    registry = default_registry()
    snapshot = build_research_snapshot(
        registry=registry, projections=envelopes,
        scope=ProjectionScope(kind="portfolio"),
        required_scopes=required_scopes or None, at=at)
    # Keep every required scope in the frozen basis, not only a category's
    # representative. Restart and mid-cycle checks must resolve all inputs.
    from ...decision.snapshot import SnapshotItem
    from ...workflow.run_contracts import ROLE_TO_CATEGORY

    items = []
    for d in detail:
        if ROLE_TO_CATEGORY.get(d["role"]) == "fundamental_analysis":
            winner = fund_winners.get(d["scope"])
            if not winner or winner[0] != d["task_id"]:
                continue
        kind, _, ident = d["scope"].partition(":")
        items.append(SnapshotItem(
            task_id=d["task_id"], agent_role=d["role"], category=ROLE_TO_CATEGORY[d["role"]],
            projection_id=d["projection_id"], content_hash=d["content_hash"], as_of=d["as_of"],
            reusable=d["status"] == "fresh", reason=d["reason"], scope_kind=kind, scope_id=ident))
    covered = {item.category for item in items}
    items.extend(item for item in snapshot.items if item.category not in covered)
    snapshot.items = items
    return snapshot, detail


def projection_context_block(detail: list[dict]) -> str:
    """The six categories rendered FROM projections (7.4) — never from legacy tables.

    A category with no usable projection is listed as 缺失, never omitted and
    never substituted with an empty string (7.5). A fundamental projection's
    `direction` is presented as a research input; no code path maps it to a
    trading action (7.9).
    """
    by_task: dict[str, list[dict]] = {}
    for d in detail:
        by_task.setdefault(d["task_id"], []).append(d)

    def line(task_id: str, label: str) -> str:
        rows = by_task.get(task_id, [])
        if task_id == "fundamental_expectation_update":
            # 例行与事件是同一类别的两种模式：任一模式覆盖即呈现该模式。
            rows = rows + by_task.get("fundamental_event_review", [])
        fresh = [r for r in rows if r["status"] == "fresh"]
        if not fresh:
            reasons = sorted({r["status"] for r in rows}) or {"missing"}
            return f"- {label}: **缺失**（{', '.join(sorted(reasons))}；{len(rows)} 个作用域均不可用）"
        best = max(fresh, key=lambda r: r["as_of"])
        head = (f"- {label}: {len(fresh)}/{len(rows)} 个作用域可用 · 最新 "
                f"{best['projection_id'][:18]}… @ {best['as_of']}")
        payload = best.get("payload") or {}
        if task_id == "layer_analysis":
            head += f" · status={payload.get('status')} · {payload.get('summary', '')[:80]}"
            if payload.get("cross_section"):
                head += f" · 横截面 {len(payload['cross_section'])} 个标的（依据 {payload.get('cross_section_basis', '')}）"
        elif task_id == "sector_allocation":
            head += (f" · stance={payload.get('stance')} · "
                     f"target_weight={payload.get('target_weight')}")
            if payload.get("layers"):
                head += (f" · {len(payload['layers'])} 层 / {len(payload.get('names', []))} 个标的"
                         + ("" if payload.get("rotation_available") in (None, True) else " · 轮动合成不可用"))
            for d in payload.get("cross_section_divergence") or []:
                moat = f" moat={d['moat_pricing']:+.1f}" if d.get("moat_pricing") is not None else ""
                head += (f"\n    截面（{d['layer']}）{d['symbol']} 量化第{d['quant_rank']} → 复合第{d['rank']}{moat}"
                         + (f" 依据：{d['rationale']}" if d.get("rationale") else "")
                         + "（仅供参考，不是权重）")
        elif task_id in ("fundamental_expectation_update",
                         "fundamental_event_review"):
            if "direction" in payload:
                head += (f" · direction={int(payload['direction']):+d}"
                         "（研究输入：预期差方向，非交易指令）")
            if payload.get("new_value") is not None:
                head += f" · {payload.get('metric')}={payload.get('new_value')}"
            elif payload.get("metric") == "narrative":
                head += " · 定性叙事更新（无可量化 KPI 读数）"
            head += f" · {(payload.get('narrative') or payload.get('driver') or '')[:80]}"
            baseline = payload.get("baseline")
            if baseline:
                consensus = baseline.get("consensus") or {}
                head += (f" · 财报前基线：{len(baseline.get('expectation_table') or [])} 维预期表"
                         + (" · 含共识" if consensus.get("eps") or consensus.get("revenue") else " · 共识缺失")
                         + (" · 含期权隐含" if baseline.get("market_implied") else " · 期权隐含缺失"))
            if payload.get("change_class"):
                head += f" · 归类={payload['change_class']}"
            if payload.get("comparison_basis"):
                head += f" · 对照基础={payload['comparison_basis']}"
        elif task_id == "macro_review":
            head += f" · regime={payload.get('regime')} · {payload.get('summary', '')[:80]}"
            if payload.get("quadrant"):
                head += (f" · 象限={payload['quadrant']}({payload.get('quadrant_state', '')})"
                         f" · 叙事 regime={payload.get('narrative_regime', '')[:60]}")
        elif task_id == "technical_review":
            head += f" · signal={payload.get('signal')} · {payload.get('summary', '')[:80]}"
            if payload.get("suggested_exposure") is not None:
                head += f" · 建议敞口上限={payload['suggested_exposure']:.0%}（非交易指令）"
        elif task_id == "information_brief":
            head += f" · {payload.get('headline', '')[:80]}"
        return head

    lines = [
        line("layer_analysis", "层级分析"),
        line("information_brief", "信息简报"),
        line("sector_allocation", "行业配置"),
        line("fundamental_expectation_update", "基本面（例行/事件）"),
        line("macro_review", "宏观评审"),
        line("technical_review", "技术面评审"),
    ]
    import json

    # Render every consumed opinion with a fully resolvable ID and hash. The
    # category summary alone loses scopes and the content that Chief must see.
    lines.extend(
        f"- {d['role']} [{d['scope']}] {d['status']} · {d['projection_id']} "
        f"sha256={d['content_hash']} · " + json.dumps(d.get("payload") or {}, ensure_ascii=False)
        for d in detail)
    return "\n".join(lines)


TASK_TO_CATEGORY = {
    "layer_analysis": "layer_analysis",
    "information_brief": "information_brief",
    "sector_allocation": "sector_allocation",
    "fundamental_expectation_update": "fundamental_analysis",
    "fundamental_event_review": "fundamental_analysis",
    "macro_review": "macro_review",
    "technical_review": "technical_review",
}


CATEGORY_LABELS: dict[str, str] = {
    "layer_analysis": "层级分析",
    "information_brief": "信息简报",
    "sector_allocation": "行业配置",
    "fundamental_analysis": "基本面",
    "macro_review": "宏观评审",
    "technical_review": "技术面评审",
}


def gap_report(snapshot, detail: list[dict]) -> str:
    """The incomplete-run report (7.8): what is missing/expired/failed, and why.

    This report is NOT a decision input: the graph stores it on
    `state.gap_report` and blocks the cycle before any context is assembled.
    """
    gaps = snapshot.gaps()
    lines = [f"研究快照不完整（{len(gaps)}/{len(snapshot.items)} 类缺口），"
             "决策周期被阻断 — 以下类别不得当作「没有意见」继续决策："]
    gap_categories = {item.category for item in gaps}
    for category in sorted(gap_categories):
        label = CATEGORY_LABELS.get(category, category)
        task_rows = [d for d in detail if TASK_TO_CATEGORY.get(d["task_id"]) == category]
        bad = [d for d in task_rows if d["status"] != "fresh"]
        for d in sorted(bad, key=lambda x: x["scope"]):
            lines.append(f"- {label}（{category}）· {d['scope']}: {d['status']}"
                         + (f"（{d['reason']}）" if d["reason"] != d["status"] else ""))
    lines.append("影响范围：不进入主理人决策、审批与执行；缺口补齐后需以新快照重新发起决策周期。")
    return "\n".join(lines)
