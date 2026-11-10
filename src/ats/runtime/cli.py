"""CLI runner: drive the chief decision graph, pausing at the Boss-approval interrupt.

    ats chief run           # dry-run, interactive Boss prompt
    ats trader buy NVDA 5   # manual order — same graph, same risk gate + approval
    ats pead score COHR --chief   # earnings-event trade, chief收口 immediately

The graph is transport-agnostic: it interrupts, this runner asks the configured
BossChannel for a verdict, then resumes with Command(resume=...). Async channels
(Feishu) checkpoint at the interrupt and `ats serve` resumes on callback.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
import os
from pathlib import Path
import sys

from langgraph.types import Command

from ..channel import get_channel
from ..config import get_config
from ..graph.checkpoint import get_checkpointer
from ..schemas.channel import ApprovalRequest, Notification
from ..workflow.schedule_runtime import scheduled_cli


def run_decision_graph(state, *, channel="cli") -> dict:
    """Run the chief decision graph through its approval interrupt.

    Sync channels (CLI) loop interrupt->verdict->resume in-process. Async
    channels (Feishu) send the card and return — the checkpointed thread is
    resumed later by `ats serve` via resume_cycle(). `channel` is a kind
    string or an already-built BossChannel.
    """
    from ..channel import get_channel as _get_channel  # late bind (tests patch it)
    from ..graph.chief import build_chief_graph

    ch = _get_channel(channel) if isinstance(channel, str) else channel
    is_async = getattr(ch, "is_async", False)
    # Async channels must survive across processes -> persistent checkpointer.
    app = build_chief_graph(checkpointer=get_checkpointer(persist=is_async))
    cfg_run = {"configurable": {"thread_id": state.cycle_id}}
    print(f"▶ {state.cycle_id} (source={state.source}, dry_run={state.dry_run})")

    result = app.invoke(state, config=cfg_run)
    if "__interrupt__" not in result:
        return result

    if is_async:
        req = ApprovalRequest.model_validate(result["__interrupt__"][0].value)
        ch.send_approval_request(req, thread_id=state.cycle_id)
        print(
            f"⏸ {state.cycle_id} awaiting Boss approval via "
            f"{getattr(ch, 'kind', 'async channel')}. Run `ats serve` to handle the callback."
        )
        return result

    # Sync (CLI): drive interrupts to completion in-process.
    while "__interrupt__" in result:
        req = ApprovalRequest.model_validate(result["__interrupt__"][0].value)
        if hasattr(ch, "push"):
            ch.push(
                Notification(
                    kind="approval_request",
                    title="Decisions pending review",
                    body=f"{len(req.decisions)} proposed trade(s)",
                )
            )
        approval = ch.request_approval(req)
        result = app.invoke(Command(resume=approval.model_dump(mode="json")), config=cfg_run)
    return result


from ..workflow.runtime_reads import cli_read_request, scoped_read  # noqa: E402

@scoped_read("fundamental")
@scheduled_cli(lambda args: "fundamental-event" if args["phase"] == "score" else "fundamental-routine",
               kind="entity", argument="symbol")
def run_pead(
    symbol: str,
    phase: str,
    *,
    dry_run: bool = True,
    auto: bool = False,
    offline: bool = False,
    use_llm: bool = True,
    transcript: str | None = None,
    channel: str = "cli",
    chief: bool = False,
) -> dict:
    """Run one PEAD phase (prep | score). v0.2: score produces a RECOMMENDATION
    persisted in the dossier (no interrupt) — the Chief makes the trade call.
    Pass chief=True to run the Chief immediately after a score completes."""
    from ..graph.pead import build_pead_graph
    from ..graph.pead_state import PeadState

    sym = symbol.upper()
    app = build_pead_graph(checkpointer=get_checkpointer(persist=False))
    now = datetime.now(timezone.utc)
    state = PeadState(
        symbol=sym,
        phase=phase,
        as_of=now,
        dry_run=dry_run,
        use_llm=use_llm,
        use_broker=not offline,
        live_data=not offline,
        transcript_source=transcript,
    )
    cfg_run = {"configurable": {"thread_id": f"pead-{sym}-{phase}-{now:%Y%m%d%H%M%S}"}}
    print(f"▶ PEAD {phase} {sym}")

    result = app.invoke(state, config=cfg_run)
    _pead_report(sym, phase, result)
    if phase == "score":
        if chief:
            run_chief(
                dry_run=dry_run, channel=channel, auto=auto, offline=offline, source="pead-chief",
                fundamental_mode="event", event_entities=[sym],
            )
        else:
            print("→ 建议已入档；运行 `ats chief run` 收口交易决策")
    return result


def _pead_report(symbol: str, phase: str, result: dict) -> None:
    print("\n" + "=" * 70)
    if phase == "prep":
        es = result.get("expectation_set")
        ms = result.get("market_setup")
        print(f"PEAD PREP COMPLETE — {symbol}")
        if es:
            print(f"Narrative: {es.narrative[:240]}")
            if es.focus_ranking:
                print("Focus: " + " > ".join(es.focus_ranking[:5]))
            print(
                f"Expectations rows: {len(es.expectations)}  | consensus EPS={es.consensus_eps} "
                f"Rev={es.consensus_revenue}"
            )
        if ms:
            print(
                f"Setup: run-up vs sector {ms.run_up_vs_sector_pct}% · EM {ms.expected_move_pct}% "
                f"· ATM IV {ms.atm_iv}% · dist-to-high {ms.dist_to_ath_pct}%"
            )
        print(f"Signal chain: {len(result.get('signal_chain', []))} names")
    else:
        sc = result.get("scorecard")
        view = result.get("event_view") or {}
        if sc:
            print(
                f"PEAD SCORE COMPLETE — {symbol}  Scorecard {sc.total:+.2f} "
                f"(门槛 {sc.threshold:+.1f}) — {sc.band}"
            )
        # Phase D: the score branch publishes a non-executable event review —
        # direction / magnitude / confidence — never recommendations.
        from ..agents.fundamental.event import direction_word

        direction = int(view.get("direction", 0))
        line = f"事件评审（非可执行）: {direction_word(direction)}"
        if view.get("magnitude") is not None:
            line += f" · 幅度 {view.get('magnitude'):+.2f}"
        if view.get("confidence") is not None:
            line += f" · 信心 {view.get('confidence'):.2f}"
        tri = result.get("tri_diffs") or {}
        if tri.get("note"):
            line += f" · {tri['note']}"
        print(f"情景: {result.get('decision_band', '—')}")
        print(line)
    print("=" * 70)


def events_list(*, days: int | None = None) -> int:
    from datetime import date, timedelta
    from zoneinfo import ZoneInfo

    from ..config import load_events

    from ..data.stores.schedule_calendar import ScheduleCalendarStore

    today = datetime.now(ZoneInfo("America/New_York")).date()
    stored = ScheduleCalendarStore().latest_events(limit=10_000)
    if stored:
        end = today + timedelta(days=days) if days is not None else None
        events = [e for e in stored if (days is None or
                  today <= date.fromisoformat(e["event_date"]) <= end)]
        for event in events:
            print(f"  {event['event_date']} [{event['event_type']:13}] {event['label']} "
                  f"v{event['event_version']} · {event['quality_status']} · "
                  f"{event['time_precision']} {event['local_time']} {event['timezone']}")
        if not events:
            print("(持久化日历中没有匹配事件；可运行 `ats events refresh` 更新日历)")
        return 0

    # Compatibility fallback until the first persisted calendar refresh.
    events = load_events()
    if days is not None:
        events = [e for e in events if today <= e.date <= today + timedelta(days=days)]
    if not events:
        print("(持久化日历为空，config/events.yaml 也没有匹配事件)")
        return 0
    for e in sorted(events, key=lambda e: e.date):
        print(f"  {e.date} [{e.kind:13}] {e.label} -> {', '.join(e.triggers)}")
    return 0


def events_calendar_command(args, *, parser) -> int:
    from ..data.products.calendar import ScheduleCalendarProduct
    from ..data.stores.schedule_calendar import ScheduleCalendarStore

    store = ScheduleCalendarStore()
    if args.action == "refresh":
        from ..data.calendar_refresh import enqueue_schedule_calendar_refresh
        from ..data.persistent_queue import PersistentIngestionQueue

        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        queue = PersistentIngestionQueue()
        submitted = enqueue_schedule_calendar_refresh(
            source_ids=args.source or None, trigger_kind="manual",
            trigger_ref=f"events-refresh:{now.isoformat()}", now=now)
        outcomes = []
        for task in submitted:
            result = queue.run_one(task_id=task["task_id"])
            current = queue.get(task["task_id"]) or {}
            outcomes.append({**task,
                             "status": (result or {}).get("status") or
                                       current.get("status", "queued"),
                             "result": (result or {}).get("stdout", {})})
        print(json.dumps({"sources": outcomes, "refresh_owner": "persistent_queue"},
                         ensure_ascii=False, indent=2, default=str))
        return 0 if all(row["status"] in {"succeeded", "no_change", "partial"}
                        for row in outcomes) else 1
    if args.action == "status":
        print(json.dumps(ScheduleCalendarProduct(store).snapshot(), ensure_ascii=False,
                         indent=2, default=str))
        return 0
    if args.action == "candidates":
        rows = store.candidates(event_id=args.event_id, review_status=args.status,
                                limit=args.limit)
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if args.action == "override":
        if not args.event_id or not args.actor or not args.reason:
            parser.error("events override requires --event-id, --actor and --reason")
        if not args.date:
            parser.error("events override currently requires --date")
        try:
            evidence = json.loads(args.evidence_json or "{}")
        except json.JSONDecodeError as exc:
            parser.error(f"--evidence-json is invalid JSON: {exc}")
        event = store.override_event(args.event_id,
                                     patch={"event_date": args.date, "local_time": "",
                                            "timezone": "", "utc_at": "",
                                            "time_precision": "date", "market_session": "unknown"},
                                     actor=args.actor, reason=args.reason, evidence=evidence)
        print(json.dumps(event, ensure_ascii=False, indent=2))
        return 0
    if args.action == "cancel":
        if not args.event_id or not args.actor or not args.reason:
            parser.error("events cancel requires --event-id, --actor and --reason")
        try:
            evidence = json.loads(args.evidence_json or "{}")
        except json.JSONDecodeError as exc:
            parser.error(f"--evidence-json is invalid JSON: {exc}")
        event = store.override_event(args.event_id, patch={"status": "cancelled"},
                                     actor=args.actor, reason=args.reason, evidence=evidence)
        from ..workflow.ownership import reconcile_calendar_trigger_versions

        event["trigger_reconciliation"] = reconcile_calendar_trigger_versions(
            calendar_store=store)
        print(json.dumps(event, ensure_ascii=False, indent=2))
        return 0
    if args.action == "withdraw":
        if not args.event_id or not args.actor or not args.reason:
            parser.error("events withdraw requires --event-id, --actor and --reason")
        try:
            evidence = json.loads(args.evidence_json or "{}")
        except json.JSONDecodeError as exc:
            parser.error(f"--evidence-json is invalid JSON: {exc}")
        event = store.withdraw_override(args.event_id, actor=args.actor,
                                        reason=args.reason, evidence=evidence)
        print(json.dumps(event, ensure_ascii=False, indent=2))
        return 0
    return events_list(days=args.days if args.action == "upcoming" else None)


def _require(value: str, flag: str, parser) -> str:
    """Refuse a missing required flag instead of proceeding with an empty value.

    Shared rather than redefined per command: four of them grew a private copy,
    and a copy that forgets the check is a command that runs with `""`.
    """
    if not value:
        parser.error(f"{flag} is required")
    return value


def intake_verification_command(args, *, parser) -> int:
    """Phase F 7.1–7.7: isolated intake verification and per-consumer disposition.

    Kept separate from `cutover` and `shadow` because this command's defining
    property is that it does NOT change anything: it runs the ten roles' access
    checks under the isolation environment and reports what it found. An operator
    who sees it next to the mutating commands would reasonably assume some of its
    actions write, so it carries its own name.
    """
    from ..workflow import consumer_disposition as disposition
    from ..workflow import intake_verification as iv

    action = args.action

    if action in {"verify", "report"}:
        consumers = ([args.consumer_id] if args.consumer_id
                     else list(iv.TEN_CONSUMERS))
        try:
            records = []
            for consumer_id in consumers:
                if consumer_id not in iv.TEN_CONSUMERS:
                    parser.error(f"unknown consumer {consumer_id!r}; expected one of "
                                 + ", ".join(iv.TEN_CONSUMERS))
                records.append(iv.scan_consumer_access(
                    consumer_id,
                    opinion_inputs=args.opinion_input or ()))
        except iv.IntakeVerificationError as exc:
            # A verification that could not run is not a clean result. Reported
            # as JSON with a non-zero exit so a script cannot read "the check
            # failed" as "nothing is wrong".
            print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
            return 1

        if action == "verify":
            payload = [record.as_row() for record in records]
            print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        else:
            print(iv.render_report(records))
        # Non-zero when anything is non-compliant, so a gate script fails on it.
        return 0 if all(r.compliant for r in records) else 1

    if action == "digest":
        records = [iv.scan_consumer_access(c) for c in iv.TEN_CONSUMERS]
        print(json.dumps({"digest": iv.verification_digest(records),
                          "evidence_id": f"intake-verification:{iv.verification_digest(records)[:16]}",
                          "consumers": len(records),
                          "compliant": sum(1 for r in records if r.compliant)},
                         ensure_ascii=False, indent=2))
        return 0

    if action == "not-tradable":
        # The refusal, exposed as a command so the rule is checkable without
        # writing a test: an isolated acceptance is not a trading authorisation.
        try:
            iv.assert_isolated_result_not_tradable(
                iv.IsolationAttestation(run_id=args.run_id or "(none)",
                                        isolation_root="", broker_write_prohibited=True,
                                        surfaces_redirected=()),
                consumer_id=args.consumer_id or "")
        except iv.IntakeVerificationError as exc:
            print(json.dumps({"tradable": False, "reason": str(exc)},
                             ensure_ascii=False, indent=2))
            return 1
        print(json.dumps({"tradable": True}, ensure_ascii=False))
        return 0

    if action == "disposition":
        try:
            reports = disposition.consumer_dispositions(
                stale_by_report_age=args.stale_input or (),
                optional_inputs=args.optional_input or ())
        except disposition.DispositionError as exc:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
            return 1
        print(disposition.render_dispositions(reports))
        return 0

    if action == "evidence-index":
        # The index task 7.8's runbook chapter is checked against. Every evidence
        # id the runbook cites must be derivable from here, or the runbook is
        # citing something that does not exist.
        records = [iv.scan_consumer_access(c) for c in iv.TEN_CONSUMERS]
        digest = iv.verification_digest(records)
        index = {record.consumer_id: {
            "evidence_id": f"intake-verification:{digest[:16]}:{record.consumer_id}",
            "entry_point": record.entry_point,
            "reached_entry_point": record.reached_entry_point,
            "compliant": record.compliant,
            "violations": [v.as_row() for v in record.violations],
            "gaps": list(record.gaps),
        } for record in records}
        print(json.dumps({"digest": digest, "consumers": index},
                         ensure_ascii=False, indent=2, default=str))
        return 0

    parser.error(f"unhandled intake action {action!r}")
    return 2


def retirement_command(args, *, parser) -> int:
    """Phase F 12.1–12.6: consumer zero, reconciliation, tombstone consistency.

    Read-only by construction. It produces verdicts and, for entries whose
    criteria now hold, the wording that would mark them retired — applying that
    wording is a separate deliberate step, because a registry edit that quietly
    promotes an entry on partial evidence is the failure 12.6 exists to prevent.
    """
    from ..workflow import consumer_scan
    from ..workflow import legacy_retirement as retirement
    from ..workflow import retirement_clearance as rc

    action = args.action
    registry = retirement.load_registry()
    live_uses = tuple(x for x in (args.uses or "").split(",") if x.strip())

    def _entries():
        wanted = {i.strip() for i in (args.identifier or "").split(",") if i.strip()}
        rows = []
        for tomb in registry.tombstones():
            if wanted and tomb.identifier not in wanted:
                continue
            rows.append(tomb)
        return rows

    if action == "zero":
        rows = _entries()
        if not rows:
            print("no matching retirement entries")
            return 1
        verdicts = []
        for tomb in rows:
            verdicts += rc.verify_consumer_zero(
                tomb.identifier, tomb.consumers, scan=consumer_scan.scan_consumer_access)
        print(rc.render_report([], verdicts))
        return 0 if all(not v.blocks_exit for v in verdicts) else 2

    if action == "reconcile":
        tomb = registry.tombstone(_require(args.identifier, "--identifier", parser))
        if tomb is None:
            print(f"no such retirement entry: {args.identifier!r}")
            return 1
        if not live_uses:
            parser.error("--uses is required: reconciliation over an unnamed set of "
                         "uses proves nothing")
        uses = {u: {"agrees": False, "detail": "not supplied by this run"} for u in live_uses}
        verdict = rc.reconcile(tomb.identifier, uses, required_uses=live_uses)
        print(json.dumps(verdict.as_row(), ensure_ascii=False, indent=2))
        return 0 if verdict.complete else 2

    if action == "tombstone":
        tomb = registry.tombstone(_require(args.identifier, "--identifier", parser))
        if tomb is None:
            print(f"no such retirement entry: {args.identifier!r}")
            return 1
        verdict = rc.check_tombstone_behaviour(
            tomb.identifier, tomb.status,
            readable_by=[c for c in (args.readable or "").split(",") if c.strip()])
        print(json.dumps(verdict.as_row(), ensure_ascii=False, indent=2))
        return 0 if verdict.verdict == rc.CONSISTENT else 2

    if action == "fallback-precheck":
        def _retired(identifier: str) -> bool:
            # No `is_retired` helper on the registry; a tombstone's own status is
            # the authority, read from the tombstone rather than inferred from
            # whether an entry happens to exist.
            tomb = registry.tombstone(identifier)
            return tomb is not None and tomb.may_exit

        ok, reason = rc.precheck_fallback_target(
            _require(args.target, "--target", parser),
            is_retired=_retired,
            is_available=(None if not args.check_available
                          else (lambda r: not _retired(r))))
        print(json.dumps({"target": args.target, "ok": ok, "reason": reason},
                         ensure_ascii=False, indent=2))
        return 0 if ok else 2

    if action == "decide":
        rows = _entries()
        if not rows:
            print("no matching retirement entries")
            return 1
        decisions = []
        g0 = rc.read_g0_gate(args.verification or None)
        trade_path = rc.read_trade_path_gate()
        for tomb in rows:
            zero = rc.verify_consumer_zero(
                tomb.identifier, tomb.consumers, scan=consumer_scan.scan_consumer_access)
            gate_open, gate_reason = rc.evaluate_gate(tomb.gate, g0=g0, trade_path=trade_path)
            decisions.append(rc.decide_entry(
                tomb.identifier, zero_verdicts=zero,
                window=rc.verify_rollback_window(
                    drilled=bool(args.drill_reference),
                    drill_reference=args.drill_reference or ""),
                tombstone=rc.check_tombstone_behaviour(tomb.identifier, tomb.status),
                unmet_dependencies=registry.unmet_dependencies(tomb.identifier),
                gate=tomb.gate, gate_open=gate_open, gate_reason=gate_reason))
        print(rc.render_report(decisions))
        return 0 if all(d.may_exit for d in decisions) else 2

    if action == "gate":
        # Read-only summary of the manual verification (G0) plus which registry entries each gate holds back.
        g0 = rc.read_g0_gate(args.verification or None)
        trade_path = rc.read_trade_path_gate()
        print(f"G0：{'已开' if g0.is_open else '未开'}——{g0.reason}")
        print(f"  核验项 {g0.total}；" + "；".join(f"{k} {v}" for k, v in sorted(g0.counts.items())))
        if g0.not_open:
            print(f"  未开放的项：{', '.join(g0.not_open)}")
        print(f"  G0 结论{'已签字' if g0.concluded else '未签字'}")
        by_gate: dict[str, list[str]] = {}
        for tomb in registry.tombstones():
            if tomb.gate and not tomb.may_exit:
                by_gate.setdefault(tomb.gate, []).append(tomb.identifier)
        for gate_name, items in sorted(by_gate.items()):
            opened, why = rc.evaluate_gate(gate_name, g0=g0, trade_path=trade_path)
            print(f"\n门 {gate_name}：{'已开' if opened else '未开'}，挂在这道门上的未退役条目 {len(items)} 条"
                  + ("" if opened else f"（{why}）"))
            if args.show:
                for name in items:
                    print(f"  - {name}")
        return 0 if g0.is_open else 2

    parser.error(f"unknown action {action!r}")


def live_command(args, *, parser) -> int:
    """Phase F 11.1–11.3: the live-trading gate and route drills.

    Separate from `read` and `dispatch` because those change routes and this one
    is the only command whose subject is **real money**. The gate is consulted
    here; the live switch itself is not performed here — keeping the two apart is
    what means a test can exercise the gate fully without any path existing by
    which it could move a real route.
    """
    from ..execution import live_authorisation as live
    from ..execution import route_drill as drill

    db = args.db or None
    action = args.action

    if action == "authorize":
        auth = live.LiveAuthorisation(
            reference=_require(args.reference, "--reference", parser),
            issuer=args.issuer,
            authorised_by=_require(args.authorised_by, "--authorised-by", parser),
            issued_by=_require(args.issued_by, "--issued-by", parser),
            scope=tuple(s.strip() for s in (args.scope or "").split(",") if s.strip()),
            environment=_require(args.environment, "--environment", parser),
            account=_require(args.account, "--account", parser),
            valid_until=_require(args.valid_until, "--valid-until", parser),
            note=args.note or "")
        try:
            live.record_authorisation(auth, actor=args.actor, path=db)
        except live.LiveAuthorisationError as exc:
            print(str(exc))
            return 1
        print(json.dumps(auth.as_row(), ensure_ascii=False, indent=2))
        return 0

    if action == "show-auth":
        auth = live.read_authorisation(args.reference or "", path=db)
        if auth is None:
            print(f"no such live authorisation: {args.reference!r}")
            return 1
        print(json.dumps({**auth.as_row(),
                          "source_verification": live.read_verification(auth.reference, path=db).as_row(),
                          "verification_history": live.verification_history(auth.reference, path=db)},
                         ensure_ascii=False, indent=2))
        return 0

    if action in {"verify", "void"}:
        # Both append a fact and never edit the authorisation. Each must carry the
        # user's own confirmation (who, their words, where and when) — an agent
        # cannot verify or void on its own say-so.
        change = live.verify_authorisation if action == "verify" else live.void_authorisation
        try:
            result = change(
                _require(args.reference, "--reference", parser),
                confirmed_by=args.confirmed_by, confirmation=args.confirmation,
                confirmation_source=args.confirmation_source, confirmed_at=args.confirmed_at,
                actor=args.actor, path=db)
        except live.LiveAuthorisationError as exc:
            print(str(exc))
            return 1
        print(json.dumps(result.as_row(), ensure_ascii=False, indent=2))
        return 0

    if action == "gate":
        decision = live.evaluate_live_switch(
            authorisation_ref=args.reference or "",
            consumer_id=args.consumer_id or "",
            environment=args.environment or "",
            account=args.account or "", path=db)
        print(json.dumps(decision.as_row(), ensure_ascii=False, indent=2))
        return 0 if decision.opened else 2

    if action in {"drill", "rollback-drill"}:
        if action == "drill":
            result = drill.run_switch_drill(
                drill_id=_require(args.drill_id, "--drill-id", parser),
                actor=args.actor, environment=args.environment or "paper",
                account=args.account or "", base_path=args.base,
                live_authorisation_ref=args.reference or "")
        else:
            result = drill.run_rollback_drill(
                drill_id=_require(args.drill_id, "--drill-id", parser),
                actor=args.actor, environment=args.environment or "paper",
                account=args.account or "", base_path=args.base,
                fallback_available=not args.fallback_unavailable,
                live_authorisation_ref=args.reference or "")
        print(drill.render_drill_report(result))
        return 0 if result.outcome == "completed" else 2

    if action == "drill-history":
        rows = drill.drill_history(args.drill_id or "",
                                   path=drill.drill_path(args.base,
                                                         args.drill_id or ""))
        print(json.dumps(rows, ensure_ascii=False, indent=2, default=str))
        return 0

    parser.error(f"unknown action {action!r}")


def dispatch_command(args, *, parser) -> int:
    """`ats dispatch`: pause/resume a workflow, the owner log, and the schedule runtime (`runtime-*`).

    The migration-period ownership / rollback-dedup reports were retired with schedule_cutover (r5 8.6).
    """
    if args.action.startswith("runtime-"):
        from ..workflow import schedule_runtime as runtime
        from datetime import datetime

        if getattr(args, "isolation_root", ""):
            from ..workflow.isolation import isolated_run
            import copy

            child = copy.copy(args)
            child.isolation_root = ""
            with isolated_run("schedule-control-cli", root=args.isolation_root):
                return dispatch_command(child, parser=parser)
        action = args.action.removeprefix("runtime-")
        try:
            scope = json.loads(args.business_scope) if getattr(args, "business_scope", "") else None
            if action == "state":
                result = runtime.snapshot()
            elif action == "reload":
                result = runtime.reload_authority()
            elif action == "prepare":
                from ..workflow.isolation import verified_isolation_root
                if verified_isolation_root() is None:
                    raise runtime.ScheduleAuthorityError("scope preparation requires isolated drill")
                result = runtime.check_owner(args.workflow, scope, args.owner)
            elif action == "freeze":
                result = {"token": runtime.freeze(args.workflow, scope, actor=args.actor, reason=args.reason)}
            elif action == "inventory":
                result = runtime.frozen_inventory(args.token)
            elif action == "handover":
                runtime.handover(args.token, to_owner=args.owner,
                    dispositions=json.loads(args.dispositions), actor=args.actor, reason=args.reason)
                result = runtime.snapshot()
            elif action == "cancel-freeze":
                runtime.cancel_freeze(args.token, actor=args.actor, reason=args.reason)
                result = runtime.snapshot()
            else:
                expected = runtime.expected_triggers(datetime.fromisoformat(args.from_time), datetime.fromisoformat(args.to_time))
                result = expected if action == "expected" else runtime.compare_triggers(expected)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        except (runtime.ScheduleAuthorityError, ValueError) as exc:
            print(json.dumps({"status": "refused", "reason": str(exc)}, ensure_ascii=False))
            return 2

    if args.action in DISPATCH_OWNER_MOVED:
        # A prompt stub, not a silent alias: the old names spoke of "switching owners", the workflow is now paused/resumed.
        print(f"❌ `ats dispatch {args.action}` 已改名：{DISPATCH_OWNER_MOVED[args.action]}")
        return 2

    if args.action in {"pause", "resume", "owner-log"}:
        from ..workflow import schedule_runtime as runtime

        try:
            if args.action == "owner-log":
                result = runtime.owner_log(args.workflow or None)
            else:
                # The stored owner value for a paused workflow is still "legacy" (a deliberate, recorded shortcut:
                # renaming it touches the stored rows and the dispatcher-runtime spec). Only the user-facing words changed.
                result = runtime.owner_switch(
                    args.workflow, to_owner="dispatcher" if args.action == "resume" else "legacy",
                    actor=args.actor, reason=args.reason, approval=args.approval,
                    void_unfinished=args.void_unfinished)
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            return 0
        except runtime.ScheduleAuthorityError as exc:
            print(json.dumps({"status": "refused", "reason": str(exc)}, ensure_ascii=False))
            return 2

    if args.action in DISPATCH_RETIRED:
        print(f"❌ `ats dispatch {args.action}` 已退役：{DISPATCH_RETIRED[args.action]}")
        return 2

    action = args.action
    if action == "ledger":
        from ..workflow import dispatch_claims as claims

        print(json.dumps(list(claims.claims(path=args.state or None)), ensure_ascii=False, indent=2))
        return 0

    parser.error(f"unknown action {action!r}")


_WORKFLOW_RUN = "ats workflow run --task {task} --scope-kind {kind} --scope-id {id}"
RESEARCH_MANUAL_MOVED = {
    ("macro", "review"): _WORKFLOW_RUN.format(task="macro-review", kind="portfolio", id="portfolio"),
    ("sector", "review"): _WORKFLOW_RUN.format(task="sector-review", kind="sector", id="<行业，如 ai_hardware>")
                          + "（会先补跑层级评审）",
    ("sector", "layer"): _WORKFLOW_RUN.format(task="layer-review", kind="layer", id="<层键>")
                         + " --inputs-json '{\"layer-review\": {\"sector\": \"<行业>\"}}'",
    ("technical", "review"): _WORKFLOW_RUN.format(task="technical-review", kind="portfolio", id="portfolio"),
    ("analyst", "information"): _WORKFLOW_RUN.format(task="information-brief", kind="portfolio", id="pead-targets"),
    ("analyst", "fundamental"): _WORKFLOW_RUN.format(task="fundamental-routine", kind="entity", id="<代码>")
                                + "（财报后的事件评审由日历在材料准入后唤醒，补跑用 ats workflow retry）",
    ("pead", "prep"): _WORKFLOW_RUN.format(task="fundamental-routine", kind="entity", id="<代码>")
                      + " --inputs-json '{\"fundamental-routine\": {\"refresh_baseline\": true}}'（财报前建基线）",
    ("pead", "score"): "财报后的事件评审由日历在财报发布且材料准入后自动唤醒；补跑用 ats workflow retry --trigger-key <键>",
    ("pead", "monitor"): _WORKFLOW_RUN.format(task="information-brief", kind="entity", id="<代码>"),
    ("pead", "watch"): _WORKFLOW_RUN.format(task="information-brief", kind="portfolio", id="pead-targets"),
    ("pead", "research"): _WORKFLOW_RUN.format(task="information-brief", kind="portfolio", id="pead-targets"),
    # Evidence collection belongs to the data refresh controller, not to a command someone has to remember to run.
    ("evidence", "observe"): "ats data evidence-observe --entity <代码>（数据刷新任务 chain_evidence_daily 每天跑）",
    ("evidence", "collect"): "第三方序列由数据刷新任务采集（chain_l1_governed_evidence_daily、chain_regional_*）；"
                             "手动补采用 ats data chain-source-collect --source <来源 id>（须在受管队列内）",
    ("evidence", "articles"): "ats data article-ingest --source <来源 id>",
    ("sector", "crosssection"): "截面已并入行业评审：用 " + _WORKFLOW_RUN.format(task="sector-review", kind="sector",
                                id="<行业>") + "，结果在层级报告第三节",
}


DISPATCH_OWNER_MOVED = {
    "owner-rollback": "停用一个工作流用 `ats dispatch pause --workflow <id> --actor <谁> --reason <为什么>`（不需要批准）。",
    "owner-switch": "恢复一个工作流用 `ats dispatch resume --workflow <id> --actor <谁> --approval <批准原文> --reason <为什么>`。",
}


# Migration-period commands removed by switch-trade r5 group 8. One release keeps a stub that says so
# instead of argparse's "invalid choice"; usage samples are archived in docs/archive/R5C_MIGRATION_COMMANDS.md.
RETIRED_MIGRATION_COMMANDS = {
    "cutover": "切换控制平面的批次登记与报告校验已随交易路径切换完成删除（r5 8.4）",
    "shadow": "影子比较、验收与影子账本工具已删除（r5 8.4）",
    "drill": "切换边界演练已删除（r5 8.4）；实盘路由演练用 `ats live drill` / `ats live rollback-drill`",
    "read": "研究侧读切流的批次执行与部署授权工具已删除（r5 8.5）",
    "batch": "切流批次清单工具已删除（r5 8.5）",
}


# The schedule ownership / rollback-dedup reports read the migration-period switch ledger (schedule_cutover), retired in
# r5 8.6. `ledger` stays until the claims ledger itself goes (8.8); pause/resume/owner-log and runtime-* are kept.
DISPATCH_RETIRED = {
    action: "切换期调度所有权与回滚去重报告已删除（r5 8.6）；停用/恢复用 pause/resume，覆盖核验用 runtime-expected/runtime-compare"
    for action in ("ownership", "executed", "record-execution", "rollback-plan", "rollback-history")
}


CONSUMER_CUTOVER_MOVED = {
    "status": "研究分析师的读路由已固定为 target，不再提供状态查看；数据产品健康用 `ats data health --consumer <id>`。",
    "health": "改用 `ats data health --consumer <id>`（不带 --consumer 时是数据平台的整体健康）。",
    "log": "改用 `ats data health-log [--consumer <id>]`。",
}


def consumer_cutover_command(args, *, parser) -> int:
    """Research consumers' read routes and data-product health. Only `restore` changes a route, and only
    toward the new one."""
    from ..workflow import consumer_cutover as cc

    db = args.db or None
    action = args.action
    if action in CONSUMER_CUTOVER_MOVED:
        # A prompt stub, not a silent alias: the command named a migration step, the replacement is health.
        print(f"❌ `ats consumer-cutover {action}` 已退役：{CONSUMER_CUTOVER_MOVED[action]}")
        return 2
    try:
        if not args.consumer_id:
            parser.error(f"consumer-cutover {action} requires --consumer-id")
        result = cc.restore(args.consumer_id, actor=args.actor, approval=args.approval,
                            reason=args.reason, path=db)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0
    except (cc.ConsumerCutoverError, ValueError) as exc:
        print(f"❌ {exc}")
        return 1


def workflow_command(args, *, parser) -> int:
    """Phase E task execution and read-only run/trigger ledger inspection."""
    from ..workflow.store import WorkflowStore

    store = WorkflowStore()
    workflow_id = args.workflow_id
    if args.action == "run" and not workflow_id:
        requested = tuple(dict.fromkeys(args.task))
        workflow_id = requested[0] if len(requested) == 1 else "analysis-bundle"
    if workflow_id and workflow_id != "analysis-bundle":
        from ..workflow.ownership import load_workflow_owners, workflow_store_for_owner

        if workflow_id not in load_workflow_owners().get("workflows", {}):
            parser.error(f"workflow ID {workflow_id!r} has no declared owner")
        store = workflow_store_for_owner(workflow_id)
    if args.action == "triggers":
        rows = store.list_triggers(workflow_id=args.workflow_id, event_id=args.event_id,
                                   schedule_id=args.schedule_id,
                                   scheduled_from=args.scheduled_from,
                                   scheduled_to=args.scheduled_to,
                                   status=args.status, limit=args.limit)
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if args.action == "runs":
        print(json.dumps(store.list_runs(status=args.status, limit=args.limit),
                         ensure_ascii=False, indent=2))
        return 0
    if args.action in {"retry", "history"}:
        if not args.trigger_key:
            parser.error(f"workflow {args.action} requires --trigger-key")
        row = next((item for item in store.list_triggers(limit=10_000)
                    if item["trigger_key"] == args.trigger_key), None)
        if row is None:
            parser.error(f"trigger {args.trigger_key!r} was not found")
        if args.action == "history":
            run = store.get_run(row.get("run_id", "")) if row.get("run_id") else None
            print(json.dumps({"trigger": row,
                              "trigger_history": store.trigger_history(args.trigger_key),
                              "workflow_run": run,
                              "workflow_run_history": store.run_history(row["run_id"])
                              if row.get("run_id") else []},
                             ensure_ascii=False, indent=2, default=str))
            return 0
        if not args.actor or not args.reason:
            parser.error("workflow retry requires --actor and --reason")
        if not store.compensate_trigger(args.trigger_key, actor=args.actor, reason=args.reason):
            parser.error("only a failed/incomplete trigger can be explicitly compensated")
        from ..agent.task_projection import ProjectionScope
        from ..workflow.dispatcher import Dispatcher, default_data_vintage
        from ..workflow.planning import build_plan
        from ..workflow.run_contracts import TriggerContext
        from ..workflow.triggers import TriggerService

        stored_request = row.get("request", {})
        frozen_context = TriggerContext.model_validate(stored_request.get("trigger", {}))
        frozen_context.requested_at = row["created_at"]
        body = stored_request.get("request", {})
        run_id = row.get("run_id", "")
        scope = ProjectionScope.model_validate(body["scope"])

        def plan_factory(context, request, fixed_run_id):
            return build_plan(
                requested_tasks=request["requested_tasks"], scope=scope,
                trigger=context, as_of=request.get("as_of", row["created_at"]),
                run_id=fixed_run_id, profile_id=request.get("profile_id", ""),
                enter_decision_cycle=bool(request.get("enter_decision_cycle", False)),
                task_inputs=request.get("task_inputs", {}),
                namespace=(store.get_run(run_id) or {}).get("plan", {}).get("tasks", [{}])[0]
                .get("instance_key", "dispatcher").split(":", 1)[0])

        result = TriggerService(store).dispatch(
            frozen_context, request=body, plan_factory=plan_factory,
            dispatcher=Dispatcher(workflow_store=store, vintage_resolver=default_data_vintage))
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result.get("status") in {"complete", "incomplete"} else 1
    if not args.task:
        parser.error("workflow run requires at least one --task")
    try:
        task_inputs = json.loads(args.inputs_json or "{}")
    except json.JSONDecodeError as exc:
        parser.error(f"--inputs-json is invalid JSON: {exc}")
    if not isinstance(task_inputs, dict):
        parser.error("--inputs-json must be a JSON object")

    from ..agent.task_projection import ProjectionScope
    from ..workflow.dispatcher import Dispatcher, default_data_vintage
    from ..workflow.planning import build_plan
    from ..workflow.run_contracts import TriggerContext
    from ..workflow.triggers import TriggerService, load_trigger_policies

    task_ids = tuple(dict.fromkeys(args.task))
    workflow_id = workflow_id or (task_ids[0] if len(task_ids) == 1 else "analysis-bundle")
    trigger = TriggerContext(
        kind=args.trigger_kind, workflow_id=workflow_id, trigger_id=args.trigger_id,
        schedule_id=args.schedule_id, scheduled_for=args.scheduled_for,
        event_id=args.event_id, event_version=args.event_version)
    trigger.validate_for_use()
    request = {
        "requested_tasks": list(task_ids),
        "scope": {"kind": args.scope_kind, "id": args.scope_id},
        "profile_id": args.decision_profile,
        "enter_decision_cycle": args.decision_cycle,
        "task_inputs": task_inputs,
    }
    if args.as_of:
        request["as_of"] = args.as_of

    def plan_factory(ctx, frozen_request, run_id):
        scope = ProjectionScope.model_validate(frozen_request["scope"])
        return build_plan(
            requested_tasks=frozen_request["requested_tasks"], scope=scope,
            trigger=ctx, as_of=frozen_request.get("as_of", ctx.requested_at),
            run_id=run_id, profile_id=frozen_request.get("profile_id", ""),
            enter_decision_cycle=bool(frozen_request.get("enter_decision_cycle", False)),
            task_inputs=frozen_request.get("task_inputs", {}))

    policy_map = load_trigger_policies()
    service = TriggerService(store, policy=policy_map.get(workflow_id,
                                                           policy_map["default"]))
    from ..workflow.consumer_health import gate as product_health_gate

    result = service.dispatch(trigger, request=request, plan_factory=plan_factory,
                              dispatcher=Dispatcher(workflow_store=store, vintage_resolver=default_data_vintage,
                                                    product_health=product_health_gate))
    from ..workflow.report_publishers import publish

    reports = publish(workflow_id, result, store_path=store.path)
    if reports:
        result = {**result, "reports": reports}
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("status") in {"complete", "incomplete", "running", "planned"} else 1


@scoped_read("chief")
def run_chief(
    *,
    execute: bool = True,
    dry_run: bool = True,
    channel: str = "cli",
    use_llm: bool = True,
    auto: bool = False,
    offline: bool = False,
    source: str = "chief",
    fundamental_mode: str = "routine",
    decision_profile: str = "ai_hardware",
    event_entities: list[str] | None = None,
) -> int:
    """One Chief decision run through the decision graph: assemble all artifacts
    -> decide -> risk gate -> persist -> Boss approval -> trade -> persist."""
    start_chief_cycle(execute=execute, dry_run=dry_run, channel=channel, use_llm=use_llm, auto=auto,
                      offline=offline, source=source, fundamental_mode=fundamental_mode,
                      decision_profile=decision_profile, event_entities=event_entities)
    return 0


def start_chief_cycle(
    *,
    execute: bool = True,
    dry_run: bool = True,
    channel: str = "cli",
    use_llm: bool = True,
    auto: bool = False,
    offline: bool = False,
    source: str = "chief",
    fundamental_mode: str = "routine",
    decision_profile: str = "ai_hardware",
    event_entities: list[str] | None = None,
) -> tuple[str, dict]:
    """Start one Chief cycle and return `(cycle_id, graph result)`; the scheduled `chief-daily` reads the result."""
    from ..graph.chief_state import ChiefDecisionState

    now = datetime.now(timezone.utc)
    if execute:
        from .approval_cli import supersede_pending

        supersede_pending(f"chief-{now:%Y%m%d-%H%M%S}", created_at=now.isoformat())
    state = ChiefDecisionState(
        cycle_id=f"chief-{now:%Y%m%d-%H%M%S}",
        as_of=now,
        source=source,
        fundamental_mode=fundamental_mode,
        event_entities=list(event_entities or []),
        decision_profile=decision_profile,
        dry_run=dry_run,
        use_llm=use_llm,
        use_broker=not offline,
        auto_approve=auto,
        execute=execute,
    )
    result = run_decision_graph(state, channel=channel)
    return state.cycle_id, result


def chief_show() -> int:
    from ..memory import get_store

    run = get_store().last_chief_run()
    if run is None:
        print("(no chief run yet — `ats chief run`)")
        return 0
    print(f"=== chief {run['cycle_id']} @ {run['as_of'][:16]} ===\n{run['manager_summary']}")
    for d in run["decisions"]:
        print(
            f"  {d['action']} {d['symbol']} ${d.get('notional_usd') or 0:,.0f} — "
            f"{(d.get('rationale') or '')[:70]}"
        )
    return 0


def chief_probe(*, offline: bool = False) -> int:
    from ..agents.chief import assemble

    ctx = assemble.build()
    print(f"=== chief context stats: {ctx.stats()} ===\n")
    print(ctx.as_context())
    return 0


def risk_report(*, write_report: bool = False, offline: bool = False) -> int:
    from ..memory import get_store
    from ..risk import assess as risk_assess, inputs as risk_inputs, report as risk_report_mod

    if offline:
        stored = get_store().latest_risk_review()
        if stored is None:
            print("❌ No stored risk review found — run once with TWS connected.")
            return 1
        age = stored.as_of.replace(tzinfo=None) if stored.as_of.tzinfo else stored.as_of
        from datetime import datetime, timezone

        age_days = (datetime.now(timezone.utc) - stored.as_of).days
        print(
            f"⚠️  offline mode — showing stored review ({age_days}d old, as of {stored.as_of:%Y-%m-%d})"
        )
        print(risk_report_mod.render(stored))
        return 0

    pf = risk_inputs.account()
    if pf is None:
        from ..config import get_config

        _port = get_config().app.broker.port or get_config().secrets.ibkr_port
        print(
            f"❌ IBKR unavailable — start TWS with API enabled (port {_port}). Use --offline to show stored review."
        )
        return 1
    risk_assess.enrich_beta(pf)
    risk_assess.enrich_options(pf)
    review = risk_assess.assess(pf)
    get_store().save_risk_review(review)
    print(risk_report_mod.render(review))
    if write_report:
        from ..config import load_macro_config

        try:
            out_dir = load_macro_config().output_dir
        except Exception:  # noqa: BLE001
            out_dir = ""
        path = risk_report_mod.write(review, out_dir)
        print(f"📝 {path}" if path else "(report dir unset — skipped)")
    return 0


def risk_daily() -> int:
    """风控日报: the book against the rules and the gate's recent verdicts, written as 风控日报-<日期>.md. Read-only."""
    from ..agents.risk_officer import daily

    out = daily.publish(push=False)
    if out.get("error"):
        print(f"❌ 风控日报失败：{out['error']}")
        return 1
    print(f"📝 {out['path']}" if out.get("path") else "(report dir unset — skipped)")
    print(f"需要你处理的事项：{out['alerts']} 条")
    return 0


def risk_memo() -> int:
    """Risk-officer narrative memo: deterministic 6-layer assess -> LLM memo -> Obsidian doc.
    Read-only: snapshots the portfolio, never submits orders."""
    from ..agents.risk_officer import report as memo_report, review as memo_review

    memo = memo_review.run()
    if memo is None:
        from ..config import get_config

        _port = get_config().app.broker.port or get_config().secrets.ibkr_port
        print(f"❌ IBKR unavailable — start TWS with API enabled (port {_port}).")
        return 1
    print(memo_report.render(memo))
    from ..config import load_macro_config

    try:
        out_dir = load_macro_config().output_dir
    except Exception:  # noqa: BLE001
        out_dir = ""
    path = memo_report.write(memo, out_dir)
    print(f"\n📝 {path}" if path else "\n(report dir unset — skipped)")
    return 0


def risk_check(symbol: str | None = None) -> int:
    """Dry-run the risk gate over stored decisions (shows block/clip without ordering)."""
    from ..memory import get_store
    from ..risk import checks as risk_checks
    from ..risk import inputs as risk_inputs
    from ..schemas.decision import TradeDecision

    rows = get_store().recent_decisions(symbol, limit=20)
    if not rows:
        print("(no stored decisions to check)")
        return 0
    seen, decisions = set(), []
    for r in rows:
        if r["symbol"] in seen:
            continue
        seen.add(r["symbol"])
        decisions.append(
            TradeDecision(
                symbol=r["symbol"],
                action=r["action"],
                notional_usd=r.get("notional_usd"),
                limit_price=r.get("limit_price"),
                rationale=r.get("rationale") or "",
            )
        )
    pf = risk_inputs.account()
    # The same review the Chief's risk gate runs. Without a readable account it fails closed and says so;
    # the retired adapter skipped every check instead (switch-trade r5 task 7.5).
    result = risk_checks.review_revision(decisions, pf)
    passed = sum(1 for ov in result.order_verdicts if ov.verdict == "approved")
    print(f"=== Risk check: {len(decisions)} decisions → {passed} pass · verdict {result.verdict} ===")
    for ov in result.order_verdicts:
        mark = "✅" if ov.verdict == "approved" else "⛔"
        reasons = "; ".join(ov.reasons)
        print(f"  {mark} {ov.symbol:6} {ov.action:6} {reasons}".rstrip())
    for n in result.notes:
        print(f"  {n}")
    return 0


def trader_portfolio(*, offline: bool = False) -> int:
    from ..trader import portfolio as tp

    pf = tp.snapshot()
    if pf is None:
        if offline:
            # Fall back to most recent stored performance snapshot for display
            from ..memory import get_store
            from datetime import datetime, timezone

            ph = get_store().performance_history(limit=1)
            if ph:
                r = ph[0]
                age_days = (datetime.now(timezone.utc) - r.as_of).days
                print(
                    f"⚠️  offline mode — showing stored snapshot ({age_days}d old, as of {r.as_of:%Y-%m-%d})"
                )
                print(f"=== Portfolio {r.account_id} @ {r.as_of:%Y-%m-%d} (stored) ===")
                print(
                    f"NetLiq ${r.net_liquidation:,.0f} · dailyP&L ${r.daily_pnl:,.0f} "
                    f"· cumP&L ${r.cumulative_pnl:,.0f} · positions {r.num_positions}"
                )
                return 0
            print("❌ No stored snapshot found — connect TWS and run `ats clerk run`.")
            return 1
        from ..config import get_config

        _port = get_config().app.broker.port or get_config().secrets.ibkr_port
        print(
            f"❌ IBKR unavailable — start TWS/Gateway with API enabled (port {_port}). Use --offline for stored data."
        )
        return 1
    print(f"=== Portfolio {pf.account_id} @ {pf.as_of:%Y-%m-%d %H:%M} ===")
    print(
        f"NetLiq ${pf.net_liquidation:,.0f} · cash ${pf.cash:,.0f} · leverage {pf.leverage:.2f}x "
        f"· dailyP&L ${pf.daily_pnl:,.0f} · realized ${pf.realized_pnl:,.0f}"
    )
    if not pf.positions:
        print("(no open positions)")
    for p in pf.positions:
        print(
            f"  {p.symbol:6} {p.qty:+.0f} @ {p.avg_cost:.2f}  mv=${p.market_value:,.0f} "
            f"w={p.weight * 100:.1f}% uPnL=${p.unrealized_pnl:,.0f}"
        )
    return 0


def trader_perf(days: int = 30, *, write_report: bool = False) -> int:
    from ..trader import performance as tperf

    rep = tperf.report(days)
    a = rep["analytics"]
    print(f"=== Performance (last {a['window_days']} snapshots) ===")
    print(
        f"NetLiq ${a['start_nav'] or 0:,.0f} → ${a['end_nav'] or 0:,.0f} · "
        f"return {a['total_return_pct']}% · cumP&L ${a['cumulative_pnl'] or 0:,.0f}"
    )
    print(
        f"maxDD {a['max_drawdown_pct']}% · winRate {a['win_rate']} · "
        f"profitFactor {a['profit_factor']} · closedTrades {a['closed_trades']}"
    )
    for name, b in a["benchmarks"].items():
        print(f"  vs {name}: {b['return_pct']}% (alpha {b['alpha_pct']}%)")
    if write_report:
        _write_perf_report(rep)
    return 0


def _write_perf_report(rep: dict) -> None:
    from ..config import load_macro_config

    try:
        out_dir = load_macro_config().output_dir
    except Exception:  # noqa: BLE001
        out_dir = ""
    if not out_dir:
        print("(report dir unset — skipped)")
        return
    from datetime import datetime, timezone
    from pathlib import Path

    a = rep["analytics"]
    lines = [
        f"# 🤖 组合绩效 — {datetime.now(timezone.utc):%Y-%m-%d}",
        "",
        f"- NetLiq: ${a['start_nav'] or 0:,.0f} → ${a['end_nav'] or 0:,.0f}",
        f"- 收益率: {a['total_return_pct']}% · 累计P&L: ${a['cumulative_pnl'] or 0:,.0f}",
        f"- 最大回撤: {a['max_drawdown_pct']}%",
        f"- 胜率: {a['win_rate']} · 盈亏比: {a['profit_factor']} · 平仓交易: {a['closed_trades']}",
    ]
    for name, b in a["benchmarks"].items():
        lines.append(f"- vs {name}: {b['return_pct']}% (alpha {b['alpha_pct']}%)")
    p = Path(out_dir) / f"组合绩效-{datetime.now(timezone.utc):%Y-%m-%d}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    print(f"📝 {p}")


def trader_orders() -> int:
    from ..broker import IBKRBroker, IBKRUnavailable

    try:
        oo = IBKRBroker().open_orders()
    except IBKRUnavailable as exc:
        print(f"❌ IBKR unavailable: {exc}")
        return 1
    if not oo:
        print("(no open orders)")
        return 0
    print("=== Open orders ===")
    for o in oo:
        print(
            f"  #{o['order_id']} {o['action']} {o['symbol']} x{o['qty']:.0f} {o['type']} [{o['status']}]"
        )
    return 0


def trader_cancel(symbol: str | None = None) -> int:
    from ..broker import IBKRBroker, IBKRUnavailable

    try:
        cancelled = IBKRBroker().cancel_all(symbol)
    except IBKRUnavailable as exc:
        print(f"❌ IBKR unavailable: {exc}")
        return 1
    print(
        f"cancelled {len(cancelled)} order(s): {cancelled}"
        if cancelled
        else "(no open orders to cancel)"
    )
    return 0


def trader_fills(symbol: str | None = None) -> int:
    from ..memory import get_store

    rows = get_store().recent_fills(symbol, limit=30)
    if not rows:
        print("(no fills recorded yet)")
        return 0
    print(f"=== Fills{' ' + symbol if symbol else ''} ===")
    for f in rows:
        rp = f"realized ${f['realized_pnl']:,.0f}" if f.get("realized_pnl") is not None else ""
        print(
            f"  {f['time'][:16]} {f['side']} {f['symbol']} {f['shares']:.0f}@{f['price']:.2f} {rp}"
        )
    return 0


def trader_execute(
    symbol: str | None = None, *, channel: str = "cli", dry_run: bool = False
) -> int:
    from ..memory import get_store
    from ..schemas.decision import TradeDecision
    from ..trader import execute as texec

    rows = get_store().recent_decisions(symbol, limit=20)
    if not rows:
        print("(no stored decisions to execute — use `ats trader buy/sell` for manual orders)")
        return 0
    seen, decisions = set(), []
    for r in rows:  # newest first; one per symbol
        if r["symbol"] in seen:
            continue
        seen.add(r["symbol"])
        decisions.append(
            TradeDecision(
                symbol=r["symbol"],
                action=r["action"],
                notional_usd=r.get("notional_usd"),
                limit_price=r.get("limit_price"),
                conviction=r.get("conviction") or 0.0,
                rationale=r.get("rationale") or "",
            )
        )
    texec.execute(decisions, source="stored-decisions", channel=channel, dry_run=dry_run)
    return 0


def trader_manual(
    action: str,
    symbol: str,
    qty: float,
    *,
    limit: float | None = None,
    channel: str = "cli",
    dry_run: bool = False,
) -> int:
    from ..trader import execute as texec

    texec.manual(
        symbol,
        action,
        qty,
        order_type="limit" if limit else "market",
        limit_price=limit,
        channel=channel,
        dry_run=dry_run,
    )
    return 0


def _print_quadrant(review) -> None:
    """Show the deterministic layer — this is what `--no-llm` exists to verify."""
    if not review.axis_inputs and review.quadrant == "transition":
        return
    print(f"   {review.quadrant_line()}")
    if review.quadrant_reason:
        print(f"      理由: {review.quadrant_reason}")
    for a in review.axis_inputs:
        val = "n/a" if a.value is None else a.value
        print(f"      · {a.label or a.key}: {val} (判据 {a.threshold}) → {a.score:+.2f}")
    dec = review.decomposition
    if dec is not None and dec.d_real_bp is not None:
        print(
            f"      · 利率分解 {dec.window_days}d: Δ名义 {dec.d_nominal_bp:+.0f}bp"
            f" = Δ实际 {dec.d_real_bp:+.0f} + Δ通胀补偿 {dec.d_breakeven_bp:+.0f}"
            f" → {dec.classification}"
        )
        if dec.real_yield_cause:
            print(f"      · 成因: {dec.real_yield_cause}")
    for s in review.shock_vs_trend:
        print(f"      · {s}")
    stale = [r.key for r in review.indicators if r.stale]
    if stale:
        print(f"      · ⚠️ 数据过旧/缺失: {', '.join(stale)}")


@scoped_read("technical")
def run_technical_review(
    name: str = "technical", *, live_data: bool = True, write_report: bool = True
) -> int:
    """Deterministic technical readings (no LLM). Advisory input to the Chief."""
    from ..agents.technical import review as tech_review

    r = tech_review.run(name, live_data=live_data, write_report=write_report)
    print(f"📐 technical {name} — 策略 {r.strategy} · {r.summary_line()}")
    for note in r.notes:
        print(f"   ⚠️  {note}")
    live = [x for x in r.readings if not x.stale]
    for x in sorted(live, key=lambda v: (v.target_exposure, v.symbol)):
        mark = " ←变化" if x.changed else ""
        print(f"   {x.one_line()}{mark}")
    stale = [x.symbol for x in r.readings if x.stale]
    if stale:
        print(f"   （未评估: {', '.join(stale)}）")
    return 0


def _technical_show_all(review, name: str) -> None:
    """Every symbol that has a reading, then the ones that cannot be computed, marked as missing data.

    `chief_block` lists only what needs Chief's attention (below full exposure, or changed); this is the full list.
    """
    ok = [r for r in review.readings if not r.stale]
    stale = [r for r in review.readings if r.stale]
    print(f"\n全部可计算标的（{len(ok)} 只）：")
    print(f"  {'代码':<8}{'建议敞口':>8}{'上次':>8}{'评分':>6}{'收盘':>11}{'SMA200':>11}  状态")
    for r in sorted(ok, key=lambda x: x.symbol):
        prev = "-" if r.prev_exposure is None else f"{r.prev_exposure:.0%}"
        flags = "".join(["恐慌层 " if r.panic_fired else "", "熊市层 " if r.bear_fired else "",
                         "较上次有变化" if r.changed else ""]).strip() or "-"
        close = "-" if r.close is None else f"{r.close:.2f}"
        sma = "-" if r.sma200 is None else f"{r.sma200:.2f}"
        print(f"  {r.symbol:<8}{r.target_exposure:>8.0%}{prev:>8}{r.score:>6}{close:>11}{sma:>11}  {flags}")
    missing = {r.symbol: r.note for r in stale}
    missing.update({sym: "取不到行情" for sym in review.skipped if sym not in missing})
    if missing:
        print(f"\n数据缺失、未出读数（{len(missing)} 只）：")
        for sym in sorted(missing):
            print(f"  {sym:<8}{missing[sym]}")


def technical_show(name: str = "technical") -> int:
    from ..memory import get_store

    store = get_store()
    latest = store.latest_technical_review(name)
    if latest is None:
        print(f"(no technical review for {name} yet — run `ats technical review`)")
        return 0
    print(f"=== technical {name} @ {latest.as_of:%Y-%m-%d} · 策略 {latest.strategy} ===")
    print(latest.chief_block(4000))
    _technical_show_all(latest, name)
    print("\nHistory:")
    for row in store.recent_technical_reviews(name):
        print(f"  {row['as_of'][:10]}  {row['summary'][:80]}")
    return 0


def technical_probe(name: str = "technical", *, live_data: bool = True) -> int:
    """Resolve the universe and compute readings WITHOUT persisting or reporting."""
    from ..agents.technical import review as tech_review

    r = tech_review.run(name, live_data=live_data, persist=False, write_report=False)
    print(
        f"=== technical probe: {len(r.readings)} readings, "
        f"strategy={r.strategy}, fingerprint={r.fingerprint} ==="
    )
    for note in r.notes:
        print(f"  note: {note}")
    print()
    print(r.chief_block(4000))
    return 0


@scoped_read("macro")
@scheduled_cli("macro-review", scope_id="")
def run_macro_review(
    name: str = "macro", *, use_llm: bool = True, live_data: bool = True, write_report: bool = True
):
    """One weekly macro strategist review: regime + rate path + sector tilts."""
    from ..agents.macro import report, review as macro_review
    from ..config import load_macro_config

    from datetime import datetime, timezone

    started = datetime.now(timezone.utc)
    review = macro_review.run(name, use_llm=use_llm, live_data=live_data)
    # run() returns the PRIOR stored review when the LLM call fails, so a failed
    # run is otherwise indistinguishable from a good one — and writing a report
    # for it would overwrite that older day's file under its own date.
    stale = use_llm and review.as_of < started
    if stale:
        print(
            f"⚠️  macro {name}: LLM 失败，以下为 {review.as_of:%Y-%m-%d %H:%M} 的旧评审"
            f"（未写报告、未更新存档）"
        )
    print(f"🌐 macro {name} — {review.regime}")
    _print_quadrant(review)
    if review.rate_path:
        print(f"   利率路径: {review.rate_path}")
    for t in review.sector_tilts:
        print(f"   {t.stance} {t.sector}: {t.rationale[:80]}")
    if review.asset_implications:
        print(f"   资产含义: {review.asset_implications}")
    if write_report and use_llm and review.sector_tilts and not stale:
        path = report.write(review, load_macro_config(name))
        print(f"   📝 {path}" if path else "   (report dir unset — skipped)")
    return review


def macro_show(name: str = "macro") -> int:
    from ..memory import get_store

    store = get_store()
    latest = store.latest_macro_review(name)
    if latest is None:
        print(f"(no macro review for {name} yet — run `ats macro review`)")
        return 0
    print(f"=== macro review {name} @ {latest.as_of:%Y-%m-%d} ===")
    print(f"Regime: {latest.regime}")
    _print_quadrant(latest)
    if latest.falsifier:
        print(f"证伪条件: {latest.falsifier}")
    print(f"利率路径: {latest.rate_path}\n\n{latest.summary}\n")
    for t in latest.sector_tilts:
        print(f"  {t.stance} {t.sector}")
    print("\nHistory:")
    for r in store.recent_macro_reviews(name):
        print(f"  {r['as_of'][:10]}  {r['regime'][:70]}")
    return 0


def macro_probe(name: str = "macro", *, live_data: bool = True) -> int:
    from ..agents.macro import assemble
    from ..config import load_macro_config

    mc = assemble.build(load_macro_config(name), live_data=live_data)
    print(f"=== macro context stats: {mc.stats()} ===\n")
    print(mc.as_context())
    return 0


@scoped_read("layer")
def run_layer_review(
    name: str = "ai_hardware",
    layer_key: str = "all",
    *,
    use_llm: bool = True,
    live_data: bool = True,
) -> int:
    """层级评审：一层或全部层的配置结论（超配/标配/低配/清仓）+ 层内选股。"""
    from ..agents.layer import layer_review
    from ..agents.sector import cross_section
    from ..config import load_sector_config
    from ..memory import get_store
    from ..schemas.sector import allocation_for_status

    cfg = load_sector_config(name)
    layers = cfg.layers if layer_key in ("", "all") else cfg.layers_by_key(layer_key)
    if not layers:
        print(f"❌ 层 {layer_key!r} 不在 {name}（可用：{', '.join(l.key for l in cfg.layers)}）")
        return 2
    prior = get_store().latest_sector_review(name)

    for layer in layers:
        prior_v = next(
            (
                prior.verdict_for(k)
                for k in [layer.key, *layer.legacy_keys]
                if prior and prior.verdict_for(k)
            ),
            None,
        )
        basket = None
        if live_data:
            try:
                _, basket = cross_section.run_layer(
                    name, layer.key, persist=False, structure=use_llm
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  （{layer.key} 截面取数失败：{exc}）")
        v, ok = layer_review.run(cfg, layer, basket=basket, prior=prior_v, use_llm=use_llm,
                                 store=get_store())
        if v is None:
            # Phase D: a failed layer is a registered gap — nothing is carried forward
            # or defaulted into a fake verdict.
            print(f"\n=== {layer.label}  [{layer.key}] ===")
            print("  ⚠️ 本轮评审失败：该层登记为缺失，未产出状态判断与投影")
            continue
        # 单层视图给的是**未经组上限约束**的数：组上限要等同组其余层都评完才算得出来。
        budget = cross_section.budget_for(
            layer, allocation_for_status(v.layer_status) if ok else None)
        group = next((g for g in cfg.layer_groups if layer.key in g.layers), None)
        flags = []
        if not v.has_claims:
            flags.append("无命题(配置缺口)")
        if not v.cross_section_applicable:
            flags.append("截面不适用")
        print(f"\n=== {layer.label}  [{layer.key}] ===")
        call = allocation_for_status(v.layer_status)
        print(
            f"  状态 {v.layer_status}（行业侧映射 {call}）· 信心 {v.confidence:.2f}"
            f" · 预算 {budget:.1%} NAV"
            f" · 周期 {v.cycle_position or '—'}" + (f"\n  ⚠️ {'；'.join(flags)}" if flags else "")
        )
        if group:
            print(
                f"  （本层属跨层组 {group.key}（上限 {group.weight_cap:.0%}，成员 "
                f"{'+'.join(group.layers)}）—— 整轮评审时预算可能被组上限按比例压低）"
            )
        for a in v.claim_attributions:
            print(f"    · {a}")
        for trg in v.reversal_triggers:
            print(f"    反转看: {trg}")
        for c in v.name_calls:
            mark = " (仅自述)" if c.self_reported_only else ""
            print(f"    {c.symbol:<10}{c.stance}{mark}  {c.rationale[:70]}")
    return 0


@scoped_read("sector")
@scheduled_cli("sector-review", kind="sector", argument="name")
def run_sector_review(
    name: str = "ai_hardware",
    *,
    use_llm: bool = True,
    live_data: bool = True,
    write_report: bool = True,
):
    """One weekly sector review: L1-L6 assessment + company calls."""
    from ..agents.sector import report, review as sector_review
    from ..config import load_sector_config

    review = sector_review.run(name, use_llm=use_llm, live_data=live_data)
    print(f"🏭 sector {name} — {review.regime}")
    for a in review.layers:
        print(f"   {a.label}: 景气 {a.boom_score:.0f} [{a.signal}] {a.supply_demand}")
    if review.rotation_advice:
        print(f"   轮动: {review.rotation_advice}")
    for c in review.company_calls:
        print(f"   {c.stance} {c.symbol} ({c.conviction:.2f}): {c.rationale[:80]}")
    if write_report and use_llm and review.company_calls:
        path = report.write(review, load_sector_config(name))
        print(f"   📝 {path}" if path else "   (report dir unset — skipped)")
    return review


@scoped_read("sector")
def run_sector_html(name: str = "ai_hardware", *, date: str = "") -> int:
    """Offline rebuild of the viz dashboard from what a past review already produced.

    Never calls an LLM, never touches yfinance/finnhub, never re-runs the corroboration
    engine — reads the ledger and re-renders (mirrors `runtime/digest.py`'s "decoupled
    from the workflows that produced the data" discipline). `date` picks WHICH stored
    review to rebuild (default: the latest); the html always reflects that review's own
    `as_of` day, since a review's `baskets`/`layer_verdicts` and that day's
    `claim_assessments` rows must come from the same run to render one consistent story.
    """
    from ..agents.sector import viz
    from ..config import load_sector_config
    from ..memory import get_store

    cfg = load_sector_config(name)
    store = get_store()
    from ..agents.sector.read_model import allocation, governed, write_html

    if governed():
        row = allocation(store, name, date=date)
        path = write_html(row, cfg.output_dir)
        if path:
            print(f"📊 {path}")
            return 0
        print("（output_dir 未配置或不存在）")
        return 1
    review = None
    if date:
        for r in store.sector_review_history(name, limit=60):
            if r.as_of.date().isoformat() == date:
                review = r
                break
        if review is None:
            print(
                f"（{name} 在 {date} 没有存档的 sector review —— "
                f"`ats sector review {name}` 跑过的日子才有）"
            )
            return 1
    else:
        review = store.latest_sector_review(name)
        if review is None:
            print(f"（{name} 还没有任何 sector review —— 先跑 `ats sector review {name}`）")
            return 1

    as_of_date = review.as_of.date().isoformat()
    layer_keys = [ly.key for ly in cfg.layers]
    assessments_by_layer = store.claim_assessments_on(layer_keys, as_of_date)
    bundle = viz.build_bundle(cfg, review, assessments_by_layer=assessments_by_layer)
    path = viz.write_html(bundle, cfg.output_dir)
    if path:
        print(f"📊 {path}")
        return 0
    print("（output_dir 未配置或不存在 —— 看 config/sectors/<name>.yaml 的 output_dir）")
    return 1


def _evidence_sources(store, *, entity: str = "") -> int:
    """Primary-source coverage: who the roster declares vs whose documents we hold.

    A declared witness we can never fetch is worse than an undeclared one — it sits in
    `未发声` forever and quietly inflates the coverage denominator, making the evidence
    look thinner than it is. This is the report that says which ones to demote.
    """
    from ..config import canonical_entity, entity_meta, load_sector_config
    from ..data import source_cache

    cfg = load_sector_config(entity or "ai_hardware")
    root = source_cache.root()
    print(f"信息源目录：{root or '(未配置 docs_root)'}\n")

    for layer in cfg.layers:
        if not layer.witness_roster:
            continue
        print(f"=== {layer.label} ({layer.key}) ===")
        for role in ("peer", "upstream", "downstream", "reference"):
            names = layer.witness_roster.get(role, [])
            if not names:
                continue
            print(f"  [{role}]")
            for raw in names:
                sym = canonical_entity(raw)
                docs = source_cache.inventory(sym)
                bad = [d for d in store.documents(entity=sym, ok_only=False) if not d["ok"]]
                meta = entity_meta(sym)
                if docs:
                    newest = max(docs, key=lambda d: d.fetched_at or "")
                    mark, detail = (
                        "✅",
                        (
                            f"{len(docs)} 份 · 最新 {newest.period or '期间未知'}"
                            f" · {len(newest.text) // 1000}k 字符"
                        ),
                    )
                elif bad:
                    mark, detail = "⚠️ ", f"抓到过但被闸拦下：{bad[0]['note']}"
                else:
                    mark, detail = "❌", "无原始文件"
                market = meta.get("market", "?")
                print(f"    {mark} {sym:<12}{meta.get('name', ''):<34}{market:<6}{detail}")
        print()
    return 0


def _evidence_probe(symbol: str, runs: int = 5) -> int:
    """Run the SAME extraction N times and print the spread.

    Exists because a single run tells you nothing about this failure mode: on
    2026-08-07 the same document and prompt returned [0, 33, 36] observations through
    OpenRouter, so any one-shot check would have "confirmed" whichever answer it drew.
    Variance is the measurement.
    """
    from ..agents.evidence import observer
    from ..config import get_config
    from ..data import source_cache

    sym = symbol.upper()
    docs = source_cache.inventory(sym)
    if not docs:
        print(f"❌ {sym} 没有缓存文档 —— 先跑 `ats evidence observe {sym}`")
        return 1
    doc = max(docs, key=lambda d: len(d.text))
    rc = get_config().app.llm.for_role("evidence_observer")
    print(f"{sym} · {doc.path.name} · {len(doc.text)} 字符")
    print(f"provider={rc.provider} model={rc.model} max_tokens={rc.max_tokens}\n")

    counts = []
    for i in range(1, runs + 1):
        obs, failure = observer.extract(sym, f"probe-{i}", doc.text)
        counts.append(len(obs))
        print(f"  第 {i} 次 → {len(obs):>3} 条" + (f"  failure={failure}" if failure else ""))
    lo, hi = min(counts), max(counts)
    print(f"\n结果 {counts} · 极差 {hi - lo} · 空结果 {counts.count(0)}/{runs}")
    print("稳定" if lo > 0 and hi - lo <= max(3, hi * 0.2) else "⚠️ 不稳定")
    return 0


def run_evidence(
    action: str,
    symbol: str | None = None,
    *,
    file: str = "",
    entity: str = "",
    limit: int = 30,
    accept: bool = False,
    reviewer: str = "",
    note: str = "",
    sector: str = "",
    layer: str = "",
    periods: list[str] | None = None,
    as_of: str = "",
    output_format: str = "markdown",
    chart_dir: str = "",
    output: str = "",
) -> int:
    """Chain evidence: observe one company's latest print, or inspect what's stored.

    Read-only with respect to trading — this path can never place an order.
    """
    if action in {"layer", "ai-production", "ai-commercialization"}:
        if not sector or not layer:
            print(
                json.dumps(
                    {
                        "status": "invalid_scope",
                        "requested_scope": {"sector": sector or None, "layer": layer or None},
                        "reason": "按层 Evidence 运行必须显式提供 --sector 和 --layer。",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2
        from ..agents.evidence import (
            COMMERCIALIZATION_CLAIM_ID,
            PRODUCTION_CLAIM_ID,
            analyst_context_paths,
            run_registered_layer_observers,
            write_layer_evidence_outputs,
        )
        from ..config import load_sector_config

        try:
            cfg = load_sector_config(sector)
        except FileNotFoundError as exc:
            print(
                json.dumps(
                    {
                        "status": "invalid_sector",
                        "requested_scope": {"sector": sector, "layer": layer},
                        "reason": str(exc),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2
        layer_cfg = cfg.layer_by_key(layer)
        if layer_cfg is None:
            print(
                json.dumps(
                    {
                        "status": "invalid_layer",
                        "requested_scope": {"sector": sector, "layer": layer},
                        "reason": "The requested layer is not present in the sector configuration.",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 2
        cutoff = datetime.fromisoformat(as_of) if as_of else None
        output_path: Path | None = None
        resolved_chart_dir = chart_dir
        if output_format == "markdown":
            if output:
                output_path = Path(output)
            elif cfg.output_dir:
                stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                output_path = (
                    Path(cfg.output_dir) / f"Evidence-{cfg.name}-{layer_cfg.key}-{stamp}.md"
                )
            else:
                print(
                    json.dumps(
                        {
                            "status": "output_path_required",
                            "requested_scope": {"sector": sector, "layer": layer},
                            "reason": "sector 配置未定义 output_dir；请用 --output 指定 Markdown 路径。",
                        },
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 2
            if not resolved_chart_dir:
                resolved_chart_dir = str(output_path.parent / f"{output_path.stem}-assets")
        claim_ids = [PRODUCTION_CLAIM_ID] if action == "ai-production" else None
        result = run_registered_layer_observers(
            sector=cfg.name,
            sector_label=cfg.label,
            layer=layer_cfg.key,
            layer_label=layer_cfg.label,
            observer_refs=layer_cfg.evidence_observers,
            periods=periods or None,
            as_of=cutoff,
            chart_dir=resolved_chart_dir,
            claim_ids=claim_ids,
        )
        if output_format == "markdown":
            assert output_path is not None
            written_paths = write_layer_evidence_outputs(result, output_path)
            print(f"📝 已写入层级 Evidence 审阅文档：{written_paths[0]}")
            for path in written_paths[1:]:
                print(f"📝 已写入独立 Observer 报告：{path}")
            json_context, yaml_context = analyst_context_paths(output_path)
            print(f"🤖 已写入 Analyst LLM 主输入：{json_context}")
            print(f"🤖 已写入等价 YAML context：{yaml_context}")
            if resolved_chart_dir and result.get("status") == "ok":
                print(f"📊 图表与 sidecar 目录：{resolved_chart_dir}")
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result.get("status") in {"ok", "no_registered_observers"} else 1

    from ..agents.evidence import observer
    from ..memory import get_store

    store = get_store()
    if action == "sources":
        return _evidence_sources(store, entity=entity)
    if action == "probe":
        if not symbol:
            print("❌ probe 需要标的：ats evidence probe NVDA --limit 5")
            return 1
        return _evidence_probe(symbol, runs=limit if limit and limit <= 20 else 5)
    if action == "show":
        rows = store.observations(entity=entity or None, limit=limit)
        if not rows:
            print("(暂无证据观测 — 等观察名单的下一次财报，或用 `ats evidence observe` 手动跑)")
            return 0
        print(f"{'实体':<10}{'指标':<22}{'类型':<18}{'立场':<12}{'方向':<7}{'期间'}")
        print("-" * 84)
        for r in rows:
            print(
                f"{r['entity']:<10}{r['metric']:<22}{r['observation_type']:<18}"
                f"{r['stance']:<12}{r['direction']:<7}{r['period'] or '—'}"
            )
            print(f"    {r['evidence_span'][:76]}")
        fails = store.observation_failures(limit=5)
        if fails:
            print("\n最近抽取失败（保留而非记成'零观测'）：")
            for f in fails:
                print(f"  {f['entity']} · {f['document_id']}: {f['reason']}")
        return 0

    if action in ("propose", "proposals", "review"):
        from ..chain import induction
        from ..config import load_pead_global

        if action == "proposals":
            rows = store.claim_proposals(limit=limit)
            if not rows:
                print("(暂无待确认命题 —— 未归属观测还没积累到触发门槛)")
                return 0
            for r in rows:
                mark = {"pending": "⏳", "accepted": "✅", "rejected": "🚫"}.get(r["status"], "·")
                print(f"{mark} {r['id']}  [{r['status']}]  {r['statement']}")
                if r.get("rationale"):
                    print(f"     理由：{r['rationale']}")
            return 0

        if action == "review":
            if not symbol:
                print("❌ 需要 proposal id：ats evidence review <id> --accept|--reject")
                return 1
            status = "accepted" if accept else "rejected"
            if not store.set_proposal_status(
                symbol, status, reviewer=reviewer or "boss", rationale=note
            ):
                print(f"❌ 未找到 proposal {symbol}")
                return 1
            print(f"{'✅ 已采纳' if accept else '🚫 已拒绝'} {symbol}")
            if accept:
                print(
                    "提醒：命题仍需你手工写进 config/sectors/<name>.yaml 的 claims: —— "
                    "坐标系只有人能扩。触发它的那批观测已冻结，不会用于印证它自己。"
                )
            return 0

        ind_cfg = load_pead_global().get("induction", {})
        if not ind_cfg.get("enabled", True):
            print("(induction.enabled=false — 归纳已关闭)")
            return 0
        proposal, reason = induction.induce(store, cfg=ind_cfg)
        print(f"触发判定：{reason}")
        if proposal is None:
            return 0
        rows_by_id = {r["id"]: r for r in store.observations(limit=500)}
        print()
        print(induction.as_card(proposal, rows_by_id))
        return 0

    if action == "report":
        from ..chain import report as chain_report
        from ..config import load_pead_global, load_sector_config

        cfg = load_sector_config(entity or "ai_hardware")
        path = chain_report.write(
            cfg,
            store,
            as_of=datetime.now(timezone.utc),
            ind_cfg=load_pead_global().get("induction", {}),
        )
        print(f"📝 {path}" if path else "(report dir unset — skipped)")
        return 0

    if action == "kbreview":
        # The same six detectors the weekly report runs, on demand. Worth its own
        # command because the report costs a full re-assessment to regenerate, while
        # the question "did my KB edit clear that finding?" should cost nothing.
        from ..chain import kb_review
        from ..config import load_sector_config

        cfg = load_sector_config(entity or "ai_hardware")
        findings = kb_review.review(cfg, store, now=datetime.now(timezone.utc))
        print("\n".join(kb_review.as_section(findings)))
        return 0

    if action == "claims":
        from ..chain import corroborate as corr
        from ..config import load_sector_config

        cfg = load_sector_config(entity or "ai_hardware")
        from ..chain.sources import source_entities_for as _source_entities

        ccfg = cfg.review.get("corroboration", {})
        any_claim = False
        for configured_layer in cfg.layers:
            if not configured_layer.claims:
                continue
            any_claim = True
            print(f"\n=== {configured_layer.label} ({configured_layer.key}) ===")
            rows_by_entity = {}
            for claim in configured_layer.claims:
                ents = {w.entity.upper() for w in claim.witnesses}
                ents |= claim.expected_witnesses() | set(claim.entities)
                ents |= _source_entities(claim)
                for e in ents:
                    rows_by_entity.setdefault(e, store.observations(entity=e, limit=200))
            for a in corr.assess_layer(configured_layer, rows_by_entity, cfg=ccfg):
                claim = next(c for c in configured_layer.claims if c.id == a.claim_id)
                # 「◐」 = one-sided AND too few independent filers -> unconfirmed,
                # NOT conflicting. 「仅自述」 = it did resolve, on one vantage point.
                mark = (
                    "◐"
                    if a.unresolved_reason == "single_stance"
                    else {
                        "supportive": "✅",
                        "contradicted": "⛔",
                        "mixed": "⚠️",
                        "resolved": "📊",
                        "unknown": "· ",
                    }.get(a.verdict, "· ")
                )
                basis = "（仅自述）" if a.basis == "self_reported" else ""
                print(
                    f"{mark} {a.claim_id:20} {a.verdict:13}{basis} 覆盖 {a.coverage:6} "
                    f"证据簇 {a.evidence_clusters} · 立场 {a.stance_classes} 类"
                )
                print(f"     {claim.statement}")
                if a.entity_readings:
                    # A cross-section's answer IS the per-company table; there is no
                    # single support/refute count to print.
                    stand = {"strong": "强", "neutral": "中", "weak": "弱", "unknown": "—"}
                    basis = {
                        "corroborated": "有交叉印证",
                        "self_reported": "仅自述",
                        "thin": "证据薄",
                    }
                    for r in a.entity_readings:
                        print(
                            f"       {r.entity:<12}{stand.get(r.standing, r.standing):<3}"
                            f"{basis.get(r.basis, r.basis):<8}"
                            f"{r.evidence_clusters} 簇/{r.stance_classes} 类  {r.reason}"
                        )
                else:
                    print(
                        f"     支持 {a.support_score:.0f} / 反驳 {a.refute_score:.0f}"
                        + (f" · 异议 {','.join(a.dissenters)}" if a.dissenters else "")
                    )
                if a.silent_witnesses:
                    # Silence is a gap, not neutrality — name who did not speak.
                    print(f"     未发声：{','.join(a.silent_witnesses)}")
                if a.note:
                    print(f"     {a.note}")
        if not any_claim:
            print("(该行业配置里还没有 claims —— 见 docs/archive/CHAIN_EVIDENCE.md)")
        return 0

    print(f"❌ 未知的 evidence 操作 {action!r}")
    return 2


def sector_show(name: str = "ai_hardware") -> int:
    from ..memory import get_store

    store = get_store()
    latest = store.latest_sector_review(name)
    if latest is None:
        print(f"(no sector review for {name} yet — run `ats sector review {name}`)")
        return 0
    print(f"=== sector review {name} @ {latest.as_of:%Y-%m-%d} ===")
    print(f"Regime: {latest.regime}\n\n{latest.summary}\n")
    if latest.layer_verdicts:
        from ..agents.sector import cross_section
        from ..config import load_sector_config
        from ..schemas.sector import allocation_for_status

        cfg = load_sector_config(name)
        order = {ly.key: i for i, ly in enumerate(cfg.layers)}
        for v in sorted(latest.layer_verdicts, key=lambda x: order.get(x.layer_key, 99)):
            layer = cfg.layer_by_key(v.layer_key)
            label = layer.label if layer else v.layer_key
            call = allocation_for_status(v.layer_status)
            budget = cross_section.budget_for(layer, call) if layer else 0.0
            flags = []
            if not v.has_claims:
                flags.append("无命题")
            if not v.cross_section_applicable:
                flags.append("截面不适用")
            print(
                f"  {label}: {call} (信心 {v.confidence:.2f}) · 预算 {budget:.1%} NAV"
                + (f"  ⚠️ {'、'.join(flags)}" if flags else "")
            )
    for a in latest.layers:
        print(f"  {a.label}: 景气 {a.boom_score:.0f} [{a.signal}]")
    print("\nHistory:")
    for r in store.recent_sector_reviews(name):
        print(f"  {r['as_of'][:10]}  {r['regime'][:70]}")
    return 0


def sector_probe(name: str = "ai_hardware", *, live_data: bool = True) -> int:
    """Assemble the review context without spending an LLM call; print stats + prompt."""
    from ..agents.sector import assemble
    from ..config import load_sector_config

    sc = assemble.build(load_sector_config(name), live_data=live_data)
    print(f"=== sector context stats: {sc.stats()} ===\n")
    print(sc.as_context())
    return 0


@scoped_read("information")
def run_information_pass(*, use_llm: bool = True, ingest_research: bool = True) -> int:
    """信息分析师独立入口（Phase D）：一轮信息简报，独立终结。

    只消费已准入文档与中性证据、发布 InformationBrief 投影；不读取其他
    分析师观点，不触发基本面流程或主理人决策。
    """
    from ..agents.information import entry

    if ingest_research:
        # Runtime submits an explicit data task; the Information Agent only reads
        # the published document product after the managed writer finishes.
        _run_managed_article_ingestion(source_ids={"semianalysis"}, trigger_kind="manual")
    summary = entry.run_information_pass(use_llm=use_llm,
                                         ingest_research=ingest_research)
    print("🛰️  information — 每轮信息简报")
    print(f"   文章 {summary['articles']} 篇 · 目标 {len(summary['targets'])} 个"
          f" · 简报投影 {summary['briefs']} 条")
    return 0


@scoped_read("fundamental")
def _run_analyst_fundamental(args) -> int:
    """基本面双模式独立入口（Phase D 5.1）：ats analyst fundamental --mode routine|event."""
    from ..agents.fundamental.entry import build_run_request, run_fundamental_pass

    if not args.symbol:
        raise SystemExit("analyst fundamental 需要 --symbol")
    default_trigger = ("information_brief_update" if args.mode == "routine"
                       else "earnings_release")
    extra = {"refresh_baseline": True} if args.mode == "routine" and getattr(args, "refresh_baseline", False) else {}
    request = build_run_request(
        args.mode, args.symbol,
        trigger=args.trigger or default_trigger,
        use_llm=not args.no_llm,
        fiscal_label=args.fiscal_label,
        cutoff=args.cutoff, **extra)
    summary = run_fundamental_pass(request)
    if summary.get("mode") == "routine":
        print("📈 fundamental (routine) — 例行预期更新")
        print(f"   标的 {summary.get('symbol')} · 归类 {summary.get('summary', {})}"
              f" · 投影 {len(summary.get('published', []))} 条")
    else:
        print(f"📈 fundamental (event) — 事件评审 {summary.get('symbol')}"
              f" {summary.get('fiscal_label', '')}")
        frozen = summary.get("frozen_at") or "(no frozen baseline)"
        print(f"   冻结基线 {frozen} · 评审 {summary.get('event_review_summary', {})}")
    return 0


def run_transcript_probe(symbols: list[str] | None = None, quarters: int = 4) -> int:
    """Audit transcript retrieval across recent quarters — no LLM, read-only.

    For each target we walk the fiscal label BACKWARDS (label arithmetic, so it works
    for offset fiscal years like NVDA's FY2027 as well as calendar-quarter filers)
    and check that the fetched transcript REPORTS the quarter we asked for.

    Acceptance: zero wrong-quarter and zero wrong-company picks (2026-11-05: a
    quarter-targeted search returned Seagate and Estee Lauder for MSFT). A miss is tolerable — scoring then falls
    back to the earnings release — but a wrong quarter silently produces a fictional
    surprise, so it must never happen.
    """
    from ..config import load_pead_config, load_pead_global
    from ..data import admission, fiscal, transcript

    targets = [s.upper() for s in (symbols or load_pead_global().get("targets", []))]
    rows, wrong, missing, skipped = [], 0, 0, []

    for sym in targets:
        cfg = load_pead_config(sym)
        year, quarter = fiscal.parse_label(cfg.fiscal_label)
        if not (year and quarter):
            skipped.append(f"{sym}（fiscal_label={cfg.fiscal_label!r} 无季度）")
            continue
        for back in range(quarters):
            q, y = quarter - back, year
            while q <= 0:
                q += 4
                y -= 1
            label = f"Q{q} {y}"
            text, src = transcript._from_search(sym, label, cfg.company_name)
            got = fiscal.detect_period(text, src) if text else None
            body = transcript.extract_body(text, src)[0] if text else ""
            locator = src.split(":", 1)[1] if ":" in src else src
            if not text:
                status, missing = "—— 未找到", missing + 1
            elif not admission.locator_names_entity(locator, sym, cfg.company_name):
                status, wrong = "❌ 错公司", wrong + 1
            elif got == (y, q):
                status = "✅ 正确"
            else:
                status, wrong = f"❌ 错季 {got}", wrong + 1
            rows.append((sym, label, status, len(text), len(body), src[:64]))
            print(
                f"  {sym:6} {label:10} {status:16} raw={len(text):7} body={len(body):7} {src[:64]}"
            )

    print(f"\n=== transcript 检索审计：{len(rows)} 次查询 ===")
    print(f"  ✅ 正确 {len(rows) - wrong - missing}   ❌ 错季/错公司 {wrong}   —— 未找到 {missing}")
    if skipped:
        print(f"  ⏭  跳过（label 无季度，待 Stage B 派生）：{', '.join(skipped)}")
    print("  验收标准：错季 = 0 且 错公司 = 0" + ("  → 通过 ✅" if wrong == 0 else "  → 未通过 ❌"))
    return 1 if wrong else 0


def pead_show(symbol: str) -> int:
    from ..memory import get_store

    store = get_store()
    recent = store.recent_dossiers(symbol.upper(), limit=1)
    if not recent:
        print(f"(no PEAD dossier for {symbol.upper()} yet — run `ats pead prep {symbol.upper()}`)")
        return 0
    d = store.get_dossier(symbol.upper(), recent[0]["fiscal_label"])
    print(f"=== PEAD dossier {d.symbol} {d.fiscal_label} (phase={d.phase}) ===")
    if d.expectation_set:
        print(f"\n[Narrative]\n{d.expectation_set.narrative}")
        print(f"\n[Valuation] {d.expectation_set.valuation}")
    if d.market_setup:
        m = d.market_setup
        print(
            f"\n[Setup] run-up vs sector {m.run_up_vs_sector_pct}% · EM {m.expected_move_pct}% "
            f"· ATM IV {m.atm_iv}% · skew {m.iv_skew}"
        )
    if d.scorecard:
        print(
            f"\n[Scorecard] 总分 {d.scorecard.total:+.2f} (门槛 {d.scorecard.threshold:+.1f}) — "
            f"{d.scorecard.band}"
        )
        for ln in d.scorecard.lines:
            print(
                f"  {ln.dim_key:14} score {ln.score:+.2f} × {ln.weight:.0%} = {ln.weighted:+.3f}  "
                f"{ln.note[:60]}"
            )
    if d.decision_summary:
        print(f"\n[Decision] {d.decision_summary}")

    # Score-run ledger: which window produced it, whether it had a transcript, and how
    # far behind the print it was — the audit trail for the score windows.
    rows = store.conn.execute(
        "SELECT * FROM pead_score_runs WHERE symbol = ? AND fiscal_label = ? ORDER BY version",
        (symbol.upper(), d.fiscal_label),
    ).fetchall()
    if rows:
        print("\n[打分台账]")
        for r in rows:
            lat = f"{r['latency_hours']:.1f}h" if r["latency_hours"] is not None else "?"
            print(
                f"  v{r['version']}  {r['scored_at'][:19]}  window={r['window'] or '-':4} "
                f"纪要={'✅' if r['has_transcript'] else '❌'} "
                f"终版={'✅' if r['final'] else '❌'} 距财报={lat} "
                f"总分={r['total'] if r['total'] is not None else '-'}"
            )
        period = store.get_period(symbol.upper(), rows[-1]["earnings_date"])
        if period:
            print(
                f"  财报: {period['earnings_date']} {period['session']}"
                f"（来源 {period['session_source']}）· label 来自 {period['label_source']}"
            )
    return 0


def resume_cycle(thread_id: str, approval, channel=None) -> dict:
    """Resume a checkpointed decision run with the Boss verdict (webhook path).

    All approval interrupts live on the chief decision graph (`chief-*` /
    `trader-*` thread ids), so it is always the graph to rebuild here.
    """
    from ..graph.chief import build_chief_graph

    if approval.reviewed_at is None:
        approval.reviewed_at = datetime.now(timezone.utc)
    app = build_chief_graph(checkpointer=get_checkpointer(persist=True))
    cfg_run = {"configurable": {"thread_id": thread_id}}
    result = app.invoke(Command(resume=approval.model_dump(mode="json")), config=cfg_run)

    if channel is not None:
        orders = result.get("order_results", [])
        channel.push(
            Notification(
                kind="fill_report",
                title=f"{thread_id}: {approval.status}",
                body=f"{len(orders)} order(s) processed",
            )
        )
    return result


def thetadata_probe(symbol: str) -> int:
    """Hit the local ThetaData terminal and dump the response shape (schema check)."""
    from ..data import options

    try:
        raw = options.thetadata_raw(symbol.upper())
    except Exception as exc:  # noqa: BLE001
        print(f"❌ ThetaData unreachable: {exc}")
        print("   Start it: put creds in var/thetadata/creds.txt, run ./scripts/start_thetadata.sh")
        return 1
    rows = raw if isinstance(raw, list) else (raw.get("response") if isinstance(raw, dict) else [])
    print(f"✅ ThetaData responded ({len(rows)} option-EOD rows).")
    # Confirm the parser end-to-end (Expected Move / IV / skew).
    setup = options.fetch(symbol.upper())
    print(
        f"   setup: EM {setup.get('expected_move_pct')}% · ATM IV {setup.get('atm_iv')}% · "
        f"skew {setup.get('iv_skew')} · exp {setup.get('expiration')} · src {setup.get('source')}"
    )
    return 0


def ibkr_probe() -> int:
    """Connectivity check: print account summary + positions, or a clear error."""
    from ..broker import IBKRBroker, IBKRUnavailable

    cfg = get_config()
    broker = IBKRBroker(sector_by_symbol={t.symbol: t.sector for t in cfg.app.tickers})
    try:
        pf = broker.get_portfolio()
    except IBKRUnavailable as exc:
        print(f"❌ IBKR unavailable: {exc}")
        print("   Start TWS/IB Gateway, enable API (port 7497 paper), trust 127.0.0.1.")
        return 1
    print(
        f"✅ Connected. account={pf.account_id or '?'}  "
        f"NetLiq=${pf.net_liquidation:,.0f}  cash=${pf.cash:,.0f}  leverage={pf.leverage:.2f}x"
    )
    if not pf.positions:
        print("   (no open positions)")
    for p in pf.positions:
        print(
            f"   {p.symbol:6} {p.qty:>8.0f} @ {p.avg_cost:>8.2f}  mv=${p.market_value:>12,.0f}  "
            f"w={p.weight:.1%}  uPnL=${p.unrealized_pnl:,.0f}"
        )
    return 0


def _setup_logging() -> None:
    import logging

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger("ats").setLevel(logging.INFO)  # our own logs at INFO, third-party quiet


def _run_managed_article_ingestion(*, source_ids: set[str] | None = None,
                                  trigger_kind: str = "manual",
                                  trigger_ref: str = "") -> list[dict]:
    """Submit article refreshes to the persistent-data worker and return outcomes."""
    from ..data.pipelines.unstructured.article_ingest import _article_sources
    from ..data.persistent_queue import PersistentIngestionQueue

    registered = _article_sources()
    selected = sorted(source_ids or (set(registered) - {"yfinance_live_news"}))
    unknown = sorted(set(selected) - set(registered))
    if unknown:
        raise ValueError(f"unregistered article source(s): {', '.join(unknown)}")
    queue = PersistentIngestionQueue()
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    if not trigger_ref:
        trigger_ref = f"{trigger_kind}:{now.isoformat()}"
    registry_paths = [Path("config/data") / name for name in
                      ("structured.yaml", "unstructured.yaml", "schedules.yaml")]
    policy_fingerprint = hashlib.sha256(b"".join(path.read_bytes()
                                                   for path in registry_paths)).hexdigest()
    outcomes: list[dict] = []
    for source_id in selected:
        task_id, created = queue.enqueue(
            source_id=source_id,
            scope={"source": source_id},
            trigger_kind=trigger_kind,
            trigger_ref=f"{trigger_ref}:{source_id}",
            command=["ats", "data", "article-ingest", "--source", source_id],
            policy_fingerprint=policy_fingerprint,
            requested_as_of=now.isoformat(), priority=30 if trigger_kind == "scheduled" else 20,
        )
        result = queue.run_one(task_id=task_id)
        current = queue.get(task_id) or {}
        outcomes.append({"source_id": source_id, "task_id": task_id,
                         "created": created,
                         "status": (result or {}).get("status") or current.get("status", "queued"),
                         "result": (result or {}).get("stdout", {})})
    return outcomes


def run_data(
    action: str,
    value: str = "",
    *,
    source: str = "",
    group: str = "",
    ingest_new: bool = False,
    series: str = "",
    entity: str = "",
    provider: list[str] | None = None,
    since: str = "",
    as_of: str = "",
    limit: int = 20,
    vintages: bool = False,
    dataset: str = "",
    metric: str = "",
    product: str = "",
    status: str = "",
    output_format: str = "json",
    periods: list[str] | None = None,
    query_scope: str = "",
    db_path: str = "",
    artifact_root: str = "",
    force: bool = False,
    force_check: bool = False,
    confirm: bool = False,
    exported: bool = False,
    purge_note: str = "",
    apply: bool = False,
    mode: str = "platform",
    release_file: str = "",
    kind: str = "",
    operation: str = "",
    window: int = 0,
    entities: str = "",
    period: str = "",
    scope: str = "",
    report_path: str = "",
    acquire: bool = False,
    chart_dir: str = "",
    provider_lookup_attempts: int = 3,
    provider_lookup_retry_seconds: float = 1.0,
    approve_title_url_review: bool = False,
    assurance_consumer: str = "",
    assurance_domain: str = "",
    assurance_contract_version: str = "",
    evidence_type: str = "",
    evidence_outcome: str = "",
    evidence_scope_json: str = "{}",
    evidence_details_json: str = "{}",
    evidence_summary: str = "",
    evidence_command_summary: str = "",
    evidence_environment_names: list[str] | None = None,
    evidence_prerequisites: list[str] | None = None,
    evidence_fingerprint_paths: list[str] | None = None,
    evidence_reason: str = "",
    evidence_event_id: str = "",
) -> int:
    """Inspect stable data products without knowing their backing tables."""
    import json

    if action == "calendar-refresh":
        source_id = source or value
        if not source_id:
            raise ValueError("calendar-refresh requires --source")
        from ..data.calendar_refresh import refresh_schedule_calendar
        from ..data.persistent_queue import require_queue_worker
        from ..data.stores.schedule_calendar import ScheduleCalendarStore

        worker_source = os.environ.get("ATS_PERSISTENT_QUEUE_SOURCE_ID", "")
        require_queue_worker(worker_source)
        result = refresh_schedule_calendar(
            source_ids={source_id}, store=ScheduleCalendarStore())
        task_id = os.environ.get("ATS_PERSISTENT_QUEUE_TASK_ID", "")
        if task_id:
            from ..data.persistent_queue import PersistentIngestionQueue

            PersistentIngestionQueue().record_lineage(task_id, {
                "runs": [row["source_run_id"] for row in result["sources"]],
                "raw": [row.get("raw_artifact_id", "") for row in result["sources"]],
                "admission": [ref for row in result["sources"]
                              for ref in row.get("candidate_ids", [])],
                "published": [ref for row in result["sources"]
                              for ref in row.get("published_refs", [])],
            })
        failed = any(row["status"] == "failed" for row in result["sources"])
        partial = any(row["status"] == "partial" for row in result["sources"])
        result["status"] = "failed" if failed else "partial" if partial else "succeeded"
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 1 if failed else 0

    if action == "calendar-finalize":
        from ..data.calendar_refresh import refresh_schedule_calendar
        from ..data.persistent_queue import require_queue_worker
        from ..data.stores.schedule_calendar import ScheduleCalendarStore

        worker_source = os.environ.get("ATS_PERSISTENT_QUEUE_SOURCE_ID", "")
        require_queue_worker(worker_source)
        result = refresh_schedule_calendar(
            store=ScheduleCalendarStore(), finalize_only=True)
        result["status"] = ("failed" if result["release_reconciliation"]["status"] == "failed"
                            else "succeeded")
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result["release_reconciliation"]["status"] != "failed" else 1

    if action == "article-ingest":
        source_id = source or value
        if not source_id:
            raise ValueError("article-ingest requires --source")
        from ..data.pipelines.unstructured.article_ingest import ingest_article_source

        result = ingest_article_source(
            source_id, human_review_approved=approve_title_url_review)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0

    if action in {"document-ingest", "fixed-ingest"}:
        source_id = source or value
        if not source_id:
            raise ValueError("document-ingest requires --source")
        from ..data.pipelines.unstructured.document_ingest import ingest_documents

        try:
            result = ingest_documents(source_id, entity=entity)
        except Exception as exc:
            result = {"status": "failed", "source_id": source_id,
                      "reason_code": type(exc).__name__}
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result["status"] in {"succeeded", "no_change", "partial",
                                         "quarantined"} else 1

    if action == "chain-source-collect":
        source_id = source or value
        if not source_id:
            raise ValueError("chain-source-collect requires --source")
        from ..chain import sources as chain_sources
        from ..data.persistent_queue import require_queue_worker
        from ..memory import get_store

        worker_source = os.environ.get("ATS_PERSISTENT_QUEUE_SOURCE_ID", "")
        require_queue_worker(worker_source)
        saved = chain_sources.collect(get_store(), source_ids={source_id})
        print(json.dumps({"status": "succeeded" if saved.get(source_id, -1) >= 0 else "partial",
                          "source_id": source_id, "saved": saved.get(source_id, -1)},
                         ensure_ascii=False))
        return 0 if saved.get(source_id, -1) >= 0 else 1

    if action == "assurance":
        import json
        from ..data import assurance

        scope = json.loads(evidence_scope_json or "{}")
        if value == "query":
            result = assurance.qualification(
                domain_id=assurance_domain, consumer_id=assurance_consumer,
                contract_version=assurance_contract_version, scope=scope,
                db_path=db_path or None)
        elif value == "history":
            result = assurance.evidence_history(
                domain_id=assurance_domain, consumer_id=assurance_consumer,
                scope=scope if scope else None, db_path=db_path or None, limit=limit)
        elif value == "record":
            if not as_of:
                raise ValueError("data assurance record requires --as-of")
            result = {"event_id": assurance.record_evidence(
                domain_id=assurance_domain, consumer_id=assurance_consumer,
                evidence_type=evidence_type, outcome=evidence_outcome, scope=scope,
                as_of=datetime.fromisoformat(as_of.replace("Z", "+00:00")),
                command_summary=evidence_command_summary,
                result_summary=evidence_summary,
                environment_names=evidence_environment_names,
                prerequisite_event_ids=evidence_prerequisites,
                details=json.loads(evidence_details_json),
                dependency_paths=evidence_fingerprint_paths,
                db_path=db_path or None)}
        elif value == "revoke":
            if not evidence_reason:
                raise ValueError("data assurance revoke requires --reason")
            result = {"event_id": assurance.revoke_evidence(
                reason=evidence_reason, event_id=evidence_event_id,
                db_path=db_path or None)}
        else:
            raise ValueError("data assurance action must be query, record, or revoke")
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0

    if action == "pead-official-disclosure-coverage":
        from ..data.pead_official_disclosures import (
            active_pead_targets,
            collect_active_packages,
            write_acceptance_report,
        )
        from ..memory.store import TradingMemory

        if not db_path or not artifact_root:
            raise ValueError(
                "pead-official-disclosure-coverage requires --db and --artifact-root "
                "so acceptance cannot write production documents"
            )
        previous_root = os.environ.get("ATS_DOCS_ROOT")
        os.environ["ATS_DOCS_ROOT"] = artifact_root
        store = TradingMemory(db_path)
        try:
            packages = collect_active_packages(store=store)
            report = write_acceptance_report(
                report_path or Path(artifact_root) / "PEAD_OFFICIAL_DISCLOSURE_ACCEPTANCE.md",
                packages,
            )
            result = {
                "scope": active_pead_targets(),
                "packages": [package.as_dict() for package in packages],
                "report": str(report),
                "complete": all(package.complete for package in packages),
                "side_effects": {
                    "llm": 0,
                    "pead_scoring": 0,
                    "chief": 0,
                    "broker_orders": 0,
                    "trades": 0,
                },
            }
        finally:
            store.conn.close()
            if previous_root is None:
                os.environ.pop("ATS_DOCS_ROOT", None)
            else:
                os.environ["ATS_DOCS_ROOT"] = previous_root
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result["complete"] else 2

    if action == "ibkr-news-diagnostics":
        from ..data.articles.ibkr_news import diagnose

        result = diagnose(
            symbol=entity or value or "NVDA",
            providers=provider or None,
            provider_lookup_attempts=provider_lookup_attempts,
            provider_lookup_retry_seconds=provider_lookup_retry_seconds,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0

    if action in {"source-acceptance", "source-publish", "source-releases"}:
        from ..data.pipelines.unstructured.source_acceptance import (
            assess_article_source,
            assess_ibkr_news_with_fallback,
            load_release_overlay,
            publish_source,
            write_acceptance_report,
        )

        if action == "source-releases":
            print(
                json.dumps(
                    load_release_overlay(release_file or None),
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
            return 0
        source_id = source or value
        if not source_id:
            raise ValueError(f"{action} requires --source or VALUE")
        previous_env: dict[str, str | None] = {}
        isolated_store = None
        try:
            if acquire:
                if source_id != "semianalysis":
                    raise ValueError("--acquire is currently only supported for semianalysis")
                if not db_path or not artifact_root:
                    raise ValueError(
                        "source-acceptance --acquire requires --db and --artifact-root"
                    )
                # Mail/RSS acquisition is permitted only into explicitly supplied
                # isolated storage.  The acceptance pass below then reads that same
                # immutable asset catalog; no Chain/PEAD/Chief operation is invoked.
                from ..data import research as research_data
                from ..memory import TradingMemory, reset_store_cache

                previous_env = {
                    name: os.environ.get(name) for name in ("ATS_DB_PATH", "ATS_DOCS_ROOT")
                }
                os.environ["ATS_DB_PATH"] = db_path
                os.environ["ATS_DOCS_ROOT"] = artifact_root
                reset_store_cache()
                isolated_store = TradingMemory(db_path)
                batch = research_data.ingest_configured_batch(store=isolated_store)
                acquired = list(batch.articles)
            else:
                acquired = []
            result = (
                assess_ibkr_news_with_fallback(human_review_approved=approve_title_url_review)
                if source_id == "ibkr_news"
                else assess_article_source(
                    source_id, human_review_approved=approve_title_url_review
                )
            )
            result["acquisition"] = {
                "requested": acquire,
                "articles": len(acquired),
                "isolated": bool(acquire),
                "transport_status": batch.transport_status if acquire else {},
            }
            if acquire and not batch.complete:
                result["outcome"] = "partial"
                result["classification"] = "partial"
                result["platform_eligible"] = False
                result["checks"].append(
                    {
                        "check": "acquisition_transport_completeness",
                        "passed": False,
                        "detail": batch.transport_status,
                    }
                )
        finally:
            if isolated_store is not None:
                isolated_store.conn.close()
            if previous_env:
                from ..memory import reset_store_cache

                for name, previous in previous_env.items():
                    if previous is None:
                        os.environ.pop(name, None)
                    else:
                        os.environ[name] = previous
                reset_store_cache()
        if report_path:
            result["report"] = str(write_acceptance_report(result, report_path))
        if action == "source-publish":
            if apply:
                result["release"] = publish_source(result, path=release_file or None, mode=mode)
            else:
                result["release"] = {
                    "applied": False,
                    "mode": mode,
                    "reason": "pass --apply to mutate release overlay",
                }
        # A source report intentionally keeps the complete candidate ledger on disk.
        # Do not flood an operator's terminal with hundreds of deferred headlines.
        rendered = result
        if report_path:
            rendered = {key: value for key, value in result.items() if key != "candidates"}
            rendered["candidate_count"] = len(result["candidates"])
            rendered["candidate_preview"] = result["candidates"][:20]
        print(json.dumps(rendered, ensure_ascii=False, indent=2, default=str))
        return 0 if result["platform_eligible"] else 2

    if action == "config":
        from ..data.catalog import load_data_catalog

        catalog = load_data_catalog()
        validation = catalog.validate()
        result = {
            "catalog": str(catalog.path),
            "version": catalog.version,
            "validation": validation.model_dump(mode="json"),
            "statuses": catalog.statuses(),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if validation.valid else 2

    if action == "financial-package-check":
        from ..data.financial_release import company_financial_release_check
        from ..data.runtime import get_platform_structured_repository

        repository = get_platform_structured_repository()
        try:
            result = company_financial_release_check(
                repository, entities=[entity] if entity else None
            )
        finally:
            repository.close()
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result["ready"] else 2

    # Interactive data commands are the governed platform surface.  The
    # compatibility facade defaults to the legacy SQLite path, which made the
    # CLI report ``no_coverage`` even when var/data.sqlite had accepted rows.
    from ..data.products import get_platform_data_products

    products = get_platform_data_products()
    # FactSet import is an operator-triggered ingestion command, not a consumer
    # query.  The normal products surface deliberately opens unstructured data
    # read-only, so replace only that repository with the governed writer before
    # this command reaches the shared document/structured pipeline.
    if action in {"factset-import", "factset-reprocess", "factset-refresh",
                  "factset-semantic-refresh"} and not db_path:
        from ..data.stores.unstructured import get_platform_unstructured_store

        products.unstructured.close()
        products._unstructured_repository = get_platform_unstructured_store()
    if db_path and action not in {
        "validate-source",
        "ingest",
        "release-check",
        "publish",
        "rollback",
        "purge-source",
        "factset-product-pin",
        "factset-product-unpin",
    }:
        from ..data.products import DataProducts
        from ..data.structured import SQLiteStructuredRepository

        isolated = SQLiteStructuredRepository(db_path, artifact_root=artifact_root or None)
        isolated.bootstrap_catalog()
        if action == "factset-semantic-refresh":
            from ..data.stores.unstructured.platform import PlatformUnstructuredRepository

            products.structured.close()
            products.unstructured.close()
            isolated_documents = PlatformUnstructuredRepository(db_path, writable=True)
            products = DataProducts(store=products.store, structured_repository=isolated,
                                    unstructured_repository=isolated_documents)
        else:
            products = DataProducts(store=products.store, structured_repository=isolated)
    if action in {"validate-source", "ingest", "release-check", "publish", "rollback",
                  "purge-source", "factset-product-pin", "factset-product-unpin"}:
        from ..data.structured import (
            ReleaseManager,
            SQLiteStructuredRepository,
            StructuredCatalog,
            ingest_source,
            validate_source_registration,
        )

        catalog = StructuredCatalog.load()
        repository = products.structured
        close_repository = False
        if db_path or artifact_root:
            if not db_path:
                raise ValueError("--artifact-root requires --db for an isolated run")
            repository = SQLiteStructuredRepository(db_path, artifact_root=artifact_root or None)
            repository.bootstrap_catalog(catalog)
            close_repository = True
        target_source = source or value
        manager = ReleaseManager(repository, catalog=catalog, path=release_file or None)
        try:
            if action == "validate-source":
                if not target_source:
                    raise ValueError("validate-source requires --source or VALUE")
                result = validate_source_registration(target_source, catalog=catalog)
            elif action == "ingest":
                if not target_source:
                    raise ValueError("ingest requires --source or VALUE")
                scope = json.loads(query_scope) if query_scope else {}
                # `--dataset` is a first-class selection, not merely a display
                # filter. Multi-dataset sources otherwise silently ingest the
                # first catalog dataset even when the scheduler names another.
                if dataset:
                    scope.setdefault("dataset_id", dataset)
                if since:
                    scope.setdefault("since", since)
                result = ingest_source(
                    repository,
                    target_source,
                    entities=[entity] if entity else [],
                    periods=periods or [],
                    query_scope=scope,
                    catalog=catalog,
                    force=force,
                )
            elif action == "release-check":
                from ..data.pipelines.structured.discovery import release_check

                result = release_check(
                    repository, group=group, source_ids=[target_source] if target_source else None,
                    ingest_new=ingest_new, force=(force or force_check), dataset_id=dataset, catalog=catalog,
                )
            elif action == "purge-source":
                # Physically deletes a retired source's data.  This is the only
                # irreversible action on the structured surface, so it is opt-in
                # twice: the caller must name the source and pass --confirm, and
                # the repository refuses any source without a retirement
                # tombstone.  Nothing else in the CLI reaches this path.
                if not target_source:
                    raise ValueError("purge-source requires --source or VALUE")
                result = repository.purge_source(
                    target_source,
                    confirm=confirm,
                    catalog=catalog,
                    actor="cli",
                    exported=exported,
                    note=purge_note,
                )
            elif action == "factset-product-pin":
                if not target_source or not confirm:
                    raise ValueError("factset-product-pin requires version VALUE and --confirm")
                result = manager.pin_factset_product(target_source)
            elif action == "factset-product-unpin":
                if not confirm:
                    raise ValueError("factset-product-unpin requires --confirm")
                result = manager.clear_factset_product_pin()
            elif action == "rollback":
                if not target_source:
                    raise ValueError("rollback requires --source or VALUE")
                result = manager.rollback(
                    kind=kind if kind in {"source", "consumer"} else "consumer",
                    target_id=target_source,
                    actor="cli",
                )
            else:
                target_id = target_source
                if not target_id:
                    raise ValueError(f"{action} requires --source or VALUE")
                check = (
                    manager.check_consumer(target_id, mode=mode)
                    if kind == "consumer"
                    else manager.check_source(target_id, mode=mode)
                )
                result = (
                    manager.apply(check)
                    if action == "publish" and apply
                    else {
                        **check,
                        "applied": False,
                        "operation": "publish_preview" if action == "publish" else "release_check",
                    }
                )
        finally:
            if close_repository:
                repository.close()
    elif action == "catalog":
        result = products.structured_catalog()
    elif action == "describe":
        if not value:
            raise ValueError("describe requires VALUE")
        result = products.describe_structured(value, kind=kind)
    elif action == "availability":
        result = products.structured_availability(entity=entity, dataset=dataset)
    elif action == "examples":
        result = products.structured_examples(dataset=dataset)
    elif action == "releases":
        from ..data.structured import load_release_overlay

        result = load_release_overlay(release_file or None)
    elif action == "sources":
        result = products.sources()
    elif action == "datasets":
        result = products.datasets()
    elif action == "metrics":
        result = products.metrics()
    elif action == "health" and assurance_consumer:
        # One research consumer's data-product health (what `ats consumer-cutover health` used to print).
        from ..workflow import consumer_health

        result = consumer_health.check(assurance_consumer)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result["status"] == "ok" else 1
    elif action == "health":
        result = products.health()
    elif action == "health-log":
        # The recorded changes of that health (a line only when it turns bad or turns good again).
        from ..workflow import consumer_cutover as cc

        print(json.dumps(cc.log(consumer_id=assurance_consumer or None), ensure_ascii=False, indent=2, default=str))
        return 0
    elif action == "quality":
        structured = products.structured_quality_report(dataset=dataset or None)
        if output_format == "markdown":
            from ..data.structured import render_quality_markdown

            print(render_quality_markdown(structured), end="")
            return 0
        # Preserve the historical document-quality top-level contract while adding
        # the structured report under an explicit namespace.
        result = structured if dataset else {**products.quality(), "structured": structured}
    elif action == "coverage":
        report = products.structured_quality_report(dataset=dataset or None)
        result = {
            "generated_at": report["generated_at"],
            "dataset_filter": report["dataset_filter"],
            "datasets": [
                {
                    "dataset_id": row["dataset_id"],
                    "catalog_status": row["catalog_status"],
                    "coverage": row["dimensions"]["coverage"],
                }
                for row in report["datasets"]
            ],
        }
    elif action == "series":
        cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00")) if as_of else None
        if metric:
            if not entity:
                raise ValueError("structured metric series requires --entity")
            result = products.metric_series(
                metric=metric,
                entity=entity,
                dataset=dataset or None,
                source_id=source or None,
                since=since or None,
                as_of=cutoff,
                include_vintages=vintages,
                source_strategy="all" if source else "selected",
            )
        else:
            result = products.indicator_series(
                source_id=source or None,
                series=series or None,
                entity=entity or None,
                since=since or None,
                as_of=cutoff,
                include_vintages=vintages,
            )
    elif action == "ai-adoption":
        if not product or not period:
            raise ValueError("ai-adoption requires --product and --period")
        cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00")) if as_of else None
        result = products.ai_work_adoption_snapshot(
            source_product=product, period=period, as_of=cutoff
        )
    elif action == "ai-job":
        if not value or not product or not period:
            raise ValueError("ai-job requires OCCUPATION plus --product and --period")
        cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00")) if as_of else None
        result = products.ai_job_profile(value, source_product=product, period=period, as_of=cutoff)
    elif action == "ai-production":
        if product and product != "1p_api":
            raise ValueError(
                "ai-production only supports governed 1p_api; Claude.ai cannot enter this proxy"
            )
        cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00")) if as_of else None
        selected_periods = periods or ([period] if period else None)
        from ..agents.evidence import observe_ai_production_penetration

        result = observe_ai_production_penetration(
            periods=selected_periods,
            as_of=cutoff,
            top_n=limit,
            chart_dir=chart_dir,
            products=products,
        )
    elif action == "ai-raw-capability":
        cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00")) if as_of else None
        from ..agents.evidence import observe_ai_raw_capability

        result = observe_ai_raw_capability(
            periods=periods or ([period] if period else None),
            as_of=cutoff, chart_dir=chart_dir, products=products,
        )
    elif action == "frontier-capability-health":
        capability_sources = {"frontier_ai_capability", "frontier_ai_capability_official_lab"}
        recent_ingestion = []
        for source_id in sorted(capability_sources):
            recent_ingestion.extend(
                products.structured.ingestion_history(
                    source_id=source_id, dataset_id="frontier_ai_capability_benchmarks", limit=20
                )
            )
        result = {
            "source_id": "frontier_ai_capability",
            "dataset_id": "frontier_ai_capability_benchmarks",
            "source_health": [row for row in products.structured.source_health()
                              if row.get("source_id") in capability_sources],
            "coverage": products.frontier_ai_capability_matrix().get("coverage", {}),
            "recent_ingestion": recent_ingestion,
        }
    elif action == "frontier-capability-discover":
        from ..data.pipelines.structured.discovery import release_check

        result = release_check(
            products.structured, source_ids=["frontier_ai_capability"],
            group="", ingest_new=False, force=True, catalog=None,
        )
    elif action == "frontier-capability-probe":
        from ..data.structured import ingest_source

        result = ingest_source(
            products.structured, "frontier_ai_capability", catalog=None,
            query_scope=json.loads(query_scope) if query_scope else {}, force=True,
        )
    elif action == "frontier-capability-audit":
        from ..data.pipelines.structured.discovery import release_check

        result = release_check(
            products.structured, source_ids=["frontier_ai_capability"],
            group="", ingest_new=True, force=True, catalog=None,
        )
    elif action == "ramp-adoption":
        cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00")) if as_of else None
        result = products.ramp_paid_adoption_snapshot(
            scope=scope or (period if period in {"adoption_overall", "adoption_overall_models", "adoption_sector"} else "adoption_overall"),
            period=period if period not in {"adoption_overall", "adoption_overall_models", "adoption_sector"} else "",
            periods=periods or None,
            as_of=cutoff, include_vintages=vintages,
        )
    elif action == "ramp-spend":
        cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00")) if as_of else None
        result = products.ramp_spend_per_employee_series(
            scope=scope or "spend_per_employee_overall", period=since or (period if period not in {"spend_per_employee_overall"} else ""),
            as_of=cutoff, include_vintages=vintages,
        )
    elif action == "ramp-model-share":
        cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00")) if as_of else None
        result = products.ramp_model_market_share_series(
            scope=scope or "model_market_share_overall", period=since or (period if period not in {"model_market_share_overall"} else ""),
            provider=entity, as_of=cutoff, include_vintages=vintages,
        )
    elif action == "earnings-insight":
        cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00")) if as_of else None
        value = (
            products.earnings_insight_vintages(as_of=cutoff, limit=limit)
            if vintages
            else products.earnings_insight_snapshot(as_of=cutoff)
        )
        if isinstance(value, list):
            result = [item.model_dump(mode="json") for item in value]
        else:
            result = value.model_dump(mode="json")
    elif action == "factset-status":
        from urllib.parse import urlsplit, urlunsplit

        def safe_url(raw):
            parts = urlsplit(str(raw or ""))
            return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

        report_rows = []
        for row in products.structured.artifacts_for(
            dataset_id="sp500_earnings_insight", limit=limit
        ):
            metadata = json.loads(row.get("metadata_json") or "{}")
            report_rows.append(
                {
                    "artifact_id": row.get("artifact_id"),
                    "content_hash": row.get("content_hash"),
                    "bytes": row.get("bytes"),
                    "fetched_at": row.get("fetched_at"),
                    "media_type": row.get("media_type"),
                    "report_date": metadata.get("report_date"),
                    "source_url": safe_url(row.get("source_url")),
                }
            )
        result = {
            **products.earnings_insight_status(limit=limit),
            "source_health": [
                row
                for row in products.structured.source_health()
                if row.get("source_id")
                in {"factset_earnings_insight_metrics", "factset_earnings_insight_doc"}
            ],
            "reports": report_rows,
            "partition_history": {
                partition: products.structured.release_manifests(
                    dataset_id="sp500_earnings_insight",
                    partition=partition,
                    passed_only=False,
                    limit=limit,
                )
                for partition in ("index_core", "sector_core")
            },
            "attempts": products.structured.ingestion_history(
                source_id="factset_earnings_insight_metrics",
                dataset_id="sp500_earnings_insight",
                limit=limit,
            ),
        }
    elif action in {"factset-review-packages", "factset-review", "factset-review-correct",
                    "factset-index-inventory", "factset-index-review",
                    "factset-index-review-packages"}:
        from ..data.stores.structured.factset_reviews import FactSetReviews
        from ..data.sources.factset_report_layout import load_layout_policy

        reviews = FactSetReviews(products.structured)
        if action == "factset-index-review-packages":
            result = reviews.list_index_packages(limit=limit)
        elif action == "factset-index-inventory":
            if not value or not confirm:
                raise ValueError("factset-index-inventory requires release id VALUE and --confirm")
            declaration = json.loads(query_scope or "{}")
            if set(declaration) != {"expected_cells", "not_disclosed", "evidence_refs"}:
                raise ValueError("index inventory requires expected_cells, not_disclosed and evidence_refs")
            releases = products.structured.release_manifests(
                dataset_id="sp500_earnings_insight", partition="index_core", limit=10000)
            release = next((row for row in releases if row["release_id"] == value), None)
            if (release is None or not release["extractor_version"].startswith(
                    "factset-text-semantic-v2:")):
                raise ValueError("semantic_index_release_not_found")
            context = release["quality"].get("index_review_context")
            if not context:
                raise ValueError("index_review_context_missing")
            package_hash = reviews.register_index(context, **declaration)
            result = {"package_hash": package_hash, "release_id": value,
                      "approved": False, "published": False}
        elif action == "factset-index-review":
            if not value or not confirm:
                raise ValueError("factset-index-review requires package hash VALUE and --confirm")
            review = json.loads(query_scope or "{}")
            if set(review) != {"decision", "reviewer", "evidence_refs", "note"}:
                raise ValueError("review requires decision, reviewer, evidence_refs and note")
            review_id = reviews.decide_index(value, **review)
            result = {"review_id": review_id, "package_hash": value,
                      "decision": review["decision"], "published": False}
        elif action == "factset-review-packages":
            result = reviews.list_packages(limit=limit)
        elif action == "factset-review-correct":
            if not value or not confirm:
                raise ValueError("factset-review-correct requires package hash VALUE and --confirm")
            review = json.loads(query_scope or "{}")
            if set(review) != {"corrections", "reviewer", "note"}:
                raise ValueError("correction requires corrections, reviewer and note")
            corrected = reviews.correct(value, **review)
            result = {"package_hash": corrected, "parent_hash": value,
                      "approved": False, "published": False}
        else:
            if not value or not confirm:
                raise ValueError("factset-review requires package hash VALUE and --confirm")
            review = json.loads(query_scope or "{}")
            if set(review) != {"decision", "reviewer", "evidence_refs", "note"}:
                raise ValueError("review requires decision, reviewer, evidence_refs and note")
            review_id = reviews.decide(value, policy=load_layout_policy(), **review)
            result = {"review_id": review_id, "package_hash": value,
                      "decision": review["decision"], "published": False}
    elif action == "factset-refresh":
        from ..data.pipelines.factset_earnings_insight import FactSetWeeklyPipeline
        from ..data.sources.factset_earnings_insight import report_refresh_url

        url = report_refresh_url(json.loads(query_scope) if query_scope else {})
        result = FactSetWeeklyPipeline(products.structured, products.unstructured).run(url=url)
    elif action == "factset-semantic-refresh":
        from ..data.pipelines.factset_earnings_insight import FactSetWeeklyPipeline
        from ..data.sources.factset_earnings_insight import report_refresh_url

        scope = json.loads(query_scope) if query_scope else {}
        if not isinstance(scope, dict) or set(scope) - {
                'url', 'index_review_id', 'review_ids', 'auto_admit', 'report_month'}:
            raise ValueError('semantic FactSet scope accepts only url, bound reviews, auto_admit and report_month')
        review_ids = scope.get('review_ids') or {}
        if not isinstance(review_ids, dict) or not all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in review_ids.items()):
            raise ValueError('factset_review_ids_invalid')
        index_review_id = scope.get('index_review_id', '')
        if not isinstance(index_review_id, str):
            raise ValueError('factset_index_review_id_invalid')
        auto_admit = scope.get('auto_admit', False)
        if not isinstance(auto_admit, bool):
            raise ValueError('factset_auto_admit_invalid')
        report_month = scope.get('report_month', '')
        if not isinstance(report_month, str):
            raise ValueError('factset_report_month_invalid')
        result = FactSetWeeklyPipeline(products.structured, products.unstructured).run_semantic(
            local_pdf=report_path or None,
            url=report_refresh_url({'url': scope['url']} if 'url' in scope else {}),
            index_review_id=index_review_id, review_ids=review_ids,
            auto_admit=auto_admit, report_month=report_month)
    elif action in {"factset-import", "factset-reprocess"}:
        if not report_path:
            raise ValueError(f"{action} requires --report-path")
        if action == "factset-reprocess" and not value:
            raise ValueError("factset-reprocess requires VALUE as extractor version")
        from ..data.pipelines.factset_earnings_insight import FactSetWeeklyPipeline

        version = value or "factset-text-v1"
        result = FactSetWeeklyPipeline(products.structured, products.unstructured).run(
            local_pdf=report_path,
            document_processor_version=f"factset-document-{version}",
            index_extractor_version=version,
            sector_extractor_version=version,
        )
    elif action == "derive":
        if not metric or not entity or not operation:
            raise ValueError("derive requires --metric, --entity and --operation")
        cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00")) if as_of else None
        base = products.metric_series(
            metric=metric,
            entity=entity,
            dataset=dataset or None,
            since=since or None,
            as_of=cutoff,
            include_vintages=vintages,
            quality="strict",
        )
        result = products.derive(
            operation=operation,
            query_result=base,
            window=window or None,
            min_periods=window or None,
        )
    elif action == "cross-section":
        selected_entities = [item.strip().upper() for item in entities.split(",") if item.strip()]
        if not metric or not selected_entities or not period:
            raise ValueError("cross-section requires --metric, --entities and --period")
        cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00")) if as_of else None
        result = products.cross_section(
            metric=metric,
            entities=selected_entities,
            period=period,
            dataset=dataset or None,
            as_of=cutoff,
        )
    elif action == "search":
        result = products.search_documents(
            value,
            entity=entity or None,
            source_contains=source or None,
            published_since=since or None,
            limit=limit,
        )
    elif action == "company":
        result = products.company_research_package(value)
    elif action == "claim":
        result = products.claim_evidence_package(value, limit=limit)
    elif action == "lineage":
        result = products.lineage(value)
    elif action == "conflicts":
        result = products.structured_conflicts(
            dataset_id=dataset or None, status=status or "open", limit=limit
        )
    elif action == "pending-mappings":
        result = products.structured_pending_mappings(status=status or "pending", limit=limit)
    elif action == "ingestion-history":
        result = products.structured_ingestion_history(
            source_id=source or None, dataset_id=dataset or None, limit=limit
        )
    elif action == "artifacts":
        result = products.structured_artifact_usage(source=source or None)
    else:
        raise ValueError(f"unknown data action: {action}")
    if output_format == "markdown" and action == "ai-production":
        from ..agents.evidence import render_ai_production_markdown

        print(render_ai_production_markdown(result), end="")
    elif output_format == "markdown" and action == "ai-raw-capability":
        from ..agents.evidence import render_ai_raw_capability_markdown

        rendered = render_ai_raw_capability_markdown(result)
        if report_path:
            target = Path(report_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rendered, encoding="utf-8")
        print(rendered, end="")
    elif output_format == "markdown" and action in {
        "catalog",
        "describe",
        "availability",
        "examples",
    }:
        from ..data.structured import render_discovery_markdown

        print(render_discovery_markdown(result), end="")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


@cli_read_request
def main(argv: list[str] | None = None) -> int:
    from ..execution.broker_write_guard import startup

    startup(caller="ats.runtime.cli.main")
    _setup_logging()
    parser = argparse.ArgumentParser(prog="ats", description="Multi-agent trading cycle runner")
    parser.add_argument("--read-scope-json", help="Exact business read scopes, keyed by consumer")
    sub = parser.add_subparsers(dest="command", required=True)
    data = sub.add_parser("data", help="统一数据产品与结构化运维入口")
    data.add_argument(
        "action",
        choices=[
            "catalog",
            "config",
            "financial-package-check",
            "pead-official-disclosure-coverage",
            "source-acceptance",
            "source-publish",
            "source-releases",
            "article-ingest",
            "article-metadata-reconcile",
            "document-ingest",
            "fixed-ingest",
            "chain-source-collect",
            "evidence-observe",
            "macro-news-collect",
            "calendar-refresh",
            "calendar-finalize",
            "assurance",
            "ibkr-news-diagnostics",
            "describe",
            "availability",
            "examples",
            "releases",
            "validate-source",
            "ingest",
            "release-check",
            "publish",
            "rollback",
            "purge-source",
            "sources",
            "datasets",
            "metrics",
            "health",
            "health-log",
            "coverage",
            "quality",
            "series",
            "ai-adoption",
            "ai-job",
            "ai-production",
            "ai-raw-capability",
            "frontier-capability-health",
            "frontier-capability-discover",
            "frontier-capability-probe",
            "frontier-capability-audit",
            "ramp-adoption",
            "ramp-spend",
            "ramp-model-share",
            "derive",
            "cross-section",
            "earnings-insight",
            "factset-status",
            "factset-review-packages",
            "factset-review",
            "factset-review-correct",
            "factset-index-inventory",
            "factset-index-review",
            "factset-index-review-packages",
            "factset-import",
            "factset-reprocess",
            "factset-refresh",
            "factset-semantic-refresh",
            "factset-product-pin",
            "factset-product-unpin",
            "search",
            "company",
            "claim",
            "lineage",
            "conflicts",
            "pending-mappings",
            "ingestion-history",
            "artifacts",
        ],
    )
    data.add_argument(
        "value",
        nargs="?",
        default="",
        help="search 查询词 / company 实体 / claim 命题 / lineage 投影 ID",
    )
    data.add_argument("--source", default="", help="series: source ID；search: 来源过滤")
    data.add_argument("--group", default="", help="release-check: 注册的 discovery source group，例如 ai_adoption")
    data.add_argument("--ingest-new", action="store_true", help="release-check: 对发现的新版本执行受治理采集")
    data.add_argument("--force-check", action="store_true", help="release-check: 忽略来源 due-check 窗口，仅重新检查 metadata")
    data.add_argument(
        "--provider",
        action="append",
        default=[],
        help="ibkr-news-diagnostics: 待探测的 provider code，可重复；默认探测全部可用项",
    )
    data.add_argument(
        "--provider-lookup-attempts",
        type=int,
        default=3,
        help="ibkr-news-diagnostics: provider 枚举的最大尝试次数（默认 3）",
    )
    data.add_argument(
        "--provider-lookup-retry-seconds",
        type=float,
        default=1.0,
        help="ibkr-news-diagnostics: provider 枚举重试间隔秒数（默认 1）",
    )
    data.add_argument(
        "--approve-title-url-review",
        action="store_true",
        help="source-acceptance/source-publish: 明确确认已人工审阅标题/URL 清单",
    )
    data.add_argument("--series", default="", help="series: 指标名称")
    data.add_argument("--metric", default="", help="series: 统一结构化 metric ID")
    data.add_argument(
        "--product",
        choices=["claude_ai", "1p_api"],
        default="",
        help="ai-adoption/ai-job: Claude source product（不可省略）",
    )
    data.add_argument("--dataset", default="", help="结构化 dataset ID 过滤")
    data.add_argument("--scope", default="", help="Ramp 图表 scope（adoption_overall 等）")
    data.add_argument("--entity", default="", help="实体过滤")
    data.add_argument("--lookback-days", type=int, default=7,
                      help="evidence-observe: 向前查找最近一次已发布财报的天数（日常 7，补抽 60）")
    data.add_argument("--dry-run", action="store_true",
                      help="evidence-observe / macro-news-collect: 只列出会处理什么，不写入")
    data.add_argument("--since", default="", help="最早期间或发布日期")
    data.add_argument("--as-of", default="", help="series: 历史可见时点（ISO 8601）")
    data.add_argument("--limit", type=int, default=20, help="通用结果条数")
    data.add_argument("--status", default="", help="conflicts / pending-mappings 状态过滤")
    data.add_argument(
        "--format",
        dest="output_format",
        choices=["json", "markdown"],
        default="json",
        help="quality/catalog/describe 等输出格式",
    )
    data.add_argument("--vintages", action="store_true", help="series: 包含所有修订版本")
    data.add_argument(
        "--kind",
        choices=["source", "consumer", "dataset", "metric"],
        default="",
        help="describe: 限定对象类型",
    )
    data.add_argument(
        "--periods", action="append", default=[], help="ingest/ai-production: 目标期间，可重复"
    )
    data.add_argument(
        "--snapshot",
        action="store_true",
        help="ai-production: 请求可重放 snapshot（该 Observer 默认始终附带 manifest）",
    )
    data.add_argument(
        "--chart-dir", default="", help="ai-production: optional PNG/sidecar output directory"
    )
    data.add_argument("--query-scope", default="", help="ingest: Provider 查询范围 JSON")
    data.add_argument(
        "--db", dest="db_path", default="", help="ingest/release: 隔离或目标 SQLite 路径"
    )
    data.add_argument(
        "--artifact-root", default="", help="ingest: 隔离 raw artifact 目录（需同时 --db）"
    )
    data.add_argument(
        "--report-path",
        default="",
        help="pead-official-disclosure-coverage/source-acceptance/ai-raw-capability: Markdown 验收报告输出路径",
    )
    data.add_argument(
        "--force", action="store_true", help="ingest: 仅用于隔离验收，跳过已发布 source 的重复保护"
    )
    data.add_argument(
        "--acquire",
        action="store_true",
        help="source-acceptance: 仅 SemiAnalysis，先采集到 --db/--artifact-root 指定的隔离资产库",
    )
    data.add_argument(
        "--confirm",
        action="store_true",
        help="purge-source: 显式确认执行物理删除；缺省只做只读干跑",
    )
    data.add_argument(
        "--exported",
        action="store_true",
        help="purge-source: 声明数据已在别处留存，写入清除记录供审计",
    )
    data.add_argument("--purge-note", default="", help="purge-source: 写入清除记录的备注")
    data.add_argument("--consumer", dest="assurance_consumer", default="",
                      help="assurance: target coverage consumer ID")
    data.add_argument("--domain", dest="assurance_domain", default="",
                      help="assurance: consumer data domain")
    data.add_argument("--contract-version", dest="assurance_contract_version", default="",
                      help="assurance: pinned consumer contract version")
    data.add_argument("--evidence-type", default="", help="assurance record: required evidence kind")
    data.add_argument("--outcome", dest="evidence_outcome", choices=["passed", "failed"], default="",
                      help="assurance record: evidence outcome")
    data.add_argument("--scope-json", dest="evidence_scope_json", default="{}",
                      help="assurance: exact JSON scope shared by all evidence")
    data.add_argument("--evidence-details-json", default="{}",
                      help="assurance record: source statuses and per-product rollback proof; no raw text")
    data.add_argument("--summary", dest="evidence_summary", default="",
                      help="assurance record: sanitized result summary")
    data.add_argument("--command-summary", dest="evidence_command_summary", default="",
                      help="assurance record: sanitized command description (no secrets)")
    data.add_argument("--environment-name", dest="evidence_environment_names", action="append", default=[],
                      help="assurance record: environment variable name only; never its value")
    data.add_argument("--prerequisite-event", dest="evidence_prerequisites", action="append", default=[],
                      help="assurance record: prerequisite evidence ID; repeatable")
    data.add_argument("--fingerprint-path", dest="evidence_fingerprint_paths", action="append", default=[],
                      help="assurance record: dependency file to hash; repeatable")
    data.add_argument("--reason", dest="evidence_reason", default="",
                      help="assurance revoke: reason")
    data.add_argument("--event-id", dest="evidence_event_id", default="",
                      help="assurance revoke: evidence event to invalidate")
    data.add_argument("--apply", action="store_true", help="publish/source-publish: 显式执行写操作")
    data.add_argument("--field-evidence", default="", help="article-metadata-reconcile: exact-version field evidence JSON")
    data.add_argument("--document-ids", default="", help="article-metadata-reconcile: bounded document ID array JSON")
    data.add_argument(
        "--mode", choices=["platform"], default="platform", help="publish: 唯一受支持的数据路径"
    )
    data.add_argument(
        "--release-file",
        default="",
        help="发布覆盖层路径；structured 默认 var/structured_data，source-* 默认 var/data/unstructured",
    )
    data.add_argument(
        "--operation", choices=["yoy", "mom", "rolling"], default="", help="derive: 派生运算"
    )
    data.add_argument("--window", type=int, default=0, help="derive rolling 窗口")
    data.add_argument("--entities", default="", help="cross-section: 逗号分隔实体")
    data.add_argument("--period", default="", help="cross-section: 比较期间")
    sub.add_parser("ibkr", help="probe IBKR paper connectivity (account + positions)")
    srv = sub.add_parser("serve", help="run the approval webhook (Feishu callbacks)")
    srv.add_argument("--host", default="0.0.0.0")
    srv.add_argument("--port", type=int, default=8000)
    sch = sub.add_parser("schedule", help="research dispatcher daemon: scheduled and calendar-event workflows")
    ct = sub.add_parser("cutover", help="已退役（switch-trade r5 8.4）：切换控制平面的批次登记与报告校验已删除，交易路径已切换")
    ct.add_argument("rest", nargs=argparse.REMAINDER)
    cc = sub.add_parser("consumer-cutover", help="仅保留 restore（把读路由不在 target 的分析师恢复到 target）；status/health/log 已迁到 `ats data health`、`ats data health-log`")
    cc.add_argument("action", choices=["status", "health", "log", "restore"])
    cc.add_argument("--consumer-id", default="", help="information | fundamental | ...")
    cc.add_argument("--actor", default="", help="restore: 操作者")
    cc.add_argument("--approval", default="", help="restore: 用户批准原文（必填）")
    cc.add_argument("--reason", default="", help="restore: 原因（必填）")
    cc.add_argument("--db", default="", help="覆盖切流控制库路径")

    sh = sub.add_parser("shadow", help="已退役（switch-trade r5 8.4）：影子比较、验收与影子账本工具已删除")
    sh.add_argument("rest", nargs=argparse.REMAINDER)
    iv = sub.add_parser(
        "intake",
        help="接入核验与逐消费者处置（交易路径切换前保留；只读，不改路由、不落生产证据）")
    iv.add_argument("action", choices=[
        "verify", "report", "digest", "not-tradable", "disposition",
        "evidence-index"])
    iv.add_argument("--consumer-id", default="",
                    help="verify/not-tradable: 单一消费者；缺省为全部十个")
    iv.add_argument("--run-id", default="", help="not-tradable: 隔离运行 ID")
    iv.add_argument("--opinion-input", action="append", default=[],
                    help="verify: 该角色实际读取的观点来源，可重复")
    iv.add_argument("--optional-input", action="append", default=[],
                    help="disposition: 缺失的可选输入，可重复")
    iv.add_argument("--stale-input", action="append", default=[],
                    help="disposition: 按报告年龄被判 stale 的来源，可重复")

    # Named `dispatch`, not `schedule`: `schedule` is Phase E's "run the daily
    # cycle" command, and a cutover reader sharing its name would be one
    # mistyped flag away from firing a real cycle.
    rt = sub.add_parser(
        "retirement",
        help="退役：消费者清零、数据对账、墓碑一致性、依赖与门（G0）检查（**只读，不改登记表**）")
    rt.add_argument("action", choices=[
        "zero", "reconcile", "tombstone", "fallback-precheck", "decide", "gate"])
    rt.add_argument("--show", action="store_true",
                    help="gate: 列出每道门卡住的条目（只读）")
    rt.add_argument("--verification", default="",
                    help="decide/gate: G0 核验记录路径（默认 openspec 里的 verification.md）")
    rt.add_argument("--identifier", default="",
                    help="逗号分隔的登记条目 id（reconcile/tombstone 用单个）")
    rt.add_argument("--uses", default="",
                    help="reconcile: 旧实现所涉的全部数据用途（逗号分隔）")
    rt.add_argument("--readable", default="",
                    help="tombstone: 仍可读取该条目的消费方（逗号分隔）")
    rt.add_argument("--target", default="", help="fallback-precheck: 回退目标")
    rt.add_argument("--check-available", action="store_true",
                    help="fallback-precheck: 同时核验目标可用性")
    rt.add_argument("--drill-reference", default="",
                    help="decide: 已演练回退的引用")

    lv = sub.add_parser(
        "live",
        help="实盘授权门与交易路径演练（**本命令不会执行真实切换**）")
    lv.add_argument("action", choices=[
        "authorize", "show-auth", "verify", "void", "gate", "drill", "rollback-drill",
        "drill-history"])
    lv.add_argument("--reference", default="", help="实盘授权引用（须以 LIVE- 开头）")
    lv.add_argument("--confirmed-by", default="", help="verify/void: 确认人（用户本人）")
    lv.add_argument("--confirmation", default="", help="verify/void: 用户确认的原文")
    lv.add_argument("--confirmation-source", default="", help="verify/void: 确认的出处（会话、日期）")
    lv.add_argument("--confirmed-at", default="", help="verify/void: 确认时间（ISO 8601）")
    lv.add_argument("--issuer", default="human", help="authorize: 签发主体（仅 human）")
    lv.add_argument("--authorised-by", default="", help="authorize: 授权人")
    lv.add_argument("--issued-by", default="", help="authorize: 签发人")
    lv.add_argument("--scope", default="", help="authorize: 逗号分隔的消费者范围")
    lv.add_argument("--environment", default="", help="environment（live/paper）")
    lv.add_argument("--account", default="", help="account")
    lv.add_argument("--valid-until", default="", help="authorize: 有效期")
    lv.add_argument("--note", default="", help="authorize: 备注")
    lv.add_argument("--consumer-id", default="", help="gate: 被切换的消费者")
    lv.add_argument("--drill-id", default="", help="drill/rollback-drill: 演练标识")
    lv.add_argument("--base", default="", help="演练：真实路由库路径（演练库由它派生）")
    lv.add_argument("--fallback-unavailable", action="store_true",
                    help="rollback-drill: 声明回退目标不可用（验证保持现状）")
    lv.add_argument("--db", default="", help="实盘授权与门禁决定库")
    lv.add_argument("--actor", default="", help="执行人")

    sp = sub.add_parser(
        "dispatch",
        help="调度所有权核验与回滚去重（交易路径切换前保留）（pause/resume 停用或恢复一个研究工作流；其余只读账本）")
    sp.add_argument("action", choices=[
        "runtime-state", "runtime-reload", "runtime-prepare", "runtime-freeze",
        "runtime-inventory", "runtime-handover", "runtime-cancel-freeze", "runtime-expected", "runtime-compare",
        "ownership", "ledger", "executed", "record-execution",
        "rollback-plan", "rollback-history", "pause", "resume", "owner-switch", "owner-rollback", "owner-log"])
    sp.add_argument("--approval", default="", help="resume: 用户批准原文或其引用（恢复必填，停用不需要）")
    sp.add_argument("--void-unfinished", default="", help="pause/resume: 作废未完成认领的理由（有未完成认领时必填，running 不可作废）")
    sp.add_argument("--state", default="", help="ledger: 认领账本库（8.8 前保留）")
    sp.add_argument("--actor", default="", help="执行人")
    sp.add_argument("--isolation-root", default="", help="runtime-* 隔离演练根目录；不修改生产 owner")
    sp.add_argument("--workflow", default="", help="runtime-* workflow ID")
    sp.add_argument("--business-scope", default="", help="runtime-* 精确 scope JSON")
    sp.add_argument("--owner", default="legacy", choices=["legacy", "shadow", "dispatcher"])
    sp.add_argument("--reason", default="")
    sp.add_argument("--token", default="", help="freeze 返回的 token")
    sp.add_argument("--dispositions", default="{}", help="逐 trigger key 处置 JSON")
    sp.add_argument("--from-time", default="", help="预期集合窗口 ISO 起点（带时区）")
    sp.add_argument("--to-time", default="", help="预期集合窗口 ISO 终点（带时区）")

    rc_parser = sub.add_parser("read", help="已退役（switch-trade r5 8.5）：研究侧读切流的批次执行与部署授权工具已删除")
    rc_parser.add_argument("rest", nargs=argparse.REMAINDER)
    bt = sub.add_parser("batch", help="已退役（switch-trade r5 8.5）：切流批次清单工具已删除")
    bt.add_argument("rest", nargs=argparse.REMAINDER)
    dr = sub.add_parser("drill", help="已退役（switch-trade r5 8.4）：切换边界演练已删除；实盘路由演练用 ats live drill / rollback-drill")
    dr.add_argument("rest", nargs=argparse.REMAINDER)
    wf = sub.add_parser("workflow", help="工作流运行及 Trigger Ledger 运维")
    wf.add_argument("action", choices=["run", "runs", "triggers", "retry", "history"])
    wf.add_argument("--task", action="append", default=[], help="run: Phase E task ID，可重复")
    wf.add_argument("--scope-kind", default="portfolio")
    wf.add_argument("--scope-id", default="portfolio")
    wf.add_argument("--trigger-kind", choices=["manual", "schedule", "event"], default="manual")
    wf.add_argument("--trigger-id", default="", help="manual: 重放时必须复用此 ID")
    wf.add_argument("--schedule-id", default="", help="run or triggers: schedule ID")
    wf.add_argument("--scheduled-for", default="", help="schedule: 原计划时刻，需含时区")
    wf.add_argument("--event-id", default="", help="event: 稳定事件 ID")
    wf.add_argument("--event-version", default="", help="event: 日历事件版本")
    wf.add_argument("--workflow-id", default="", help="trigger policy/ledger workflow ID")
    wf.add_argument("--trigger-key", default="", help="retry/history: stable Trigger Ledger key")
    wf.add_argument("--scheduled-from", default="", help="triggers: 计划时刻起点（含时区）")
    wf.add_argument("--scheduled-to", default="", help="triggers: 计划时刻终点（含时区）")
    wf.add_argument("--actor", default="", help="retry: operator identity")
    wf.add_argument("--reason", default="", help="retry: audited compensation reason")
    wf.add_argument("--decision-profile", default="")
    wf.add_argument("--decision-cycle", action="store_true")
    wf.add_argument("--inputs-json", default="{}")
    wf.add_argument("--as-of", default="", help="显式冻结的 ISO 8601 as-of 时点")
    wf.add_argument("--status", default="")
    wf.add_argument("--limit", type=int, default=100)
    td = sub.add_parser("thetadata", help="probe the local ThetaData terminal (inspect schema)")
    td.add_argument("symbol")
    se = sub.add_parser("sector", help="行业分析：show / probe / html / kbperturb（review、layer、crosssection 已退役，用 ats workflow run）")
    se.add_argument(
        "action", choices=["review", "show", "probe", "crosssection", "kbperturb", "layer", "html"]
    )
    se.add_argument("name", nargs="?", default="ai_hardware")
    se.add_argument(
        "--layer",
        default="all",
        help="crosssection/layer: layer key (e.g. L4_interconnect) or 'all'",
    )
    se.add_argument("--date", default="", help="html: YYYY-MM-DD 的已存档 review（默认最新一次）")
    se.add_argument(
        "--structure", action="store_true", help="crosssection: blend KB structure analyst"
    )
    se.add_argument(
        "--mode",
        default="poison",
        choices=["poison", "ablate", "control"],
        help="kbperturb: 倒序判据(poison) / 删掉判据(ablate) / 同一份笔记跑两次测噪声底(control)",
    )
    se.add_argument("--no-llm", action="store_true", help="assemble + stub review, no LLM")
    se.add_argument("--offline", action="store_true", help="skip yfinance (store/static only)")
    se.add_argument("--no-report", action="store_true", help="skip the Obsidian report file")
    evi = sub.add_parser("evidence", help="产业链证据：show / sources / probe / layer 与命题治理（claims、report、propose、proposals、review、kbreview）——只读，绝不下单；observe、collect、articles 已退役，见数据刷新任务")
    evi.add_argument(
        "action",
        choices=[
            "observe",
            "show",
            "claims",
            "report",
            "sources",
            "probe",
            "propose",
            "proposals",
            "review",
            "kbreview",
            "collect",
            "articles",
            "layer",
            "ai-production",
        ],
    )
    evi.add_argument("symbol", nargs="?", help="observe: 标的，如 MU")
    evi.add_argument("--file", default="", help="observe: 用本地文档而不是自动抓取")
    evi.add_argument("--entity", default="", help="show: 只看某实体 / claims: 行业名")
    evi.add_argument("--limit", type=int, default=30, help="show/proposals: 条数")
    evi.add_argument("--accept", action="store_true", help="review: 采纳（默认拒绝）")
    evi.add_argument("--reviewer", default="", help="review: 审阅人")
    evi.add_argument("--note", default="", help="review: 理由")
    evi.add_argument(
        "--sector",
        default="",
        help="layer/ai-production: 必填，sector 配置名",
    )
    evi.add_argument(
        "--layer",
        default="",
        help="layer/ai-production: 必填，只运行该层已注册的 Observer",
    )
    evi.add_argument(
        "--periods", action="append", default=[], help="layer/ai-production: 目标期间，可重复"
    )
    evi.add_argument("--as-of", default="", help="layer/ai-production: 历史可见时点（ISO 8601）")
    evi.add_argument(
        "--format",
        dest="output_format",
        choices=["json", "markdown"],
        default="markdown",
        help="layer/ai-production: 输出格式（默认 Markdown 审阅文档）",
    )
    evi.add_argument("--output", default="", help="layer/ai-production: Markdown 审阅文档路径")
    evi.add_argument("--chart-dir", default="", help="layer/ai-production: PNG/sidecar 输出目录")
    ev = sub.add_parser("events", help="事件日历 (list / upcoming / refresh / status / candidates)")
    ev.add_argument("action", choices=["list", "upcoming", "refresh", "status",
                                       "candidates", "override", "cancel", "withdraw"])
    ev.add_argument("--days", type=int, default=30, help="upcoming window")
    ev.add_argument("--source", action="append", default=[], help="refresh: source ID，可重复")
    ev.add_argument("--event-id", default="")
    ev.add_argument("--date", default="", help="override: 新日期 YYYY-MM-DD")
    ev.add_argument("--actor", default="")
    ev.add_argument("--reason", default="")
    ev.add_argument("--evidence-json", default="{}")
    ev.add_argument("--status", default="")
    ev.add_argument("--limit", type=int, default=100)
    ch = sub.add_parser("chief", help="chief 首席统一决策 (run / show / probe)")
    ch.add_argument("action", choices=["run", "show", "probe"])
    ch.add_argument("--live", action="store_true", help="execute for real (default dry-run)")
    ch.add_argument("--yes", action="store_true", help="auto-approve (non-interactive)")
    ch.add_argument("--no-llm", action="store_true")
    ch.add_argument("--offline", action="store_true", help="skip live broker read")
    ch.add_argument("--fundamental-mode", choices=["routine", "event"], default="routine")
    ch.add_argument("--event-entity", action="append", default=[], dest="event_entities",
                    help="event cycle: an entity whose event starts it (repeatable; default: every "
                         "target with a usable event review)")
    ch.add_argument("--decision-profile", default="ai_hardware")
    ch.add_argument("--no-execute", action="store_true", help="decide only, don't call trader")
    ch.add_argument("--channel", choices=["cli", "pending", "feishu", "feishu_bot"], default="cli",
                    help="cli: 前台批准/驳回修改/拒绝；pending: 留待 `ats approval` 回答并发一条提醒")
    from .approval_cli import add_parser as _add_approval_parser

    _add_approval_parser(sub)
    rk = sub.add_parser("risk", help="risk officer 风控 (report / memo / daily / check)")
    rk.add_argument("action", choices=["report", "memo", "daily", "check"])
    rk.add_argument("symbol", nargs="?", help="check: filter stored decisions by ticker")
    rk.add_argument("--report", action="store_true", help="report: also write an Obsidian file")
    rk.add_argument("--offline", action="store_true", help="show stored review without IBKR")
    jr = sub.add_parser(
        "journal",
        help="交易日志 (doctor / invalidate / review / calibrate / reflect / ledger)；"
        "对账、标记、回合、预测打分由 `ats clerk run` 编排",
    )
    jr.add_argument(
        "action",
        choices=[
            "doctor",
            "invalidate",
            "review",
            "calibrate",
            "reflect",
            "ledger",
        ],
    )
    jr.add_argument("--month", help="ledger: YYYY-MM（默认本月）")
    jr.add_argument(
        "--no-llm",
        action="store_true",
        help="invalidate: 只算 horizon_overdue_days，不调 LLM 判定失效；"
        "reflect: 只出确定性证据+当前需要处理清单，不调 LLM 生成假设",
    )
    jr.add_argument("--quarterly", action="store_true", help="calibrate: 按季度出报告（默认按月）")
    ck = sub.add_parser(
        "clerk", help="Clerk 确定性账本编排：run（串联对账/标记/回合/预测/绩效）")
    ck.add_argument("action", choices=["run", "rebuild", "gaps", "import-statement",
                                       "classify-fill", "baseline", "flex-backfill", "capture-fills",
                                       "fx-backfill"],
                    help="run: 幂等执行一个对账窗口；rebuild: 重建绩效/归因读模型"
                         "（只写派生读模型）；gaps: 查看未清偿审计异常；"
                         "import-statement: 从 IBKR 交易确认报告 PDF 补录历史成交；"
                         "classify-fill: 登记一笔旧成交的合约类型（须你确认）；"
                         "baseline: 期初建账（默认只显示计划，加 --apply 才写入）；"
                         "flex-backfill: 用 IBKR Flex 对账单补入库里缺的成交（加 --dry-run 只看）；"
                         "capture-fills: 现在读一次 TWS 当日成交（不动 Clerk 窗口）；"
                         "fx-backfill: 给没有汇率的旧成交补汇率（Flex 对账单优先，其次当前汇率估算；"
                         "只写汇率与空币种，加 --dry-run 只看）")
    ck.add_argument("--as-of", help="baseline: 建账日 YYYY-MM-DD")
    ck.add_argument("--operator", default="boss", help="baseline: 批准人")
    ck.add_argument("--reason", help="baseline: 建账原因（必填）")
    ck.add_argument("--apply", action="store_true", help="baseline: 真正写入（默认只显示计划）")
    ck.add_argument("--file", help="import-statement: 交易确认报告 PDF 路径")
    ck.add_argument("--exec-id", help="classify-fill: 成交的 exec_id")
    ck.add_argument("--sec-type", choices=["STK", "OPT", "CASH"], help="classify-fill: 合约类型")
    ck.add_argument("--instrument", help="classify-fill: 合约键（期权 OCC 代码；不明用 'SYM OPT?'）")
    ck.add_argument("--multiplier", type=float, default=None, help="classify-fill: 乘数")
    ck.add_argument("--basis", help="classify-fill: 依据（必填）")
    ck.add_argument("--decided-by", default="boss", help="classify-fill: 决定人")
    ck.add_argument("--dry-run", action="store_true", help="只读演练，不写入")
    ck.add_argument("--window-start", help="对账窗口起点 YYYY-MM-DD（默认今天）")
    ck.add_argument("--window-end", help="对账窗口终点 YYYY-MM-DD（默认今天）")
    ck.add_argument("--period", help="rebuild: 期间 YYYY-MM（默认当月）")
    tr = sub.add_parser(
        "trader", help="IBKR trader: portfolio / perf / fills / execute / buy / sell"
    )
    tr.add_argument(
        "action",
        choices=[
            "portfolio",
            "perf",
            "fills",
            "orders",
            "cancel",
            "execute",
            "buy",
            "sell",
        ],
    )
    tr.add_argument("symbol", nargs="?", help="ticker (execute/fills optional; buy/sell required)")
    tr.add_argument("qty", nargs="?", type=float, help="shares (buy/sell)")
    tr.add_argument("--limit", type=float, help="limit price (buy/sell); omit for market")
    tr.add_argument("--days", type=int, default=30, help="perf window (snapshots)")
    tr.add_argument("--report", action="store_true", help="perf: also write an Obsidian report")
    tr.add_argument(
        "--channel",
        choices=["cli", "feishu", "feishu_bot"],
        default="cli",
        help="approval channel for orders",
    )
    tr.add_argument(
        "--offline", action="store_true", help="portfolio: show stored snapshot without IBKR"
    )
    tr.add_argument(
        "--dry-run", action="store_true", help="go through approval but place no orders"
    )
    te = sub.add_parser(
        "technical", help="技术面：show / probe（review 已退役，用 ats workflow run）"
    )
    te.add_argument("action", choices=["review", "show", "probe"])
    te.add_argument("name", nargs="?", default="technical")
    te.add_argument("--offline", action="store_true", help="skip broker + price fetch")
    te.add_argument("--no-report", action="store_true", help="skip the Obsidian report file")
    ma = sub.add_parser("macro", help="宏观分析：show / probe（review 已退役，用 ats workflow run）")
    ma.add_argument("action", choices=["review", "show", "probe"])
    ma.add_argument("name", nargs="?", default="macro")
    ma.add_argument("--no-llm", action="store_true", help="assemble + stub review, no LLM")
    ma.add_argument("--offline", action="store_true", help="skip FRED/yfinance/Tavily")
    ma.add_argument("--no-report", action="store_true", help="skip the Obsidian report file")
    an = sub.add_parser(
        "analyst", help="已退役：information / fundamental 由 Dispatcher 运行，用 ats workflow run"
    )
    an.add_argument("action", choices=["information", "fundamental"],
                    help="information: 信息分析师一轮简报（4.9 独立入口）；"
                         "fundamental: 基本面双模式运行（5.1 独立入口）")
    an.add_argument("--mode", choices=["routine", "event"], default="routine",
                    help="fundamental 运行模式：routine 例行（简报/预期数据触发）、"
                         "event 事件（财报/公司事件触发）")
    an.add_argument("--symbol", help="fundamental 目标的标的")
    an.add_argument("--trigger", default="",
                    help="触发类型（routine: information_brief_update|expectation_data_change；"
                         "event: earnings_release|company_event）；缺省按模式取默认")
    an.add_argument("--fiscal-label", dest="fiscal_label", default="",
                    help="event 模式的目标报告期（如 Q3 FY2026）")
    an.add_argument("--cutoff", default="", help="event 模式的基线冻结时刻（ISO）")
    an.add_argument("--refresh-baseline", dest="refresh_baseline", action="store_true",
                    help="routine 模式：按最新信息简报重建下一期财报前基线（财报发布后拒绝）")
    an.add_argument("--no-ingest", action="store_true",
                    help="跳过数据层研究文章采集，只处理已准入文档")
    an.add_argument("--no-llm", action="store_true", help="无 LLM：只做识别与投影，不抽取")
    pe = sub.add_parser(
        "pead", help="PEAD：show / transcriptprobe（prep、score、monitor、watch、research 已退役，用 ats workflow run）"
    )
    pe.add_argument(
        "action",
        choices=[
            "prep",
            "score",
            "show",
            "monitor",
            "watch",
            "research",
            "transcriptprobe",
        ],
    )
    pe.add_argument("symbol", nargs="?", help="ticker (omit for `watch` / `research`)")
    pe.add_argument(
        "--quarters",
        type=int,
        default=4,
        help="transcriptprobe: how many recent quarters per target",
    )
    pe.add_argument("--transcript", help="path or URL to the earnings-call transcript (score)")
    pe.add_argument("--live", action="store_true", help="execute (IBKR paper); default dry-run")
    pe.add_argument("--yes", action="store_true", help="auto-approve (non-interactive)")
    pe.add_argument("--offline", action="store_true", help="skip live data + IBKR (local only)")
    pe.add_argument("--no-llm", action="store_true", help="skip LLM (stub agents)")
    pe.add_argument(
        "--channel",
        choices=["cli", "feishu", "feishu_bot"],
        default="cli",
        help="approval channel when --chief executes",
    )
    pe.add_argument(
        "--chief",
        action="store_true",
        help="score: run the Chief immediately after the recommendation persists",
    )
    args = parser.parse_args(argv)

    moved = RESEARCH_MANUAL_MOVED.get((args.command, getattr(args, "action", "")))
    if moved:
        print(f"❌ `ats {args.command} {args.action}` 已退役：研究分析师由 Dispatcher 运行，请用\n   {moved}")
        return 2

    if args.command in RETIRED_MIGRATION_COMMANDS:
        print(f"❌ `ats {args.command}` 已退役：{RETIRED_MIGRATION_COMMANDS[args.command]}；"
              "用法存档见 docs/archive/R5C_MIGRATION_COMMANDS.md")
        return 2

    # Business and ordinary inspection entries never create a missing authority. The administrative
    # bootstrap (`ats cutover state --bootstrap`) went with `ats cutover` (r5 8.4); isolated runs bootstrap
    # their own authority in `isolation.isolated_run`.
    from ..workflow.cutover import assert_runtime_startup
    assert_runtime_startup(caller="ats.runtime.cli.main")

    if args.command == "data":
        persistent_write = args.action in {
            "ingest", "article-ingest", "document-ingest", "fixed-ingest", "factset-import", "factset-reprocess",
            "factset-refresh", "factset-semantic-refresh", "calendar-refresh", "calendar-finalize",
        } or (args.action == "release-check" and args.ingest_new)
        persistent_write = persistent_write or args.action == "chain-source-collect"
        persistent_write = persistent_write or (
            args.action in {"evidence-observe", "macro-news-collect"} and not args.dry_run)
        persistent_write = persistent_write or (args.action == "article-metadata-reconcile" and args.apply)
        worker_task_id = os.environ.get("ATS_PERSISTENT_QUEUE_TASK_ID", "")
        if persistent_write:
            from ..data.persistent_queue import PersistentIngestionQueue

            queue = PersistentIngestionQueue()
            if worker_task_id:
                worker_id = os.environ.get("ATS_PERSISTENT_QUEUE_LEASE_OWNER", "")
                worker_source = os.environ.get("ATS_PERSISTENT_QUEUE_SOURCE_ID", "")
                if not queue.valid_lease(worker_task_id, worker_id, worker_source):
                    parser.error("persistent data writes require a valid queue worker lease")
            else:
                # Only explicitly isolated acceptance runs may write directly to a
                # disposable database; every operational/persistent write is queued.
                isolated_replay = bool(args.force and args.db_path and args.artifact_root)
                if not isolated_replay:
                    raw_args = list(argv) if argv is not None else sys.argv[1:]
                    trigger_at = datetime.now(timezone.utc).replace(second=0, microsecond=0)
                    source_id = args.source or args.value or args.group or args.dataset
                    if args.action == "evidence-observe":
                        source_id = source_id or args.entity
                    elif args.action == "macro-news-collect":
                        source_id = source_id or "macro_theme_news"
                    if not source_id:
                        parser.error("persistent ingestion requires --source, VALUE, --group or --dataset")
                    registry_bytes = b"".join(
                        (Path("config/data") / name).read_bytes()
                        for name in ("structured.yaml", "unstructured.yaml", "schedules.yaml")
                    )
                    policy = hashlib.sha256(registry_bytes + " ".join(raw_args).encode()).hexdigest()
                    task_scope = {"source": args.source or args.value, "group": args.group,
                                  "dataset": args.dataset, "entity": args.entity,
                                  "periods": args.periods, "query_scope": args.query_scope}
                    if args.action.startswith("factset-"):
                        task_scope["sources"] = [
                            "factset_earnings_insight_doc",
                            "factset_earnings_insight_metrics",
                        ]
                    task_id, created = queue.enqueue(
                        source_id=source_id,
                        scope=task_scope,
                        trigger_kind="manual", trigger_ref=trigger_at.isoformat(),
                        command=["ats", *raw_args], policy_fingerprint=policy,
                        requested_as_of=trigger_at.isoformat(), priority=20,
                    )
                    result = queue.run_one(task_id=task_id)
                    if result is None:
                        existing = queue.get(task_id) or {}
                        print(json.dumps({"task_id": task_id, "created": created,
                                          "status": existing.get("status", "queued")},
                                         ensure_ascii=False))
                        return 0
                    print(json.dumps({"task_id": task_id, "status": result["status"],
                                      "ingestion_result": result.get("stdout", {})},
                                     ensure_ascii=False, indent=2, default=str))
                    return 0 if result["status"] in {"succeeded", "no_change", "quarantined", "partial"} else 1
        if args.action == "macro-news-collect":
            from ..data.collection.macro_news import collect

            packet = collect(dry_run=args.dry_run)
            print(json.dumps(packet, ensure_ascii=False, default=str))
            return 1 if packet["status"] == "failed" else 0
        if args.action == "evidence-observe":
            from .evidence_observe import observe_entity

            if not args.entity:
                parser.error("data evidence-observe requires --entity")
            packet = observe_entity(args.entity, lookback_days=args.lookback_days, dry_run=args.dry_run)
            print(json.dumps(packet, ensure_ascii=False, default=str))
            return 1 if packet["status"] == "failed" else 0
        if args.action == 'article-metadata-reconcile':
            from ..data.pipelines.unstructured.article_metadata import reconcile_article_metadata
            if not args.source or not args.as_of or not args.document_ids:
                parser.error('metadata reconciliation requires --source, --as-of and bounded --document-ids')
            ids = json.loads(Path(args.document_ids).read_text())
            if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
                parser.error('document-ids must be a nonempty string array')
            fields = json.loads(Path(args.field_evidence).read_text()) if args.field_evidence else None
            result = reconcile_article_metadata(args.source,
                now=datetime.fromisoformat(args.as_of), dry_run=not args.apply,
                document_ids=ids, field_evidence=fields, limit=args.limit)
            states = {row['status'] for row in result['rows']}
            result['status'] = ('partial' if 'body_integrity_gap' in states else
                                'no_change' if states == {'no_change'} else 'succeeded')
            print(json.dumps(result, ensure_ascii=False, default=str))
            return 0
        if args.action in {"search", "company", "claim", "lineage"} and not args.value:
            parser.error(f"data {args.action} requires VALUE")
        return run_data(
            args.action,
            args.value,
            source=args.source,
            group=args.group,
            ingest_new=args.ingest_new,
            series=args.series,
            entity=args.entity,
            provider=args.provider,
            since=args.since,
            as_of=args.as_of,
            limit=args.limit,
            vintages=args.vintages,
            dataset=args.dataset,
            metric=args.metric,
            status=args.status,
            product=args.product,
            output_format=args.output_format,
            periods=args.periods,
            query_scope=args.query_scope,
            db_path=args.db_path,
            artifact_root=args.artifact_root,
            force=args.force,
            force_check=args.force_check,
            confirm=args.confirm,
            exported=args.exported,
            purge_note=args.purge_note,
            apply=args.apply,
            mode=args.mode,
            release_file=args.release_file,
            kind=args.kind,
            operation=args.operation,
            window=args.window,
            entities=args.entities,
            period=args.period,
            scope=args.scope,
            report_path=args.report_path,
            acquire=args.acquire,
            chart_dir=args.chart_dir,
            provider_lookup_attempts=args.provider_lookup_attempts,
            provider_lookup_retry_seconds=args.provider_lookup_retry_seconds,
            approve_title_url_review=args.approve_title_url_review,
            assurance_consumer=args.assurance_consumer,
            assurance_domain=args.assurance_domain,
            assurance_contract_version=args.assurance_contract_version,
            evidence_details_json=args.evidence_details_json,
            evidence_type=args.evidence_type,
            evidence_outcome=args.evidence_outcome,
            evidence_scope_json=args.evidence_scope_json,
            evidence_summary=args.evidence_summary,
            evidence_command_summary=args.evidence_command_summary,
            evidence_environment_names=args.evidence_environment_names,
            evidence_prerequisites=args.evidence_prerequisites,
            evidence_fingerprint_paths=args.evidence_fingerprint_paths,
            evidence_reason=args.evidence_reason,
            evidence_event_id=args.evidence_event_id,
        )
    if args.command == "ibkr":
        return ibkr_probe()
    if args.command == "serve":
        from .server import serve

        serve(host=args.host, port=args.port)
        return 0
    if args.command == "schedule":
        from .dispatcher_daemon import start

        start()
        return 0
    if args.command == "consumer-cutover":
        return consumer_cutover_command(args, parser=parser)
    if args.command == "intake":
        return intake_verification_command(args, parser=parser)
    if args.command == "retirement":
        return retirement_command(args, parser=parser)

    if args.command == "live":
        return live_command(args, parser=parser)

    if args.command == "dispatch":
        return dispatch_command(args, parser=parser)

    if args.command == "workflow":
        return workflow_command(args, parser=parser)
    if args.command == "thetadata":
        return thetadata_probe(args.symbol)
    if args.command == "sector":
        if args.action == "show":
            return sector_show(args.name)
        if args.action == "probe":
            return sector_probe(args.name, live_data=not args.offline)
        if args.action == "kbperturb":
            # Layer 2 of KB validation: the audit shows the analyst CITES the criteria,
            # this shows whether they PRODUCED the score. Never persists, never touches
            # the production notes.
            from ..agents.sector import kb_perturb

            if args.layer in ("", "all"):
                print("kbperturb 需要指定 --layer（一次只扰动一层）")
                return 2
            base, other = kb_perturb.run(args.name, args.layer, mode=args.mode)
            print()
            print(kb_perturb.render(base, other, mode=args.mode))
            return 0

        if args.action == "layer":
            return run_layer_review(
                args.name, args.layer, use_llm=not args.no_llm, live_data=not args.offline
            )
        if args.action == "html":
            return run_sector_html(args.name, date=args.date)
        run_sector_review(
            args.name,
            use_llm=not args.no_llm,
            live_data=not args.offline,
            write_report=not args.no_report,
        )
        return 0
    if args.command == "evidence":
        return run_evidence(
            args.action,
            args.symbol,
            file=args.file,
            entity=args.entity,
            limit=args.limit,
            accept=args.accept,
            reviewer=args.reviewer,
            note=args.note,
            sector=args.sector,
            layer=args.layer,
            periods=args.periods,
            as_of=args.as_of,
            output_format=args.output_format,
            chart_dir=args.chart_dir,
            output=args.output,
        )
    if args.command == "events":
        return events_calendar_command(args, parser=parser)
    if args.command == "approval":
        from .approval_cli import dispatch as _approval_dispatch

        return _approval_dispatch(args)
    if args.command == "chief":
        if args.action == "show":
            return chief_show()
        if args.action == "probe":
            return chief_probe(offline=args.offline)
        return run_chief(
            execute=not args.no_execute,
            dry_run=not args.live,
            channel=args.channel,
            use_llm=not args.no_llm,
            auto=args.yes,
            offline=args.offline,
            fundamental_mode=args.fundamental_mode,
            decision_profile=args.decision_profile,
            event_entities=args.event_entities,
        )
    if args.command == "risk":
        if args.action == "report":
            return risk_report(write_report=args.report, offline=getattr(args, "offline", False))
        if args.action == "memo":
            return risk_memo()
        if args.action == "daily":
            return risk_daily()
        return risk_check(args.symbol)
    if args.command == "clerk":
        if args.action == "run":
            from ..execution import clerk

            out = clerk.clerk_run(dry_run=args.dry_run,
                                  window_start=args.window_start,
                                  window_end=args.window_end)
            print(f"Clerk run {out['run_id']} [{out['status']}]"
                  + ("（复用已有窗口结果）" if out.get("reused") else ""))
            for step, r in out.get("steps", {}).items():
                print(f"  {step}: {r}")
            for e in out.get("errors", []):
                print(f"  ⚠️ {e}")
            if out.get("gaps_registered"):
                print(f"  登记对账缺口 {out['gaps_registered']} 个")
            if not args.dry_run:
                from ..execution import clerk_report

                report = clerk_report.publish(out, source="手动")
                print(f"  日报 → {report.get('path') or report.get('error') or '（未配置 Obsidian 输出目录）'}")
            return 1 if out["status"] == "failed" else 0
        if args.action == "baseline":
            from ..data.runtime.clerk_reads import ClerkBrokerReads
            from ..execution import baseline
            from ..memory import get_store
            from ..trader.execute import IBKRBroker
            from ..workflow.runtime_reads import scoped_read

            if not (args.as_of and args.reason):
                print("需要 --as-of 和 --reason")
                return 2
            store = get_store()

            @scoped_read("clerk")
            def _baseline():
                return baseline.establish_baseline(
                    store=store, broker=ClerkBrokerReads(IBKRBroker(), store), as_of=args.as_of,
                    operator=args.operator, reason=args.reason, apply=args.apply)

            plan = _baseline()
            print(f"期初建账 {plan['as_of']}：持仓 {plan['positions']} 个，净值 {plan['net_liquidation']:,.2f}")
            print(f"  封存历史异常：{plan['sealed'] or '无'}；作废当日已完成的运行：{plan['voided_runs'] or '无'}")
            print("  已写入" if plan["applied"] else
                  ("  已建账，无需重复" if plan["already"] else "  （演练，未写入；加 --apply 执行）"))
            return 0
        if args.action == "classify-fill":
            from ..memory import get_store

            if not (args.exec_id and args.sec_type and args.instrument and args.basis):
                print("需要 --exec-id --sec-type --instrument --basis")
                return 2
            mult = args.multiplier or (100.0 if args.sec_type == "OPT" else 1.0)
            try:
                get_store().classify_fill(args.exec_id, sec_type=args.sec_type,
                                          instrument_key=args.instrument, multiplier=mult,
                                          basis=args.basis, decided_by=args.decided_by)
            except KeyError:
                print(f"没有这笔成交：{args.exec_id}")
                return 1
            print(f"已登记：{args.exec_id} → {args.sec_type} {args.instrument!r}（乘数 {mult:g}）")
            return 0
        if args.action == "flex-backfill":
            from ..trader import flex_statement

            out = flex_statement.backfill(dry_run=args.dry_run)
            print(flex_statement.render(out))
            return 0 if out["status"] in ("ok", "skipped") else 1
        if args.action == "fx-backfill":
            from ..trader import flex_statement, fx_backfill
            from ..trader.execute import IBKRBroker

            flex_rows: list[dict] = []
            creds = flex_statement.credentials()
            if creds is not None:
                try:
                    flex_rows = flex_statement.parse_trades(flex_statement.fetch_statement(*creds))
                except Exception as exc:  # noqa: BLE001
                    print(f"Flex 对账单读不到（{flex_statement._scrub(str(exc), creds[0])[:120]}），只用当前汇率估算")
            out = fx_backfill.backfill(dry_run=args.dry_run, flex_fills=flex_rows,
                                       live_rates=lambda: IBKRBroker().exchange_rates())
            print(fx_backfill.render(out))
            return 0
        if args.action == "capture-fills":
            from ..trader import fills_capture, reconcile

            out = fills_capture.capture()
            print(reconcile.render(out))
            return 1 if out["errors"] else 0
        if args.action == "import-statement":
            from ..trader import statement_import

            if not args.file:
                print("需要 --file <交易确认报告.pdf>")
                return 2
            try:
                out = statement_import.import_statement(Path(args.file), dry_run=args.dry_run)
            except statement_import.StatementMismatch as exc:
                print(f"对账单校验未通过，整批不写入：{exc}")
                return 1
            print(f"对账单补录{'（演练，未写入）' if out['dry_run'] else ''}：执行明细 "
                  f"{out['executions']} 条，核对：{out['verified_by']}；"
                  f"跳过 {out['skipped_sections']}；与 TWS 已抓成交重复 "
                  f"{out['duplicates_of_tws']} 条；归因 {out['origins']}；按类型 {out['by_type']}；"
                  f"新写入 {out['inserted']} 条，补合约字段 {out['contract_backfilled']} 条；"
                  f"按券商记录认定旧成交合约 {len(out['legacy_classified'])} 笔")
            return 0
        if args.action == "rebuild":
            from ..execution import rebuild
            from ..memory import get_store

            period = args.period or datetime.now(timezone.utc).strftime("%Y-%m")
            for kind_fn in (rebuild.rebuild_performance, rebuild.rebuild_attribution):
                out = kind_fn(get_store(), period=period)
                print(f"  {kind_fn.__name__}: {out['status']}"
                      f"（method={out.get('method_version', 'clerk-v1')}, "
                      f"facts={out.get('source_facts_hash', '-')[:12]}）")
            return 0
        if args.action == "gaps":
            from ..execution import rebuild
            from ..memory import get_store

            rows = rebuild.list_open_exceptions(get_store())
            if not rows:
                print("无未清偿审计异常 ✅")
                return 0
            by_kind: dict[str, int] = {}
            for r in rows:
                by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
            print(f"未清偿审计异常 {len(rows)} 项：")
            for kind, n in sorted(by_kind.items()):
                print(f"  {kind}: {n}")
            return 1
    if args.command == "journal":
        if args.action == "invalidate":
            from ..journal import invalidation

            return invalidation.run(use_llm=not args.no_llm)
        if args.action == "review":
            from ..journal import episode_report

            return episode_report.run()
        if args.action == "calibrate":
            from ..journal import calibration

            return calibration.run(quarterly=args.quarterly)
        if args.action == "reflect":
            from ..journal import critic

            return critic.run(use_llm=not args.no_llm)
        if args.action == "ledger":
            from ..journal import report as journal_report

            return journal_report.run(args.month or "")
        from ..journal import doctor

        return doctor.run()
    if args.command == "trader":
        if args.action == "portfolio":
            return trader_portfolio(offline=getattr(args, "offline", False))
        if args.action == "perf":
            return trader_perf(args.days, write_report=args.report)
        if args.action == "fills":
            return trader_fills(args.symbol)
        if args.action == "orders":
            return trader_orders()
        if args.action == "cancel":
            return trader_cancel(args.symbol)
        if args.action == "execute":
            return trader_execute(args.symbol, channel=args.channel, dry_run=args.dry_run)
        # buy / sell — manual order (symbol + qty required)
        if not args.symbol or args.qty is None:
            parser.error(f"trader {args.action} requires SYMBOL and QTY")
        return trader_manual(
            args.action,
            args.symbol,
            args.qty,
            limit=args.limit,
            channel=args.channel,
            dry_run=args.dry_run,
        )
    if args.command == "technical":
        if args.action == "show":
            return technical_show(args.name)
        if args.action == "probe":
            return technical_probe(args.name, live_data=not args.offline)
        return run_technical_review(
            args.name, live_data=not args.offline, write_report=not args.no_report
        )
    if args.command == "macro":
        if args.action == "show":
            return macro_show(args.name)
        if args.action == "probe":
            return macro_probe(args.name, live_data=not args.offline)
        run_macro_review(
            args.name,
            use_llm=not args.no_llm,
            live_data=not args.offline,
            write_report=not args.no_report,
        )
        return 0
    if args.command == "analyst":
        if args.action == "information":
            return run_information_pass(use_llm=not args.no_llm,
                                        ingest_research=not args.no_ingest)
        if args.action == "fundamental":
            return _run_analyst_fundamental(args)
        return 1
    if args.command == "pead":
        if args.action == "transcriptprobe":
            return run_transcript_probe(
                [args.symbol] if args.symbol else None, quarters=args.quarters
            )
        if not args.symbol:
            parser.error("pead %s requires a symbol" % args.action)
        if args.action == "show":
            return pead_show(args.symbol)
        run_pead(
            args.symbol,
            args.action,
            dry_run=not args.live,
            auto=args.yes,
            offline=args.offline,
            use_llm=not args.no_llm,
            transcript=args.transcript,
            channel=args.channel,
            chief=getattr(args, "chief", False),
        )
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
