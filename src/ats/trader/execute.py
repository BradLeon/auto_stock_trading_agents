"""Approval-gated order execution. ANY order to the broker requires human
confirmation; every attempt persists its full context. Deterministic, no LLM.

v0.3: the actual pipeline (risk gate -> approval interrupt -> place -> persist)
lives in the chief decision graph (graph/chief.py). This module keeps the
reusable pieces the graph nodes call, plus thin `execute()`/`manual()` wrappers
so `ats trader execute/buy/sell` funnel into the same graph.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone

from ..broker import IBKRBroker, IBKRUnavailable
from ..schemas.decision import BossApproval, TradeDecision, action_direction
from ..schemas.memory import TradeLogEntry

log = logging.getLogger("ats.trader.execute")

_LIVE_PORTS = {7496, 4001}   # TWS/Gateway live; 7497/4002 are paper


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Thin entry points — both run through the chief decision graph (decide=False)
# --------------------------------------------------------------------------- #
def execute(decisions: list[TradeDecision], *, source: str, channel: str = "cli",
            dry_run: bool = False, auto: bool = False,
            event_data: dict[str, dict] | None = None) -> list[TradeLogEntry]:
    """Size, apply the 6-layer risk gate, request human approval, place, persist."""
    from ..graph.chief_state import ChiefDecisionState
    from ..runtime.cli import run_decision_graph

    now = _now()
    state = ChiefDecisionState(
        cycle_id=f"trader-{now:%Y%m%d%H%M%S}", as_of=now, source=source,
        dry_run=dry_run, auto_approve=auto, decide=False,
        seed_decisions=list(decisions), event_data=event_data or {})
    result = run_decision_graph(state, channel=channel)
    return list(result.get("order_results") or [])


def manual(symbol: str, action: str, qty: float, *, order_type: str = "limit",
           limit_price: float | None = None, channel: str = "cli", dry_run: bool = False,
           auto: bool = False) -> list[TradeLogEntry]:
    d = TradeDecision(symbol=symbol.upper(), action=action, qty=qty, order_type=order_type,
                      limit_price=limit_price, rationale="manual order")
    return execute([d], source="manual", channel=channel, dry_run=dry_run, auto=auto)


# --------------------------------------------------------------------------- #
# Reusable pieces (called by graph/chief.py nodes)
# --------------------------------------------------------------------------- #
def size_decisions(decisions: list[TradeDecision]) -> list[tuple[TradeDecision, float]]:
    return [(d, _size(d)) for d in decisions]


def as_overnight_limits(decisions: list[TradeDecision],
                        slippage_pct: float = 0.5) -> tuple[list[TradeDecision], list[str]]:
    """Reprice market orders as limits, for decisions raised outside market hours.

    The PEAD after-close window scores at 20:00 ET and the Boss approves it around
    08:00 Asia — hours before the open. A market order submitted then simply queues
    to the open, and on a post-earnings gap that is the worst available fill. A limit
    off the last close caps the damage: if the gap blows through it the order just
    doesn't fill, which for a stock that already moved 12% is usually the right answer.

    Returns (decisions, notes). Decisions already carrying a limit are left alone.
    A decision whose reference price can't be fetched stays a market order and says
    so in the notes — better a visible market order on the approval card than a
    silently invented limit.
    """
    out: list[TradeDecision] = []
    notes: list[str] = []
    for d in decisions:
        if d.order_type == "limit" and d.limit_price:
            out.append(d)
            continue
        ref = _last_price(d.symbol)
        if not ref:
            out.append(d)
            notes.append(f"{d.symbol}: 取不到参考价，保持市价单（隔夜单请人工确认）")
            continue
        # Buying needs a worse price to fill; selling needs a better one. `hold` never
        # reaches here (no order), and an unknown action raises instead of defaulting.
        mult = (1 + slippage_pct / 100 if action_direction(
            d.action, where="execute._to_limit_orders") > 0
            else 1 - slippage_pct / 100)
        limit = round(ref * mult, 2)
        out.append(d.model_copy(update={"order_type": "limit", "limit_price": limit}))
        notes.append(f"{d.symbol}: 隔夜单改限价 {limit}（参考 {ref:.2f} {mult:+.2%}）")
    return out, notes


def build_approval_summary(sized: list[tuple[TradeDecision, float]],
                           risk_notes: list[str], source: str) -> str:
    """Banner + risk block + order lines — the body the Boss sees on the card."""
    from ..config import get_config

    secrets = get_config().secrets
    env = get_config().app.environment
    is_live = secrets.ibkr_port in _LIVE_PORTS or env == "live"
    banner = (f"account={secrets.ibkr_account or '(default)'} @ {secrets.ibkr_host}:{secrets.ibkr_port} "
              f"[{'⚠️ LIVE ACCOUNT' if is_live else 'paper'}]")
    # Show the declared plan and the ledger id: the Boss should see, at the moment of
    # approving, that every order is already on the record with its exit criterion.
    def _line(d, q) -> str:
        px = f"@ {d.limit_price}" if d.limit_price else "(mkt)"
        plan = []
        if d.stop_price:
            plan.append(f"止 {d.stop_price:g}")
        if d.target_price:
            plan.append(f"标 {d.target_price:g}")
        if d.planned_horizon_days:
            plan.append(f"{d.planned_horizon_days}日")
        tail = f" [{d.setup}" + (f" · {' · '.join(plan)}" if plan else "") + "]" if (
            d.setup and d.setup != "unknown") or plan else ""
        return f"  {d.action.upper()} {d.symbol} x{q:.0f} {px}{tail} — {d.rationale[:60]}"

    lines = "\n".join(_line(d, q) for d, q in sized)
    risk_block = ("\n风控: " + "; ".join(risk_notes)) if risk_notes else "\n风控: 无破限 ✅"
    summary = f"{banner}\nsource={source}{risk_block}\nOrders:\n{lines}"
    if is_live:
        summary = "⚠️⚠️ 实盘账户，请务必确认 ⚠️⚠️\n" + summary
    return summary


def cancelled_entries(sized: list[tuple[TradeDecision, float]], cycle_id: str,
                      status: str) -> list[TradeLogEntry]:
    return [TradeLogEntry(order_id="", cycle_id=cycle_id, symbol=d.symbol,
                          action=d.action, qty=q, order_type=d.order_type,
                          limit_price=d.limit_price, status="cancelled",
                          submitted_at=_now(), rationale=d.rationale,
                          error=f"not executed ({status})")
            for d, q in sized]


# ── 自动下单停用（Phase F 任务 7.9 裁决，2026-10-06）────────────────────────
#
# 停用原因：下单前需要「参考价」把按金额的决策换算成股数、并给市价单算滑点保护，
# 但行情数据**未授权给 trader**（`MARKET_DATA` 的 allowed_consumers 只有 technical/risk，
# 而 trader 的 products 只有 `APPROVED_EXECUTION_AUTHORIZATION`，且授权链十项字段里
# 只有行情**时点**、没有价格）。这是契约与现实不符，不是可以顺手改的越权读取。
#
# 为何不直接去掉取价：`_size`（:288）用同一个价做金额→股数换算，砍掉它会让
# 「只给金额、未给股数」的决策算出 0 股而**静默不下单**——那是丢弃而非市价下单。
# 故按下单开关整体停用，让每次不成交都有一个可见且有据的原因。
#
# 为何不用「改授权」那条路：授权清单是受证据指纹约束的文件，改它会作废全部已登记
# 证据。运行期间以人工在券商下单规避，是用户 2026-10-06 的决定。
#
# TODO(方案 A，补授权后单独开发)：给 trader's products 补声明行情数据，让参考价
# 取自受治理读取面；届时恢复本开关与 `_to_limit_orders` / `_size` 的取价。
# 该改动须在**任何资格证据登记之前**完成（design 决策 9），否则作废全部已登记证据。
AUTO_EXECUTION_ENABLED = False
AUTO_EXECUTION_DISABLED_REASON = (
    "自动下单已停用：参考价所需的行情数据未授权给 trader（契约冲突，见任务 7.9）。"
    "当前以人工在券商下单替代；恢复须按方案 A 补授权后单独开发。")


def place_orders(to_place: list[tuple[TradeDecision, float]], cycle_id: str,
                 *, revision_no: int = 0,
                 authorization: dict | None = None
                 ) -> tuple[list[TradeLogEntry], list[dict]]:
    """Submit via IBKR; degrade to error entries (never raises) if TWS is down.

    Execution authorization gate (tasks 7.2/7.9): a batch may only be submitted
    against a complete ExecutionAuthorization (dict form, §10.4). Bare
    instructions — no authorization — are rejected before the broker is touched.

    Retry idempotency (task 7.8): before submitting, each order's local record
    is checked. One that is already submitted/filled locally is NOT re-submitted
    (the outcome is uncertain — it stays for reconciliation); only confirmed
    unsubmitted intents proceed.

    自动下单开关（任务 7.9）：停用时**在触碰券商之前**拒绝，理由入账。
    刻意与上面的授权拒绝同形——「不成交」必须有可见且有据的原因，
    而不是一个数字为 0 的静默丢弃。
    """
    if not AUTO_EXECUTION_ENABLED:
        entries = [TradeLogEntry(order_id="", cycle_id=cycle_id, symbol=d.symbol,
                                 action=d.action, qty=q, revision_no=revision_no,
                                 order_seq=i, status="rejected", submitted_at=_now(),
                                 rationale=d.rationale,
                                 error=f"auto execution disabled: {AUTO_EXECUTION_DISABLED_REASON}")
                   for i, (d, q) in enumerate(to_place)]
        print(f"🚫 自动下单已停用（{len(entries)} 笔未提交）："
              f"{AUTO_EXECUTION_DISABLED_REASON}")
        return entries, []

    if not authorization:
        entries = [TradeLogEntry(order_id="", cycle_id=cycle_id, symbol=d.symbol,
                                 action=d.action, qty=q, revision_no=revision_no,
                                 order_seq=i, status="rejected", submitted_at=_now(),
                                 rationale=d.rationale,
                                 error="missing execution authorization")
                   for i, (d, q) in enumerate(to_place)]
        print("🚫 执行被拒绝：缺少完整执行授权（审查+审批+修订绑定）")
        return entries, []

    # Task 2.1: every entry that reaches for the broker carries the full
    # decision chain, taken from the verified authorization — never rebuilt.
    chain = {"decision_hash": authorization.get("decision_hash", ""),
             "approval_id": authorization.get("approval_id", "")}

    from ..memory import get_store

    store = get_store()
    held: list[tuple[int, TradeLogEntry]] = []
    fresh: list[tuple[int, TradeDecision, float]] = []
    for i, (d, q) in enumerate(to_place):
        coid = store.client_order_id(cycle_id, revision_no, i, d.symbol, d.action)
        prior = store.conn.execute(
            "SELECT status FROM trades WHERE client_order_id = ?", (coid,)).fetchone()
        if prior is not None and prior["status"] in ("submitted", "filled", "pending"):
            # Uncertain outcome: the first attempt may have reached the broker.
            # Never produce a second logical order — leave it to reconciliation.
            held.append((i, TradeLogEntry(
                order_id="", cycle_id=cycle_id, symbol=d.symbol, action=d.action,
                qty=q, revision_no=revision_no, order_seq=i,
                status=prior["status"], submitted_at=_now(), rationale=d.rationale,
                error="retry skipped: prior attempt outcome uncertain "
                      "(pending reconciliation)", **chain)))
        else:
            fresh.append((i, d, q))

    entries: list[TradeLogEntry] = [e for _, e in held]
    fills: list[dict] = []
    if fresh:
        try:
            broker = IBKRBroker()
            ordered = [(d, q) for _, d, q in fresh]
            submitted = broker.place_orders(ordered, cycle_id,
                                            revision_no=revision_no, chain=chain)
            fills = broker.get_fills()
            entries.extend(submitted)
        except IBKRUnavailable as exc:
            print(f"❌ IBKR unavailable: {exc}")
            entries.extend([TradeLogEntry(
                order_id="", cycle_id=cycle_id, symbol=d.symbol, action=d.action,
                qty=q, revision_no=revision_no, order_seq=i, status="error",
                submitted_at=_now(), rationale=d.rationale, error=str(exc), **chain)
                for i, d, q in fresh])
    return entries, fills


def approval_divergence(approval: BossApproval | None,
                        proposed: list[TradeDecision]) -> dict:
    """Where the human disagreed with the agent.

    This is the most valuable field in the journal, and it used to be discarded:
    `trade_context_json` kept only status + reviewer. In an automated system the
    human-journal staple "did I follow my plan?" is trivially yes — the machine
    cannot deviate. The meaningful analogue is the reverse: where did the Boss drop,
    override or add to what the agent proposed. Recording it is the only way to later
    ask whether those interventions helped or hurt.
    """
    if approval is None:
        return {}
    proposed_syms = [d.symbol for d in proposed]
    eff_syms = [d.symbol for d in approval.effective_decisions(proposed)]
    out = {
        "status": approval.status,
        "reviewer": approval.reviewer,
        "reviewed_at": approval.reviewed_at.isoformat() if approval.reviewed_at else None,
        "comment": approval.comment,
        "proposed_symbols": proposed_syms,
        "effective_symbols": eff_syms,
        "dropped_symbols": [s for s in proposed_syms if s not in eff_syms],
        "added_symbols": [s for s in eff_syms if s not in proposed_syms],
        "rejected_symbols": list(approval.rejected_symbols),
        "overrides": [d.model_dump(mode="json") for d in approval.overrides],
        "direct_instructions": [d.model_dump(mode="json") for d in approval.direct_instructions],
    }
    out["diverged"] = bool(out["dropped_symbols"] or out["added_symbols"]
                           or out["overrides"] or approval.status != "approved")
    return out


def trade_context_json(source: str, approval: BossApproval | None,
                       decisions: list[TradeDecision], *,
                       decision: TradeDecision | None = None,
                       risk_notes: list[str] | None = None) -> str:
    """Per-order audit snapshot.

    `decision` narrows the payload to the single order this row is about — every row
    previously carried the whole cycle's decisions (900-1200 bytes duplicated N times).
    `risk_notes` records why sizing was clipped or an order blocked; those were printed
    to the console and shown on the approval card, then thrown away.
    """
    own = [decision] if decision is not None else decisions
    return json.dumps({
        "source": source,
        "approval": approval_divergence(approval, decisions),
        "risk_notes": list(risk_notes or []),
        "decisions": [d.model_dump(mode="json") for d in own],
    }, ensure_ascii=False)


def pead_event_data() -> dict[str, dict]:
    """Expected Move per PEAD target from the freshest dossiers (for the L6 risk gate)."""
    from ..config import load_pead_config, load_pead_global
    from ..memory import get_store

    out: dict[str, dict] = {}
    for sym in load_pead_global().get("targets", []):
        try:
            d = get_store().get_dossier(sym.upper(), load_pead_config(sym).fiscal_label)
            if d and d.market_setup and d.market_setup.expected_move_pct:
                out[sym.upper()] = {"expected_move_pct": d.market_setup.expected_move_pct}
        except Exception:  # noqa: BLE001
            continue
    return out


def _size(d: TradeDecision) -> float:
    if d.qty:
        return float(abs(d.qty))
    if d.notional_usd:
        px = _last_price(d.symbol)
        if px and math.isfinite(px) and px > 0:
            return float(round(d.notional_usd / px))
    return 0.0


def _last_price(symbol: str) -> float | None:
    """Reference price for sizing and slippage caps. **Currently disabled.**

    Kept as a named function rather than deleted so the future plan-A work has one
    obvious place to change, and so the reason travels with the code. Reading a
    price is an unauthorized read while `MARKET_DATA` is not declared for trader
    (task 7.9), and the auto-execution gate refuses the batch before any sizing
    needs it — so the provider is never reached.

    TODO(方案 A，补授权后恢复): return the governed read instead of None.
    """
    return None


def _last_price_enabled(symbol: str) -> float | None:
    """DISABLED — retained for the plan-A restore, called by nothing.

    TODO(方案 A，补授权后恢复): this becomes the governed read.

    Deliberately a SEPARATE function rather than a `return None` followed by
    unreachable code: dead code after an unconditional return reads as live to
    the next reader, and "is this still called?" is exactly the question task
    7.9 turns on. The intake scan treats a `TODO` + `DISABLED` docstring on an
    uncalled function as a retained path rather than a live bypass.
    """
    try:
        from ..data import market_data
        from ..schemas.market import Ticker

        snap = market_data.fetch_snapshot(Ticker(symbol=symbol))
        # Walk back past empty bars: pre-open, yfinance appends today's bar
        # with close=NaN before the session has traded.
        for bar in reversed(snap.history):
            px = bar.close
            if px is not None and math.isfinite(px) and px > 0:
                return float(px)
        return None
    except Exception:  # noqa: BLE001
        return None
