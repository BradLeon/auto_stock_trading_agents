"""Clerk 日报 — the account as of one Clerk run, in plain language.

Written for the account owner: what is held and how it is doing, what traded, whether the books agree with the
broker, and what (if anything) needs a decision. Deterministic and read-only — no LLM, no ledger write.
Regenerated in full on every call. Feishu is pushed only when something needs a person.

A failure to render or write the report never fails the run: `publish` returns the error instead.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ..trader import fx

log = logging.getLogger("ats.clerk")

TAIPEI = ZoneInfo("Asia/Taipei")
_MAX_ROWS = 30
_ORIGIN = {"manual": "手工下单", "system": "系统下单"}
_RIGHT = {"C": "认购", "P": "认沽"}


def _money(v) -> str:
    return f"-${-v:,.0f}" if v < 0 else f"${v:,.0f}"


def _signed(v) -> str:
    return f"{v:+,.0f}"


def _ts(stamp) -> datetime:
    value = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _taipei(stamp) -> str:
    return _ts(stamp).astimezone(TAIPEI).strftime("%m-%d %H:%M")


def _name(p: dict) -> str:
    if p.get("sec_type") != "OPT":
        return p["symbol"]
    expiry = str(p.get("expiry") or "")
    day = f"{expiry[:4]}-{expiry[4:6]}-{expiry[6:]}" if len(expiry) == 8 else expiry
    return f"{p['symbol']} {day} {_RIGHT.get(str(p.get('right') or '')[:1].upper(), '期权')} {p.get('strike'):g}"


def collect(store, out: dict, *, source: str) -> dict:
    from ..broker.ibkr import UNREACHABLE_MARK
    from . import baseline

    conn = store.conn
    run = conn.execute("SELECT * FROM clerk_runs WHERE run_id = ?", (out["run_id"],)).fetchone()
    if run is None:
        raise LookupError(f"clerk run {out['run_id']} has no clerk_runs row")
    counts = json.loads(run["counts_json"] or "{}")
    error = run["error"] or ""
    text = f"{error} " + " ".join((counts.get("reconcile") or {}).get("errors", []))
    tws = "没连上" if UNREACHABLE_MARK in text else ("读取异常" if "IBKR" in text or "broker unavailable" in text else "连通")

    snapshot = baseline.snapshot_in_run(store, run["started_at"] or "", run["finished_at"])
    stale = snapshot is None
    if stale:
        snapshot = baseline.latest_snapshot(store)
    previous = baseline.snapshot_before(store, snapshot["as_of"]) if snapshot else None
    changes = baseline.position_changes(store, previous, snapshot) if snapshot else None

    since = previous["as_of"] if previous else f"{run['window_start']}T00:00:00+00:00"
    until = snapshot["as_of"] if snapshot and not stale else (run["finished_at"] or datetime.now(timezone.utc).isoformat())
    fills = []
    for r in conn.execute(
            "SELECT time, captured_at, symbol, side, shares, price, commission, realized_pnl, origin, "
            "instrument_key, multiplier, sec_type, currency, fx_to_usd, fx_source "
            "FROM fills ORDER BY COALESCE(time, captured_at)"):
        moment = r["time"] or r["captured_at"]
        if moment and _ts(since) < _ts(moment) <= _ts(until):
            fills.append(dict(r) | {"at": moment})
    orders = [dict(r) for r in conn.execute(
        "SELECT symbol, action, qty, status, submitted_at FROM trades WHERE submitted_at > ? AND submitted_at <= ? "
        "ORDER BY submitted_at", (since, until))]

    recent = [dict(r) | {"at": r["time"] or r["captured_at"]} for r in conn.execute(
        "SELECT time, captured_at, symbol, side, shares, price, commission, realized_pnl, origin, multiplier, "
        "currency, fx_to_usd, fx_source "
        "FROM fills WHERE COALESCE(time, captured_at, '') != '' ORDER BY COALESCE(time, captured_at) DESC LIMIT 10")]
    exceptions = [dict(r) for r in conn.execute(
        "SELECT kind, symbol, window_start, window_end, basis, first_seen_at FROM ledger_exceptions "
        "WHERE status = 'open' ORDER BY first_seen_at")]
    return {"exposure": _exposure_by_position(store, snapshot),
            "run_id": run["run_id"], "status": run["status"], "reused": bool(out.get("reused")),
            "source": source, "window": (run["window_start"], run["window_end"]),
            "started_at": run["started_at"] or "", "finished_at": run["finished_at"], "error": error,
            "tws": tws, "counts": counts, "snapshot": snapshot, "stale_snapshot": stale,
            "previous": previous, "changes": changes, "fills": fills, "recent_fills": recent, "orders": orders, "exceptions": exceptions,
            "day": _ts(run["finished_at"] or run["started_at"]).astimezone(TAIPEI).date().isoformat(),
            "baseline": baseline.baseline_date(store),
            "waived": conn.execute("SELECT COUNT(*) FROM ledger_exceptions WHERE status = 'waived'").fetchone()[0]}


def _flex_still_behind(d: dict) -> str:
    """The last day the Flex statement covers, when it has not yet reached the day of this run (US Eastern).

    A statement is a business day behind, so a trade made since the last one is not in it yet; the Clerk fills it
    in on a later run by itself. '' when the statement is current, was not had, or states no date."""
    flex = (d["counts"].get("reconcile") or {}).get("flex") or {}
    through = flex.get("through") or ""
    if flex.get("status") != "ok" or not through:
        return ""
    from zoneinfo import ZoneInfo

    run_day = _ts(d["finished_at"] or d["started_at"]).astimezone(ZoneInfo("America/New_York")).date().isoformat()
    return through if through < run_day else ""


def attention(d: dict, *, only_new: bool = False) -> list[str]:
    """What needs a person; written so it can be read without the rest of the report.

    `only_new` keeps what this run found (what is worth a push); the full list also carries what is still open."""
    items = []
    if d["status"] == "failed":
        if d["tws"] == "没连上":
            items.append("今天的账没有对完：没能连上 TWS。请确认 TWS 已登录，然后运行 `ats clerk run` 重跑。")
        else:
            items.append("今天的账没有对完：" + (d["error"] or "原因未记录")[:200] + "。请把这条发给我排查。")
    changes = d["changes"]
    if changes and changes["mismatched"]:
        names = "、".join(f"{r['symbol']}（应有 {r['expected']:g}，券商实际 {r['actual']:g}）"
                         for r in changes["mismatched"][:6])
        waiting = _flex_still_behind(d)
        if waiting:
            items.append(f"{len(changes['mismatched'])} 个标的的持仓数量与成交记录对不上：{names}。"
                         f"不需要你现在做什么：券商的 Flex 对账单目前只到 {waiting}（美东），这几天的成交还没进去；"
                         "Clerk 每次对账后都会用它补漏，等它覆盖到这几天，成交会自动补入，这条差异随之消失。"
                         "如果 Flex 已经覆盖到那几天还是对不上，下一份日报才会请你核对。")
        else:
            flex = (d["counts"].get("reconcile") or {}).get("flex") or {}
            why = ("Flex 对账单已覆盖到这几天，仍然对不上" if flex.get("status") == "ok"
                   else "Flex 对账单这次没能取到（" + str(flex.get("reason") or flex.get("error") or flex.get("status")
                                                  or "未运行") + "）")
            items.append(f"{len(changes['mismatched'])} 个标的的持仓数量与成交记录对不上：{names}。{why}。"
                         "常见原因：期权到期或被行权、公司行为、非本账户的成交。"
                         "请先在 IBKR 里核对这些标的的成交，缺的成交用交易确认报告补录。")
    unattributed = (d["counts"].get("reconcile") or {}).get("unattributed", 0)
    if unattributed:
        items.append(f"有 {unattributed} 笔成交不知道是谁下的单（既不是系统单，也无法判断为手工单），已登记待查。")
    for e in d["exceptions"]:
        if only_new and e["first_seen_at"] < d["started_at"]:
            continue
        if e["kind"] == "reconciliation_gap":
            items.append(f"{e['window_start']} 至 {e['window_end']} 这段时间的成交没能和券商核对，账本可能缺成交。")
        elif e["kind"] == "unattributed_fill" and not unattributed:
            items.append(f"{e['symbol'] or ''} 一笔成交来源不明，已登记待查。")
        elif e["kind"] == "broken_link":
            items.append(f"{e['symbol'] or ''} 一笔旧订单查不到当初的决策依据。")
    if only_new:
        return items
    if d["snapshot"] is None:
        items.append("还没有任何持仓快照，持仓清单和持仓核对暂时没有数据。")
    elif d["stale_snapshot"] and not d["reused"]:
        items.append("这次运行没有保存持仓快照（可能连不上 TWS），下面的持仓用的是更早的快照，时间见标注。")
    return items


def _conclusion(d: dict) -> str:
    snap = d["snapshot"]
    if snap is None:
        return "账户数据还没有生成。"
    value = sum(p["market_value"] for p in snap["positions"])
    pnl = sum(p["unrealized_pnl"] for p in snap["positions"])
    traded = f"今天有 {len(d['fills'])} 笔成交" if d["fills"] else "今天没有成交"
    parts = [f"{traded}；账户净值 {_money(snap['net_liquidation'])}，持仓 {len(snap['positions'])} 个，"
             f"浮动盈亏合计 {_money(pnl)}"]
    todo = attention(d)
    parts.append(f"有 {len(todo)} 件事需要你处理" if todo else "没有需要你处理的事")
    if d["status"] == "failed":
        parts[0] = "今天的账没有对完；" + parts[0]
    return "；".join(parts) + "。"


def _exposure_by_position(store, snap: dict | None) -> dict:
    """True exposure per holding, from the same risk engine the 风控日报 uses (live; {} when unreachable)."""
    if snap is None:
        return {}
    try:
        from ..agents.risk_officer import daily
        from ..risk import exposure

        d = daily.collect(store)
        if d["source"] != "live" or d["review"] is None:
            return {}
        return exposure.by_key(d["exposure"])
    except Exception:  # noqa: BLE001 - the extra columns must never fail the clerk's report
        return {}


def _exposure_cells(p: dict, exposure: dict, nav: float) -> tuple[str, float | None, str]:
    """(Delta/倍数, 真实敞口 USD, 敞口占净值) for one holding; None / "—" where the book cannot say."""
    from ..config import load_instrument_risk_registry
    from ..risk.exposure import option_key_of_position

    if p["sec_type"] == "OPT":
        row = exposure.get(("OPT",) + option_key_of_position(p))
    else:
        row = exposure.get(("STK", p["symbol"]))
        if row is None:               # no live risk read: a stock's exposure is still market value × multiplier
            mult = load_instrument_risk_registry().resolve(p["symbol"]).exposure_multiplier
            value = p["market_value"] * mult
            return (f"{mult:g}× 每日目标" if mult != 1 else "1"), value, (f"{value / nav:.1%}" if nav else "—")
    if row is None:
        return "—", None, "—"
    return row["factor"], row["exposure"], f"{row['nav_pct']:.1%}"


def _holdings(snap: dict, exposure: dict | None = None) -> list[str]:
    exposure = exposure or {}
    nav = snap.get("net_liquidation") or 0.0
    lines = ["| 标的 | 类型 | 数量 | 成本价 | 现价 | 市值 | 占净值 | Delta/倍数 | 真实敞口 | 敞口/净值 | 浮动盈亏 | 浮盈亏% |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    total_value = total_pnl = total_exposure = 0.0
    for p in sorted(snap["positions"], key=lambda p: -abs(p["market_value"])):
        mult = (p.get("multiplier") or 1) if p["sec_type"] == "OPT" else 1
        cost = p["avg_cost"] / mult
        basis = abs(cost * p["qty"] * mult) or 1
        factor, value, share = _exposure_cells(p, exposure, nav)
        lines.append(f"| {_name(p)} | {'期权' if p['sec_type'] == 'OPT' else '股票'} | {p['qty']:g} | {cost:,.2f} | "
                     f"{p['market_price']:,.2f} | {_money(p['market_value'])} | {p['weight']:.1%} | "
                     f"{factor} | {'—' if value is None else _money(value)} | {share} | "
                     f"{_signed(p['unrealized_pnl'])} | {p['unrealized_pnl'] / basis * 100:+.1f}% |")
        total_value += p["market_value"]
        total_pnl += p["unrealized_pnl"]
        total_exposure += value or 0.0
    lines.append(f"| **合计** | | | | | **{_money(total_value)}** | | | **{_money(total_exposure)}** | "
                 f"**{total_exposure / nav:.1%}** | **{_signed(total_pnl)}** | |" if nav else
                 f"| **合计** | | | | | **{_money(total_value)}** | | | | | **{_signed(total_pnl)}** | |")
    return lines


def _fill_table(fills: list[dict]) -> list[str]:
    """Amounts are in each contract's own currency; a known rate adds the USD equivalent, a missing one says so."""
    lines = ["| 时间（台北） | 标的 | 方向 | 数量 | 价格 | 成交额 | 手续费 | 已实现盈亏 | 来源 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for f in fills[:_MAX_ROWS]:
        ccy = (f.get("currency") or "").upper()
        amount = f["shares"] * f["price"] * (f["multiplier"] or 1)
        pnl = ("—" if f["realized_pnl"] is None else "+0" if not f["realized_pnl"]       # an opening fill realizes nothing
               else fx.money(f["realized_pnl"], f, signed=True))
        fee = "—" if f["commission"] is None else fx.money(f["commission"], f, digits=2)
        price = format(f["price"], ",.2f") + ("" if ccy in ("", "USD") else f" {ccy}")
        lines.append(f"| {_taipei(f['at'])} | {f['symbol']} | {'买入' if f['side'] == 'BOT' else '卖出'} | "
                     f"{f['shares']:g} | {price} | {fx.money(amount, f)} | {fee} | {pnl} | "
                     f"{_ORIGIN.get(f['origin'], '来源未判定')} |")
    return lines


def render(d: dict) -> str:
    snap = d["snapshot"]
    start, end = d["window"]
    lines = [f"# Clerk 日报 — {d['day']}（台北时间）", "", f"**一句话：** {_conclusion(d)}", ""]
    if d["reused"]:
        lines += ["> 本次调用复用了该窗口已有的结果，没有重新执行；以下是首次运行的结果。", ""]
    if snap is None:
        lines += ["还没有持仓快照。", ""]
    else:
        stamp = _taipei(snap["as_of"])
        lines += [f"## 一、账户总览（IBKR {stamp} 台北时间）" + ("　⚠ 这是更早的快照，本次运行没有保存" if d["stale_snapshot"] else ""),
                  "", "| 净值 | 现金 | 持仓净市值（多头减空头） | 券商口径当日盈亏 | 已实现盈亏（券商口径） |",
                  "|---|---|---|---|---|",
                  f"| {_money(snap['net_liquidation'])} | {_money(snap['cash'])} | "
                  f"{_money(sum(p['market_value'] for p in snap['positions']))} | {_signed(snap['daily_pnl'])} | "
                  f"{_signed(snap['realized_pnl'])} |", ""]
        previous = d["previous"]
        if previous:
            gap = (_ts(snap["as_of"]) - _ts(previous["as_of"])).days
            lines += [f"相比上一份快照（{_taipei(previous['as_of'])}）：净值 "
                      f"{_signed(snap['net_liquidation'] - previous['net_liquidation'])}"
                      f"（含出入金{'' if gap <= 1 else f'，间隔 {gap} 天，不是单日变动'}）。", ""]
        else:
            lines += ["这是第一份快照，作为后续核对的起点。", ""]
        lines += ["## 二、持仓清单（按市值从大到小）", ""] + _holdings(snap, d.get("exposure")) + [
            "", "数量为负表示空头（例如卖出开仓的期权）。**真实敞口**才是账户实际承担的方向风险："
            "杠杆 ETF 按倍数放大，期权按 Δ × 张数 × 乘数 × 标的价（卖出看跌是正敞口，市值只是权利金）；"
            "敞口取自生成报告时的实时读数，与快照时点可能略有出入；「—」表示这次没读到实时 Delta。", ""]

        lines += ["## 三、持仓变化核对", ""]
        changes = d["changes"]
        if changes["baseline"]:
            lines += ["今天是期初建账的起点，没有可比较的上一份快照。从下一次运行开始，每次都会核对："
                      "上次持仓 + 期间成交 = 现在持仓。", ""]
        else:
            moved = [r for r in changes["rows"] if r["traded"] or abs(r["actual"] - r["before"]) > 1e-6]
            lines += [f"对照上一份快照（{_taipei(changes['since'])}）：上次持仓加期间成交，应当等于券商现在的持仓。", ""]
            if not moved:
                lines += ["没有任何标的的持仓或成交发生变化。", ""]
            else:
                lines += ["| 标的 | 上次持仓 | 期间成交（买为正） | 应有持仓 | 券商实际 | 结果 |", "|---|---|---|---|---|---|"]
                lines += [f"| {r['symbol']}{'（期权）' if r['sec_type'] == 'OPT' else ''} | {r['before']:g} | "
                          f"{r['traded']:+g} | {r['expected']:g} | {r['actual']:g} | "
                          f"{'一致' if abs(r['difference']) <= 1e-6 else '**对不上，差 %+g**' % r['difference']} |"
                          for r in moved[:_MAX_ROWS]] + [""]

    lines += ["## 四、成交记录", ""]
    lines += (_fill_table(d["fills"]) if d["fills"] else ["这段时间（自上一份快照起）没有成交。"]) + [""]
    if d["recent_fills"]:
        lines += ["最近 10 笔成交（不限日期，供参考）：", ""] + _fill_table(d["recent_fills"]) + [""]
    if d["orders"]:
        lines += ["系统自动提交的订单：" + "；".join(f"{o['symbol']} {o['action']} {o['qty']:g}（{o['status']}）"
                                                    for o in d["orders"][:10]), ""]

    reconcile = d["counts"].get("reconcile") or {}
    lines += ["## 五、系统自检", ""]
    lines.append(f"- 运行：{'完成' if d['status'] == 'completed' else '失败' if d['status'] == 'failed' else d['status']}；"
                 f"TWS {d['tws']}；窗口 {start if start == end else f'{start} 至 {end}'}（UTC 日期）。")
    if "reconcile" in d["counts"]:
        lines.append(f"- 从券商读到 {reconcile.get('fills_seen', 0)} 笔成交，新增入账 {reconcile.get('fills_new', 0)} 笔："
                     f"系统订单 {reconcile.get('linked', 0)}、手工单 {reconcile.get('manual', 0)}、"
                     f"来源无法判断 {reconcile.get('unattributed', 0)}。")
    else:
        lines.append("- 这次没有读到券商成交（对账没有执行）。")
    positions = d["counts"].get("positions") or {}
    lines.append("- 持仓快照：" + ("已保存。" if positions.get("saved") else "本次没有保存。"))
    if d["baseline"]:
        lines.append(f"- 账本从 {d['baseline']} 起算（期初建账）；之前遗留的 {d['waived']} 条提示已封存，原始记录保留，"
                     "不影响今天的数字。")
    lines.append("")

    todo = attention(d)
    lines += ["## 六、需要你做的事", ""]
    lines += [f"{i}. {item}" for i, item in enumerate(todo, 1)] if todo else ["无。"]
    lines += ["", f"> 生成于 {datetime.now(TAIPEI):%Y-%m-%d %H:%M}（台北） ｜ 运行 `{d['run_id']}` ｜ {d['source']}"]
    return "\n".join(lines) + "\n"


def publish(out: dict, *, source: str, store=None) -> dict:
    """Write the report; push Feishu only when something needs attention. Never raises."""
    try:
        from ..memory import get_store
        from ..runtime.digest import _push, _write_md

        store = store or get_store()
        d = collect(store, out, source=source)
        path = _write_md(f"Clerk日报-{d['day']}.md", render(d))
        reasons = [] if d["reused"] else attention(d, only_new=True)
        if reasons:
            _push("error" if d["status"] == "failed" else "info", f"Clerk 日报 {d['day']} — 需要你处理",
                  "\n".join(f"· {r}" for r in reasons) + (f"\n报告：{path.name}" if path else ""))
        return {"path": str(path) if path else None, "alerts": len(reasons)}
    except Exception as exc:  # noqa: BLE001 - a report must never fail the run it describes
        log.warning("clerk report failed: %s", exc)
        return {"error": str(exc)[:200]}
