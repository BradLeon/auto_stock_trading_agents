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
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, Field

log = logging.getLogger("ats.digest")

# Human-readable names for breach layer keys (the raw keys — L1-L4_chip_design,
# L3-组合beta … — are jargon nobody can read on a phone card).
_LAYER_HUMAN = {
    "L3-组合beta": "组合 beta（加权）", "L3-相关簇": "相关簇拥挤度", "L5-压测": "极端情景压测亏损",
    "L1-止损": "个股止损", "L4-回撤": "组合回撤", "L4-日损": "单日亏损",
    "L2-杠杆": "组合杠杆", "L2-现金": "现金占比", "L1-单票": "单票仓位",
    "L4_chip_design": "L4 芯片设计层仓位", "L5_fab": "L5 制造层仓位",
    "L6_equipment": "L6 设备层仓位", "L2_cloud": "L2 云服务层仓位",
    "L3_dc_infra": "L3 数据中心基建层仓位", "L1_app": "L1 应用层仓位",
}


def _humanize_breach(b) -> str:
    """'L1-L4_chip_design：19% vs ≤15%' → 'L4 芯片设计层仓位 19%，限额 ≤15%'."""
    m = re.search(r"(L\d_[a-z_]+)", b.layer)   # chain-layer concentration keys
    name = _LAYER_HUMAN.get(m.group(1)) if m else None
    name = name or _LAYER_HUMAN.get(b.layer, b.layer)
    return f"{name} {b.actual}，限额 {b.limit}"


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
    # Daily P&L from the risk review (perf.daily_pnl can be an unsettled reqPnL
    # garbage read); keep NetLiq/累计 from the perf record.
    nl = (review.net_liquidation if review is not None else None) or (perf.net_liquidation if perf else 0.0)
    dp = review.daily_pnl_pct if review is not None else None
    if dp is not None:
        dp_line = f" · 日盈亏 {dp}%（${dp / 100 * nl:,.0f}，仅盘中/不含盘前后）"
    elif perf is not None:
        dp_line = f" · 日盈亏 ${perf.daily_pnl:,.0f}"
    else:
        dp_line = ""
    cum = f" · 累计 ${perf.cumulative_pnl:,.0f}" if perf is not None else ""
    parts = [f"# 🤖 每日简报 · 绩效风控 — {now:%Y-%m-%d}", "",
             "## 绩效", f"- NetLiq **${nl:,.0f}**{dp_line}{cum}", ""]
    if review is not None:
        from ..risk import report as risk_report

        body = risk_report.render(review)
        body = "\n".join(body.split("\n")[2:]) if body.lstrip().startswith("#") else body  # drop its own H1
        parts += [f"## 风控（6 层 · 期权已并入 · 数据截至 {review.as_of:%Y-%m-%d %H:%M} UTC，最近一次快照）",
                  "", body]
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
        for b in review.breaches:            # human-readable: what the metric is + 实值/限额
            body.append(f"⚠ {_humanize_breach(b)}")
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
        events = sorted(
            [e for e in store.recent_events(sym, limit=40)
             if (e.get("triage_score") or 0) >= min_triage and (e.get("published_at") or "") >= cutoff],
            key=lambda e: e.get("triage_score") or 0, reverse=True)   # 重点新闻在前
        insights = [i for i in store.recent_insights(sym, limit=10)
                    if (i.get("created_at") or "") >= cutoff
                    and (i.get("summary") or "").strip() and (i.get("confidence") or 0) > 0]  # 滤掉空/失败 insight
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
             f"（近 {lookback_hours}h，按投资重要度排序）", "",
             "> 图例：**投资要点**=当日综合投资含义 · **Δthesis**=论点变化 · "
             "新闻行 `[日期 · 材料度 X]` 材料度=该新闻与投资相关性的自动评分(0-1，越高越重要) · "
             "**📰 insight**=研报观点(方向/影响/置信)。", ""]
    for sym in order:
        d = per[sym]
        b = briefs.get(sym)
        parts.append(f"## {(b.direction + ' ') if b else ''}{sym}")
        if b is not None and b.takeaway:            # 短综合（LLM），供快速判断
            parts += ["", f"**投资要点**（重要度 {b.importance:.2f}）：{b.takeaway}"]
        if d["delta"]:                              # 详细论点变化（monitor 综合），二者互补
            parts += ["", "**Δthesis（论点变化）**：" + d["delta"].replace("\n", " ")]
        parts.append("")
        zh = b.headlines_zh if b is not None else []
        for idx, e in enumerate(d["events"]):       # 材料度 = 新闻与投资相关性评分 0-1
            t = f" — {zh[idx]}" if idx < len(zh) else ""
            parts.append(f"- [{(e.get('published_at') or '')[:10]} · 材料度 {e.get('triage_score') or 0:.2f}] "
                         f"{(e.get('headline') or '')[:160]}{t}")
        for i in d["insights"]:                     # 研报 insight（方向/影响路径/置信）
            parts.append(f"- 📰 [{i.get('direction', '')}/{i.get('impact_path', '')} · "
                         f"置信 {i.get('confidence') or 0:.2f}] {(i.get('summary') or '')[:180]}")
        parts.append("")
    path = _write_md(f"每日情报-{now:%Y-%m-%d}.md", "\n".join(parts))

    # --- thumbnail card: insight-first, sorted by investment importance ---
    body = [f"{len(per)} 票有情报 · {n_ev} 事件 · {n_in} insight · {n_delta} Δthesis（按投资重要度排序）"]
    if briefs:
        for sym in order:            # 全展示，重点在前
            b = briefs.get(sym)
            if b is not None and b.takeaway:
                body.append(f"{b.direction} {sym}：{b.takeaway}")
    else:   # LLM unavailable → top headlines by triage
        flat = sorted(((e.get("triage_score") or 0, sym, e.get("headline") or "")
                       for sym, d in per.items() for e in d["events"]), reverse=True)
        body += [f"· {sym} [{sc:.2f}] {hl[:56]}" for sc, sym, hl in flat[:4]]
    _push("info", f"每日情报 {now:%m-%d}", "\n".join(body))
    return path
