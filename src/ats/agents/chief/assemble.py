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
        trades = [t for t in store.recent_trades(sym, limit=8) if (t.get("cycle_id") or "") == cid]
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
        # Layer-level scores (if available)
        for lv in getattr(r, "layer_views", None) or []:
            score_str = f"{lv.score}" if getattr(lv, "score", None) is not None else ""
            lines.append(f"  层 {lv.key}: 景气{score_str} [{lv.regime}] {lv.summary[:80]}")
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
    for f in store.recent_fills(limit=5):
        rp = f" realized ${f['realized_pnl']:,.0f}" if f.get("realized_pnl") is not None else ""
        lines.append(f"  近期成交: {f['side']} {f['symbol']} {f['shares']:.0f}@{f['price']:.2f}{rp}")
    return "\n".join(lines)
