"""Dynamic analysis boundary acceptance. Only usable in complete isolation.

This observes executed Python/C calls, not declared imports. It is an acceptance
instrument, not a sandbox or a production qualification. Unexecuted branches
still require static review. Violations invalidate the entire acceptance even
when a role catches the immediate refusal.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

from .intake_verification import BASE_TABLE_MODULES, PROVIDER_MODULES, THIRD_PARTY_DATA_CLIENTS

ROLES = {"layer", "information", "sector", "fundamental", "macro", "technical"}
GOVERNED = ("ats.data.products", "ats.data.consumer_api", "ats.workflow.consumer_reads",
            "ats.workflow.runtime_reads", "ats.data.assurance")
FORBIDDEN = (*PROVIDER_MODULES, *BASE_TABLE_MODULES, *THIRD_PARTY_DATA_CLIENTS,
             "ats.data.fundamentals", "ats.data.macro")
OWNERS={"layer_analysis":"layer", "information_brief":"information", "sector_allocation":"sector",
        "fundamental_expectation_update":"fundamental", "fundamental_event_review":"fundamental",
        "macro_review":"macro", "technical_review":"technical"}
EDGES={("layer","sector"),("information","fundamental")}


@contextmanager
def verify_analysis_access():
    from .isolation import verified_isolation_root

    if verified_isolation_root() is None:
        raise PermissionError("analysis_verification_requires_complete_isolation")
    rows=[]
    violations=[]
    previous=sys.getprofile()
    previous_thread=threading.getprofile()

    def profile(frame, event, arg):
        if event not in {"call", "c_call", "return"}:
            return
        current=frame.f_globals.get("__name__", "")
        if event=="return":
            if current=="ats.workflow.consumer_reads" and frame.f_code.co_name=="read_projection" and isinstance(arg,dict):
                rows.append({"consumer":frame.f_locals["consumer"],"api":"task_projection",
                    "producer":frame.f_locals["owners"].get(arg["agent_role"]),
                    "refs":[arg["projection_id"]],"content_hash":arg["content_hash"],
                    "role":arg["agent_role"],"scope":frame.f_locals["scope"].model_dump(mode="json"),
                    "status":"complete"})
            return
        if event=="call" and not current.startswith((*FORBIDDEN,*GOVERNED,"ats.memory")):
            return
        if event=="c_call" and not isinstance(getattr(arg,"__self__",None),(sqlite3.Connection,sqlite3.Cursor)):
            return
        cursor=frame.f_back if event=="call" else frame
        governed=False
        actor=""
        while cursor:
            module=cursor.f_globals.get("__name__", "")
            governed=governed or module.startswith(GOVERNED)
            # Existing document-processing leases are lifecycle metadata, not
            # research reads or opinion writeback. Permit only these two bridges.
            if module=="ats.memory.store" and cursor.f_code.co_name in {
                "begin_document_processing", "finish_document_processing"}:
                governed=True
            if module=="ats.agents.base" and cursor.f_code.co_name=="run_structured":
                governed=True  # Model transport is distinct from market/data providers.
            pieces=module.split(".")
            if len(pieces)>2 and pieces[:2]==["ats","agents"] and pieces[2] in ROLES:
                actor=pieces[2]
                break
            cursor=cursor.f_back
        if not actor:
            return
        name=current+"."+frame.f_code.co_name
        reason=""
        if event=="call" and current.startswith(FORBIDDEN) and not governed:
            reason="provider_or_raw_repository_bypass"
        if event=="call" and current=="ats.memory.store" and not governed:
            role=frame.f_locals.get("agent_role","")
            if frame.f_code.co_name=="get_task_projection":
                store=frame.f_locals["self"]
                row=store.conn.execute("SELECT agent_role FROM task_projection_envelopes WHERE projection_id=?",
                                       (frame.f_locals["projection_id"],)).fetchone()
                role=row[0] if row else ""
            if frame.f_code.co_name in {"get_task_projection","task_projection_envelopes"}:
                producer=OWNERS.get(role)
                if producer!=actor and (producer,actor) not in EDGES:
                    reason="undeclared_raw_opinion_input"
        if event=="c_call" and not governed:
            connection=getattr(arg,"__self__",None)
            if isinstance(connection,sqlite3.Cursor):
                connection=connection.connection
            if isinstance(connection,sqlite3.Connection) and getattr(arg,"__name__","") in {
                "execute", "executemany", "executescript", "cursor"}:
                paths=[r[2] for r in connection.execute("PRAGMA database_list") if r[2]]
                shared={str(Path(os.environ[key]).resolve()) for key in
                        ("ATS_DATA_DB_PATH","ATS_STRUCTURED_DB_PATH") if key in os.environ}
                if any(str(Path(p).resolve()) in shared for p in paths):
                    reason="shared_fact_sql_bypass"
                    name="sqlite3.Connection."+arg.__name__
        if reason:
            row={"consumer":actor,"call":name,"status":"blocked","reason_code":reason}
            rows.append(row);violations.append(row)
            raise PermissionError(reason)
        if event=="call" and (current.startswith(GOVERNED) or
                              frame.f_code.co_name=="save_task_projection_envelope"):
            rows.append({"consumer":actor,"call":name,"status":"called"})

    sys.setprofile(profile)
    threading.setprofile(profile)
    try:
        yield rows
    finally:
        sys.setprofile(previous)
        threading.setprofile(previous_thread)
        if violations:
            raise PermissionError("analysis_acceptance_failed: "+str(violations))
