"""Stage C — the per-episode review card.

`交易复盘-<SYM>-<开仓YYYYMMDD>.md`, one per CLOSED episode. Fully deterministic, no
LLM: renders the EpisodeCard projection (episode + opening plan + legs + predictions)
through `skills/trade-journal/TEMPLATE.md`'s `{{slot}}` placeholders —
same filler as `agents/pead/report.py`. The document structure lives in that
template; edit it to change layout, not this module.

Regenerated in full every run, never appended — a hand-edited note is disposable,
same policy as journal/report.py's ledger. Pushed to Feishu once per episode
(tracked in `journal_meta`) so a reconcile run that closes five positions doesn't
re-spam five thumbnails on every later regen.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..schemas.journal import EpisodeCard, JournalEntry, TradeEpisode
from .card import build_card

log = logging.getLogger("ats.journal")

TEMPLATE_PATH = (Path(__file__).resolve().parents[1]
                 / "skills" / "trade-journal" / "TEMPLATE.md")

_NA = "（暂无数据）"

_DIRECTION_CN = {"long": "多头", "short": "空头"}
_STATUS_CN = {"open": "持有中", "closed": "已平仓"}
_EXIT_REASON_CN = {
    "target_hit": "✅ 止盈（按计划）",
    "stop_hit": "✅ 止损（按计划，判断错但执行对）",
    "thesis_invalidated": "✅ 论点失效（预登记条件触发）",
    "horizon_reached": "到期（未触发止盈止损）",
    "risk_forced": "风控强制减仓",
    "boss_override": "人工干预平仓",
    "drift": "⚠️ 漂移（未按任何既定理由退出）",
}
_SOURCE_CN = {
    "pead_score": "打分卡（漂移方向/强度）",
    "expected_move": "期权隐含预期波幅",
    "consensus_pt": "卖方目标价",
}


def _money(v) -> str:
    return f"${v:,.0f}" if v not in (None, 0) else "—"


def _pct(v) -> str:
    return f"{v:+.2f}%" if v is not None else "—"


# --------------------------------------------------------------------------- #
# 一、计划 vs 实际
# --------------------------------------------------------------------------- #
def _plan_vs_actual(episode: TradeEpisode, plan: JournalEntry | None) -> str:
    if plan is None:
        return "（无预登记计划——手工单或存量持仓，仅结果质量可评，决策质量不可评）"
    size_plan = (_money(plan.intended_notional) if plan.intended_notional
                else (f"{plan.intended_qty:.0f}股" if plan.intended_qty else "—"))
    order_plan = f"{plan.order_type}" + (f" @ {plan.limit_price:g}" if plan.limit_price else "")
    exit_at_stop = (f"{episode.avg_exit:g}" if episode.avg_exit is not None
                   and episode.exit_reason == "stop_hit" else "—")
    exit_at_target = (f"{episode.avg_exit:g}" if episode.avg_exit is not None
                      and episode.exit_reason == "target_hit" else "—")
    lines = [
        "| 维度 | 计划（预登记，不可变） | 实际 |",
        "|---|---|---|",
        f"| 规模 | {size_plan} | 开仓均价 {_money(episode.avg_entry)} |",
        f"| 委托 | {order_plan} | 首腿成交 {_money(plan.avg_fill_price)}"
        f"{f' · 滑点 {plan.slippage_bps:.0f}bp' if plan.slippage_bps is not None else ''} |",
        f"| 止损（声明，未挂单） | {plan.stop_price if plan.stop_price else '—'} | {exit_at_stop} |",
        f"| 止盈（声明） | {plan.target_price if plan.target_price else '—'} | {exit_at_target} |",
        f"| 计划持有 | {f'{plan.planned_horizon_days} 个交易日' if plan.planned_horizon_days else '未设定'} "
        f"| 实际持有 {f'{episode.holding_days} 个交易日' if episode.holding_days is not None else '（进行中）'} |",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 二、R / MAE / MFE / 持有期
# --------------------------------------------------------------------------- #
def _performance(episode: TradeEpisode) -> str:
    r = (f"{episode.r_multiple:+.2f}R（{episode.risk_unit_source}）"
        if episode.r_multiple is not None else
        (f"{episode.r_multiple_mtm:+.2f}R MTM（{episode.risk_unit_source}）"
         if episode.r_multiple_mtm is not None else "—（无风险单位，未猜分母）"))
    lines = [
        f"- 已实现盈亏：**{_money(episode.realized_pnl)}**"
        + (f"（另有未实现 {_money(episode.unrealized_pnl)}）" if episode.unrealized_pnl else ""),
        f"- R 倍数：**{r}**",
        f"- MAE / MFE：{_pct(episode.mae_pct)} / {_pct(episode.mfe_pct)}"
        f"（{episode.mae_source}，仅日线精度）"
        if episode.mae_pct is not None or episode.mfe_pct is not None else "- MAE / MFE：—",
        f"- 相对板块超额：{_pct(episode.excess_vs_sector_pct)}",
        f"- 佣金：{_money(episode.commission)}",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 三、legs 明细
# --------------------------------------------------------------------------- #
def _legs(card: EpisodeCard) -> str:
    if not card.legs:
        return "（无可关联的加减仓记录）"
    lines = ["| 日期 | 动作 | 理由 |", "|---|---|---|"]
    for leg in card.legs:
        lines.append(f"| {leg.as_of:%Y-%m-%d} | {leg.action} | {leg.rationale or '—'} |")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 四、决策快照（原文照录）
# --------------------------------------------------------------------------- #
def _decision_snapshot(plan: JournalEntry | None) -> str:
    if plan is None:
        return _NA
    parts = [f"> **开仓理由**：{plan.rationale or '（未记录）'}"]
    if plan.invalidation.strip():
        parts.append(f"> **预登记失效条件**：{plan.invalidation}")
    if plan.regime_risk_state:
        parts.append(f"> **当时组合风控状态**：{plan.regime_risk_state}")
    return "\n>\n".join(parts)


# --------------------------------------------------------------------------- #
# 五、证据质量
# --------------------------------------------------------------------------- #
def _evidence_quality(plan: JournalEntry | None) -> str:
    if plan is None or plan.ev_score_total is None:
        return _NA
    tx = "有纪要" if plan.ev_has_transcript else "**缺纪要（凭发布稿打分）**"
    lat = (f"· 财报→打分滞后 {plan.ev_score_latency_h:.1f}h"
          if plan.ev_score_latency_h is not None else "")
    em = (f"· 期权隐含预期波幅 ±{plan.ev_expected_move_pct:.1f}%"
         if plan.ev_expected_move_pct is not None else "")
    return (f"- 打分卡总分：**{plan.ev_score_total:+.2f}**（{plan.ev_score_band or '—'}）\n"
           f"- {tx} {lat} {em}")


# --------------------------------------------------------------------------- #
# 六、人审分歧
# --------------------------------------------------------------------------- #
def _approval_divergence(plan: JournalEntry | None, symbol: str) -> str:
    if plan is None or plan.approval is None:
        return _NA
    ap = plan.approval
    if not ap.diverged:
        return f"无分歧 — 状态 {ap.status or '—'}"
    verdict = "**被否**" if symbol in ap.dropped_symbols else (
        "**加做**" if symbol in ap.added_symbols else f"{ap.status}（有改动）")
    parts = [f"- 裁决：{verdict}"]
    if ap.comment:
        parts.append(f"- 批注：{ap.comment}")
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# 七、预测 vs 实现（四周期）
# --------------------------------------------------------------------------- #
def _predictions(card: EpisodeCard) -> str:
    if not card.predictions:
        return "（本回合无关联预测——非 PEAD 事件单，或尚未打分）"
    blocks = []
    for pred, outcomes in card.predictions:
        header = (f"**{_SOURCE_CN.get(pred.source, pred.source)}**："
                 f"预测值 {pred.predicted_value:+.2f}" if pred.predicted_value is not None
                 else f"**{_SOURCE_CN.get(pred.source, pred.source)}**")
        if pred.predicted_band:
            header += f"（{pred.predicted_band}）"
        if pred.ref_price is not None:
            header += f"，可行动日 {pred.ref_date} 收盘 ${pred.ref_price:.2f}"
        if not outcomes:
            blocks.append(header + "\n\n（尚无到期周期）")
            continue
        lines = [header, "", "| 周期 | 实际涨跌 | 超额(板块) | 超额(基准) |", "|---|---|---|---|"]
        for o in outcomes:
            lines.append(f"| T+{o.horizon_days} | {_pct(o.realized_pct)} "
                         f"| {_pct(o.excess_vs_sector_pct)} | {_pct(o.excess_vs_bench_pct)} |")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


# --------------------------------------------------------------------------- #
# 八、exit_reason
# --------------------------------------------------------------------------- #
def _exit_reason(episode: TradeEpisode) -> str:
    if episode.exit_reason is None:
        return "（未平仓，暂无退出分类）" if episode.status == "open" else _NA
    label = _EXIT_REASON_CN.get(episode.exit_reason, episode.exit_reason)
    planned = {True: "是", False: "否", None: "—"}[episode.exit_as_planned]
    return f"- 分类：**{label}**\n- 是否按计划退出：{planned}"


# --------------------------------------------------------------------------- #
# 反链
# --------------------------------------------------------------------------- #
def _backlinks(plan: JournalEntry | None, card: EpisodeCard) -> str:
    links = []
    if plan is not None:
        links.append(f"[[首席决策-{plan.as_of:%Y-%m-%d-%H%M}]]")
    fiscal_labels = sorted({p.ref_key.split(":", 1)[1] for p, _ in card.predictions
                           if ":" in p.ref_key})
    if fiscal_labels:
        from ..data.fiscal import canonical_tag

        for label in fiscal_labels:
            links.append(f"[[基本面分析-{card.episode.symbol}-{canonical_tag(label)}]]")
    return ("**关联笔记**：" + " · ".join(links)) if links else ""


# --------------------------------------------------------------------------- #
# render + write
# --------------------------------------------------------------------------- #
def render_card(card: EpisodeCard) -> str:
    ep, plan = card.episode, card.plan
    now = datetime.now(timezone.utc)
    slots = {
        "symbol": ep.symbol,
        "opened_date": f"{ep.opened_at:%Y%m%d}",
        "updated_at": f"{now:%Y-%m-%d %H:%M} UTC",
        "direction_cn": _DIRECTION_CN.get(ep.direction, ep.direction),
        "status_cn": _STATUS_CN.get(ep.status, ep.status),
        "setup": ep.setup,
        "episode_id": ep.episode_id,
        "plan_vs_actual": _plan_vs_actual(ep, plan),
        "performance": _performance(ep),
        "legs": _legs(card),
        "decision_snapshot": _decision_snapshot(plan),
        "evidence_quality": _evidence_quality(plan),
        "approval_divergence": _approval_divergence(plan, ep.symbol),
        "predictions": _predictions(card),
        "exit_reason": _exit_reason(ep),
        "backlinks": _backlinks(plan, card),
    }
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    return re.sub(r"\{\{(\w+)\}\}", lambda m: slots.get(m.group(1), _NA), template)


def _card_filename(episode: TradeEpisode) -> str:
    return f"交易复盘-{episode.symbol}-{episode.opened_at:%Y%m%d}.md"


def write_card(store, episode: TradeEpisode) -> Path | None:
    from ..runtime.digest import _write_md

    card = build_card(store, episode, with_predictions=True)
    return _write_md(_card_filename(episode), render_card(card))


def _push_once(store, episode: TradeEpisode) -> None:
    """Feishu thumbnail — exactly once per episode, tracked in journal_meta so
    re-running this job (idempotent regen, like the ledger) doesn't re-notify."""
    from ..runtime.digest import _push

    meta_key = f"card_pushed:{episode.episode_id}"
    if store.get_meta(meta_key):
        return
    pnl = f"{_money(episode.realized_pnl)}" if episode.realized_pnl is not None else "—"
    r = f"{episode.r_multiple:+.2f}R" if episode.r_multiple is not None else "—"
    reason = _EXIT_REASON_CN.get(episode.exit_reason, episode.exit_reason or "—")
    _push("info", f"交易复盘 {episode.symbol} 已平仓",
         f"盈亏 {pnl} · {r} · {reason}")
    store.set_meta(meta_key, "1")


# --------------------------------------------------------------------------- #
# 每周交易复盘-<YYYY-Www>.md — this week's closures + the open drift/overdue list
# --------------------------------------------------------------------------- #
def _week_label(d: date) -> str:
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _week_bounds(as_of: date) -> tuple[date, date]:
    monday = as_of - timedelta(days=as_of.isocalendar()[2] - 1)
    return monday, monday + timedelta(days=6)


def render_weekly(closed: list[TradeEpisode], flagged: list[TradeEpisode],
                  week_label: str) -> str:
    lines = [f"# 每周交易复盘 — {week_label}", "",
            f"> 生成于 {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC", "",
            "## 本周关闭的回合", ""]
    if not closed:
        lines.append("（本周无回合关闭）")
    else:
        lines += ["| 标的 | 方向 | 开仓 | 平仓 | 已实现盈亏 | R | exit_reason | 复盘卡 |",
                  "|---|---|---|---|---|---|---|---|"]
        for ep in closed:
            reason = _EXIT_REASON_CN.get(ep.exit_reason, ep.exit_reason or "—")
            r = f"{ep.r_multiple:+.2f}R" if ep.r_multiple is not None else "—"
            lines.append(
                f"| {ep.symbol} | {_DIRECTION_CN.get(ep.direction, ep.direction)} "
                f"| {ep.opened_at:%Y-%m-%d} | {ep.closed_at:%Y-%m-%d} "
                f"| {_money(ep.realized_pnl)} | {r} | {reason} "
                f"| [[{_card_filename(ep)[:-3]}]] |")
    lines += ["", "## 未平仓 · 失效/超期清单（当下可行动）", ""]
    if not flagged:
        lines.append("（无失效或超期未平仓位）")
    else:
        lines += ["| 标的 | 方向 | 开仓 | 状态 |", "|---|---|---|---|"]
        for ep in flagged:
            flags = []
            if ep.invalidation_triggered:
                flags.append("⚠️ 论点失效")
            if ep.horizon_overdue_days:
                flags.append(f"超期 {ep.horizon_overdue_days} 天")
            lines.append(f"| {ep.symbol} | {_DIRECTION_CN.get(ep.direction, ep.direction)} "
                        f"| {ep.opened_at:%Y-%m-%d} | {' · '.join(flags)} |")
    return "\n".join(lines) + "\n"


def write_weekly(*, store=None, as_of: date | None = None) -> Path | None:
    from ..memory import get_store
    from ..runtime.digest import _write_md

    store = store or get_store()
    today = as_of or datetime.now(timezone.utc).date()
    start, end = _week_bounds(today)
    closed = [ep for ep in store.list_episodes(status="closed", limit=100_000)
             if ep.closed_at and start <= ep.closed_at.date() <= end]
    closed.sort(key=lambda e: e.closed_at)
    flagged = [ep for ep in store.list_episodes(status="open", limit=100_000)
              if ep.invalidation_triggered or (ep.horizon_overdue_days or 0) > 0]
    flagged.sort(key=lambda e: e.symbol)
    label = _week_label(today)
    return _write_md(f"每周交易复盘-{label}.md", render_weekly(closed, flagged, label))


def run() -> int:
    from ..memory import get_store

    store = get_store()
    written = 0
    for ep in store.list_episodes(status="closed", limit=100_000):
        path = write_card(store, ep)
        if path is not None:
            written += 1
        _push_once(store, ep)
    weekly_path = write_weekly(store=store)
    print(f"交易复盘：{written} 份已（重新）生成" + (f" · 周报 → {weekly_path}" if weekly_path else ""))
    return 0
