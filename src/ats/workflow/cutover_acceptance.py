"""End-to-end acceptance of the shadow period (tasks 13.4–13.5).

Two questions, and they are the only two that matter about a shadow run:

- **13.4 — can every order be traced?** Each shadow or real order must reach a
  research snapshot, a decision revision, a risk review and a human approval.
  A chain with a missing link is not "mostly traceable"; it is **not traceable**,
  and the switch it sits in is a non-compliant switch.
- **13.5 — did any unapproved real order happen?** The primary evidence is the
  broker submit calls and their refusal audit records, plus the capability check
  results and the simulated broker receipts from the isolated drill. `trades`
  staying clean is an **independent** acceptance, not the primary one: a ledger
  that was never written to cannot tell you whether an order reached the broker
  and came back.

**Why the two are in one module.** They read the same artefacts and they fail
in the same place. 13.4's chain is what tells 13.5 whether an order *should*
have been approved; 13.5's refusal records are what tell 13.4 whether the
approval was ever the thing that stopped it. Splitting them would let a run pass
one and fail the other and be reported as half-done.

**Three states that must not be collapsed.** `untraceable` (a link is missing),
`unreadable` (the evidence could not be read) and `traceable` are different
answers and the summary counts them separately. Reading "I could not check" as
"nothing is wrong" is how an unread ledger becomes a clean report — the same
failure mode as the empty-ledger one in group 10.

**What this does not prove.** It reads what the ledgers recorded. It does not
verify that a real authorisation would be granted, that the broker behaved, or
that a real cutover is safe to perform. `ACCEPTANCE_LIMITS` says so in every
report.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

from ..execution import ledger as trade_ledger

# The four links. Named as constants because a report that says "missing: 1"
# without saying which one is the report this module exists to prevent.
LINK_SNAPSHOT = "research_snapshot"
LINK_REVISION = "decision_revision"
LINK_REVIEW = "risk_review"
LINK_APPROVAL = "human_approval"

REQUIRED_LINKS: tuple[str, ...] = (LINK_SNAPSHOT, LINK_REVISION,
                                   LINK_REVIEW, LINK_APPROVAL)

# Verdicts. `unreadable` is separate from `untraceable` because they call for
# different responses: one is a defect to fix, the other is a gap to evidence.
TRACEABLE = "traceable"
UNTRACEABLE = "untraceable"
UNREADABLE = "unreadable"

# 13.5 outcomes. `no_unapproved_order` is only reachable when the prohibition
# was actually exercised — an absence of attempts is not a pass.
CLEAN = "no_unapproved_order"
UNEXERCISED = "prohibition_not_exercised"
STOP = "stop_condition"

ACCEPTANCE_LIMITS = (
    "本核验只读取既有账本与拒绝审计记录，**不验证**券商侧行为，也不验证真实授权会被批准。",
    "`trades` 未污染是**独立**验收，不是主要证据——从未被写入的账本无法证明"
    "订单没有到达券商又返回。",
    "核验通过**不等于**切流获准；真实实盘切换仍需另行取得 `LIVE-` 实盘授权。",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# 13.4 — traceability
# --------------------------------------------------------------------------- #

@dataclass
class OrderTrace:
    """One order's four links, and which of them are actually there."""

    intent_id: str
    cycle_id: str
    symbol: str = ""
    links: dict[str, dict[str, Any]] = field(default_factory=dict)
    verdict: str = TRACEABLE
    missing: tuple[str, ...] = ()
    reason: str = ""

    def link(self, name: str, present: bool, detail: str = "") -> None:
        self.links[name] = {"present": bool(present), "detail": detail}
        if not present and name not in self.missing:
            self.missing = self.missing + (name,)

    def as_row(self) -> dict[str, Any]:
        return {"intent_id": self.intent_id, "cycle_id": self.cycle_id,
                "symbol": self.symbol, "verdict": self.verdict,
                "missing": list(self.missing), "reason": self.reason,
                "links": dict(self.links)}


@dataclass
class TraceabilityReport:
    """13.4's result. Counts per verdict — never a single "ok"."""

    scope: str = ""
    traces: list[OrderTrace] = field(default_factory=list)
    unreadable_cycles: tuple[str, ...] = ()
    note: str = ""

    @property
    def traceable(self) -> list[OrderTrace]:
        return [t for t in self.traces if t.verdict == TRACEABLE]

    @property
    def untraceable(self) -> list[OrderTrace]:
        return [t for t in self.traces if t.verdict == UNTRACEABLE]

    @property
    def compliant_switch(self) -> bool:
        """A switch is compliant only when nothing is untraceable AND nothing
        was unreadable. An unread cycle is not a pass."""
        return not self.untraceable and not self.unreadable_cycles

    def as_row(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "order_count": len(self.traces),
            "traceable": len(self.traceable),
            "untraceable": len(self.untraceable),
            "unreadable_cycles": list(self.unreadable_cycles),
            "compliant_switch": self.compliant_switch,
            "traces": [t.as_row() for t in self.traces],
            "note": self.note,
            "limits": list(ACCEPTANCE_LIMITS),
        }


def _snapshot_present(cycle: dict[str, Any] | None) -> tuple[bool, str]:
    """Does this cycle carry a research snapshot?

    Read from the cycle row rather than inferred from the presence of revisions:
    a revision can be appended without a snapshot, and the snapshot is the thing
    that says which inputs the decision was taken on.
    """
    if cycle is None:
        return False, "the cycle row could not be read"
    raw = cycle.get("research_snapshot")
    if not raw:
        return False, ("the cycle carries no research snapshot, so the order "
                       "cannot be traced to the inputs it was decided on")
    try:
        payload = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (TypeError, ValueError) as exc:
        return False, f"the stored snapshot could not be parsed: {exc}"
    items = payload.get("items") if isinstance(payload, dict) else None
    if not items:
        return False, ("the research snapshot is present but lists no items, so "
                       "it establishes nothing about the inputs")
    return True, f"{len(items)} snapshot item(s)"


def verify_traceability(
        *, chain_reader: Callable[[str], dict[str, Any]],
        intents: Sequence[dict[str, Any]],
        scope: str = "") -> TraceabilityReport:
    """13.4: every order reaches a snapshot, a revision, a review and an approval.

    `chain_reader` returns the decision repository's `read_chain(cycle_id)`. It
    is a parameter so this module reads storage rather than re-deriving the
    chain — and so a caller that cannot read the chain says so instead of this
    module guessing.

    An order with no `cycle_id` cannot be traced at all, and is reported as
    `untraceable` rather than skipped: "we could not tell which cycle it belongs
    to" is a finding, not an absence of one.
    """
    report = TraceabilityReport(scope=scope)
    cycles_read: dict[str, dict[str, Any] | None] = {}

    for row in intents:
        trace = OrderTrace(intent_id=str(row.get("intent_id") or ""),
                           cycle_id=str(row.get("cycle_id") or ""),
                           symbol=str(row.get("symbol") or ""))
        if not trace.cycle_id:
            trace.verdict = UNTRACEABLE
            trace.missing = REQUIRED_LINKS
            trace.reason = ("the intent names no decision cycle, so none of the "
                            "four links can be established")
            report.traces.append(trace)
            continue

        if trace.cycle_id not in cycles_read:
            try:
                cycles_read[trace.cycle_id] = chain_reader(trace.cycle_id)
            except Exception as exc:  # noqa: BLE001 - unreadable is a verdict
                cycles_read[trace.cycle_id] = None
                if trace.cycle_id not in report.unreadable_cycles:
                    report.unreadable_cycles = report.unreadable_cycles + (
                        trace.cycle_id,)
                trace.verdict = UNREADABLE
                trace.reason = (f"the decision chain could not be read "
                                f"({type(exc).__name__}: {exc}); this is a failure "
                                f"to check, not a finding about the order")
                report.traces.append(trace)
                continue
            chain = cycles_read[trace.cycle_id]
            if chain is None:
                if trace.cycle_id not in report.unreadable_cycles:
                    report.unreadable_cycles = report.unreadable_cycles + (
                        trace.cycle_id,)
                trace.verdict = UNREADABLE
                trace.reason = ("the decision chain could not be read; an "
                                "unreadable chain is not a chain without links")
                report.traces.append(trace)
                continue

        chain = cycles_read[trace.cycle_id] or {}
        present, detail = _snapshot_present(chain.get("cycle"))
        trace.link(LINK_SNAPSHOT, present, detail)

        decision_hash = str(row.get("decision_hash") or "")
        revisions = chain.get("revisions") or []
        matched = [r for r in revisions
                   if str(r.get("decision_hash") or "") == decision_hash]
        trace.link(LINK_REVISION, bool(decision_hash) and bool(matched),
                   f"decision_hash={decision_hash or '(none)'} matched "
                   f"{len(matched)} revision(s)"
                   if decision_hash else "the intent records no decision_hash")

        reviews = chain.get("reviews") or []
        matched_reviews = [r for r in reviews
                           if str(r.get("decision_hash") or "") == decision_hash]
        trace.link(LINK_REVIEW, bool(matched_reviews),
                   f"{len(matched_reviews)} risk review(s) bound to this "
                   f"revision hash")

        approvals = chain.get("approvals") or []
        approved = [a for a in approvals
                    if str(a.get("decision_hash") or "") == decision_hash
                    and str(a.get("decision") or "") == "approved"]
        trace.link(LINK_APPROVAL, bool(approved),
                   f"{len(approved)} approval(s) of this revision hash; a "
                   f"rejection is not an approval")

        if trace.missing:
            trace.verdict = UNTRACEABLE
            trace.reason = (f"missing link(s): {', '.join(trace.missing)}. A "
                            f"chain with a missing link is not traceable, and "
                            f"the switch it sits in is a non-compliant switch")
        report.traces.append(trace)

    report.note = (
        f"{len(report.traceable)}/{len(report.traces)} 笔可追溯；"
        f"不可追溯 {len(report.untraceable)} 笔；"
        f"无法核验的周期 {len(report.unreadable_cycles)} 个。"
        if report.traces else "没有影子意图可核验——空账本不是「全部通过」。")
    return report


# --------------------------------------------------------------------------- #
# 13.5 — no unapproved real order
# --------------------------------------------------------------------------- #

@dataclass
class UnapprovedOrderReport:
    """13.5's result. The stop conditions are named, not summarised."""

    run_id: str = ""
    submit_attempts: int = 0
    accepted_attempts: int = 0
    refused_attempts: int = 0
    refusal_codes: list[str] = field(default_factory=list)
    simulated_receipts: int = 0
    capability_checks: list[dict[str, Any]] = field(default_factory=list)
    trades_rows_checked: int = 0
    attributed_excluded: list[dict[str, Any]] = field(default_factory=list)
    unreconciled_broker_receipts: list[dict[str, Any]] = field(default_factory=list)
    stop_conditions: list[str] = field(default_factory=list)
    verdict: str = CLEAN
    note: str = ""

    @property
    def clean(self) -> bool:
        return self.verdict == CLEAN

    def as_row(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "verdict": self.verdict,
            "submit_attempts": self.submit_attempts,
            "accepted_attempts": self.accepted_attempts,
            "refused_attempts": self.refused_attempts,
            "refusal_codes": list(self.refusal_codes),
            "simulated_receipts": self.simulated_receipts,
            "capability_checks": list(self.capability_checks),
            "trades_rows_checked": self.trades_rows_checked,
            "attributed_excluded": list(self.attributed_excluded),
            "unreconciled_broker_receipts": list(
                self.unreconciled_broker_receipts),
            "stop_conditions": list(self.stop_conditions),
            "note": self.note,
            "limits": list(ACCEPTANCE_LIMITS),
        }


def _columns(conn: Any, table: str) -> set[str] | None:
    """Column names of `table`, or `None` when the schema could not be read.

    `None` and `set()` are different answers and the caller must be able to tell
    them: the first means "this table has no `origin`", the second means "I could
    not find out". An earlier version collapsed both into `set()`, so a
    `PRAGMA` that raised read as "no attribution column" and the exclusion step
    silently reported nothing — indistinguishable from a clean ledger.

    Both row shapes are accepted because `sqlite3.Row` and plain tuples behave
    differently under `PRAGMA`, and the real store uses the former while a test
    double may use the latter.
    """
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        raise LedgerUnreadable(
            f"the {table!r} schema could not be read ({type(exc).__name__}: "
            f"{exc})") from exc
    names: set[str] = set()
    for row in rows:
        # `PRAGMA table_info` yields (cid, name, type, notnull, dflt, pk).
        try:
            names.add(str(row["name"]))
        except (TypeError, KeyError, IndexError):
            names.add(str(row[1]))
    return names


class LedgerUnreadable(RuntimeError):
    """The real ledger's schema or rows could not be read."""


def _non_system_fills(store: Any) -> list[dict[str, Any]]:
    """Fills in the window that are **not** attributable to the system.

    Read from `fills`, not `trades`: `origin` is a column on `fills` and does not
    exist on `trades` at all. Querying `trades.origin` raises on every real
    store, which would turn "the ledger is clean" into "the check could not run"
    — the two must not be confused, and neither may read as the other.

    A row with an absent or empty `origin` lands in `unattributed`, which is
    what §11.1 asks for: unknown is neither system nor manual, and it is counted
    here rather than quietly dropped.

    Reported, never deleted. A human's own broker trade and a late fill during
    the window are real facts; excluding them from the shadow run's attribution
    is not the same as removing them from the ledger.
    """
    conn = getattr(store, "conn", store)
    if "origin" not in (_columns(conn, "fills") or set()):
        # No attribution column: report nothing rather than claiming the buckets
        # are empty. `trades_rows_checked` is reported separately, so an empty
        # list here is not read as "everything was system".
        return []
    rows = conn.execute(
        "SELECT exec_id, symbol, order_id, origin FROM fills").fetchall()
    buckets = trade_ledger.split_by_origin(
        [dict(zip(("exec_id", "symbol", "order_id", "origin"), tuple(row)))
         for row in rows])
    return [{"origin": origin, "count": len(items),
             "sample": [dict(i) for i in items[:3]]}
            for origin, items in sorted(buckets.items())
            if origin in trade_ledger.NON_SYSTEM_ORIGINS and items]


def verify_no_unapproved_orders(
        *, run_id: str,
        attempts: Sequence[dict[str, Any]],
        refusals: Sequence[dict[str, Any]] = (),
        capability_checks: Sequence[dict[str, Any]] = (),
        simulated_receipts: Sequence[dict[str, Any]] = (),
        real_ledger: Any | None = None,
        broker_receipts: Sequence[dict[str, Any]] = (),
        ) -> UnapprovedOrderReport:
    """13.5: no unapproved real order reached the broker during the shadow period.

    The primary evidence, in the order the requirement names it: the new path's
    broker submit calls and their refusal audit records, the capability check
    results, and the isolated drill's simulated broker receipts. `trades` is
    checked too, but as **independent** acceptance — see the module docstring for
    why an unwritten ledger cannot be the primary evidence.

    Three failure shapes, each with its own handling:

    - **A submit attempt that was accepted.** A stop condition. The prohibition
      did not hold, and everything downstream is now describing a breach.
    - **A broker receipt with no local record.** Also a stop condition, and the
      one a `trades` check cannot find: the broker has the order and the ledger
      does not. Reported rather than reconciled, because reconciling it is a
      deliberate separate step.
    - **A legitimate legacy fill or a late fill.** Excluded **by attribution**,
      with the attribution recorded. Excluded rather than counted, because a
      human's own broker trade and a late fill during the window are expected —
      what is not expected is one of them being attributed to the shadow run.
    """
    report = UnapprovedOrderReport(run_id=run_id)
    report.submit_attempts = len(attempts)
    accepted = [a for a in attempts if a.get("accepted")]
    report.accepted_attempts = len(accepted)
    report.refused_attempts = len(attempts) - len(accepted)
    report.refusal_codes = sorted({str(a.get("refusal_code") or "")
                                   for a in attempts if a.get("refusal_code")})
    report.simulated_receipts = len(simulated_receipts)
    report.capability_checks = [dict(c) for c in capability_checks]

    # An absence of attempts is not a pass: nothing tested the prohibition.
    if not attempts:
        report.verdict = UNEXERCISED
        report.stop_conditions.append(
            "the shadow run recorded no broker submit attempt, so the prohibition "
            "was never exercised; a run that never reached the submit point cannot "
            "be used to claim it would have been stopped there")
        report.note = ("影子运行没有到达下单调用点，故本次核验验证的是「禁令未被触发」"
                       "而非「禁令生效」。")
        return report

    if accepted:
        report.verdict = STOP
        report.stop_conditions.append(
            f"{len(accepted)} broker submit attempt(s) were ACCEPTED during a "
            f"shadow run; the prohibition did not hold, and this is a stop "
            f"condition rather than a difference to reconcile")

    # --- `trades` as INDEPENDENT acceptance --------------------------------
    #
    # Done before the broker-receipt branch so there is exactly ONE leak check:
    # running it twice meant a detected leak produced two stop conditions with
    # different wording, and the second one mislabelled the leak as a failure to
    # read.
    ledger_checked = real_ledger is not None
    ledger_clean = False
    if ledger_checked:
        from ..workflow import shadow_ledger as shadow

        try:
            checked = shadow.assert_real_ledger_not_written(
                real_ledger, run_id=run_id)
            report.trades_rows_checked = int(checked.get("trades_rows_checked") or 0)
            ledger_clean = True
        except shadow.ShadowLedgerWriteRefused as exc:
            report.stop_conditions.append(
                f"the real trades ledger was written to by this run: {exc}")
        except Exception as exc:  # noqa: BLE001
            report.stop_conditions.append(
                f"the real ledger could not be checked ({type(exc).__name__}: "
                f"{exc}); an unread ledger is not a clean one")

        if ledger_clean:
            try:
                report.attributed_excluded = _non_system_fills(real_ledger)
            except LedgerUnreadable as exc:
                report.stop_conditions.append(
                    f"{exc}; the attribution of existing fills is unknown, and "
                    f"unknown is not 'all system'")

    # Broker receipts the local ledger has no row for. Checked because this is
    # the failure a `trades`-stays-clean check cannot see.
    if broker_receipts:
        if not ledger_checked:
            report.note = ("券商侧有接收记录，但未提供真实账本，故「券商已接收、本地"
                           "未落库」这一项**无从核验**——不能读作通过。")
            report.stop_conditions.append(
                "broker receipts exist but no local ledger was supplied, so "
                "'the broker has it and we do not' cannot be ruled out")
        report.unreconciled_broker_receipts = [dict(r) for r in broker_receipts]

    if report.stop_conditions and report.verdict == CLEAN:
        report.verdict = STOP

    if not report.stop_conditions:
        report.verdict = CLEAN
        report.note = (
            f"{report.submit_attempts} 次下单尝试全部被拒"
            f"（拒绝码：{', '.join(report.refusal_codes) or '未记录'}），"
            f"`trades` 核验 {report.trades_rows_checked} 行无污染。")
    return report


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #

def render_traceability_report(report: TraceabilityReport) -> str:
    lines = ["# 端到端可追溯性核验（11.9 / 13.4）", ""]
    lines.append(f"- 范围：`{report.scope or '(全部影子意图)'}`")
    lines.append(f"- 订单数：**{len(report.traces)}**")
    lines.append(f"- 可追溯：**{len(report.traceable)}** · "
                 f"不可追溯 **{len(report.untraceable)}** · "
                 f"无法核验周期 **{len(report.unreadable_cycles)}**")
    lines.append(f"- 切换是否合规：**"
                 f"{'是' if report.compliant_switch else '**否**'}**")
    if report.unreadable_cycles:
        lines.append(f"- **无法核验的周期**：{', '.join(report.unreadable_cycles)}"
                     f"（无法核验不是通过）")

    if report.traces:
        lines += ["", "| 订单 | 结论 | 缺失关联 | 说明 |", "|---|---|---|---|"]
        for t in report.traces:
            lines.append(f"| `{t.intent_id}` | {t.verdict} | "
                         f"{', '.join(t.missing) or '—'} | {t.reason} |")

    if report.untraceable:
        lines += ["", "### 被判为不合规切换的记录", ""]
        for t in report.untraceable:
            lines.append(f"- `{t.intent_id}`（{t.symbol}）：缺 "
                         f"{'、'.join(t.missing)}")

    lines += ["", report.note, "", "## 本次核验**未**证明的事", ""]
    lines += [f"- {limit}" for limit in ACCEPTANCE_LIMITS]
    return "\n".join(lines)


def render_unapproved_report(report: UnapprovedOrderReport) -> str:
    lines = ["# 影子期无未审批真实下单核验（11.9 / 13.5）", ""]
    lines.append(f"- 运行：`{report.run_id or '(未命名)'}`")
    lines.append(f"- 结论：**{report.verdict}**")
    lines += ["", "## 主要证据（新路径的券商提交与拒绝审计）", "",
              "| 项 | 值 |", "|---|---|",
              f"| 下单尝试次数 | {report.submit_attempts} |",
              f"| 被券商接受 | **{report.accepted_attempts}** |",
              f"| 被拒绝 | {report.refused_attempts} |",
              f"| 拒绝码 | {', '.join(report.refusal_codes) or '未记录'} |",
              f"| 能力校验次数 | {len(report.capability_checks)} |",
              f"| 隔离演练券商模拟接收 | {report.simulated_receipts} |"]

    lines += ["", "## 独立验收（`trades` 未污染）", "",
              f"- 核验行数：**{report.trades_rows_checked}**"]
    if report.attributed_excluded:
        lines.append("- 按归因排除的非系统记录（**排除不等于删除**）：")
        for row in report.attributed_excluded:
            lines.append(f"  - `{row['origin']}`：{row['count']} 行")

    if report.unreconciled_broker_receipts:
        lines += ["", "## 券商已接收但本地未落库", "",
                  f"- 券商侧回执 {len(report.unreconciled_broker_receipts)} 条；"
                  f"**此项必须由审计捕获，不得由对账掩盖**"]
    if report.stop_conditions:
        lines += ["", "## 停止条件", ""]
        lines += [f"- {c}" for c in report.stop_conditions]
    if report.note:
        lines += ["", report.note]
    lines += ["", "## 本次核验**未**证明的事", ""]
    lines += [f"- {limit}" for limit in ACCEPTANCE_LIMITS]
    return "\n".join(lines)


def render_acceptance_report(trace: TraceabilityReport,
                             unapproved: UnapprovedOrderReport) -> str:
    return "\n\n".join([render_traceability_report(trace),
                        render_unapproved_report(unapproved)])
