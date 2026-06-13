"""Shared Context-Memory reader used by every BossChannel's report lookup."""

from __future__ import annotations

from ..schemas.channel import ReportBundle


def build_report_bundle(query: str) -> ReportBundle:
    """Pull a symbol's recent analyst reports + trades from Context Memory."""
    from ..memory import get_store

    symbol = query.strip().upper()
    if not symbol:
        return ReportBundle(query=query, summary="usage: report <SYMBOL>")

    store = get_store()
    reports = store.recent_reports(symbol, limit=8)
    trades = store.recent_trades(symbol, limit=8)

    lines = [f"History for {symbol}:"]
    if reports:
        lines.append("  Reports:")
        lines += [f"    [{r['cycle_id']}] {r['role']}: {r['signal']} "
                  f"(conv {r['conviction']:.2f}) — {r['thesis'][:120]}" for r in reports]
    if trades:
        lines.append("  Trades:")
        lines += [f"    [{t['cycle_id']}] {t['action']} {t['qty']:.0f} [{t['status']}]"
                  for t in trades]
    if not reports and not trades:
        lines.append("  (no history yet)")
    return ReportBundle(query=query, reports=reports, summary="\n".join(lines))
