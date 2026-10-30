"""`ats approval` — the Boss's command-line answer to a pending decision cycle (design D6).

A cycle waits for approval only when it has orders that need trading AND a passed risk review.
Three answers exist: approve, revise (reject this revision with a change request, the Chief
revises once more), reject (the cycle ends). Every answer names the revision by at least 12
characters of its hash, so a stale answer can never land on a newer revision.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from ..decision.repository import (DecisionAuditRepository, approval_idempotency_key,
                                   review_round_of)
from ..decision.state import CycleStatus, is_terminal

MIN_HASH_PREFIX = 12
MAX_SNAPSHOT_AGE_BUDGET_SECONDS = 60


class ApprovalRefused(Exception):
    """The answer cannot be applied; the message says why and what is current."""


def _repo() -> DecisionAuditRepository:
    from ..memory import get_store

    return DecisionAuditRepository(get_store())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _orders(revision) -> list[dict]:
    return json.loads(revision["orders_json"] or "[]")


# --- what is waiting -------------------------------------------------------------------------- #
def _waiting_since(repo, cycle) -> datetime | None:
    row = repo.conn.execute(
        "SELECT created_at FROM cycle_events WHERE cycle_id = ? AND event_type = 'status_change' "
        "ORDER BY event_id DESC LIMIT 1", (cycle["cycle_id"],)).fetchone()
    stamp = (row["created_at"] if row else None) or cycle["updated_at"]
    try:
        value = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def pending_cycles(repo: DecisionAuditRepository) -> list[dict]:
    """Cycles that need the Boss: pending approval, orders to place, current revision passed risk."""
    out = []
    rows = repo.conn.execute(
        "SELECT * FROM decision_cycles WHERE status = ? ORDER BY created_at",
        (CycleStatus.PENDING_APPROVAL.value,)).fetchall()
    for cycle in rows:
        rev = repo.latest_revision(cycle["cycle_id"])
        if rev is None or not [o for o in _orders(rev) if o.get("action") != "hold"]:
            continue
        review = repo.effective_review(cycle["cycle_id"], rev["revision_no"], rev["decision_hash"])
        if review is None or review["verdict"] != "approved":
            continue                        # in the middle of a risk loop, or never reviewed: not yet the Boss's
        since = _waiting_since(repo, cycle)
        out.append({"cycle_id": cycle["cycle_id"], "revision_no": rev["revision_no"],
                    "hash": rev["decision_hash"], "verdict": review["verdict"],
                    "snapshot": review["portfolio_snapshot_id"], "since": since,
                    "orders": _orders(rev)})
    return out


def _fmt_wait(since: datetime | None) -> str:
    if since is None:
        return "?"
    minutes = int((_now() - since).total_seconds() // 60)
    return f"{minutes // 60}h{minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"


def pending() -> int:
    rows = pending_cycles(_repo())
    if not rows:
        print("（没有待审批的周期）")
        return 0
    print(f"{'周期':28} {'修订':>4} {'hash(12)':14} {'风控':9} {'组合快照':30} {'已等待':>8}")
    for r in rows:
        print(f"{r['cycle_id']:28} {r['revision_no']:>4} {r['hash'][:12]:14} {r['verdict']:9} "
              f"{r['snapshot']:30} {_fmt_wait(r['since']):>8}")
    return 0


def supersede_pending(new_cycle_id: str, *, created_at: str | None = None) -> list[str]:
    """A new cycle replaces any cycle still waiting for the Boss; each one is told so once.

    Forgetting to approve costs nothing but the opportunity: the next cycle takes its place, and a
    superseded cycle can never be approved later.
    """
    from ..channel.pending_channel import notify, superseded_text
    from ..decision.snapshot import supersede_cycle

    repo = _repo()
    done = []
    # A cycle waiting for the Boss, and one sent back by risk that nobody revised: neither may outlive the day.
    for row in repo.conn.execute(
            "SELECT cycle_id, status FROM decision_cycles WHERE status IN (?, ?) AND cycle_id != ?",
            (CycleStatus.PENDING_APPROVAL.value, CycleStatus.RISK_REJECTED.value, new_cycle_id)).fetchall():
        if supersede_cycle(repo, row["cycle_id"], reason=f"被新周期 {new_cycle_id} 取代，未审批",
                           actor="approval", created_at=created_at):
            done.append(row["cycle_id"])
            if row["status"] == CycleStatus.PENDING_APPROVAL.value:      # only these ever reached the Boss
                notify(*superseded_text(row["cycle_id"], new_cycle_id))
    return done


# --- locating the thing being answered ----------------------------------------------------------- #
def _first_result(repo, cycle_id: str, prefix: str, decision: str):
    return repo.conn.execute(
        "SELECT * FROM boss_approvals WHERE cycle_id = ? AND decision = ? AND decision_hash LIKE ? "
        "ORDER BY created_at LIMIT 1", (cycle_id, decision, prefix + "%")).fetchone()


def _resolve(repo, cycle_id: str, prefix: str):
    """The cycle, its current revision and effective review — or the reason this answer is refused."""
    if len(prefix) < MIN_HASH_PREFIX:
        raise ApprovalRefused(f"修订 hash 前缀至少 {MIN_HASH_PREFIX} 位（收到 {len(prefix)} 位）")
    cycle = repo.get_cycle(cycle_id)
    if cycle is None:
        raise ApprovalRefused(f"没有这个周期：{cycle_id}")
    status = cycle["status"]
    if status == CycleStatus.SUPERSEDED.value:
        raise ApprovalRefused(f"{cycle_id} 已被新周期取代（失效），不能再审批")
    if is_terminal(status):
        raise ApprovalRefused(f"{cycle_id} 已终结（{status}），不能再审批")
    if status != CycleStatus.PENDING_APPROVAL.value:
        raise ApprovalRefused(f"{cycle_id} 当前状态是 {status}，不在待审批")
    rev = repo.latest_revision(cycle_id)
    review = repo.effective_review(cycle_id, rev["revision_no"], rev["decision_hash"]) if rev else None
    if rev is None or review is None or review["verdict"] != "approved":
        raise ApprovalRefused(f"{cycle_id} 的当前修订没有通过风控，不进入审批")
    if not rev["decision_hash"].startswith(prefix):
        raise ApprovalRefused(
            f"hash 不匹配：{cycle_id} 当前是修订 {rev['revision_no']}，hash 前缀 {rev['decision_hash'][:MIN_HASH_PREFIX]}")
    return cycle, rev, review


def _graph(persist: bool = True):
    from ..graph.chief import build_chief_graph
    from ..graph.checkpoint import get_checkpointer

    return build_chief_graph(checkpointer=get_checkpointer(persist=persist))


def _thread(cycle_id: str) -> dict:
    return {"configurable": {"thread_id": cycle_id}}


def _checkpoint_values(app, cycle_id: str) -> dict:
    snap = app.get_state(_thread(cycle_id))
    values = getattr(snap, "values", None) or {}
    if not values:
        raise ApprovalRefused(f"{cycle_id} 没有可恢复的检查点（决策进程不是用待审批通道启动的，或检查点已清理）")
    return values


# --- show ----------------------------------------------------------------------------------------- #
def _lock_reason(values: dict) -> str:
    from ..trader import execute as texec

    reasons = []
    if values.get("dry_run"):
        reasons.append("演练模式（dry-run）")
    if texec.execution_locked():
        reasons.append("实盘锁定（自动执行开关关闭，没有可用的交易路由）")
    return "；".join(reasons)


def show(cycle_id: str) -> int:
    repo = _repo()
    cycle = repo.get_cycle(cycle_id)
    if cycle is None:
        print(f"没有这个周期：{cycle_id}")
        return 1
    rev = repo.latest_revision(cycle_id)
    review = repo.effective_review(cycle_id, rev["revision_no"], rev["decision_hash"]) if rev else None
    print(f"周期 {cycle_id} · 状态 {cycle['status']} · 修订 {rev['revision_no'] if rev else '-'}"
          f" · hash {rev['decision_hash'] if rev else '-'}")
    if rev:
        print("\n订单：")
        for o in _orders(rev):
            size = (f"${o['notional_usd']:,.0f}" if o.get("notional_usd") else
                    f"{o['qty']:g} 股" if o.get("qty") else "")
            print(f"  {o.get('action')} {o.get('symbol')} {size}  {str(o.get('rationale', ''))[:100]}")
        from ..decision.repository import option_advice_lines, rationale_summary

        if rev["rationale"]:
            print(f"\n主理人摘要：{rationale_summary(rev['rationale'])}")
        for line in option_advice_lines(rev["rationale"]):
            print(line)
    if review:
        print(f"\n风控审查：{review['verdict']}（规则集 {review['ruleset_version']} · 组合快照 {review['portfolio_snapshot_id']}"
              f" · 行情 as-of {review['market_as_of']}）")
        violations = json.loads(review["violations_json"] or "[]")
        for v in violations:
            print(f"  违规 {v.get('rule_id')}：{v.get('detail')}")
        if review["allowed_boundary_json"]:
            print(f"  风控边界：{review['allowed_boundary_json']}")
        if review["notes"]:
            print(f"  备注：{review['notes']}")
    if cycle["research_snapshot"]:
        from ..decision.snapshot import frozen_snapshot_items

        items = frozen_snapshot_items(json.loads(cycle["research_snapshot"]))
        print("\n研究快照引用：")
        for item in items:
            print(f"  {item.category or item.task_id}: {item.projection_id} @ {item.as_of}")
    reason = None
    try:
        reason = _lock_reason(_checkpoint_values(_graph(), cycle_id))
    except ApprovalRefused:
        from ..trader import execute as texec
        reason = "实盘锁定" if texec.execution_locked() else None
    if reason:
        print(f"\n⚠ 批准后不会下单：{reason}。批准只会记录并做执行前检查。")
    rounds = repo.conn.execute(
        "SELECT COUNT(*) FROM boss_approvals WHERE cycle_id = ? AND decision = 'revise'",
        (cycle_id,)).fetchone()[0]
    print(f"\n驳回修改已用 {rounds}/2 次。")
    return 0


# --- the three answers ---------------------------------------------------------------------------- #
def _resume(cycle_id: str, approval, *, app=None) -> dict:
    from ..runtime.cli import resume_cycle

    return resume_cycle(cycle_id, approval)


def _report(cycle_id: str, result: dict) -> int:
    """Say what the resume did: finished, or came back for another approval."""
    if "__interrupt__" in result:
        from ..channel.pending_channel import notify, pending_summary, reminder_text
        from ..schemas.channel import ApprovalRequest

        req = ApprovalRequest.model_validate(result["__interrupt__"][0].value)
        title, body = reminder_text(cycle_id, req.revision_no, pending_summary(req))
        print(f"⏸ 新修订 {req.revision_no}（hash {req.decision_hash[:MIN_HASH_PREFIX]}）已通过风控，等待你审批。")
        notify(title, body)
        return 0
    repo = _repo()
    cycle = repo.get_cycle(cycle_id)
    print(f"✓ {cycle_id} → {cycle['status']}"
          + (f"（{cycle['final_outcome']}）" if cycle and cycle["final_outcome"] else ""))
    return 0


def _actor(actor: str | None) -> str:
    if actor:
        return actor
    import getpass

    return getpass.getuser()


def _approval(status: str, actor: str, note: str = ""):
    from ..schemas.decision import BossApproval

    return BossApproval(status=status, reviewer=actor, reviewed_at=_now(), comment=note,
                        channel="cli-approval")


def reject(cycle_id: str, prefix: str, *, note: str = "", actor: str | None = None) -> int:
    repo = _repo()
    dup = _first_result(repo, cycle_id, prefix, "rejected")
    if dup is not None and len(prefix) >= MIN_HASH_PREFIX:
        print(f"已拒绝过（{dup['created_at']}），首次结果：{dup['comment'] or '无备注'}")
        return 0
    try:
        _resolve(repo, cycle_id, prefix)
        _checkpoint_values(_graph(), cycle_id)
    except ApprovalRefused as exc:
        print(f"✗ {exc}")
        return 1
    return _report(cycle_id, _resume(cycle_id, _approval("rejected", _actor(actor), note)))


def revise(cycle_id: str, prefix: str, *, note: str, actor: str | None = None) -> int:
    repo = _repo()
    try:
        if not note.strip():
            raise ApprovalRefused("revise 必须给修改意见（--note）")
        _, rev, _ = _resolve(repo, cycle_id, prefix)
        used = repo.conn.execute(
            "SELECT COUNT(*) FROM boss_approvals WHERE cycle_id = ? AND decision = 'revise'",
            (cycle_id,)).fetchone()[0]
        if used >= 2:
            raise ApprovalRefused("已用完 2 次驳回修改，只能 approve 或 reject")
        _checkpoint_values(_graph(), cycle_id)
    except ApprovalRefused as exc:
        print(f"✗ {exc}")
        return 1
    return _report(cycle_id, _resume(cycle_id, _approval("revise", _actor(actor), note)))


def approve(cycle_id: str, prefix: str, *, actor: str | None = None) -> int:
    """Approve the revision on the card.

    The portfolio snapshot a review rests on goes stale within 60 seconds, which a person at a command
    line always exceeds. So approving first takes a fresh snapshot and re-runs the deterministic review
    on the SAME revision (no model call, no new revision, no new human approval): still approved means
    this one approval stands, recorded after the new review; a different verdict means the orders now
    break a rule, and the approval is refused so the Boss picks revise or reject.
    """
    repo = _repo()
    dup = _first_result(repo, cycle_id, prefix, "approved")
    if dup is not None and len(prefix) >= MIN_HASH_PREFIX:
        print(f"已批准过（{dup['created_at']}），首次结果：{repo.get_cycle(cycle_id)['status']}")
        return 0
    started = time.monotonic()
    try:
        cycle, rev, previous = _resolve(repo, cycle_id, prefix)
        app = _graph()
        values = _checkpoint_values(app, cycle_id)
        refresh = _refresh_review(repo, app, cycle_id, rev, previous, values)
    except ApprovalRefused as exc:
        print(f"✗ {exc}")
        return 1
    print(refresh)
    result = _resume(cycle_id, _approval("approved", _actor(actor)))
    print(f"（批准到执行共 {time.monotonic() - started:.1f} 秒，快照时限 {MAX_SNAPSHOT_AGE_BUDGET_SECONDS} 秒）")
    return _report(cycle_id, result)


def _approved_set(review, decisions) -> set[tuple[str, str]]:
    return {(d.symbol, d.action) for d, ov in zip(decisions, review.order_verdicts)
            if ov.verdict == "approved"}


def _refresh_review(repo, app, cycle_id: str, rev, previous, values: dict) -> str:
    """Fresh snapshot → deterministic review of the same revision → checkpoint updated. Returns the diff text."""
    from ..risk import checks as risk_checks
    from ..trader import portfolio as tport
    from ..graph.chief import _ruleset_version

    decisions = list(values.get("decisions") or [])
    if not decisions:
        raise ApprovalRefused(f"{cycle_id} 的检查点里没有订单，无法重审")
    if not values.get("use_broker", True):
        raise ApprovalRefused(f"{cycle_id} 是离线运行，没有账户快照可刷新")
    pf = tport.snapshot()
    if pf is None:
        raise ApprovalRefused("取不到新的账户快照，不能批准（批准必须基于 60 秒内的快照）")
    review = risk_checks.review_revision(decisions, pf, event_data=values.get("event_data") or None,
                                         cycle_id=cycle_id)
    before = _approved_set(values["risk_review"], decisions) if values.get("risk_review") else set()
    after = _approved_set(review, decisions)
    if review.verdict != "approved" or after != before:
        lines = ["✗ 用新快照重审后结论不同，不能批准这一版："]
        lines += [f"  - {n}" for n in review.notes[:6]] or ["  - 订单集合与上次审查不同"]
        lines.append("  请选择 `ats approval revise`（附修改意见）或 `ats approval reject`。")
        raise ApprovalRefused("\n".join(lines))
    next_round = max(int(values.get("risk_round") or 0), review_round_of(previous["review_id"])) + 1
    snapshot_id = f"pf:{pf.as_of.isoformat()}"
    repo.record_review(
        review_id=f"{cycle_id}:r{rev['revision_no']}:review:r{next_round}",
        cycle_id=cycle_id, revision_no=rev["revision_no"], decision_hash=rev["decision_hash"],
        ruleset_version=_ruleset_version(), portfolio_snapshot_id=snapshot_id,
        market_as_of=review.basis.market_as_of if review.basis else "",
        verdict=review.verdict, violations=[v.model_dump() for v in review.violations],
        allowed_boundary=review.allowed_boundary.model_dump(),
        before_metrics=review.before_metrics, after_metrics=review.after_metrics,
        notes="\n".join(review.notes) + "\n[批准时刷新快照重审，同一修订]",
        created_at=_now().isoformat())
    app.update_state(_thread(cycle_id), {
        "portfolio": pf, "portfolio_snapshot_id": snapshot_id, "risk_review": review,
        "risk_round": next_round}, as_node="persist_decision")
    return _diff_text(previous, review, snapshot_id)


def _diff_text(previous, review, snapshot_id: str) -> str:
    lines = [f"✓ 重审通过（同一修订，新快照 {snapshot_id}），与上次审查的差异："]
    old = json.loads(previous["after_metrics_json"] or "{}")
    changed = False
    for key, value in (review.after_metrics or {}).items():
        was = old.get(key)
        if was != value and isinstance(value, (int, float)) and isinstance(was, (int, float)):
            lines.append(f"  {key}: {was:g} → {value:g}")
            changed = True
    if not changed:
        lines.append("  指标无变化")
    return "\n".join(lines)


# --- argparse glue ---------------------------------------------------------------------------------- #
def add_parser(sub) -> None:
    ap = sub.add_parser("approval", help="Boss 审批：pending / show / approve / revise / reject")
    verbs = ap.add_subparsers(dest="verb", required=True)
    verbs.add_parser("pending", help="列出待审批的周期（需要下单且已通过风控）")
    sp = verbs.add_parser("show", help="完整审批对象")
    sp.add_argument("cycle")
    for name in ("approve", "revise", "reject"):
        vp = verbs.add_parser(name)
        vp.add_argument("cycle")
        vp.add_argument("--revision", required=True, help=f"修订 hash 前缀，至少 {MIN_HASH_PREFIX} 位")
        vp.add_argument("--actor", default=None, help="审批人，默认本机用户名")
        if name != "approve":
            vp.add_argument("--note", default="", required=(name == "revise"),
                            help="修改意见（revise 必填）" if name == "revise" else "拒绝备注（可选）")


def dispatch(args) -> int:
    if args.verb == "pending":
        return pending()
    if args.verb == "show":
        return show(args.cycle)
    if args.verb == "approve":
        return approve(args.cycle, args.revision, actor=args.actor)
    if args.verb == "revise":
        return revise(args.cycle, args.revision, note=args.note, actor=args.actor)
    return reject(args.cycle, args.revision, note=args.note, actor=args.actor)
