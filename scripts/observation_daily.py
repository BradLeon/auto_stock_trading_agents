"""Observation window (design.md D8): one read-only report of what each trading day left behind, and the seven stop conditions.

Usage:  .venv/bin/python scripts/observation_daily.py [窗口起算日 YYYY-MM-DD，默认 2026-11-04]

Reads var/ats.sqlite in read-only mode; writes nothing. A condition shown as ✗ or ? needs a person to look
(see verification.md 3.24); it does not stop anything by itself.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
START = sys.argv[1] if len(sys.argv) > 1 else "2026-11-04"
END = "2026-11-06"
db = sqlite3.connect(f"file:{ROOT / 'var' / 'ats.sqlite'}?mode=ro", uri=True)
db.row_factory = sqlite3.Row


def q(sql: str, *args):
    return db.execute(sql, args).fetchall()


def one(sql: str, *args):
    return db.execute(sql, args).fetchone()[0]


def mark(ok: bool | None) -> str:
    return "✓" if ok else ("?" if ok is None else "✗")


days, d = [], date.fromisoformat(START)
NOW = datetime.now(timezone.utc)
while d <= min(NOW.date(), date.fromisoformat(END)):
    if d.weekday() < 5:                       # holidays are not known here: a missing day on one needs a human eye
        days.append(d.isoformat())
    d += timedelta(days=1)

print(f"# 观察窗口 {START} 起（{len(days)} 个工作日，窗口共 3 个交易日：11-04、11-05、11-06）\n")
print("## 每日一行\n")
print("| 日期 | 定时周期 | 终态 | 修订数 | 风控审查 | 你的答复 | Clerk 当日运行 |")
print("|---|---|---|---|---|---|---|")
failures: list[tuple[str, bool]] = []
chain_bad: list[str] = []
dup_days: list[str] = []
for day in days:
    cycles = q("SELECT cycle_id, status FROM decision_cycles WHERE trigger_source = 'scheduled' "
               "AND substr(created_at, 1, 10) = ? ORDER BY created_at", day)
    if not cycles and day == NOW.date().isoformat() and (NOW.hour, NOW.minute) < (15, 30):
        print(f"| {day} | 待运行（美东 09:45 = UTC 14:45） | — | — | — | — | — |")
        continue
    if not cycles:
        print(f"| {day} | 无 | — | — | — | — | {one('SELECT COALESCE(MAX(status), \"无\") FROM clerk_runs WHERE kind = \"full\" AND window_end = ?', day)} |")
        failures.append((day, True))
        continue
    if len(cycles) > 1:
        dup_days.append(day)
    for c in cycles:
        cid = c["cycle_id"]
        revs = one("SELECT COUNT(*) FROM decision_revisions WHERE cycle_id = ?", cid)
        reviews = one("SELECT COUNT(*) FROM decision_risk_reviews WHERE cycle_id = ?", cid)
        answers = [r["decision"] for r in q("SELECT decision FROM boss_approvals WHERE cycle_id = ? ORDER BY created_at", cid)]
        snap = one("SELECT research_snapshot IS NOT NULL AND research_snapshot != '' FROM decision_cycles WHERE cycle_id = ?", cid)
        refused = one("SELECT COUNT(*) FROM cycle_events WHERE cycle_id = ? AND payload LIKE '%订单规范化拒绝%'", cid)
        # A revision refused at order normalization never reaches the risk review, by design: the refusal is on the record.
        if revs and not (snap and reviews) and not (snap and not reviews and refused):
            chain_bad.append(f"{cid}: 有修订但缺{'研究快照' if not snap else '风控审查'}")
        if c["status"] in ("approved_not_executed", "approval_rejected") and not answers:
            chain_bad.append(f"{cid}: 终态 {c['status']} 但没有审批记录")
        failures.append((day, c["status"] == "manual_review" and not revs))
        clerk = one("SELECT COALESCE(MAX(status), '无') FROM clerk_runs WHERE kind = 'full' AND window_end = ?", day)
        print(f"| {day} | {cid} | {c['status']} | {revs} | {reviews} | {'、'.join(answers) or '—'} | {clerk} |")

print("\n## 七条停止条件\n")
since = f"{START}"
# Under the live lock an approval writes its orders as "rejected / auto execution disabled" with no broker order id:
# those are records of NOT submitting. A real submission carries a broker order id.
trades = one("SELECT COUNT(*) FROM trades WHERE submitted_at >= ? AND COALESCE(order_id, '') != ''", since)
lock_records = one("SELECT COUNT(*) FROM trades WHERE submitted_at >= ? AND COALESCE(order_id, '') = ''", since)
system_fills = one("SELECT COUNT(*) FROM fills WHERE time >= ? AND origin IS NOT NULL "
                   "AND origin NOT IN ('manual', 'unattributed')", since)
c1 = trades == 0 and system_fills == 0
zero = subprocess.run([sys.executable, "-m", "ats.runtime.cli", "retirement", "zero", "--identifier",
                       "chief.legacy_table_direct_reads"], cwd=ROOT, capture_output=True, text=True)
c2 = "清零" in zero.stdout
bad_bind = q("SELECT b.cycle_id FROM boss_approvals b LEFT JOIN decision_risk_reviews r ON r.cycle_id = b.cycle_id "
             "AND r.revision_no = b.revision_no AND r.decision_hash = b.decision_hash "
             "WHERE b.created_at >= ? AND r.review_id IS NULL", since)
stale = q("SELECT b.cycle_id FROM boss_approvals b JOIN decision_cycles c ON c.cycle_id = b.cycle_id "
          "WHERE b.created_at >= ? AND b.decision = 'approve' AND c.status = 'superseded'", since)
c3 = not bad_bind and not stale
c4 = not chain_bad
unlinked = one("SELECT COUNT(*) FROM fills WHERE time >= ? AND origin IS NOT NULL AND origin NOT IN ('manual', 'unattributed') "
               "AND (cycle_id IS NULL OR cycle_id = '')", since)
c5 = unlinked == 0
manual = q("SELECT cycle_id FROM decision_cycles WHERE trigger_source != 'scheduled' AND created_at >= ?", since)
c6 = not dup_days
consecutive = any(failures[i][1] and failures[i + 1][1] for i in range(len(failures) - 1))

rows = [
    (1, "出现真实券商提交", mark(c1), f"带券商订单号的订单记录 {trades} 条，系统来源成交 {system_fills} 笔（都应为 0）；另有 {lock_records} 条无订单号的“未提交”记录（实盘锁定下的批准，不算提交）"),
    (2, "主理人上下文读旧表", mark(c2), "ats retirement zero chief.legacy_table_direct_reads → " + ("清零" if c2 else "未清零，需看输出")),
    (3, "审批绑定错误", mark(c3), f"批准的 hash 对不上风控审查 {len(bad_bind)} 条；批准了已被取代的周期 {len(stale)} 条"),
    (4, "决策链断裂", mark(c4), "；".join(chain_bad) or "每个周期都能从快照连到修订、风控、审批"),
    (5, "Clerk 误归因", mark(c5), f"系统来源但没有周期联接的成交 {unlinked} 笔（应为 0）。同一窗口重放新事实：靠测试，脚本不查"),
    (6, "重复触发或重复请求模型", mark(c6 and not manual),
     (f"同一天多个定时周期：{dup_days}；" if dup_days else "每个交易日最多 1 个定时周期；")
     + (f"窗口内有手动周期 {[m['cycle_id'] for m in manual]}（手动运行也在花模型请求，窗口期间不要跑）；" if manual else "没有手动周期；")
     + "模型请求数请对照 OpenRouter 当日首席请求次数（正常 2–3 次）"),
    (7, "主理人连续 2 个交易日失败", mark(None if consecutive else True),
     ("出现连续两天没有出修订的定时周期，需判断是不是数据缺口（数据缺口不算）" if consecutive
      else "没有连续两个失败日（11-02、11-03 的失败发生在窗口开始前，不计）")),
]
print("| # | 条件 | 结论 | 依据 |")
print("|---|---|---|---|")
for n, name, verdict, basis in rows:
    print(f"| {n} | {name} | {verdict} | {basis} |")
answers_total = q("SELECT decision, COUNT(*) n FROM boss_approvals WHERE created_at >= ? GROUP BY decision", since)
print("\n## 审批样本（要求至少 3 次，批准、拒绝各至少 1 次）\n")
print("；".join(f"{r['decision']} {r['n']} 次" for r in answers_total) or "还没有审批记录")
print("\n✓ 正常　✗ 触发，停止并找我　? 要人工判断")
