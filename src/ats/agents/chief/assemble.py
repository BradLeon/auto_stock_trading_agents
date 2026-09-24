"""Chief context assembly — read-only gathering of ALL published artifacts.

Blocks (each degrades to "" on failure): live portfolio, PEAD dossiers (with
freshness), sector review company_calls, macro review sector_tilts, risk review
state/breaches, and the track record. Pure code, no LLM.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("ats.agents.chief.assemble")

FRESH_SCORE_DAYS = 3   # a score-phase dossier older than this is background, not actionable


@dataclass
class ChiefContext:
    as_of: datetime
    net_liquidation: float = 0.0
    held_symbols: set = field(default_factory=set)          # symbols currently held
    # (symbol, fiscal_label) of scores that are actionable THIS cycle (fresh + unconsumed).
    # The chief flow marks these consumed after a successful run → a PEAD score fires ONCE.
    actionable_scores: set = field(default_factory=set)
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


def build(*, live_broker: bool = True) -> ChiefContext:
    from ...config import get_config

    ctx = ChiefContext(as_of=datetime.now(timezone.utc))
    ctx.blocks["组合现状 (trader)"] = _portfolio_block(ctx, live_broker)
    if not ctx.net_liquidation:
        ctx.net_liquidation = get_config().app.account.net_liquidation_usd
    ctx.blocks["PEAD 档案（新鲜=事件信号一次；否则背景）"] = _pead_block(ctx.held_symbols, ctx)
    ctx.blocks["技术面（择时/敞口建议，非方向判断）"] = _technical_block()
    ctx.blocks["行业评审（倾斜修正）"] = _sector_block(ctx.held_symbols)
    ctx.blocks["宏观评审（倾斜修正）"] = _macro_block()
    ctx.blocks["风控状态（硬约束）"] = _risk_block()
    ctx.blocks["近期决策与执行（时序·勿重复）"] = _recent_actions_block()
    ctx.blocks["战绩反馈"] = _track_record_block()
    return ctx


def _portfolio_block(ctx: ChiefContext, live_broker: bool) -> str:
    if not live_broker:
        return "(offline — 无实时持仓)"
    try:
        from ...trader import portfolio as tport

        pf = tport.snapshot()
        if pf is None:
            return "(IBKR 不可达 — 无实时持仓)"
        ctx.net_liquidation = pf.net_liquidation
        ctx.held_symbols = {p.symbol.upper() for p in pf.positions}
        # effective cash = raw cash + cash-equivalent credit (SGOV/SHV/BRK-B etc.)
        from ...config import get_config
        from ...risk.assess import _norm_sym
        ce_norm = {_norm_sym(k): v for k, v in (get_config().app.risk.cash_equivalents or {}).items()}
        held_hc = {p.symbol: ce_norm[_norm_sym(p.symbol)]
                   for p in pf.positions if _norm_sym(p.symbol) in ce_norm}
        cash_credit = sum(p.market_value * (1.0 - held_hc[p.symbol])
                          for p in pf.positions if p.symbol in held_hc)
        eff_cash_pct = (pf.cash + cash_credit) / pf.net_liquidation if pf.net_liquidation else 0.0
        lines = [f"NetLiq ${pf.net_liquidation:,.0f} · cash {pf.cash/pf.net_liquidation:.0%}"
                 f"（含现金等价物有效 {eff_cash_pct:.0%}）· "
                 f"杠杆 {pf.leverage:.2f}x · 日盈亏 ${pf.daily_pnl:,.0f}"]
        for p in pf.positions:
            tag = " [现金等价物]" if p.symbol in held_hc else ""
            if (getattr(p, "sec_type", "STK") or "STK") == "OPT":
                tag += " [期权·已并入6层风控(Δ名义/BSM，见风控块)]"
            lines.append(f"  {p.symbol} w={p.weight:.1%} uPnL=${p.unrealized_pnl:,.0f}{tag}")
        return "\n".join(lines)
    except Exception as exc:  # noqa: BLE001
        log.warning("chief portfolio block failed: %s", exc)
        return ""


def _score_age_days(run: dict | None, dossier, now: datetime) -> int:
    """Days since the score was produced.

    Prefers pead_score_runs.scored_at — dossier.updated_at is bumped daily by the
    monitor, so it measures "last touched", not "when scored".
    """
    stamp = (run or {}).get("scored_at")
    if stamp:
        try:
            ts = datetime.fromisoformat(stamp)
            return (now - (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc))).days
        except ValueError:
            pass
    up = dossier.updated_at
    return (now - up.replace(tzinfo=up.tzinfo or timezone.utc)).days


def _pead_block(held_symbols: set | None = None, ctx: "ChiefContext | None" = None) -> str:
    """PEAD dossiers. A score is ACTIONABLE (event trade, once) only if it's a fresh
    score AND not yet consumed by a prior chief cycle; otherwise it's background
    (thesis / positioning) — the actionable "分析师建议 分步减仓" line is dropped so
    the chief doesn't re-trigger the same trim day after day."""
    from ...config import load_pead_config, load_pead_global
    from ...memory import get_store

    store = get_store()
    now = datetime.now(timezone.utc)
    parts = []
    for sym in load_pead_global().get("targets", []):
        try:
            cfg = load_pead_config(sym)
            d = store.get_dossier(sym.upper(), cfg.fiscal_label)
            if d is None:
                # The label may have been DERIVED from the earnings calendar rather
                # than hand-written in config (data/period.resolve_fiscal_label), so
                # an exact config-label lookup can miss a dossier that exists. Fall
                # back to the freshest stored one — better the real quarter than a
                # silently empty PEAD block.
                recent = store.recent_dossiers(sym.upper(), limit=1)
                if recent:
                    d = store.get_dossier(sym.upper(), recent[0]["fiscal_label"])
        except Exception:  # noqa: BLE001
            continue
        if d is None:
            continue
        is_score = d.phase == "score"
        run = store.latest_score_run(sym.upper(), d.fiscal_label) if is_score else None
        # Age from the SCORE, not from the dossier: the daily monitor rewrites the row
        # and bumps updated_at, so a scored dossier is perpetually "0 days old" and
        # FRESH_SCORE_DAYS would never expire it.
        age = _score_age_days(run, d, now)
        consumed = store.is_score_consumed(sym.upper(), d.fiscal_label)
        # A transcript-less v1 is deliberately withheld: the window keeps retrying for
        # the transcript, and only the final score is offered to the Chief — so the
        # Chief responds once, to the best available evidence.
        final = bool(run and (run["has_transcript"] or run["final"])) if run else is_score
        fresh = is_score and final and age <= FRESH_SCORE_DAYS and not consumed
        if fresh and ctx is not None:
            ctx.actionable_scores.add((sym.upper(), d.fiscal_label))
        if fresh:
            tag = "，**新鲜可行动（事件响应·仅此一次，动作后即失效）**"
        elif is_score and consumed:
            tag = "，仅背景（score 已消费→只作论点/持仓定位）"
        elif is_score and not final:
            tag = "，仅背景（v1 缺电话会纪要，等纪要到位后重打为终版）"
        else:
            tag = "，仅背景"
        lines = [f"### {sym} ({d.fiscal_label}, phase={d.phase}, 打分于 {age} 天前{tag})"]
        if d.scorecard:
            lines.append(f"Scorecard: {d.scorecard.total:+.2f} (门槛 {d.scorecard.threshold:+.1f}) "
                         f"— {d.scorecard.band}")
        if run and not run["has_transcript"]:
            lines.append("⚠️ 证据: 仅财报稿/8-K 数字，**无电话会纪要** —— guidance 与"
                         "管理层口风缺失，打分偏保守，仓位建议已减半")
        if d.decision_summary and fresh:            # 可行动建议仅在新鲜未消费时出现
            lines.append(f"PEAD 分析师建议: {d.decision_summary}")
        if d.signal_chain_summary:
            # Cross-ticker read-through (upstream capacity / downstream CapEx → this name).
            # The industry_analyst produces it at prep; it used to reach only the Obsidian
            # report, never the decision desk.
            lines.append(f"信号链: {d.signal_chain_summary[:300]}")
        if d.market_setup:
            ms = d.market_setup
            lines.append(f"Setup: 抢跑 vs 板块 {ms.run_up_vs_sector_pct}% · EM {ms.expected_move_pct}%")
        if d.expectation_set and d.expectation_set.narrative:
            lines.append("叙事尾部: …" + d.expectation_set.narrative[-400:])
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _cycle_day(cid: str) -> str:
    m = re.search(r"-(\d{8})-", cid or "")
    return f"{m.group(1)[4:6]}-{m.group(1)[6:8]}" if m else "?"


def _recent_actions_block() -> str:
    """Per-symbol most-recent decision + REAL execution status. Suppression is keyed on
    whether the order actually REACHED THE BROKER (a trades row), not on the mere
    existence of a past proposal:
      - filled / submitted → in-flight or done → hard 'don't re-stack';
      - a proposal with NO trades row → never executed (unapproved) → shown as context
        only, NOT a hard block. In an approval-gated system an un-approved proposal is
        not a commitment, so if the situation still warrants it, re-proposing for
        approval is legitimate — this is what stops a never-approved 'ghost' from
        suppressing new decisions forever."""
    from ...memory import get_store

    store = get_store()
    decisions = store.recent_decisions(limit=20)   # DESC by rowid → first per symbol = latest
    if not decisions:
        return ""
    latest: dict[str, dict] = {}
    for d in decisions:
        latest.setdefault(d["symbol"], d)

    inflight, proposed = [], []
    for sym, d in latest.items():
        cid = d.get("cycle_id") or ""
        # Task 6.4: trade history now comes via the Internal State API —
        # same rows, plus as-of/completeness qualification at the boundary.
        from ...execution import state_api

        trades = [t for t in state_api.recent_trades(store, sym, limit=8)
                  if (t.get("cycle_id") or "") == cid]
        line = f"- {sym}: [{_cycle_day(cid)}] {d['action']} ${d.get('notional_usd') or 0:,.0f}"
        if any((t.get("status") or "") == "filled" for t in trades):
            inflight.append(line + " → 已成交（仓位已变，勿重复）")
        elif trades:
            inflight.append(line + " → 已提交未成交（在途，勿叠单）")
        else:
            proposed.append(line)

    lines: list[str] = []
    if inflight:
        lines.append("**在途/已成交（勿重复）**：已成交或已下单在途的同标同向单不要再叠一笔；分步已在进行的按剩余仓位而非机械重复。")
        lines += inflight
    if proposed:
        lines.append("**曾提议但未执行（仅参考，不构成重复）**：以下是过往提过、但从未下单（未获审批）的同标决策。"
                     "它们不是承诺——若当前情况仍成立，可再次提交审批；若已不成立，忽略即可。")
        lines += proposed
    return "\n".join(lines)


def _technical_block() -> str:
    """Deterministic timing/exposure readings. Advisory only — never an order.

    Deliberately placed AFTER the PEAD block: PEAD says what a name is worth,
    technical says whether now is a good moment to hold that much of it. The
    Chief needs the thesis before the timing overlay on it.
    """
    from ..technical import context as tech_context

    return tech_context.chief_block(max_chars=1200)


def _sector_block(held_symbols: set | None = None) -> str:
    """Sector tilts for Chief. Company-level calls are filtered to:
    (a) symbols in the live portfolio, OR
    (b) explicit non-hold calls (增持/减持/卖出) — highest-signal deviations only.
    This keeps the context focused; the full sector report is in Obsidian.
    """
    from ...config import load_pead_global
    from ...memory import get_store

    held = {s.upper() for s in (held_symbols or set())}
    parts = []
    for name in load_pead_global()["sector_review"]["sectors"]:
        r = get_store().latest_sector_review(name)
        if r is None or r.regime.startswith("("):
            continue
        lines = [f"[{name} @ {r.as_of:%Y-%m-%d}] {r.regime}"]
        if r.rotation_advice:
            lines.append(f"轮动建议: {r.rotation_advice}")
        # Layer-level 景气 (L1-L6). The weekly engine computes these for every layer —
        # they belong on the decision desk, not only in the Obsidian report. (This read
        # used to look for a `layer_views` attribute that SectorReview never had, so the
        # loop silently never ran and the chief never saw per-layer 景气 at all.)
        for lv in r.layers:
            lines.append(f"  层 {lv.label or lv.key}: 景气{lv.boom_score:.0f} "
                         f"[{lv.signal}] {lv.supply_demand[:80]}")
        lines += _basket_lines(r)
        # Company calls: only held positions + actionable (非持有) calls
        for c in r.company_calls:
            sym = c.symbol.upper()
            is_held = sym in held
            is_actionable = c.stance not in ("持有",)
            if is_held or is_actionable:
                tag = "★持仓" if is_held else ""
                lines.append(f"  {c.stance} {c.symbol}{tag} ({c.conviction:.2f}): {c.rationale[:100]}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _basket_lines(review) -> list[str]:
    """Cross-section: where the structural evidence DISAGREES with the pure-quant rank.

    Deliberately reports the disagreement and its reason, never the suggested weights.
    The factor model computes "三星 13% / 海力士 9%"; handing those numbers to the Chief
    would get them copied, which quietly moves the sizing decision to a model nobody is
    accountable for. The Chief is the only decision-maker — it gets the evidence and the
    relative ordering, and decides sizing itself.
    """
    from ...config import load_pead_global

    try:
        if not load_pead_global()["sector_review"].get("feed_chief_basket", True):
            return []
    except Exception:  # noqa: BLE001
        return []
    out: list[str] = []
    for b in getattr(review, "baskets", None) or []:
        if not b.structural:
            continue          # pure-quant ranking says nothing new about position
        moved = [r for r in b.rows if r.quant_rank and r.rank and r.rank != r.quant_rank]
        if not moved:
            continue
        out.append(f"  截面（{b.layer_key}）复合排名 vs 纯量化排名的分歧：")
        for r in sorted(moved, key=lambda x: x.rank)[:5]:
            why = (r.rationale or "").strip().replace("\n", " ")
            moat = f" moat={r.moat_pricing:+.1f}" if r.moat_pricing is not None else ""
            out.append(f"    {r.symbol} 量化第{r.quant_rank} → 复合第{r.rank}{moat}"
                       + (f" 依据：{why[:110]}" if why else ""))
    return out


def _macro_block() -> str:
    from ...config import load_pead_global
    from ...memory import get_store

    r = get_store().latest_macro_review(load_pead_global()["macro_review"]["name"])
    return r.regime_block(1500) if r and not r.regime.startswith("(") else ""


def _risk_block() -> str:
    from ...memory import get_store

    r = get_store().latest_risk_review()
    if r is None:
        return ""
    block = r.regime_block(1200)
    if r.directive:
        return block
    if r.risk_state == "derisk":
        block = "**⛔ de-risk 态：只允许减仓决策，禁止任何新买。**\n" + block
    return block


def _track_record_block() -> str:
    from ...memory import get_store

    store = get_store()
    lines = []
    perf = store.last_performance()
    if perf:
        lines.append(f"最新绩效: NetLiq ${perf.net_liquidation:,.0f} 日盈亏 ${perf.daily_pnl:,.0f} "
                     f"累计 ${perf.cumulative_pnl:,.0f}")
    for d in store.recent_decisions(limit=8):
        lines.append(f"  近期决策: {d['action']} {d['symbol']} "
                     f"${d.get('notional_usd') or 0:,.0f} — {(d.get('rationale') or '')[:50]}")
    # Task 6.4: fills also come via the Internal State API now.
    from ...execution import state_api

    for f in state_api.recent_fills(store, limit=5):
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
    from types import SimpleNamespace

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
    if projection_id:
        row = store.get_task_projection(projection_id)
        if (row is None or row.get("agent_role") != role
                or row.get("scope_kind") != scope.kind or row.get("scope_id") != scope.id
                or (content_hash and row.get("content_hash") != content_hash)):
            return None
        return row
    rows = store.task_projection_envelopes(
        agent_role=role, scope_kind=scope.kind, scope_id=scope.id, limit=1)
    return rows[0] if rows else None


def scan_projection_scopes(store, *, at=None,
                           projection_manifest: list[dict] | None = None) -> list[dict]:
    """Per (task, scope) projection state — the fine-grained gap detail.

    `status` speaks the snapshot vocabulary: `fresh` (usable), `missing`,
    `expired`, `scope_mismatch`, `schema_mismatch`… the reason strings come
    straight from Phase A's `reuse_decision` so the report never forks terms.
    """
    from ...agent.task_projection import reuse_decision

    manifest = None
    if projection_manifest is not None:
        manifest = {(item["role"], item["scope_kind"], item["scope_id"]): item
                    for item in projection_manifest}
    detail: list[dict] = []
    for task_id, plan in projection_query_plan().items():
        for scope in plan["scopes"]:
            selected = (manifest.get((plan["role"], scope.kind, scope.id))
                        if manifest is not None else None)
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
                         projection_manifest: list[dict] | None = None) -> tuple[Any, list[dict]]:
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

    plan = projection_query_plan()
    detail = scan_projection_scopes(store, at=at,
                                    projection_manifest=projection_manifest)
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
    entity_count = len(plan["fundamental_expectation_update"]["scopes"])
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
        elif task_id == "sector_allocation":
            head += (f" · stance={payload.get('stance')} · "
                     f"target_weight={payload.get('target_weight')}")
        elif task_id in ("fundamental_expectation_update",
                         "fundamental_event_review"):
            if "direction" in payload:
                head += (f" · direction={int(payload['direction']):+d}"
                         "（研究输入：预期差方向，非交易指令）")
            if "new_value" in payload:
                head += f" · {payload.get('metric')}={payload.get('new_value')}"
            head += f" · {(payload.get('narrative') or payload.get('driver') or '')[:80]}"
        elif task_id == "macro_review":
            head += f" · regime={payload.get('regime')} · {payload.get('summary', '')[:80]}"
        elif task_id == "technical_review":
            head += f" · signal={payload.get('signal')} · {payload.get('summary', '')[:80]}"
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
    return "\n".join(lines)


# Legacy block each category used to be read from (7.4 dual-read comparison).
# `information_brief` has no legacy counterpart — the role is new in Phase D —
# so it is excluded from the presence comparison.
CATEGORY_TO_LEGACY_BLOCK: dict[str, str] = {
    "layer_analysis": "行业评审（倾斜修正）",
    "sector_allocation": "行业评审（倾斜修正）",
    "fundamental_analysis": "PEAD 档案（新鲜=事件信号一次；否则背景）",
    "macro_review": "宏观评审（倾斜修正）",
    "technical_review": "技术面（择时/敞口建议，非方向判断）",
}

TASK_TO_CATEGORY = {
    "layer_analysis": "layer_analysis",
    "information_brief": "information_brief",
    "sector_allocation": "sector_allocation",
    "fundamental_expectation_update": "fundamental_analysis",
    "fundamental_event_review": "fundamental_analysis",
    "macro_review": "macro_review",
    "technical_review": "technical_review",
}


def dual_read_diffs(ctx: "ChiefContext", detail: list[dict]) -> dict[str, int]:
    """Per-category presence disagreement between the two read paths (7.4).

    0 means the projection path and the legacy direct read agree that the
    category is (or is not) represented in the context; 1 means exactly one of
    them has it. Counts are recorded, never used to gate — the gate is the
    snapshot's own completeness.
    """
    category_fresh: dict[str, bool] = {}
    for d in detail:
        category = TASK_TO_CATEGORY.get(d["task_id"])
        if category is None:
            continue
        category_fresh[category] = category_fresh.get(category, False) \
            or d["status"] == "fresh"
    diffs: dict[str, int] = {}
    for category, block in CATEGORY_TO_LEGACY_BLOCK.items():
        legacy_present = bool(ctx.blocks.get(block))
        proj_present = category_fresh.get(category, False)
        diffs[category] = int(legacy_present != proj_present)
    return diffs


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
