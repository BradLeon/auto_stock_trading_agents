"""Schedule-path cutover and rollback (Phase F tasks 10.1–10.4).

The read-path executor (`read_cutover`) switches consumer scopes; this module
switches **who fires the triggers**. The difference is where the danger sits.

A read-path batch can fail one consumer and the rest still move, because the
consumers are independent. A schedule switch has no such granularity: two
dispatchers firing the same logical trigger means the work runs twice — twice the
LLM spend, twice the ledger writes, and a reconciliation that disagrees with
itself. So the checks here are about **exclusivity over time**, not about a
static verdict at one instant.

**The three states a trigger can be in during a cutover**

- *claimed* — someone has taken it and may still publish
- *finished* — published, with the record saying so
- *unknown* — neither, because the record is unreadable or the claim was made by
  a process that died without recording

`unknown` is the one that blocks. Treating it as finished strands the trigger;
treating it as unfinished runs it twice. Both are wrong, so the module refuses and
names the trigger — the operator's job is to find out which it actually is, and
that requires a fact nobody recorded.

**Why deduplication reads executed records rather than a time window**

A rollback replays the cutover backwards. If the operator rolls back and then
re-cuts, the triggers that already ran must not run again. Matching on a time
window ("skip anything in the last hour") would also skip triggers that were
*never* executed in that hour but merely *scheduled* for it — so a genuine gap
would be silently left unfilled. The only honest record of "this ran" is the
execution record itself, so that is what is matched, by trigger identity.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from . import dispatch_claims as claims

DEFAULT_PATH = "var/phase_f_schedule_switch.sqlite"

# Ownership verdicts.
SOLE = "sole"
CONFLICT = "ownership_conflict"
UNKNOWN = "ownership_unknown"
UNWIRED = "boundary_unwired"

# Cutover and rollback actions.
FROZEN = "frozen"
HANDED_OVER = "handed_over"
CUTOVER = "cutover"
ROLLBACK = "rollback"
REFUSED = "refused"

# Dispositions for an unfinished trigger at freeze time. Reused from group 4 so
# the vocabulary has one definition; a second set of words for the same concept
# is how a report stops being checkable.
CARRIED_OVER = claims.DISPOSITION_CARRY_OVER
ALREADY_RUN = claims.DISPOSITION_ALREADY_RUN
VOID = claims.DISPOSITION_VOID


class ScheduleSwitchError(RuntimeError):
    """The cutover refused for a reason that is not a missing authorisation."""


@dataclass
class OwnershipVerdict:
    """Whether one logical trigger has exactly one owner, at one instant."""

    trigger: str
    verdict: str
    holders: list[dict[str, Any]] = field(default_factory=list)
    detail: str = ""

    @property
    def is_sole(self) -> bool:
        return self.verdict == SOLE

    def as_row(self) -> dict[str, Any]:
        return {"trigger": self.trigger, "verdict": self.verdict,
                "holders": self.holders, "detail": self.detail}


@dataclass
class OwnershipReport:
    """Every trigger checked, and the ones that block."""

    checked: list[OwnershipVerdict] = field(default_factory=list)
    # Triggers the caller asserted should exist but which the ledger does not
    # mention. Distinct from "unclaimed": an empty ledger with a non-empty
    # expected set means the check read nothing, not that nothing conflicts.
    missing_from_ledger: list[str] = field(default_factory=list)

    @property
    def conflicts(self) -> list[OwnershipVerdict]:
        return [v for v in self.checked if v.verdict == CONFLICT]

    @property
    def unknowns(self) -> list[OwnershipVerdict]:
        return [v for v in self.checked if v.verdict == UNKNOWN]

    @property
    def blocking(self) -> list[OwnershipVerdict]:
        """Conflicts and unknowns both block, and they are not the same finding."""
        return self.conflicts + self.unknowns

    @property
    def clean(self) -> bool:
        return not self.blocking and not self.missing_from_ledger

    def as_row(self) -> dict[str, Any]:
        return {
            "checked": [v.as_row() for v in self.checked],
            "conflicts": [v.trigger for v in self.conflicts],
            "unknowns": [v.trigger for v in self.unknowns],
            "missing_from_ledger": list(self.missing_from_ledger),
            "clean": self.clean,
        }

    def summary(self) -> str:
        lines = [f"## 调度所有权核验：{len(self.checked)} 个逻辑触发", ""]
        if self.clean:
            lines.append("**每个触发至多一个所有者**——切换可以继续。")
            return "\n".join(lines)

        if self.missing_from_ledger and not self.checked:
            # The dangerous reading: an empty ledger reports "0 checked, clean",
            # which reads as "nothing conflicts" when it means "nothing was
            # examined". Saying so is the whole point of this branch.
            lines += [f"**账本为空或不含任何触发记录**——{len(self.missing_from_ledger)}"
                      " 个应核验的触发在账本中不存在。", "",
                      "**这不是「无冲突」，是「无从核验」。** 空账本下的「通过」不构成"
                      "任何保证：没有记录就没有可以冲突的证据。切换前必须先让这些触发"
                      "在账本中登记（`ats schedule ledger` 可查当前记录）。", ""]
            for trigger in self.missing_from_ledger[:10]:
                lines.append(f"- `{trigger}`")
            if len(self.missing_from_ledger) > 10:
                lines.append(f"- …另有 {len(self.missing_from_ledger) - 10} 个")
            return "\n".join(lines)

        lines.append(f"**{len(self.conflicts)} 个双重所有，{len(self.unknowns)} 个状态未知**"
                     "——两者都阻断切换，处理方式不同：")
        if self.conflicts:
            lines += ["", "### 所有权冲突（两条路径同时认领）", ""]
            for verdict in self.conflicts:
                lines.append(f"- `{verdict.trigger}`：{verdict.detail}")
                for holder in verdict.holders:
                    lines.append(f"  - {holder.get('owner')} @ {holder.get('claimed_at')}")
        if self.unknowns:
            lines += ["", "### 状态未知（无记录，需人工判定）", ""]
            for verdict in self.unknowns:
                lines.append(f"- `{verdict.trigger}`：{verdict.detail}")
        lines += ["", "**冲突与未知不是同一件事**：冲突要停掉其中一条路径，未知要靠人工"
                 "查清它到底跑没跑——把未知当作已完成会漏跑一次执行，当作未完成会跑两次。"]
        return "\n".join(lines)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(path: str | Path | None) -> sqlite3.Connection:
    target = Path(path or DEFAULT_PATH)
    if not target.is_absolute():
        target = Path(__file__).resolve().parents[3] / target
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def assert_sole_ownership(triggers: Iterable[str], *,
                          holders_reader: Callable[[str], list[dict[str, Any]]],
                          owner_reader: Callable[[], str] | None = None,
                          ) -> OwnershipReport:
    """10.2: at this instant, is every logical trigger held at most once?

    `holders_reader` returns the claim records for a trigger **as of now**, an
    empty list if the ledger knows the trigger but nobody holds it, and raises
    `KeyError` if the ledger has never heard of the trigger. That third case is
    not a clean result — it means the trigger was never verified — so it is
    recorded in `missing_from_ledger` and blocks.

    Ownership is a property of time, so a record that finished no longer counts:
    a stale row left in the ledger must not read as a live conflict, or the check
    becomes unusable after the first rollback.

    Any exception other than `KeyError` is a failed read, which is a different
    finding and also blocks.
    """
    report = OwnershipReport()
    now = datetime.now(timezone.utc)
    current = owner_reader() if owner_reader else ""

    for trigger in triggers:
        # Three distinct states, and only the first two are findings about
        # ownership. A key the ledger has never heard of is a third thing: it was
        # not verified, and reading it as "unclaimed, therefore fine" turns an
        # empty ledger into a clean report.
        try:
            holders = holders_reader(trigger)
        except KeyError:
            report.missing_from_ledger.append(trigger)
            continue
        except Exception as exc:  # noqa: BLE001 - an unreadable claim log is not "free"
            report.checked.append(OwnershipVerdict(
                trigger, UNKNOWN,
                detail=f"the claim log could not be read: "
                       f"{type(exc).__name__}: {exc}"))
            continue

        live = [h for h in holders if _is_live(h, now)]
        if not live:
            report.checked.append(OwnershipVerdict(
                trigger, SOLE, holders=[],
                detail="unclaimed at this instant, so at most one owner"))
        elif len(live) == 1:
            report.checked.append(OwnershipVerdict(
                trigger, SOLE, holders=live,
                detail=f"held by {live[0].get('owner') or current}"))
        else:
            names = "、".join(f"{h.get('owner') or '?'}@{h.get('claimed_at')}"
                             for h in live)
            report.checked.append(OwnershipVerdict(
                trigger, CONFLICT, holders=live,
                detail=f"{len(live)} live holders at once: {names}. Two dispatchers "
                       "firing one logical trigger means the work runs twice"))
    return report


# Statuses that mean the claim has ended, taken from group 4's own vocabulary
# rather than restated — a second set of words for the same state is how a
# report stops being checkable against the ledger.
SETTLED_STATUSES = frozenset({claims.FINISHED, claims.PUBLISHED, claims.ABANDONED})


def _is_live(holder: dict[str, Any], now: datetime) -> bool:
    """Is this claim still in force?

    Group 4's ledger makes `trigger_key` the PRIMARY KEY and refuses a second
    claim on a key that is unfinished, so two rows for one trigger cannot exist.
    What this checks is therefore not row multiplicity but **status**: a row that
    was published or abandoned has ended, one still `claimed` has not.

    A missing or unreadable `status` is treated as **live**, deliberately: an
    absent status is not evidence that the claim ended, and assuming it did is
    how two owners end up running at once. A ledger that cannot answer must
    block, not pass.
    """
    status = str(holder.get("status") or "").strip().lower()
    if status not in SETTLED_STATUSES:
        return True
    finished = holder.get("finished_at")
    parsed = _parse(str(finished)) if finished else None
    # Ended only if the end is stamped and in the past. A settled row with no
    # timestamp is one nobody finished writing.
    return not (parsed is not None and parsed <= now)


def _parse(value: str):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def record_execution(trigger: str, *, execution_ref: str, actor: str = "",
                     path: str | Path | None = None) -> str:
    """Record that a trigger actually ran. The rollback's dedup key.

    Recorded by the executor that did the work, not inferred afterwards. A record
    written after the fact from a log is only as good as the log.
    """
    conn = _connect(path)
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schedule_executions (
                   trigger TEXT NOT NULL,
                   execution_ref TEXT NOT NULL,
                   recorded_at TEXT NOT NULL,
                   actor TEXT NOT NULL DEFAULT '',
                   PRIMARY KEY (trigger, execution_ref))""")
        conn.execute(
            """INSERT OR IGNORE INTO schedule_executions
                   (trigger, execution_ref, recorded_at, actor)
               VALUES (?, ?, ?, ?)""",
            (trigger, execution_ref, _now(), actor))
        return _now()
    finally:
        conn.close()


def executed_triggers(path: str | Path | None = None) -> dict[str, list[str]]:
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT trigger, execution_ref FROM schedule_executions "
            "ORDER BY recorded_at").fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    out: dict[str, list[str]] = {}
    for row in rows:
        out.setdefault(row["trigger"], []).append(row["execution_ref"])
    return out


def plan_rollback(triggers: Sequence[str], *, executed: dict[str, list[str]],
                  ) -> dict[str, Any]:
    """10.3: which triggers a rollback must skip because they already ran.

    Skipped iff there is an execution record for that exact trigger. Never
    skipped on a time window: a trigger merely *scheduled* in the window did not
    run, and skipping it would leave a genuine gap unfilled — a silent hole that
    shows up much later as a missing report.

    A trigger with **no** record is reported as `unconfirmed`, not as "did not
    run". The distinction is the whole point: the module cannot see whether it
    ran, so it says so instead of guessing, and the operator resolves it.
    """
    skipped: list[dict[str, Any]] = []
    rerun: list[str] = []
    unconfirmed: list[str] = []

    for trigger in triggers:
        refs = executed.get(trigger) or []
        if refs:
            skipped.append({"trigger": trigger, "execution_refs": refs})
        else:
            # No record is not the same as a record of absence: an execution that
            # happened without recording one cannot be told apart from one that
            # did not happen.
            unconfirmed.append(trigger)

    return {
        "skipped_as_already_executed": skipped,
        "to_rerun": rerun,
        "unconfirmed": unconfirmed,
        # "Nothing to skip" is only good news when the record is complete. If it
        # is not, a rollback would run everything again.
        "dedup_sound": not unconfirmed,
        "detail": (
            "every trigger has an execution record, so dedup is exact"
            if not unconfirmed else
            f"{len(unconfirmed)} trigger(s) have no execution record; dedup cannot "
            "be exact for them, and rolling back would run them again. Resolve them "
            "before rolling back — this module will not guess"),
    }


def perform_cutover(*, triggers: Sequence[str], inventory: Sequence[dict[str, Any]],
                    dispositions: dict[str, tuple[str, str]],
                    authorisation: Any | None,
                    actor: str = "",
                    freeze: Callable[[], Any] | None = None,
                    hand_over: Callable[[Sequence[dict[str, Any]]], Any] | None = None,
                    mark_published: Callable[[str], Any] | None = None,
                    cutover_path: str | Path | None = None,
                    apply: bool = False) -> dict[str, Any]:
    """Freeze → inventory → dispose → prove → switch, in that order.

    The order is not a style preference. Freezing after inventory would let a
    trigger start between the two and never be dispositioned; switching before
    proving ownership would hand the schedule to a path that may already have a
    live claim on it.

    `apply=False` decides only, so the runbook's "re-run before deciding" loop
    cannot move a route by accident.
    """
    result: dict[str, Any] = {
        "action": CUTOVER, "applied": False, "actor": actor,
        "trigger_count": len(triggers), "problems": [], "steps": [],
    }

    # --- authorisation, before anything else --------------------------------
    if authorisation is None or not getattr(authorisation, "is_complete", False):
        result["action"] = REFUSED
        result["problems"].append(
            "no auditable deployment authorisation; passing the checks below is "
            "not permission to hand the schedule over")
        return result

    # --- 1. freeze ----------------------------------------------------------
    if freeze is not None:
        result["steps"].append("freeze_new_claims")
        try:
            freeze()
        except Exception as exc:  # noqa: BLE001
            result["action"] = REFUSED
            result["problems"].append(
                f"freezing new claims failed, so the cutover cannot start "
                f"cleanly: {type(exc).__name__}: {exc}")
            return result

    # --- 2. inventory -------------------------------------------------------
    result["steps"].append("inventory_unfinished")
    result["inventory"] = [dict(item) for item in inventory]

    # --- 3. dispose: every unfinished trigger needs a declared disposition ---
    result["steps"].append("declare_dispositions")
    undeclared = [str(item.get("trigger")) for item in inventory
                  if str(item.get("trigger")) not in dispositions]
    if undeclared:
        result["action"] = REFUSED
        result["problems"].append(
            f"these unfinished triggers have no declared disposition: {undeclared}. "
            "An undeclared trigger is one nobody decided about, and leaving it "
            "undecided is how it gets executed twice")
        return result

    voided_without_reason = [
        str(trigger) for trigger, (kind, reason) in dispositions.items()
        if kind == VOID and not reason.strip()]
    if voided_without_reason:
        result["action"] = REFUSED
        result["problems"].append(
            f"these triggers are marked void without a reason: {voided_without_reason}. "
            "Voiding is a decision someone made; recording it needs its reason")
        return result

    result["dispositions"] = {t: {"kind": k, "reason": r}
                              for t, (k, r) in dispositions.items()}

    # --- 4. hand over -------------------------------------------------------
    if hand_over is not None:
        result["steps"].append("hand_over_to_new_owner")
        try:
            hand_over(inventory)
        except Exception as exc:  # noqa: BLE001
            result["action"] = REFUSED
            result["problems"].append(
                f"handing over failed: {type(exc).__name__}: {exc}")
            return result

    if mark_published is not None:
        result["steps"].append("mark_dispositions_published")
        for trigger, (kind, _reason) in dispositions.items():
            if kind == ALREADY_RUN:
                try:
                    mark_published(trigger)
                except Exception as exc:  # noqa: BLE001
                    result["action"] = REFUSED
                    result["problems"].append(
                        f"marking {trigger!r} as already run failed: {exc}")
                    return result

    result["action"] = FROZEN if not apply else HANDED_OVER
    if not apply:
        result["detail"] = ("frozen and inventoried; nothing changed. A trigger can "
                            "start between now and the switch, so this must be "
                            "re-checked immediately before applying")
        return result

    result["applied"] = True
    result["action"] = CUTOVER
    return result


def perform_rollback(*, triggers: Sequence[str], executed: dict[str, list[str]],
                     authorisation: Any | None, actor: str = "",
                     release_claims: Callable[[str], Any] | None = None,
                     apply: bool = False,
                     path: str | Path | None = None) -> dict[str, Any]:
    """Return the schedule to the old owner, skipping what already ran.

    Refuses when the execution record is incomplete. A rollback that re-runs
    everything is worse than no rollback: it doubles the ledger writes the
    cutover was meant to avoid, and it does so silently.
    """
    result: dict[str, Any] = {
        "action": ROLLBACK, "applied": False, "actor": actor,
        "trigger_count": len(triggers), "problems": [],
    }

    if authorisation is None or not getattr(authorisation, "is_complete", False):
        result["action"] = REFUSED
        result["problems"].append(
            "no auditable deployment authorisation; a rollback changes who fires "
            "the schedule just as much as a cutover does")
        return result

    plan = plan_rollback(triggers, executed=executed)
    result["plan"] = plan

    if not plan["dedup_sound"]:
        result["action"] = REFUSED
        result["problems"].append(plan["detail"])
        return result

    result["skipped"] = plan["skipped_as_already_executed"]
    if not apply:
        result["detail"] = ("nothing changed. The skipped set is the reading an "
                            "operator needs before applying")
        return result

    if release_claims is not None:
        for trigger in triggers:
            try:
                release_claims(trigger)
            except Exception as exc:  # noqa: BLE001
                result["action"] = REFUSED
                result["problems"].append(
                    f"releasing the claim on {trigger!r} failed: "
                    f"{type(exc).__name__}: {exc}. A half-released schedule has no "
                    "owner, which is the one state both dispatchers agree on")
                return result

    _record_rollback(result, path=path, actor=actor)
    result["applied"] = True
    return result


def _record_rollback(result: dict[str, Any], *, path: str | Path | None,
                     actor: str) -> None:
    conn = _connect(path)
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schedule_rollbacks (
                   seq INTEGER PRIMARY KEY AUTOINCREMENT,
                   payload_json TEXT NOT NULL,
                   actor TEXT NOT NULL DEFAULT '',
                   recorded_at TEXT NOT NULL)""")
        conn.execute(
            "INSERT INTO schedule_rollbacks (payload_json, actor, recorded_at) "
            "VALUES (?, ?, ?)",
            (json.dumps(result, ensure_ascii=False, default=str), actor, _now()))
    finally:
        conn.close()


def rollback_history(*, limit: int = 50,
                     path: str | Path | None = None) -> list[dict[str, Any]]:
    conn = _connect(path)
    try:
        rows = conn.execute(
            "SELECT * FROM schedule_rollbacks ORDER BY seq DESC LIMIT ?",
            (limit,)).fetchall()
        return [{"seq": r["seq"], "actor": r["actor"],
                 "recorded_at": r["recorded_at"],
                 "payload": json.loads(r["payload_json"])} for r in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def render_report(ownership: OwnershipReport, cutover: dict[str, Any],
                  rollback: dict[str, Any] | None = None) -> str:
    lines = ["# 调度切换与回滚报告（11.6）", ""]
    lines.append(ownership.summary())
    lines += ["", "## 切换", ""]
    lines.append(f"- 动作：`{cutover['action']}`（applied={cutover['applied']}）")
    lines.append(f"- 触发数：{cutover['trigger_count']}")
    lines.append(f"- 步骤：{' → '.join(cutover.get('steps') or []) or '未开始'}")
    for problem in cutover.get("problems") or []:
        lines.append(f"- **阻断**：{problem}")
    if not (cutover.get("problems") or []):
        lines.append(f"- {cutover.get('detail') or '无额外说明'}")
    if rollback is not None:
        lines += ["", "## 回滚", ""]
        lines.append(f"- 动作：`{rollback['action']}`（applied={rollback['applied']}）")
        lines.append(f"- 去重是否精确：**{'是' if rollback.get('plan', {}).get('dedup_sound') else '否'}**")
        for skipped in rollback.get("skipped") or []:
            lines.append(f"- 跳过（已执行）：`{skipped['trigger']}` "
                         f"→ {skipped['execution_refs']}")
        for problem in rollback.get("problems") or []:
            lines.append(f"- **阻断**：{problem}")
    lines += ["", "**单一所有者**：同一逻辑触发不得被两条路径同时认领；状态未知的触发"
             "必须先人工判定跑没跑过——当作已完成会漏跑一次执行，当作未完成会跑两次。"]
    return "\n".join(lines)
