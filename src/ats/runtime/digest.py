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

    # thumbnail card
    if review is not None:
        body = []
        if perf is not None:
            body.append(f"NetLiq ${perf.net_liquidation:,.0f} · 日盈亏 ${perf.daily_pnl:,.0f}")
        line = f"状态 {review.risk_state} · 现金(有效) {review.effective_cash_pct:.0%}"
        if review.portfolio_beta is not None:
            line += f" · beta {review.portfolio_beta:.2f}"
        body.append(line)
        if review.breaches:
            body.append("破限：" + "；".join(b.layer for b in review.breaches))
        _push("error" if review.risk_state == "derisk" else "info",
              f"绩效·风控 {now:%m-%d} — {review.risk_state}（{len(review.breaches)} 破限）", "\n".join(body))
    elif perf is not None:
        _push("info", f"绩效 {now:%m-%d}",
              f"NetLiq ${perf.net_liquidation:,.0f} · 日盈亏 ${perf.daily_pnl:,.0f}")
    return path


# --------------------------------------------------------------------------- #
# intel digest (I1)
# --------------------------------------------------------------------------- #
def intel_digest(lookback_hours: int = 24) -> Path | None:
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
    n_ev = n_in = 0
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

    if not per:
        log.info("intel digest: nothing material in the last %dh", lookback_hours)
        return None

    parts = [f"# 🤖 每日情报 — {now:%Y-%m-%d}", "",
             f"> {len(per)} 只标的有新情报 · {n_ev} 条高分事件 · {n_in} 条研报 insight（近 {lookback_hours}h）", ""]
    for sym, d in per.items():
        parts.append(f"## {sym}")
        if d["delta"]:
            parts += ["", "**Δthesis**：" + d["delta"].replace("\n", " "), ""]
        for e in d["events"]:
            parts.append(f"- [{(e.get('published_at') or '')[:10]} · triage {e.get('triage_score') or 0:.2f}] "
                         f"{(e.get('headline') or '')[:180]}")
        for i in d["insights"]:
            parts.append(f"- 📰 [{i.get('direction', '')}/{i.get('impact_path', '')} · "
                         f"{i.get('confidence') or 0:.2f}] {(i.get('summary') or '')[:180]}")
        parts.append("")
    path = _write_md(f"每日情报-{now:%Y-%m-%d}.md", "\n".join(parts))

    # thumbnail card: top events by triage across tickers
    flat = sorted(((e.get("triage_score") or 0, sym, e.get("headline") or "")
                   for sym, d in per.items() for e in d["events"]), reverse=True)
    body = [f"{len(per)} 票有情报 · {n_ev} 事件 · {n_in} insight"]
    body += [f"· {sym} [{sc:.2f}] {hl[:56]}" for sc, sym, hl in flat[:4]]
    _push("info", f"每日情报 {now:%m-%d}", "\n".join(body))
    return path
