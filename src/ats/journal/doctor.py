"""Read-only data-quality report for the trade record.

This is the instrument every later journal stage is measured against, so it must be
honest about gaps rather than tidy. It answers one question per section:

  1. 捕获   — did we actually record what happened to our orders?
  2. 幂等   — is one intent one row?
  3. 链路   — do decision → order → fill actually join up?
  4. 归属   — which fills are ours vs manual TWS trades?
  5. 覆盖   — which session days did we observe at all?
  6. 素材   — what can be backfilled without new capture?

Nothing here writes. `ats journal doctor`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class Finding:
    label: str
    value: str
    ok: bool | None = None          # None = neutral fact, not a pass/fail
    detail: str = ""


@dataclass
class Section:
    title: str
    findings: list[Finding] = field(default_factory=list)


def _pct(n: int, d: int) -> str:
    return f"{n}/{d}" + (f" ({n / d:.0%})" if d else "")


# Rows written before the journal landed carry no client_order_id. They cannot be
# repaired — the broker only serves the current day's executions — so they are counted
# separately. A check that stays permanently red for unfixable history just trains the
# reader to ignore the whole report.
_LIVE = "client_order_id IS NOT NULL"
_LEGACY = "client_order_id IS NULL"


def _n(conn, sql: str) -> int:
    return conn.execute(sql).fetchone()[0]


def _capture(conn) -> Section:
    s = Section("1. 捕获完整性 —— 订单结果有没有被记下来")
    total = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    if not total:
        s.findings.append(Finding("trades", "0 行", None, "还没有任何订单记录"))
        return s

    legacy = _n(conn, f"SELECT COUNT(*) FROM trades WHERE {_LEGACY}")
    if legacy:
        s.findings.append(Finding(
            "历史行（日志上线前）", str(legacy), None,
            "无 client_order_id。券商只提供当日执行，无法回溯修复 —— 下方检查已排除它们"))

    # Fill facts are only OWED by orders that actually filled — measuring them against
    # all rows (most of which errored or were cancelled) understates the real state.
    filled = _n(conn, f"SELECT COUNT(*) FROM trades WHERE status='filled' AND {_LIVE}")
    row = conn.execute(
        "SELECT SUM(avg_fill_price IS NOT NULL), SUM(realized_pnl IS NOT NULL), "
        f"SUM(filled_at IS NOT NULL) FROM trades WHERE status='filled' AND {_LIVE}").fetchone()
    px, pnl, fat = (r or 0 for r in row)
    oid = _n(conn, "SELECT COUNT(*) FROM trades WHERE order_id IS NOT NULL AND order_id != ''")

    s.findings.append(Finding("trades 总行数", str(total)))
    s.findings.append(Finding("其中已成交（新制）", str(filled), None, "以下三项只对已成交单计算"))
    s.findings.append(Finding("  有成交价 avg_fill_price", _pct(px, filled), px == filled,
                              "3 秒轮询之后才成交的单不会回填" if px < filled else ""))
    s.findings.append(Finding("  有盈亏 realized_pnl", _pct(pnl, filled), pnl == filled,
                              "_insert_trades 写 None；需 reconcile 回填" if pnl < filled else ""))
    s.findings.append(Finding("  有成交时间 filled_at", _pct(fat, filled), fat == filled))
    s.findings.append(Finding("有 order_id", _pct(oid, total), None,
                              "cancelled/error 单本就没有 order_id"))

    states = conn.execute(
        "SELECT status, COUNT(*) FROM trades GROUP BY status ORDER BY 2 DESC").fetchall()
    s.findings.append(Finding("状态分布", " · ".join(f"{r[0]} {r[1]}" for r in states)))

    zombie = _n(conn, "SELECT COUNT(*) FROM trades "
                      f"WHERE status='submitted' AND avg_fill_price IS NULL AND {_LIVE}")
    if zombie:
        s.findings.append(Finding(
            "僵尸 submitted 行", str(zombie), False,
            "已报但结果未知 —— 跑 `ats journal reconcile` 收口"))

    # Only cycles that actually produced an order under the new regime can be judged;
    # the 27 historical cycles were written before set_cycle_approval existed.
    ncyc = _n(conn, "SELECT COUNT(DISTINCT cycle_id) FROM trades WHERE " + _LIVE)
    napr = _n(conn, "SELECT COUNT(DISTINCT t.cycle_id) FROM trades t "
                    "JOIN cycles c ON c.cycle_id = t.cycle_id "
                    f"WHERE {_LIVE.replace('client_order_id', 't.client_order_id')} "
                    "AND c.approval_status IS NULL")
    if ncyc:
        s.findings.append(Finding("cycles.approval_status 已回填", _pct(ncyc - napr, ncyc),
                                  napr == 0, "persist 节点在拿到裁决后回填" if napr else ""))
    return s


def _idempotency(conn) -> Section:
    s = Section("2. 幂等 —— 一个意图是不是一行")

    def dups(where: str):
        return conn.execute(
            "SELECT cycle_id, symbol, action, COUNT(*) n FROM trades "
            f"WHERE {where} GROUP BY cycle_id, symbol, action HAVING n > 1 "
            "ORDER BY n DESC").fetchall()

    live = dups(_LIVE)
    if live:
        extra = sum(r[3] - 1 for r in live)
        s.findings.append(Finding("重复的意图组（新制）", str(len(live)), False,
                                  f"多出 {extra} 行 —— 唯一索引本应阻止，需排查"))
        for cid, sym, act, n in live[:5]:
            s.findings.append(Finding(f"  {sym} {act}", f"{n} 行", None, cid))
    else:
        s.findings.append(Finding("重复的意图组（新制）", "无", True,
                                  "client_order_id 唯一索引；重试记 attempt+1"))

    old = dups(_LEGACY)
    if old:
        extra = sum(r[3] - 1 for r in old)
        s.findings.append(Finding("历史重复（上线前，不可修）", f"{len(old)} 组 / 多 {extra} 行",
                                  None, "当时无幂等键，IBKR 掉线重试逐次插新行"))
    return s


def _chain(conn) -> Section:
    s = Section("3. 链路 —— 决策 → 订单 → 成交 能否串起来")
    d_no_t = conn.execute(
        "SELECT COUNT(*) FROM decisions d WHERE NOT EXISTS "
        "(SELECT 1 FROM trades t WHERE t.cycle_id=d.cycle_id AND t.symbol=d.symbol)"
    ).fetchone()[0]
    t_no_d = conn.execute(
        "SELECT COUNT(*) FROM trades t WHERE NOT EXISTS "
        "(SELECT 1 FROM decisions d WHERE d.cycle_id=t.cycle_id AND d.symbol=t.symbol)"
    ).fetchone()[0]
    s.findings.append(Finding("有决策但无订单", str(d_no_t), None,
                              "决策被风控拦下或未获批 —— 正常，但现在无处查证原因"))
    s.findings.append(Finding("有订单但无决策", str(t_no_d), None,
                              "manual / stored-decisions 路径绕过了 persist_decision"))

    # Durable identity coverage. orderId alone is not joinable across sessions (TWS
    # resets it), so what matters is how many LIVE rows carry order_ref / perm_id.
    live = _n(conn, f"SELECT COUNT(*) FROM trades WHERE {_LIVE}")
    if live:
        tagged = _n(conn, "SELECT COUNT(*) FROM trades WHERE "
                          f"{_LIVE} AND COALESCE(order_ref,'') != ''")
        s.findings.append(Finding(
            "订单带持久标识 order_ref", _pct(tagged, live), tagged == live,
            "未打标的单只能靠 order_id+同交易日 推定归属" if tagged < live else
            "成交归属不再依赖日期启发式"))
    unlinked = _n(conn, "SELECT COUNT(*) FROM fills WHERE COALESCE(origin,'') = ''")
    nfills = _n(conn, "SELECT COUNT(*) FROM fills")
    if nfills:
        s.findings.append(Finding(
            "成交已判定归属", _pct(nfills - unlinked, nfills), unlinked == 0,
            "跑 `ats journal reconcile` 判定" if unlinked else ""))
    return s


def _attribution(conn) -> Section:
    s = Section("4. 成交归属 —— 哪些是系统单，哪些是你手工下的")
    total = conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    if not total:
        s.findings.append(Finding("fills", "0 行", None))
        return s
    orphans = conn.execute(
        "SELECT symbol, order_id, realized_pnl FROM fills "
        "WHERE order_id NOT IN (SELECT order_id FROM trades WHERE order_id != '')"
    ).fetchall()
    s.findings.append(Finding("fills 总行数", str(total)))
    s.findings.append(Finding("可匹配到系统订单", _pct(total - len(orphans), total)))
    for sym, oid, pnl in orphans:
        s.findings.append(Finding(f"  无主成交 {sym}", f"order_id={oid} 盈亏 {pnl:+.2f}", None,
                                  "账户级执行流会带回你在 TWS 手工下的单"))
    return s


def _coverage(conn) -> Section:
    s = Section("5. 观测覆盖 —— 哪些交易日真的被记录了")
    days = [r[0] for r in conn.execute(
        "SELECT DISTINCT substr(time,1,10) FROM fills ORDER BY 1").fetchall()]
    perf = conn.execute("SELECT COUNT(*) FROM performance").fetchone()[0]
    s.findings.append(Finding("有成交记录的交易日", str(len(days)),
                              None, " · ".join(days) if days else "无"))
    s.findings.append(Finding("performance 快照", str(perf), None,
                              "reqExecutions 只返回当日执行 —— 没跑快照的那天成交永久丢失"))
    if days:
        s.findings.append(Finding("⚠️ 追溯边界", f"{days[0]} 之前", None,
                                  "此前的成交不可恢复（除非 IBKR Flex Query 导入），"
                                  "不得当成『没有交易』"))
    return s


def _material(conn) -> Section:
    s = Section("6. 可回填素材 —— 无需新增捕获就能算的")
    n_score = n_em = n_pt = 0
    bands: list[str] = []
    for (payload,) in conn.execute("SELECT payload FROM pead_dossier"):
        try:
            d = json.loads(payload)
        except (TypeError, ValueError):
            continue
        sc = d.get("scorecard") or {}
        ms = d.get("market_setup") or {}
        es = d.get("expectation_set") or {}
        if sc.get("total") is not None:
            n_score += 1
            bands.append(f"{d.get('symbol')} {sc['total']:+.2f}")
        if ms.get("expected_move_pct"):
            n_em += 1
        if es.get("consensus_target_price"):
            n_pt += 1
    s.findings.append(Finding("已打分的 scorecard", str(n_score), None,
                              " · ".join(bands) if bands else ""))
    s.findings.append(Finding("expected_move 预期波动", str(n_em)))
    s.findings.append(Finding("consensus 目标价", str(n_pt)))
    pnl_fills = conn.execute("SELECT COUNT(*) FROM fills WHERE realized_pnl != 0").fetchone()[0]
    s.findings.append(Finding("有盈亏的成交", str(pnl_fills), None,
                              "样本远小于打分数 —— 校准比盈亏累积快得多"))
    return s


def collect(conn) -> list[Section]:
    return [f(conn) for f in (_capture, _idempotency, _chain,
                              _attribution, _coverage, _material)]


def render(sections: list[Section]) -> str:
    out: list[str] = ["=== 交易记录体检 (journal doctor) ==="]
    for sec in sections:
        out.append(f"\n{sec.title}")
        for f in sec.findings:
            mark = "  " if f.ok is None else ("✅" if f.ok else "❌")
            line = f" {mark} {f.label:26} {f.value}"
            out.append(line if not f.detail else f"{line}\n        ↳ {f.detail}")
    return "\n".join(out)


def run() -> int:
    from ..memory import get_store

    sections = collect(get_store().conn)
    print(render(sections))
    bad = sum(1 for s in sections for f in s.findings if f.ok is False)
    print(f"\n  {bad} 项需要修复。" if bad else "\n  未发现问题。")
    return 0
