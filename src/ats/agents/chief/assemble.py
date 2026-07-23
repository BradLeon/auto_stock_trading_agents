"""Chief context assembly — read-only gathering of ALL published artifacts.

Blocks (each degrades to "" on failure): live portfolio, PEAD dossiers (with
freshness), sector review company_calls, macro review sector_tilts, risk review
state/breaches, and the track record. Pure code, no LLM.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

log = logging.getLogger("ats.agents.chief.assemble")

FRESH_SCORE_DAYS = 3   # a score-phase dossier older than this is background, not actionable


@dataclass
class ChiefContext:
    as_of: datetime
    net_liquidation: float = 0.0
    held_symbols: set = field(default_factory=set)          # symbols currently held
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
    ctx.blocks["PEAD 档案（主 alpha 信号）"] = _pead_block(ctx.held_symbols)
    ctx.blocks["行业评审（倾斜修正）"] = _sector_block(ctx.held_symbols)
    ctx.blocks["宏观评审（倾斜修正）"] = _macro_block()
    ctx.blocks["风控状态（硬约束）"] = _risk_block()
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


def _pead_block(held_symbols: set | None = None) -> str:
    from ...config import load_pead_config, load_pead_global
    from ...memory import get_store

    store = get_store()
    now = datetime.now(timezone.utc)
    parts = []
    held = held_symbols or set()
    for sym in load_pead_global().get("targets", []):
        try:
            cfg = load_pead_config(sym)
            d = store.get_dossier(sym.upper(), cfg.fiscal_label)
        except Exception:  # noqa: BLE001
            continue
        if d is None:
            continue
        age = (now - d.updated_at.replace(tzinfo=d.updated_at.tzinfo or timezone.utc)).days
        fresh = d.phase == "score" and age <= FRESH_SCORE_DAYS
        head = (f"### {sym} ({d.fiscal_label}, phase={d.phase}, 更新于 {age} 天前"
                + ("，**新鲜可行动**" if fresh else "，仅背景") + ")")
        lines = [head]
        if d.scorecard:
            lines.append(f"Scorecard: {d.scorecard.total:+.2f} (门槛 {d.scorecard.threshold:+.1f}) "
                         f"— {d.scorecard.band}")
        if d.decision_summary:
            lines.append(f"PEAD 分析师建议: {d.decision_summary}")
        if d.market_setup:
            ms = d.market_setup
            lines.append(f"Setup: 抢跑 vs 板块 {ms.run_up_vs_sector_pct}% · EM {ms.expected_move_pct}%")
        if d.expectation_set and d.expectation_set.narrative:
            lines.append("叙事尾部: …" + d.expectation_set.narrative[-400:])
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


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
