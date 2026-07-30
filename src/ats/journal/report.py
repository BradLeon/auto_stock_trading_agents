"""交易台账 — the readable ledger.

One rolling monthly Obsidian table, fully regenerated from sqlite (never appended, so
a hand-edited or corrupted note is disposable). SQLite stays authoritative; nothing here
is ever read back into the system.

A row is an INTENT, not a fill. Of the first 52 order rows, 24 errored and 16 were
cancelled — "the order evaporated" is this system's most common outcome, and a ledger
of fills only would hide the single thing most worth noticing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

log = logging.getLogger("ats.journal")

_STATUS_ZH = {"filled": "已成交", "submitted": "已报", "cancelled": "已撤", "error": "失败",
              "expired": "未成交失效", "rejected": "被拒", "pending": "待报", "": "—"}


def _money(v) -> str:
    return f"${v:,.0f}" if v else "—"


def _plan(e) -> str:
    bits = []
    if e.stop_price:
        bits.append(f"止 {e.stop_price:g}")
    if e.target_price:
        bits.append(f"标 {e.target_price:g}")
    if e.planned_horizon_days:
        bits.append(f"{e.planned_horizon_days}日")
    return " · ".join(bits) or "—"


def _evidence(e) -> str:
    """v1/v2 + 纪要 + 打分延迟 — the agent-native "how good was my read"."""
    if e.ev_score_total is None:
        return "—"
    tx = "有纪要" if e.ev_has_transcript else "**缺纪要**"
    lat = f" · 滞后{e.ev_score_latency_h:.0f}h" if e.ev_score_latency_h else ""
    return f"{e.ev_score_total:+.2f} · {tx}{lat}"


def _gates(e) -> str:
    mine = [n for n in e.risk_notes if n.startswith(e.symbol)]
    return f"{len(mine)} 条" if mine else ("—" if not e.risk_notes else
                                           f"({len(e.risk_notes)} 条全局)")


def _approval(e) -> str:
    ap = e.approval
    if ap is None:
        return "—"
    if ap.diverged:
        return "**被否**" if e.symbol in ap.dropped_symbols else f"{ap.status}(有改动)"
    return ap.status or "—"


def render_ledger(entries: list, month: str) -> str:
    """Markdown for one month from a list[JournalEntry].

    Deterministic: same entries in, same bytes out (modulo the generation stamp).
    """
    lines = [f"# 交易台账 — {month}", "",
             f"> 生成于 {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC ｜ "
             f"共 {len(entries)} 个意图 ｜ 一行 = 一个**意图**，不是一笔成交", ""]
    if not entries:
        lines.append("（本月无交易意图）")
        return "\n".join(lines) + "\n"

    filled = sum(1 for e in entries if e.terminal_status == "filled")
    lost = sum(1 for e in entries
               if e.terminal_status in ("error", "cancelled", "expired"))
    diverged = sum(1 for e in entries if e.approval and e.approval.diverged)
    lines += [f"- 成交 {filled} ｜ 未成交/失败 {lost} ｜ 人审有分歧 {diverged}", "",
              "| 日期 | 标的 | 动作 | setup | 规模 | 信心 | 计划 | 证据 | 风控 | 审批 | 执行 | 台账号 |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for e in entries:
        day = f"{e.as_of:%Y-%m-%d}"
        size = _money(e.intended_notional) if e.intended_notional else (
            f"{e.intended_qty:.0f}股" if e.intended_qty else "—")
        conv = f"{e.conviction:.2f}" if e.conviction else "—"
        st = _STATUS_ZH.get(e.terminal_status or "", e.terminal_status or "—")
        if (e.submit_attempts or 0) > 1:
            st += f" ×{e.submit_attempts}"
        lines.append(
            f"| {day} | {e.symbol} | {e.action} | {e.setup or '—'} | {size} "
            f"| {conv} | {_plan(e)} | {_evidence(e)} | {_gates(e)} | {_approval(e)} "
            f"| {st} | `{e.entry_id}` |")

    # Invalidation criteria are the point of pre-registration; surface them so they can
    # actually be checked later rather than buried in a column.
    inval = [e for e in entries if e.invalidation.strip()]
    if inval:
        lines += ["", "## 论点失效条件（预登记）", ""]
        lines += [f"- **{e.symbol}** ({e.as_of:%Y-%m-%d}): {e.invalidation}" for e in inval]
    return "\n".join(lines) + "\n"


def write_ledger(month: str = "", *, store=None) -> str | None:
    """Regenerate 交易台账-<YYYY-MM>.md. Returns the path, or None if no output dir."""
    from ..memory import get_store
    from ..runtime.digest import _write_md

    store = store or get_store()
    month = month or f"{datetime.now(timezone.utc):%Y-%m}"
    entries = [e for e in store.journal_entries(since=f"{month}-01")
               if f"{e.as_of:%Y-%m}" == month]
    entries.sort(key=lambda e: (e.as_of, e.symbol))
    path = _write_md(f"交易台账-{month}.md", render_ledger(entries, month))
    return str(path) if path else None


def run(month: str = "") -> int:
    path = write_ledger(month)
    print(f"📒 交易台账 → {path}" if path else "（未配置 Obsidian 输出目录）")
    return 0
