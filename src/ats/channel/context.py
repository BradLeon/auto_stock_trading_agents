"""Shared Context-Memory reader used by every BossChannel's report lookup."""

from __future__ import annotations

from ..schemas.channel import ReportBundle


def build_report_bundle(query: str) -> ReportBundle:
    """Pull a symbol's current dossier + recent trades from Context Memory."""
    from ..memory import get_store

    symbol = query.strip().upper()
    if not symbol:
        return ReportBundle(query=query, summary="usage: report <SYMBOL>")

    store = get_store()
    # The per-cycle `reports` table was superseded by the PEAD dossier and has not been
    # written since; reading it rendered an empty "Reports:" block every time. Read the
    # artifact that actually holds a name's current thesis.
    dossiers = store.recent_dossiers(symbol, limit=3)
    trades = store.recent_trades(symbol, limit=8)

    lines = [f"History for {symbol}:"]
    if dossiers:
        lines.append("  Dossiers:")
        for meta in dossiers:
            d = store.get_dossier(symbol, meta["fiscal_label"])
            head = f"    [{meta['fiscal_label']}] phase={meta['phase']}"
            if d and d.scorecard:
                head += f" · scorecard {d.scorecard.total:+.2f} ({d.scorecard.band})"
            lines.append(head)
            if d and d.decision_summary:
                lines.append(f"      {d.decision_summary[:120]}")
    if trades:
        lines.append("  Trades:")
        for t in trades:
            pnl = t["realized_pnl"] if "realized_pnl" in t.keys() else None
            lines.append(f"    [{t['cycle_id']}] {t['action']} {t['qty']:.0f} [{t['status']}]"
                         + (f" 盈亏 {pnl:+.2f}" if pnl else ""))
    if not dossiers and not trades:
        lines.append("  (no history yet)")
    return ReportBundle(query=query, summary="\n".join(lines))
