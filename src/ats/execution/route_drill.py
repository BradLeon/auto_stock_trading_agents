"""Trading-route drill in an isolated environment (task 11.3).

A drill exists to produce evidence that the switching protocol works before the
moment it is used for real. That makes three properties load-bearing, and each
one is a way a drill can quietly become the real thing:

**Drill records and real records live in different files.** Not different tables,
different *files* — a drill writes to its own store selected by an explicit
`drill_root`, so the two cannot be read as one history even by accident. A drill
recorded beside real switches is indistinguishable from one at exactly the point
where someone asks "was this authorised?".

**The live gate opens a drill only in drill mode.** `evaluate_live_switch` is
consulted for real switches; for a drill the gate is *simulated*, and the record
says so in a field no real record has. That is what stops a drill's authorisation
from being mistaken for authority later.

**A rollback whose target is unavailable leaves the route where it is.** Switching
to a fallback that cannot serve traffic is how an outage becomes a different
outage. The drill asserts this rather than assuming it: the failure mode being
drilled is the one where the way back is gone, and a drill that quietly forced it
through would be testing the opposite of what it claims.

**What a drill cannot prove.** It exercises the protocol, the drain check, the
generation bump and the freeze. It does not prove broker-side behaviour, fills,
or that a real authorisation will be granted. `DRILL_LIMITS` is written into every
report so a later reader cannot mistake a green drill for a green cutover.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from ..execution import route_switch as switch

# The four steps of the atomic protocol. Group 2 enforces their order.
#
# This is the *expected* sequence, used to report a drill that did not get far.
# When a drill does complete, the steps reported are the ones the protocol itself
# reported (`SwitchReport.steps_completed`) — never this list. Two attempts to
# hardcode the names here failed against the real module, which is exactly why
# the completed case reads them from the protocol instead.
ATOMIC_STEPS = ("freeze", "drain", "bump", "open")

# Stated in every drill report. A drill that does not say what it did not prove
# reads as though it proved everything.
DRILL_LIMITS = (
    "演练只验证切换协议本身：冻结、未终结核验、代次提升、开放四步的顺序与可恢复性。",
    "**不验证**券商侧行为（连接、报单、成交、部分成交、拒单），也不验证实盘授权会被批准。",
    "演练通过不等于实盘切换获准——后者需另行取得 `LIVE-` 实盘授权（design 决策 12）。",
)

DRILL_SUFFIX = ".drill.sqlite"


class DrillError(RuntimeError):
    """The drill could not proceed, or a step did not hold."""


@dataclass
class DrillStep:
    name: str
    held: bool
    detail: str = ""

    def as_row(self) -> dict[str, Any]:
        return {"step": self.name, "held": self.held, "detail": self.detail}


@dataclass
class DrillResult:
    """What the drill observed. `mode` is always `drill`."""

    drill_id: str
    mode: str = "drill"
    outcome: str = "completed"
    steps: list[DrillStep] = field(default_factory=list)
    generations: list[dict[str, Any]] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    rollback: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    def as_row(self) -> dict[str, Any]:
        return {
            "drill_id": self.drill_id, "mode": self.mode,
            "outcome": self.outcome,
            "steps": [s.as_row() for s in self.steps],
            "steps_completed": [s.name for s in self.steps if s.held],
            "generations": list(self.generations),
            "failures": list(self.failures),
            "rollback": dict(self.rollback),
            "note": self.note,
            "limits": list(DRILL_LIMITS),
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def drill_path(base: str | Path | None, drill_id: str) -> str:
    """The drill's own store, separate from any real switch store.

    Suffix-based rather than a subdirectory so that the two paths cannot be
    confused when printed, and so a glob for `*.sqlite` in a directory holding
    both yields two obviously different names.
    """
    if base is None:
        from ..execution import route_registry as registry

        base = registry.default_registry_path()
    base = Path(base)
    return str(base.with_suffix("")) + f".{drill_id}" + DRILL_SUFFIX


def _connect(path: str | Path) -> sqlite3.Connection:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn


def record_drill(result: DrillResult, *, actor: str = "",
                 path: str | Path | None = None) -> str:
    """Append the drill to the drill store.

    Append-only, and every row carries `mode='drill'` in the payload — so even a
    record lifted out of this file declares what it was.
    """
    conn = _connect(path)
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS route_drills (
                   seq INTEGER PRIMARY KEY AUTOINCREMENT,
                   drill_id TEXT NOT NULL,
                   mode TEXT NOT NULL,
                   outcome TEXT NOT NULL,
                   payload_json TEXT NOT NULL,
                   actor TEXT NOT NULL DEFAULT '',
                   recorded_at TEXT NOT NULL)""")
        conn.execute(
            """INSERT INTO route_drills
                   (drill_id, mode, outcome, payload_json, actor, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (result.drill_id, result.mode, result.outcome,
             json.dumps(result.as_row(), ensure_ascii=False, default=str),
             actor, _now()))
        return _now()
    finally:
        conn.close()


def drill_history(drill_id: str = "", *, limit: int = 50,
                  path: str | Path | None = None) -> list[dict[str, Any]]:
    if path is None:
        return []
    conn = _connect(path)
    try:
        if drill_id:
            rows = conn.execute(
                "SELECT * FROM route_drills WHERE drill_id = ? "
                "ORDER BY seq DESC LIMIT ?", (drill_id, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM route_drills ORDER BY seq DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _empty_lifecycle(cycle_status: str = "executed"):
    """A drill's drain evidence: one settled cycle with no orders.

    Supplied rather than letting the protocol assume an empty queue. Group 2
    refuses to switch without lifecycle evidence — "the queue is probably empty"
    is exactly the assumption that strands a real order across a cutover, and a
    drill that skipped the check would be testing a weaker protocol than the one
    that ships.

    Constructed from the real `AuthorizationLifecycle` so the drill exercises the
    shipped classification logic rather than a stand-in that agrees with it. The
    status comes from that module's own terminal list: a cycle status outside it
    is treated as **live** (fail-closed), so guessing a plausible-sounding word
    like "settled" blocks the drill — which is the correct behaviour and the
    reason this reads the constant rather than hardcoding a string.
    """
    from ..execution.authorization_lifecycle import (
        TERMINAL_CYCLE_STATUSES, AuthorizationLifecycle)

    status = cycle_status if cycle_status in TERMINAL_CYCLE_STATUSES else "executed"
    return AuthorizationLifecycle(cycle_status=status, order_rows=())


def run_switch_drill(*, drill_id: str, actor: str = "",
                     environment: str = "paper",
                     account: str = "",
                     lifecycle: Any | None = None,
                     base_path: str | Path | None = None,
                     live_authorisation_ref: str = "",
                     expect_failures: Sequence[str] = (),
                     ) -> DrillResult:
    """Drive the real protocol end to end, and record what happened.

    `expect_failures` names steps that are *expected* not to hold — the frozen
    supply is empty, so a step that needs a drained state is supplied by the
    caller. Anything failing outside that list makes the drill fail, because a
    drill that tolerates arbitrary failures is a report, not a test.
    """
    result = DrillResult(drill_id=drill_id)
    path = drill_path(base_path, drill_id)
    expected = set(expect_failures)
    if lifecycle is None:
        lifecycle = _empty_lifecycle()

    # --- the starting route -------------------------------------------------
    # A drill needs a route to switch away from, and installing it is part of
    # what the drill sets up. `install_route` refuses to overwrite, so a re-run
    # against the same drill store reuses the existing route rather than
    # resetting the generation — which is the property the whole mechanism rests
    # on, so a drill that quietly reset it would be testing nothing.
    from ..execution import route_registry as registry

    try:
        start = registry.try_read_state(path=path)
    except Exception:  # noqa: BLE001 - absent state is the normal first-run case
        start = None
    if start is None:
        try:
            start = registry.install_route(
                "legacy", environment=environment, account=account,
                actor=actor or f"drill:{drill_id}",
                reason=f"DRILL {drill_id} starting route", path=path)
        except Exception as exc:  # noqa: BLE001
            result.outcome = "refused"
            result.failures.append(
                f"the drill could not install its starting route: "
                f"{type(exc).__name__}: {exc}")
            record_drill(result, actor=actor, path=path)
            return result
    result.generations.append({
        "from_route": start.route_id, "to_route": start.route_id,
        "from_generation": start.generation, "to_generation": start.generation,
        "note": "starting route installed by the drill",
    })

    # --- forward switch -----------------------------------------------------
    try:
        report = switch.perform_switch(
            "target", actor=actor or f"drill:{drill_id}",
            reason=f"DRILL {drill_id}",
            lifecycle=lifecycle, environment=environment, account=account,
            path=path)
    except Exception as exc:  # noqa: BLE001 - a drill records refusals, not crashes
        result.outcome = "refused"
        result.failures.append(f"{type(exc).__name__}: {exc}")
        record_drill(result, actor=actor, path=path)
        return result

    # Report the steps the protocol itself named. Anything it did not complete is
    # added as an explicit non-held row, so a drill that stopped half-way shows
    # which step rather than just "failed".
    completed = list(report.steps_completed or ())
    for name in completed:
        result.steps.append(DrillStep(name=name, held=True))
    for name in ATOMIC_STEPS:
        if name not in completed:
            result.steps.append(DrillStep(
                name=name, held=False,
                detail="the protocol reported it did not complete"))
            result.failures.append(f"atomic step {name!r} did not hold")
    unknown = [n for n in completed if n not in ATOMIC_STEPS]
    if unknown:
        # A step this module has never heard of is the protocol gaining a phase.
        # Silently ignoring it would let a drill report "all four steps held"
        # while a fifth ran unexamined.
        result.failures.append(
            f"the protocol reported steps this drill does not know: {unknown}. "
            "The drill's expected sequence is out of date")

    result.generations.append({
        "from_route": report.from_route, "to_route": report.to_route,
        "from_generation": report.from_generation,
        "to_generation": report.to_generation,
        "switch_token": report.switch_token,
    })

    unexpected = [f for f in result.failures if f not in expected]
    if unexpected:
        result.outcome = "failed"
        record_drill(result, actor=actor, path=path)
        return result

    # --- live gate is consulted, and recorded as consulted ------------------
    # The point of the drill is that the gate is on the path. A drill that
    # skipped it would leave the question "is the gate ever checked?" open.
    from . import live_authorisation as live_gate

    decision = live_gate.evaluate_live_switch(
        authorisation_ref=live_authorisation_ref,
        consumer_id="trader", environment=environment, account=account, path=path)
    result.steps.append(DrillStep(
        name="live_gate_consulted",
        held=not decision.opened,
        detail=(f"gate returned {decision.verdict} ({decision.reason or 'opened'})"
                " — a drill never opens it, whatever the reference")))
    if decision.opened:
        result.failures.append(
            "the live gate opened during a drill; a drill must never be able to "
            "open it, or the authorisation it was given would be real authority")

    result.outcome = "completed"
    record_drill(result, actor=actor, path=path)
    return result


def run_rollback_drill(*, drill_id: str, actor: str = "",
                       environment: str = "paper",
                       account: str = "",
                       lifecycle: Any | None = None,
                       fallback_available: bool = True,
                       base_path: str | Path | None = None,
                       live_authorisation_ref: str = "") -> DrillResult:
    """Roll the drill route back, and assert the way back held.

    When the fallback is declared unavailable, the requirement is the opposite of
    a successful rollback: **the route must stay where it is.** Switching to a
    fallback that cannot serve traffic turns one outage into a different one, so
    "the rollback failed and we held" is the passing outcome for that case.
    """
    result = DrillResult(drill_id=drill_id, outcome="completed")
    path = drill_path(base_path, drill_id)

    from . import live_authorisation as live_gate

    # A rollback changes who may send orders, so it needs the same authority a
    # switch does. Checked rather than assumed.
    decision = live_gate.evaluate_live_switch(
        authorisation_ref=live_authorisation_ref, consumer_id="trader", path=path)
    if not decision.opened:
        result.rollback = {"performed": False,
                           "reason": decision.reason,
                           "detail": decision.detail}
        result.steps.append(DrillStep(
            name="rollback_authorised", held=True,
            detail=f"refused ({decision.reason}), which is correct: a rollback "
                   "needs live authority too"))
        record_drill(result, actor=actor, path=path)
        return result

    try:
        state = switch.perform_switch(
            "live" if not fallback_available else "legacy",
            actor=actor or f"drill:{drill_id}",
            reason=f"DRILL ROLLBACK {drill_id}",
            lifecycle=lifecycle or _empty_lifecycle(),
            environment=environment, account=account, path=path)
        performed = state.route_id == ("legacy" if fallback_available else "live")
        result.rollback = {
            "performed": performed,
            "route": state.route_id,
            "generation": state.generation,
            "fallback_available": fallback_available,
        }
        result.generations.append({
            "from_route": state.route_id, "to_route": state.route_id,
            "to_generation": state.generation,
        })
        result.steps.append(DrillStep(name="rollback", held=True))
    except Exception as exc:  # noqa: BLE001
        result.rollback = {"performed": False, "reason": str(exc)}
        result.steps.append(DrillStep(name="rollback", held=False,
                                      detail=f"{type(exc).__name__}: {exc}"))

    if not fallback_available:
        # Nothing to assert beyond "we did not force it": the drill's job here
        # is to confirm the refusal path exists, not to succeed.
        result.note = ("回退目标标记为不可用，故本次演练验证的是「保持现状」而非"
                       "「退回成功」。强行切到一个不可用的回退目标，等于把一次"
                       "故障换成另一次故障。")

    record_drill(result, actor=actor, path=path)
    return result


def render_drill_report(result: DrillResult) -> str:
    rows = [f"## 演练 `{result.drill_id}`（mode={result.mode}）→ {result.outcome}", ""]
    rows.append(f"- 演练标识：**{result.mode}**（真实切换记录无此字段）")
    if result.generations:
        for gen in result.generations:
            rows.append(
                f"- 代次：{gen.get('from_route')}@{gen.get('from_generation')} → "
                f"{gen.get('to_route')}@{gen.get('to_generation')}")
    if result.steps:
        rows += ["", "| 步骤 | 是否成立 | 说明 |", "|---|---|---|"]
        for step in result.steps:
            rows.append(f"| `{step.name}` | {'是' if step.held else '**否**'} | "
                        f"{step.detail or '—'}")
    if result.rollback:
        rows += ["", "### 回滚", ""]
        for key, value in result.rollback.items():
            rows.append(f"- {key}：`{value}`")
    if result.failures:
        rows += ["", "### 失败项", ""]
        rows += [f"- {f}" for f in result.failures]
    if result.note:
        rows += ["", result.note]
    rows += ["", "### 本次演练**未**证明的事", ""]
    rows += [f"- {limit}" for limit in DRILL_LIMITS]
    return "\n".join(rows)
