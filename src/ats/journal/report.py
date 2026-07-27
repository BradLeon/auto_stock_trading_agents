"""交易台账 — the readable ledger.

One rolling monthly Obsidian table, fully regenerated from sqlite (never appended, so
a hand-edited or corrupted note is disposable). SQLite stays authoritative; nothing here
is ever read back into the system.

A row is an INTENT, not a fill. Of the first 52 order rows, 24 errored and 16 were
cancelled — "the order evaporated" is this system's most common outcome, and a ledger
of fills only would hide the single thing most worth noticing.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

log = logging.getLogger("ats.journal")

_STATUS_ZH = {"filled": "已成交", "submitted": "已报", "cancelled": "已撤", "error": "失败",
              "expired": "未成交失效", "rejected": "被拒", "pending": "待报", "": "—"}


def _money(v) -> str:
    return f"${v:,.0f}" if v else "—"


def _plan(e: dict) -> str:
    bits = []
    if e.get("stop_price"):
        bits.append(f"止 {e['stop_price']:g}")
    if e.get("target_price"):
        bits.append(f"标 {e['target_price']:g}")
    if e.get("planned_horizon_days"):
        bits.append(f"{e['planned_horizon_days']}日")
    return " · ".join(bits) or "—"


def _evidence(e: dict) -> str:
    """v1/v2 + 纪要 + 打分延迟 — the agent-native "how good was my read"."""
    if e.get("ev_score_total") is None:
        return "—"
    tx = "有纪要" if e.get("ev_has_transcript") else "**缺纪要**"
    lat = f" · 滞后{e['ev_score_latency_h']:.0f}h" if e.get("ev_score_latency_h") else ""
    return f"{e['ev_score_total']:+.2f} · {tx}{lat}"


def _gates(e: dict) -> str:
    notes = json.loads(e.get("risk_notes_json") or "[]")
    mine = [n for n in notes if n.startswith(e["symbol"])]
    return f"{len(mine)} 条" if mine else ("—" if not notes else f"({len(notes)} 条全局)")


def _approval(e: dict) -> str:
    st = e.get("approval_status") or "—"
    div = json.loads(e.get("approval_divergence") or "{}")
    if div.get("diverged"):
        if e["symbol"] in (div.get("dropped_symbols") or []):
            return "**被否**"
        return f"{st}(有改动)"
    return st


def render_ledger(entries: list[dict], month: str) -> str:
    """Markdown for one month. Deterministic: same rows in, same bytes out."""
    lines = [f"# 交易台账 — {month}", "",
             f"> 生成于 {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC ｜ "
             f"共 {len(entries)} 个意图 ｜ 一行 = 一个**意图**，不是一笔成交", ""]
    if not entries:
        lines.append("（本月无交易意图）")
        return "\n".join(lines) + "\n"

    filled = sum(1 for e in entries if e.get("terminal_status") == "filled")
    lost = sum(1 for e in entries
               if e.get("terminal_status") in ("error", "cancelled", "expired"))
    diverged = sum(1 for e in entries
                   if json.loads(e.get("approval_divergence") or "{}").get("diverged"))
    lines += [f"- 成交 {filled} ｜ 未成交/失败 {lost} ｜ 人审有分歧 {diverged}", "",
              "| 日期 | 标的 | 动作 | setup | 规模 | 信心 | 计划 | 证据 | 风控 | 审批 | 执行 | 台账号 |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for e in entries:
        day = (e.get("as_of") or "")[:10]
        size = _money(e.get("intended_notional")) if e.get("intended_notional") else (
            f"{e['intended_qty']:.0f}股" if e.get("intended_qty") else "—")
        conv = f"{e['conviction']:.2f}" if e.get("conviction") else "—"
        st = _STATUS_ZH.get(e.get("terminal_status") or "", e.get("terminal_status") or "—")
        att = e.get("submit_attempts") or 0
        if att > 1:
            st += f" ×{att}"
        lines.append(
            f"| {day} | {e['symbol']} | {e['action']} | {e.get('setup') or '—'} | {size} "
            f"| {conv} | {_plan(e)} | {_evidence(e)} | {_gates(e)} | {_approval(e)} "
            f"| {st} | `{e['entry_id']}` |")

    # Invalidation criteria are the point of pre-registration; surface them so they can
    # actually be checked later rather than buried in a column.
    inval = [e for e in entries if (e.get("invalidation") or "").strip()]
    if inval:
        lines += ["", "## 论点失效条件（预登记）", ""]
        lines += [f"- **{e['symbol']}** ({(e.get('as_of') or '')[:10]}): {e['invalidation']}"
                  for e in inval]
    return "\n".join(lines) + "\n"


def write_ledger(month: str = "", *, store=None) -> str | None:
    """Regenerate 交易台账-<YYYY-MM>.md. Returns the path, or None if no output dir."""
    from ..memory import get_store
    from ..runtime.digest import _write_md

    store = store or get_store()
    month = month or f"{datetime.now(timezone.utc):%Y-%m}"
    entries = [e for e in store.journal_entries(since=f"{month}-01")
               if (e.get("as_of") or "").startswith(month)]
    entries.sort(key=lambda e: (e.get("as_of") or "", e["symbol"]))
    path = _write_md(f"交易台账-{month}.md", render_ledger(entries, month))
    return str(path) if path else None


def run(month: str = "") -> int:
    path = write_ledger(month)
    print(f"📒 交易台账 → {path}" if path else "（未配置 Obsidian 输出目录）")
    return 0
