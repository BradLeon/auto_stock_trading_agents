"""Daily digests surfaced to the Boss: a detailed Obsidian .md + a thumbnail Feishu card.

Two independent, config-gated digests, both read from Context Memory (decoupled from
the workflows that produced the data — they never re-run analysis):
  - intel:      today's material PEAD events/insights + per-target Δthesis
  - perf/risk:  the performance snapshot + the deterministic 6-layer risk review

The daily intel (I1) and perf/risk (I5) workflows already run every session but were
silent (push_context_updates off, no digest). These always surface both a structured
report (Obsidian) and a thumbnail card (Feishu). Never raises — a digest failure must
not break the daily cascade.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, Field

log = logging.getLogger("ats.digest")


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #
def _out_dir() -> str:
    from ..config import load_macro_config

    try:
        return load_macro_config().output_dir or ""
    except Exception:  # noqa: BLE001
        return ""


def _write_md(filename: str, text: str) -> Path | None:
    d = _out_dir()
    if not d:
        log.info("digest md skipped: output_dir unset")
        return None
    folder = Path(d)
    if not folder.is_dir():
        log.warning("digest md skipped: output_dir missing %s", folder)
        return None
    path = folder / filename
    path.write_text(text, encoding="utf-8")
    return path


def _push(kind: str, title: str, body: str) -> None:
    try:
        from ..channel import get_channel
        from ..schemas.channel import Notification

        get_channel().push(Notification(kind=kind, title=title, body=body))  # config channel (feishu_bot)
    except Exception as exc:  # noqa: BLE001 - push is best-effort
        log.info("digest push skipped: %s", exc)


def _enabled(key: str) -> bool:
    from ..config import load_pead_global

    try:
        return bool(load_pead_global().get("digest", {}).get(key, True))
    except Exception:  # noqa: BLE001
        return True


# --------------------------------------------------------------------------- #
# perf / risk digest (I5)
# --------------------------------------------------------------------------- #
def perf_risk_digest() -> Path | None:
    if not _enabled("perf_risk"):
        return None
    from ..memory import get_store

    store = get_store()
    perf = store.last_performance()
    review = store.latest_risk_review()
    if perf is None and review is None:
        log.info("perf/risk digest: no snapshot yet")
        return None

    now = datetime.now(timezone.utc)
    parts = [f"# 🤖 每日绩效·风控 — {now:%Y-%m-%d}", ""]
    if perf is not None:
        parts += ["## 绩效",
                  f"- NetLiq **${perf.net_liquidation:,.0f}** · 日盈亏 **${perf.daily_pnl:,.0f}** "
                  f"· 累计 ${perf.cumulative_pnl:,.0f}", ""]
    if review is not None:
        from ..risk import report as risk_report

        parts += ["## 风控（6 层 · 期权已并入）", "", risk_report.render(review)]
    path = _write_md(f"每日绩效风控-{now:%Y-%m-%d}.md", "\n".join(parts))

    # thumbnail card — daily P&L from the risk review (consistent with the report;
    # perf.daily_pnl can be a not-yet-settled IBKR reqPnL garbage read).
    if review is not None:
        nl = review.net_liquidation or (perf.net_liquidation if perf else 0.0)
        dp = review.daily_pnl_pct
        dp_txt = f" · 日盈亏 {dp}%（${dp / 100 * nl:,.0f}，盘中）" if dp is not None else ""
        body = [f"NetLiq ${nl:,.0f}{dp_txt}"]
        line = f"状态 {review.risk_state} · 现金(有效) {review.effective_cash_pct:.0%}"
        if review.portfolio_beta is not None:
            line += f" · beta {review.portfolio_beta:.2f}"
        body.append(line)
        for b in review.breaches:            # each breach: actual vs limit (cheap, high-signal)
            body.append(f"⚠ {b.layer}：{b.actual} vs {b.limit}")
        _push("error" if review.risk_state == "derisk" else "info",
              f"绩效·风控 {now:%m-%d} — {review.risk_state}（{len(review.breaches)} 破限）", "\n".join(body))
    elif perf is not None:
        _push("info", f"绩效 {now:%m-%d}",
              f"NetLiq ${perf.net_liquidation:,.0f} · 日盈亏 ${perf.daily_pnl:,.0f}")
    return path


# --------------------------------------------------------------------------- #
# intel digest (I1) — LLM synthesizes a per-ticker 中文 investment takeaway
# (direction + importance for sorting) + translates headlines; degrades to a
# headline-only digest if the LLM is unavailable.
# --------------------------------------------------------------------------- #
class _TickerBrief(BaseModel):
    symbol: str = Field(description="标的代码，原样回填")
    direction: str = Field("⚪", description="方向，只能是单个 emoji：🔴(利空/看空) / 🟢(利好/看多) / ⚪(中性或混合)；不要文字")
    importance: float = Field(0.5, description="0-1，对持仓/交易决策的重要度（催化剂×确定性×相关性），越高越靠前")
    takeaway: str = Field("", description="一句中文投资含义，≤40字；不要在此写 importance/direction 字样")
    headlines_zh: list[str] = Field(default_factory=list, description="英文头条的中文翻译，与输入头条一一对应、同序")


class _IntelBriefView(BaseModel):
    briefs: list[_TickerBrief] = Field(default_factory=list)


def _brief(per: dict) -> dict:
    """One cheap-LLM call → {SYM: _TickerBrief} (投资要点 + direction + importance +
    头条翻译). Empty dict on failure — the caller falls back to a headline digest."""
    try:
        from ..agents.base import run_structured

        blocks = []
        for sym, d in per.items():
            lines = [f"### {sym}"]
            if d["delta"]:
                lines.append("Δthesis: " + d["delta"].replace("\n", " ")[:400])
            for e in d["events"]:
                lines.append(f"[triage {e.get('triage_score') or 0:.2f}] {(e.get('headline') or '')[:180]}")
            for i in d["insights"]:
                lines.append(f"insight[{i.get('direction', '')}]: {(i.get('summary') or '')[:180]}")
            blocks.append("\n".join(lines))
        ctx = ("逐标的输出投资要点。headlines_zh 按各标的 [triage …] 头条的出现顺序逐条翻译。\n\n"
               + "\n\n".join(blocks))
        view = run_structured("intel_brief", _IntelBriefView, ctx, skill_slug="intel-brief")
        return {b.symbol.upper(): b for b in view.briefs}
    except Exception as exc:  # noqa: BLE001 - overlay is best-effort
        log.warning("intel brief LLM failed, headline fallback: %s", exc)
        return {}


def intel_digest(lookback_hours: int = 24, *, use_llm: bool = True) -> Path | None:
    if not _enabled("intel"):
        return None
    from ..config import load_pead_config, load_pead_global
    from ..memory import get_store

    store = get_store()
    g = load_pead_global()
    targets = g.get("targets", [])
    min_triage = float(g.get("monitor", {}).get("triage", {}).get("min_score", 0.35))
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()
    now = datetime.now(timezone.utc)

    per: dict[str, dict] = {}
    n_ev = n_in = n_delta = 0
    for sym in targets:
        events = [e for e in store.recent_events(sym, limit=40)
                  if (e.get("triage_score") or 0) >= min_triage and (e.get("published_at") or "") >= cutoff]
        insights = [i for i in store.recent_insights(sym, limit=10)
                    if (i.get("created_at") or "") >= cutoff]
        delta = ""
        try:
            d = store.get_dossier(sym, load_pead_config(sym).fiscal_label)
            narr = d.expectation_set.narrative if (d and d.expectation_set) else ""
            if narr and "[update" in narr:
                delta = "[update" + narr.split("[update", 1)[1][:500]
        except Exception:  # noqa: BLE001
            pass
        if events or insights or delta:
            per[sym] = {"events": events, "insights": insights, "delta": delta}
            n_ev += len(events)
            n_in += len(insights)
            n_delta += 1 if delta else 0

    if not per:
        log.info("intel digest: nothing material in the last %dh", lookback_hours)
        return None

    briefs = _brief(per) if use_llm else {}

    def _rank(sym: str) -> float:
        b = briefs.get(sym)
        if b is not None:
            return b.importance
        return max([e.get("triage_score") or 0 for e in per[sym]["events"]] or [0.0])

    order = sorted(per, key=_rank, reverse=True)   # 投资重要度：重点在前

    # --- detailed .md ---
    parts = [f"# 🤖 每日情报 — {now:%Y-%m-%d}", "",
             f"> {len(per)} 只标的有新情报 · {n_ev} 事件 · {n_in} insight · {n_delta} Δthesis"
             f"（近 {lookback_hours}h，按投资重要度排序）", ""]
    for sym in order:
        d = per[sym]
        b = briefs.get(sym)
        parts.append(f"## {(b.direction + ' ') if b else ''}{sym}")
        if b is not None and b.takeaway:
            parts += ["", f"**投资要点**：{b.takeaway}（重要度 {b.importance:.2f}）", ""]
        elif d["delta"]:
            parts += ["", "**Δthesis**：" + d["delta"].replace("\n", " "), ""]
        zh = b.headlines_zh if b is not None else []
        for idx, e in enumerate(d["events"]):
            t = f" — {zh[idx]}" if idx < len(zh) else ""
            parts.append(f"- [{(e.get('published_at') or '')[:10]} · {e.get('triage_score') or 0:.2f}] "
                         f"{(e.get('headline') or '')[:160]}{t}")
        for i in d["insights"]:
            parts.append(f"- 📰 [{i.get('direction', '')}/{i.get('impact_path', '')} · "
                         f"{i.get('confidence') or 0:.2f}] {(i.get('summary') or '')[:180]}")
        parts.append("")
    path = _write_md(f"每日情报-{now:%Y-%m-%d}.md", "\n".join(parts))

    # --- thumbnail card: insight-first, sorted by investment importance ---
    body = [f"{len(per)} 票有情报 · {n_ev} 事件 · {n_in} insight · {n_delta} Δthesis"]
    if briefs:
        for sym in order[:6]:
            b = briefs.get(sym)
            if b is not None and b.takeaway:
                body.append(f"{b.direction} {sym}：{b.takeaway}")
    else:   # LLM unavailable → top headlines by triage
        flat = sorted(((e.get("triage_score") or 0, sym, e.get("headline") or "")
                       for sym, d in per.items() for e in d["events"]), reverse=True)
        body += [f"· {sym} [{sc:.2f}] {hl[:56]}" for sc, sym, hl in flat[:4]]
    _push("info", f"每日情报 {now:%m-%d}", "\n".join(body))
    return path
