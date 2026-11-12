"""风控日报 — the book against the rules, and what the risk gate did with the last orders, in plain language.

Written for the account owner, by the Risk Officer's deterministic engine (`risk/assess.py`): no LLM, no ledger write,
nothing persisted but the markdown file. Same contract as the Clerk 日报: regenerated in full on every call, pushed to
Feishu only when something needs a person, and a failure to build or write it never fails the run that asked for it.

Four questions it answers: what state is the book in (and may the Chief add risk); how close is every limit; what did
the gate do with the most recent orders (approved / rejected, and which rule said so); what, if anything, is for the
owner to do.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger("ats.risk_officer.daily")

TAIPEI = ZoneInfo("Asia/Taipei")
_STATE_TEXT = {
    "NORMAL": "正常：可以在预算内增加风险",
    "LIMITED": "受限：离限额很近或有提示，增加风险要在剩余预算内",
    "REPAIR_ONLY": "只修复：有硬限额被突破，只允许降低风险",
    "EMERGENCY": "紧急：回撤、日亏、期权指派或保证金出了大问题，只允许降风险和补流动性",
    "DATA_INVALID": "数据无效：风险读数不可信（净值为零或短 put 概率未知），只允许降风险",
}
_VERDICT_TEXT = {"approved": "通过", "rejected": "驳回"}
_NEAR = 0.8                  # a limit used beyond this share is flagged "接近"


def _pct(v, digits: int = 1) -> str:
    return "—" if v is None else f"{v * 100:.{digits}f}%"


def _status(used: float | None) -> str:
    if used is None:
        return "—"
    return "**破限**" if used > 1.0 else ("接近" if used >= _NEAR else "正常")


def limit_rows(review, rc) -> list[dict]:
    """Every headline limit with where the book stands against it (`used` = share of the limit consumed)."""
    rows: list[dict] = []

    def upper(name, actual, limit, *, fmt=_pct, note=""):
        used = None if actual is None or not limit else max(actual, 0.0) / limit
        rows.append({"name": name, "actual": fmt(actual), "limit": f"≤ {fmt(limit)}", "used": used, "note": note})

    top = max(review.economic_exposures, key=lambda e: abs(e.net_delta_weight), default=None)
    upper("单个标的最大净敞口", abs(top.net_delta_weight) if top else None, rc.max_position_pct,
          note=top.label if top else "")
    upper("总杠杆（扣除现金等价物）", review.effective_leverage, rc.max_gross_leverage,
          fmt=lambda v: "—" if v is None else f"{v:.2f}x")
    cash, floor = review.effective_cash_pct, rc.cash_floor_pct
    if floor < 0:       # may borrow: the floor is a borrowing ceiling, used = share of it already borrowed
        rows.append({"name": "有效现金占比", "actual": _pct(cash), "limit": f"≥ {_pct(floor, 0)}",
                     "used": max(-cash, 0.0) / -floor, "note": "负数 = 已借入；底线与杠杆上限同一条边界"})
    else:
        rows.append({"name": "有效现金占比", "actual": _pct(cash), "limit": f"≥ {_pct(floor)}",
                     "used": None if not floor else (floor / cash if cash > 0 else 9.99), "note": "现金越少，占用越高"})
    upper("组合 beta", review.portfolio_beta, rc.beta_cap, fmt=lambda v: "—" if v is None else f"{v:.2f}")
    cluster = max(review.clusters, key=lambda c: c.weight, default=None)
    upper("最大相关簇权重", cluster.weight if cluster else None, rc.cluster_weight_cap,
          note="、".join(cluster.members[:5]) if cluster else "")
    if review.drawdown_pct is not None:
        rows.append({"name": "最大回撤", "actual": f"{review.drawdown_pct:.1f}%", "limit": f"≥ -{rc.max_drawdown_pct:.0%}",
                     "used": max(-review.drawdown_pct, 0.0) / (rc.max_drawdown_pct * 100), "note": ""})
    if review.daily_pnl_pct is not None:
        rows.append({"name": "今日盈亏", "actual": f"{review.daily_pnl_pct:.2f}%",
                     "limit": f"≥ -{rc.daily_loss_limit_pct:.0%}",
                     "used": max(-review.daily_pnl_pct, 0.0) / (rc.daily_loss_limit_pct * 100), "note": "仅盘中"})
    if review.stress:
        worst = min(review.stress, key=lambda s: s.loss_pct)
        rows.append({"name": "最差压力测试", "actual": f"{worst.loss_pct:.1f}%", "limit": f"≥ -{rc.max_stress_loss_pct:.0%}",
                     "used": max(-worst.loss_pct, 0.0) / (rc.max_stress_loss_pct * 100), "note": worst.scenario})
    if review.margin:
        m = review.margin
        if m.margin_util is not None:
            upper("保证金利用率", m.margin_util, rc.max_margin_util_pct, note="IBKR" if m.source == "ibkr" else "估算")
        if m.excess_liq_pct is not None and rc.min_excess_liquidity_pct:
            rows.append({"name": "剩余流动性", "actual": _pct(m.excess_liq_pct), "limit": f"≥ {_pct(rc.min_excess_liquidity_pct)}",
                         "used": rc.min_excess_liquidity_pct / m.excess_liq_pct if m.excess_liq_pct > 0 else 9.99,
                         "note": "IBKR" if m.source == "ibkr" else "估算"})
    if review.event_risks:
        worst_event = max(review.event_risks, key=lambda e: e.event_loss_pct)
        rows.append({"name": "财报事件最大损失", "actual": f"{worst_event.event_loss_pct:.2f}%",
                     "limit": f"≤ {rc.max_event_loss_pct:.0%}", "used": worst_event.event_loss_pct / (rc.max_event_loss_pct * 100),
                     "note": worst_event.symbol})
    return rows


def layer_rows(review) -> list[dict]:
    rows = [{"name": layer.label or layer.key, "weight": layer.weight, "cap": layer.cap,
             "static_cap": layer.static_cap, "allocation": layer.allocation, "source": layer.cap_source,
             "used": layer.weight / layer.cap if layer.cap else None, "breached": layer.breached}
            for layer in review.chain_layers if layer.cap and (layer.weight or layer.breached)]
    return sorted(rows, key=lambda r: -(r["used"] or 0))


_SOURCE_TEXT = {"static_no_allocation": "无配置结论（用静态）", "static_stale": "配置结论已过期（用静态）"}


def _money(v) -> str:
    return "—" if v is None else f"{v:,.0f}"


def _exposure_section(rows: list[dict], review) -> list[str]:
    """Market value next to true exposure: leveraged ETFs and options are not what their market value says."""
    nav = review.net_liquidation or 1.0
    lines = ["", "持仓：市值与真实敞口（真实敞口 = 市值 × 杠杆倍数；期权按 Δ × 张数 × 乘数 × 标的价；"
             "卖出看跌为正敞口）：", "",
             "| 标的 | 持仓 | 市值(USD) | Delta/倍数 | 真实敞口(USD) | 敞口/净值 | 所在层 |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        qty = "—" if r["qty"] is None else f"{r['qty']:g}"
        lines.append(f"| {r['name']} | {qty} | {_money(r['market_value'])} | {r['factor']} | "
                     f"{_money(r['exposure'])} | {_pct(r['nav_pct'])} | {r['layer']} |")
    total = sum(r["exposure"] or 0.0 for r in rows)
    lines.append(f"| **合计** | | | | **{_money(total)}** | **{_pct(total / nav)}** | |")
    lines += ["", f"按真实敞口算，整本账是净值的 {total / nav:.2f} 倍；总杠杆限额（{review.effective_leverage:.2f}x 那一行）"
              "目前仍按市值口径计，两者的差就是杠杆 ETF 的倍数和期权 Δ。"]
    loose = [r for r in rows if not r["mapped"] and abs(r["exposure"] or 0.0) > 0]
    if loose:
        lines += ["", "**未归入任何产业链层**：" + "；".join(
            f"{r['name']} {_pct(r['nav_pct'])}" for r in loose)
            + "（指数/广谱期权本来就不属于某一层；其他标的请到 `config/risk.yaml` 的 `instruments` 登记归层）。"]
    return lines


def recent_reviews(store, *, now: datetime, hours: int = 36, limit: int = 4) -> list[dict]:
    """The risk gate's latest reviews of Chief revisions: which orders, which verdict, which rule said so."""
    since = (now - timedelta(hours=hours)).isoformat()
    out = []
    for r in store.recent_decision_risk_reviews(since, limit):
        try:
            violations = json.loads(r["violations_json"] or "[]")
            orders = json.loads(r["orders_json"] or "[]")
        except ValueError:
            violations, orders = [], []
        out.append({"cycle_id": r["cycle_id"], "revision_no": r["revision_no"], "verdict": r["verdict"],
                    "created_at": r["created_at"], "notes": (r["notes"] or "").strip(),
                    "violations": violations, "orders": orders})
    return out


def collect(store, *, portfolio=None, now: datetime | None = None) -> dict:
    """Read the book (live unless handed one), assess it, and gather the gate's recent reviews."""
    from ...risk import assess as risk_assess
    from ...risk import inputs as risk_inputs

    now = now or datetime.now(timezone.utc)
    rc = risk_inputs.rules().limits
    source, note, review = "live", "", None
    try:
        if portfolio is None:
            portfolio = risk_inputs.account()
            if portfolio is None:
                raise RuntimeError("broker account unavailable")
        pf = portfolio.model_copy(deep=True)
        risk_assess.enrich_beta(pf)
        risk_assess.enrich_options(pf)
        review = risk_assess.assess(pf)
    except Exception as exc:  # noqa: BLE001 - fall back to the last posture the Chief cycle recorded
        note = f"没能读到实时账户（{str(exc)[:120]}）"
        review, source = store.latest_risk_review(), "saved"
    from ...risk import exposure as risk_exposure

    return {"now": now, "rc": rc, "review": review, "source": source, "note": note,
            "rows": limit_rows(review, rc) if review else [], "layers": layer_rows(review) if review else [],
            "exposure": risk_exposure.exposure_rows(review, portfolio if source == "live" else None) if review else [],
            "reviews": recent_reviews(store, now=now)}


def attention(d: dict) -> list[str]:
    """What needs a person, written so it reads alone."""
    review, items = d["review"], []
    if review is None:
        return ["没有可用的风险读数：既读不到实时账户，也没有保存过的风险评估。请确认 TWS 已登录。"]
    if d["source"] == "saved":
        items.append(f"这份日报用的是上次保存的风险评估（{review.as_of:%m-%d %H:%M} UTC），不是实时账户：{d['note']}。")
    state = review.directive.state if review.directive else "NORMAL"
    for b in review.breaches:
        items.append(f"{b.layer} 破限：实际 {b.actual}，限额 {b.limit}。处置：{b.action}。")
    if state in ("EMERGENCY", "DATA_INVALID"):
        items.append(f"风险状态是 {state}：首席只能降低风险。请先看上面的破限，再决定是否人工处理。")
    rejected = [r for r in d["reviews"][:1] if r["verdict"] == "rejected"]
    if rejected:
        items.append(f"最近一次决策周期 {rejected[0]['cycle_id']} 的订单被风控驳回，原因见第四节。")
    return items


def render(d: dict) -> str:
    review, rc, now = d["review"], d["rc"], d["now"]
    lines = [f"# 风控日报 {now.astimezone(TAIPEI):%Y-%m-%d}", ""]
    if review is None:
        return "\n".join(lines + ["没有可用的风险读数。", "", "## 需要你做的事", ""]
                         + [f"{i}. {t}" for i, t in enumerate(attention(d), 1)]) + "\n"
    directive = review.directive
    state = directive.state if directive else "NORMAL"
    lines += ["## 一、结论", "",
              f"- 风险状态：**{state}** — {_STATE_TEXT.get(state, '')}",
              f"- 净值 ${review.net_liquidation:,.0f}；破限 {len(review.breaches)} 项，提示 {len(review.cautions)} 项。",
              f"- 数据：{'实时账户' if d['source'] == 'live' else '上次保存的评估（' + str(review.as_of)[:16] + ' UTC）'}。"]
    notes = [n for n in review.notes.splitlines() if n.strip() and "breach(es)" not in n]
    if notes:
        lines += [f"- 注意：{n.strip()}" for n in notes]
    lines += ["", "## 二、各项限额用了多少", "", "| 项目 | 现状 | 限额 | 用掉 | 状态 | 备注 |", "|---|---|---|---|---|---|"]
    lines += [f"| {r['name']} | {r['actual']} | {r['limit']} | {_pct(r['used'], 0)} | {_status(r['used'])} | {r['note']} |"
              for r in d["rows"]]
    if d["layers"]:
        lines += ["", "产业链分层（只列有持仓的层；权重按真实敞口算；上限 = 静态天花板 × 行业分析师最新配置结论，"
                  "只下调不上调，天花板见 `config/risk.yaml`）：", "",
                  "| 层 | 权重 | 静态上限 | 配置结论 | 有效上限 | 用掉 | 状态 |", "|---|---|---|---|---|---|---|"]
        lines += [f"| {r['name']} | {_pct(r['weight'])} | {_pct(r['static_cap'])} | "
                  f"{r['allocation'] or _SOURCE_TEXT.get(r['source'], '—')} | {_pct(r['cap'])} | {_pct(r['used'], 0)} | "
                  f"{'**破限**' if r['breached'] else _status(r['used'])} |" for r in d["layers"]]
    if d.get("exposure"):
        lines += _exposure_section(d["exposure"], review)
    if review.breaches or review.cautions:
        lines += ["", "## 三、破限与提示", ""]
        lines += [f"- **破限** {b.layer}：实际 {b.actual}，限额 {b.limit} → {b.action}" for b in review.breaches]
        lines += [f"- 提示 {c.layer}：{c.actual}，{c.limit} → {c.action}" for c in review.cautions]
    else:
        lines += ["", "## 三、破限与提示", "", "没有破限，也没有提示。"]
    if directive:
        lines += ["", "给首席的风险预算：",
                  f"- 允许的动作：{', '.join(directive.allowed_actions) or '无'}；"
                  f"{'可以' if directive.can_increase_risk else '不可以'}增加风险。"]
        if directive.blocked_entities:
            lines.append(f"- 不得增加的标的：{'、'.join(directive.blocked_entities)}")
        if directive.blocked_layers:
            lines.append(f"- 不得增加的产业链层：{'、'.join(directive.blocked_layers)}")
        if directive.risk_budget_remaining:
            lines.append("- 剩余预算：" + "；".join(f"{k} {v:.1%}" for k, v in directive.risk_budget_remaining.items()))

    lines += ["", "## 四、风控闸门最近怎么审订单", ""]
    if not d["reviews"]:
        lines += ["最近 36 小时没有决策周期经过风控审查。", ""]
    for r in d["reviews"]:
        at = datetime.fromisoformat(str(r["created_at"]).replace("Z", "+00:00"))
        lines.append(f"**{r['cycle_id']}**（第 {r['revision_no']} 版，{at.astimezone(TAIPEI):%m-%d %H:%M}）："
                     f"{_VERDICT_TEXT.get(r['verdict'], r['verdict'])}")
        for order in r["orders"][:8]:
            size = (f"${order['notional_usd']:,.0f}" if order.get("notional_usd")
                    else f"{order.get('qty')} 股" if order.get("qty") else "—")
            lines.append(f"- {str(order.get('action', '')).upper()} {order.get('symbol')} {size}")
        for v in r["violations"][:8]:
            lines.append(f"  - 规则 `{v.get('rule_id')}`" + (f"（{v.get('entity')}）" if v.get("entity") else "")
                         + f"：{v.get('detail', '')}")
        lines.append("")

    todo = attention(d)
    lines += ["## 五、需要你做的事", ""] + ([f"{i}. {t}" for i, t in enumerate(todo, 1)] if todo else ["无。"])
    lines += ["", "> 规则全文：`config/risk.yaml`；订单逐笔审查在 `src/ats/risk/checks.py`（单笔上限、事件损失预算、"
              "合并后果不得新破限、增仓要有新近基本面研究）。",
              f"> 生成于 {now.astimezone(TAIPEI):%Y-%m-%d %H:%M}（台北）"]
    return "\n".join(lines) + "\n"


def publish(store=None, *, portfolio=None, push: bool = True) -> dict:
    """Write 风控日报-YYYY-MM-DD.md; push Feishu only when something needs a person. Never raises."""
    try:
        from ...memory import get_store
        from ...runtime.digest import _push, _write_md

        store = store or get_store()
        d = collect(store, portfolio=portfolio)
        path = _write_md(f"风控日报-{d['now'].astimezone(TAIPEI):%Y-%m-%d}.md", render(d))
        reasons = attention(d)
        if push and reasons and d["review"] is not None and (d["review"].breaches or d["source"] == "saved"):
            _push("info", f"风控日报 {d['now'].astimezone(TAIPEI):%Y-%m-%d} — 需要你处理",
                  "\n".join(f"· {r}" for r in reasons) + (f"\n报告：{path.name}" if path else ""))
        return {"path": str(path) if path else None, "alerts": len(reasons)}
    except Exception as exc:  # noqa: BLE001 - a report must never fail what asked for it
        log.warning("risk daily failed: %s", exc)
        return {"error": str(exc)[:200]}
