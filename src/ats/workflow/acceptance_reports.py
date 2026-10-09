"""Append-only independent new-entry acceptance, separate from legacy comparisons.

Reports are observations, never execution authorization. Each execution carries
its own pre-run matrix. Both new runs must satisfy that matrix independently;
different prose and projection IDs do not require a difference waiver.
"""
from __future__ import annotations

import json
import sqlite3
import hashlib
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, UTC
from pathlib import Path
from types import SimpleNamespace

from . import acceptance_assertions as assertions, acceptance_matrix as matrices
from . import shadow_replay as replay
from .acceptance_disposition import dispose

TYPE = "new-entry-acceptance-v1"
ENTRY = "ats.workflow.acceptance_reports.dispatch_entry"
SCHEMA = """
CREATE TABLE IF NOT EXISTS new_entry_reports(report_id TEXT PRIMARY KEY, body TEXT NOT NULL, body_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS new_entry_signoffs(seq INTEGER PRIMARY KEY AUTOINCREMENT,
 report_id TEXT NOT NULL REFERENCES new_entry_reports(report_id), action TEXT NOT NULL,
 actor TEXT NOT NULL, authority TEXT NOT NULL, reason TEXT NOT NULL, body_hash TEXT NOT NULL,
 previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL, recorded_at TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS new_report_no_update BEFORE UPDATE ON new_entry_reports
 BEGIN SELECT RAISE(ABORT,'acceptance reports are append-only'); END;
CREATE TRIGGER IF NOT EXISTS new_report_no_delete BEFORE DELETE ON new_entry_reports
 BEGIN SELECT RAISE(ABORT,'acceptance reports are append-only'); END;
CREATE TRIGGER IF NOT EXISTS new_signoff_no_update BEFORE UPDATE ON new_entry_signoffs
 BEGIN SELECT RAISE(ABORT,'signoffs are append-only'); END;
CREATE TRIGGER IF NOT EXISTS new_signoff_no_delete BEFORE DELETE ON new_entry_signoffs
 BEGIN SELECT RAISE(ABORT,'signoffs are append-only'); END;
"""


def _path(path):
    from .shadow_reports import default_shadow_db_path
    from ..config import REPO_ROOT
    target = Path(path or default_shadow_db_path())
    return target if target.is_absolute() else REPO_ROOT / target


def _read(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _write(path):
    path = _path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def fingerprint():
    from .business_replay_inputs import implementation_hashes
    return implementation_hashes()


def _config(matrix):
    from ..config import REPO_ROOT
    root = Path(matrix.body["plan"]["config_root"] or REPO_ROOT / "config").resolve()
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*.yaml"))}


def dispatch_entry(*, read_input, logical_time, matrix, value, root):
    """Concrete independent replay entry: real Dispatcher, frozen transports.

    Research and decision/trading use the same real business runner. No caller
    supplied results, legacy entry or alternate role adapters are accepted.
    """
    from .business_replay_inputs import Tape
    value.validate()
    if value.input_hash() != read_input.__self__.value.input_hash():
        raise ValueError("execution value differs from recorded frozen inputs")
    matrix = matrices.restore(matrix.as_row())
    matrices.check_inputs(matrix, value.packet)
    if logical_time.isoformat() != matrix.body["plan"]["as_of"]:
        raise ValueError("fixed execution clock differs")
    if value.contents.get("model_config", {}).get("dependency_hashes") != fingerprint():
        raise ValueError("fixed implementation fingerprint drifted")
    tape = Tape(replay=read_input.__self__, rows=value.contents["model_config"]["rows"])
    seed = value.contents["projection_hash"]["seed"]
    if {name:hashlib.sha256(text.encode()).hexdigest() for name,text in seed["config"].items()} != _config(matrix):
        raise ValueError("seed configuration differs from pre-run matrix configuration")
    from .acceptance_runner import _execute
    tape.record_clock = value.contents["model_config"].get("record_clock", False)
    output = _execute(matrix,seed,tape,root,value.contents["projection_hash"].get("execution", {}))
    return {"type": TYPE, "matrix": matrix.as_row(), "_packet": asdict(value.packet), **output,
            "consumed_reads": sorted(read_input.__self__.used),
            "implementation": fingerprint(), "config": _config(matrix), "tradable": False}



def run(*,matrix,input_store,input_hash,root,replay_run_id):
    """Execute a pre-frozen matrix on fixed inputs in a fresh isolated side."""
    value = replay.load_inputs(input_hash,path=input_store)
    return replay.ReplayReads(value).invoke(dispatch_entry,run_id=replay_run_id,path=input_store,
        matrix=matrices.restore(matrix.as_row()),value=value,root=root)


def _expectations(matrix):
    body = matrix.body
    # Execution IDs differ, while independent requirements/config/scope do not.
    body.pop("plan_hash")
    body["plan"].pop("run_id")
    body["plan"].pop("plan_id")
    return body


def _measure(run, value):
    output = run["output"]
    if run["entry"] != ENTRY or output.get("type") != TYPE or output.get("tradable") is not False:
        raise ValueError("actual new-entry execution evidence missing; historical/tool report only")
    from ..config import REPO_ROOT
    source = Path(run["source"]).resolve()
    if source != Path(__file__).resolve() or not source.is_relative_to(REPO_ROOT / "src/ats") or (
            hashlib.sha256(source.read_bytes()).hexdigest() != run["source_hash"]):
        raise ValueError("execution entry fingerprint drifted")
    matrix = matrices.restore(output["matrix"])
    if (run["input_hash"] != value.input_hash() or output["_packet"] != asdict(value.packet)
            or run["logical_time"] != value.packet.surfaces["logical_eval_time"]
            or not run["reads"] or run["reads"] != output["consumed_reads"]):
        raise ValueError("actual fixed execution/input/read binding differs")
    if output["implementation"] != fingerprint() or output["config"] != _config(matrix):
        raise ValueError("execution implementation/config fingerprint drifted")
    root = Path(output["side_root"]).resolve()
    actual_config = {str(p.relative_to(root/"config")):hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in sorted((root/"config").rglob("*.yaml"))}
    if actual_config != output["config"]:
        raise ValueError("actual execution configuration drifted")
    from .isolation import build_environment, inspect_isolated_records
    env = build_environment(root)
    if Path(output["store"]).resolve() != env.path_for("ATS_DB_PATH").resolve():
        raise ValueError("execution ledger escapes isolated side")
    # Only readonly SQLite connections: checking never opens TradingMemory and
    # therefore cannot run schema migrations or fabricate missing evidence.
    with inspect_isolated_records(root), closing(_read(output["store"])) as conn, closing(_read(env.path_for("ATS_DISPATCH_STATE_PATH"))) as schedule:
        actual_schedule = {"claims": [dict(r) for r in schedule.execute("SELECT * FROM schedule_runtime_claims")],
                           "history": [dict(r) for r in schedule.execute("SELECT * FROM schedule_runtime_history ORDER BY seq")]}
        if actual_schedule != {k: output["schedule"][k] for k in actual_schedule}:
            raise ValueError("actual schedule record drifted")
        if matrix.body["batch_class"] == "trading":
            refusal = output.get("broker_refusal")
            if not refusal or not refusal.get("reason_code"):
                raise ValueError("actual real-broker refusal missing")
            row = conn.execute("SELECT body FROM acceptance_broker_refusals WHERE refusal_id=?",(refusal["refusal_id"],)).fetchone()
            if not row or json.loads(row[0]) != refusal:
                raise ValueError("durable real-broker refusal differs")
        evidence = {**output, "store": SimpleNamespace(conn=conn,path=output["store"]), "inputs":value}
        result = assertions.evaluate(matrix,evidence)
    return matrix, result


def _executions(proof, input_hash):
    ids = [proof["new_run_id"], proof["replay_run_id"]]
    if not all(ids) or ids[0] == ids[1]:
        raise ValueError("distinct actual new/replay run IDs required")
    value = replay.load_inputs(input_hash,path=proof["input_store"])
    with closing(_read(proof["input_store"])) as conn:
        rows = [conn.execute("SELECT body FROM shadow_replay_runs WHERE run_id=?",(i,)).fetchone() for i in ids]
    if not all(rows):
        raise ValueError("actual execution evidence missing")
    runs = [json.loads(r[0]) for r in rows]
    if [r["run_id"] for r in runs] != ids:
        raise ValueError("execution run IDs differ")
    measured = [_measure(r,value) for r in runs]
    if _expectations(measured[0][0]) != _expectations(measured[1][0]):
        raise ValueError("new/replay requirements differ")
    if (runs[0]["output"]["store"] == runs[1]["output"]["store"] or
            measured[0][0].body["plan"]["run_id"] == measured[1][0].body["plan"]["run_id"]):
        raise ValueError("replay must execute independently in another ledger/run")
    return value, runs, measured


def record(*, report_id, input_hash, proof, actor, path=None, supersedes="", legacy_diagnostics=()):
    if not report_id.strip() or not actor.strip():
        raise ValueError("report ID and actor required")
    value, runs, measured = _executions(proof,input_hash)
    matrix = measured[0][0]
    body = {"type":TYPE,"report_id":report_id,"requirements_version":matrices.REQUIREMENTS_VERSION,
        "matrix_version":matrices.VERSION,"matrix":matrix.as_row(),"scope":value.packet.scope,
        "consumer_id":value.packet.consumer_id,"batch_class":value.packet.batch_class,
        "input_hash":input_hash,"proof":proof,"executions":[
            {"run_id":r["run_id"],"workflow_run_id":m.body["plan"]["run_id"],"matrix_hash":m.matrix_hash,
             "source_hash":r["source_hash"],"store":r["output"]["store"],"output_refs":sorted({ref for a in result.results for ref in a.refs}),
             "assertions":result.as_dict(),"disposition":dispose(result)} for r,(m,result) in zip(runs,measured)],
        "implementation":fingerprint(),"config":_config(matrix),"supersedes":supersedes,
        "legacy_diagnostics":list(legacy_diagnostics),"actor":actor,"recorded_at":datetime.now(UTC).isoformat()}
    with closing(_write(path)) as conn:
        if supersedes:
            prior = conn.execute("SELECT body FROM new_entry_reports WHERE report_id=?",(supersedes,)).fetchone()
            if prior is None:
                raise ValueError("superseded report missing")
            previous = json.loads(prior[0])["proof"]
            if {previous["new_run_id"],previous["replay_run_id"]} & {proof["new_run_id"],proof["replay_run_id"]}:
                raise ValueError("retest requires new actual executions")
        conn.execute("INSERT INTO new_entry_reports VALUES (?,?,?)",(report_id,_json(body),replay.digest(body)))
        conn.commit()
    return body


def read(report_id, *, path=None):
    with closing(_read(_path(path))) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='new_entry_reports' AND type='table'").fetchone():
            raise ValueError("new-entry report missing; historical comparison has no new execution evidence")
        row = conn.execute("SELECT * FROM new_entry_reports WHERE report_id=?",(report_id,)).fetchone()
        if row is None:
            raise ValueError("new-entry report missing; historical comparison has no new execution evidence")
        body = json.loads(row["body"])
        if body.get("type") != TYPE or body.get("report_id") != report_id or replay.digest(body) != row["body_hash"]:
            raise ValueError("new-entry report hash/type invalid")
        events = [dict(r) for r in conn.execute("SELECT * FROM new_entry_signoffs WHERE report_id=? ORDER BY seq",(report_id,))]
    previous = ""
    for event in events:
        if event["action"] not in {"signed_off","rejected","revoked"} or not all(
                str(event[k]).strip() for k in ("actor","authority","reason","recorded_at")):
            raise ValueError("valid signoff identity/authority/reason missing")
        payload = {k:v for k,v in event.items() if k not in {"seq","event_hash"}}
        if event["previous_hash"] != previous or event["body_hash"] != row["body_hash"] or replay.digest(payload) != event["event_hash"]:
            raise ValueError("signoff chain invalid")
        previous = event["event_hash"]
    return {"body":body,"body_hash":row["body_hash"],"events":events,
            "status":events[-1]["action"] if events else "unsigned"}


def _validate(body):
    if body["requirements_version"] != matrices.REQUIREMENTS_VERSION or body["matrix_version"] != matrices.VERSION:
        raise ValueError("requirement/matrix version inapplicable")
    value, runs, measured = _executions(body["proof"],body["input_hash"])
    if body["matrix"] != measured[0][0].as_row() or body["scope"] != value.packet.scope:
        raise ValueError("report scope/matrix differs from execution")
    if (body["consumer_id"] != value.packet.consumer_id or body["batch_class"] != value.packet.batch_class
            or body["report_id"] == ""):
        raise ValueError("report consumer/class invalid")
    if body["implementation"] != fingerprint() or body["config"] != _config(measured[0][0]):
        raise ValueError("report fingerprint drifted")
    if len(body["executions"]) != 2:
        raise ValueError("both actual new/replay conclusions required")
    for saved,run,(matrix,result) in zip(body["executions"],runs,measured):
        if _json(saved["assertions"]) != _json(result.as_dict()) or saved["matrix_hash"] != matrix.matrix_hash:
            raise ValueError("actual assertions differ from immutable report")
        if (saved["run_id"] != run["run_id"] or saved["workflow_run_id"] != matrix.body["plan"]["run_id"]
                or saved["source_hash"] != run["source_hash"] or saved["store"] != run["output"]["store"]
                or saved["output_refs"] != sorted({ref for a in result.results for ref in a.refs})
                or saved["disposition"] != dispose(result)):
            raise ValueError("report execution IDs/refs/disposition differ")
        if not result.passed:
            raise ValueError("required assertions failed or untested; repair/measure and create a new report")


def signoff(*, report_id, action, actor, authority, reason, path=None):
    if action not in {"signed_off","rejected","revoked"} or not all(str(v).strip() for v in (actor,authority,reason)):
        raise ValueError("explicit actor/authority/reason and known action required")
    state = read(report_id,path=path)
    if action == "signed_off":
        _validate(state["body"])
    with closing(_write(path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute("SELECT event_hash FROM new_entry_signoffs WHERE report_id=? ORDER BY seq DESC LIMIT 1",(report_id,)).fetchone()
        event = {"report_id":report_id,"action":action,"actor":actor,"authority":authority,"reason":reason,
            "body_hash":state["body_hash"],"previous_hash":previous[0] if previous else "","recorded_at":datetime.now(UTC).isoformat()}
        conn.execute("INSERT INTO new_entry_signoffs(report_id,action,actor,authority,reason,body_hash,previous_hash,event_hash,recorded_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (*[event[k] for k in ("report_id","action","actor","authority","reason","body_hash","previous_hash")],replay.digest(event),event["recorded_at"]))
        conn.commit()
    return read(report_id,path=path)


def check_batch_report(batch, *, path=None):
    try:
        state = read(batch.shadow_report_id,path=path)
        body = state["body"]
        if state["status"] != "signed_off":
            raise ValueError("valid signoff missing/rejected/revoked")
        classes = {"research_read":{"research_read"},"schedule":{"research_read"},
                   "internal_state_approval":{"decision","trading"},"live_trader":{"trading"}}
        if body["scope"] != batch.scope or body["batch_class"] not in classes.get(batch.batch_class,set()):
            raise ValueError("report scope/class inapplicable")
        dimensions = {"input_snapshot":"inputs","schedule_omission":"schedule","analyst_output":"analysis",
                      "risk_verdict":"risk","approval_chain":"approval","trade_attribution":"attribution"}
        required = set(getattr(batch,"required_surfaces", ()))
        required = {dimensions.get(s,s) for s in required}
        available = {a["dimension"] for a in body["matrix"]["assertions"] if a["required"]}
        if batch.batch_class == "internal_state_approval":
            required.update({"risk","approval","attribution"})
        if not required <= available:
            raise ValueError("requested required dimensions not measured")
        consumers = set(getattr(batch,"consumers",()) or getattr(batch,"consumer_ids",()))
        if getattr(batch,"consumer_id", ""):
            consumers.add(batch.consumer_id)
        covered = {body["consumer_id"]}
        roles = {"information-brief":"information","layer-review":"layer","sector-review":"sector",
                 "fundamental-event":"fundamental","fundamental-routine":"fundamental",
                 "macro-review":"macro","technical-review":"technical"}
        covered.update(roles[t["task_id"]] for t in body["matrix"]["plan"]["tasks"])
        if "risk" in available:
            covered.add("risk")
        if {"approval","attribution"} <= available:
            covered.update({"chief","trader","clerk"})
        if not consumers <= covered:
            raise ValueError("report does not cover all requested consumers")
        _validate(body)
        return True, []
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
        return False,[f"new-entry acceptance {batch.shadow_report_id!r} refused: {exc}"]
