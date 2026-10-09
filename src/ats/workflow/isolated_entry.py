"""Run a real business callable inside the intake isolation context (task 7.1)."""
from __future__ import annotations

import inspect
import json
import sqlite3
import uuid
from contextlib import closing, nullcontext
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from .intake_verification import isolated_verification


def _json(value):
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return asdict(value)
    raise TypeError(f"unsupported business output: {type(value).__name__}")


@dataclass(frozen=True)
class IsolatedEntryResult:
    entry_id: str
    output: object
    attestation: dict
    record_path: str
    tradable: bool = False


def _tag_origin(value, root):
    """Keep origin on early rejections and nested business results as well."""
    from ..schemas.memory import TradeLogEntry

    if isinstance(value, TradeLogEntry):
        if value.isolation_root and Path(value.isolation_root).resolve() != root:
            raise PermissionError("business result belongs to another isolation root")
        value.isolation_root = str(root)
    elif isinstance(value, BaseModel):
        for name in type(value).model_fields:
            _tag_origin(getattr(value, name), root)
    elif isinstance(value, dict):
        if "exec_id" in value:
            if value.get("isolation_root") and Path(value["isolation_root"]).resolve() != root:
                raise PermissionError("fill result belongs to another isolation root")
            value["isolation_root"] = str(root)
        for item in value.values():
            _tag_origin(item, root)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _tag_origin(item, root)


def run_isolated_entry(*, run_id, entry, root=None, shadow=False, **kwargs):
    """Call the business implementation, record output, never register qualification.

    External feeds/LLM/approval-channel dependencies remain the entry's own
    interfaces. Tests inject local fixtures there, keeping the business gates.
    """
    from ..execution.shadow_execution import shadow_execution
    from ..memory import get_store
    from .consumer_reads import trace_reads

    identity = uuid.uuid4().hex
    source = Path(inspect.getsourcefile(inspect.unwrap(entry))).resolve()
    import hashlib

    row = {"entry_id": identity, "run_id": run_id, "entry": f"{entry.__module__}.{entry.__qualname__}",
           "source": str(source), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
           "status": "failed", "tradable": False}
    try:
        with isolated_verification(run_id, root=root) as attestation:
            record_path = Path(attestation.isolation_root).resolve() / "business_entries.sqlite"
            transport = shadow_execution(run_id=run_id, store=get_store()) if shadow else nullcontext()
            with transport, trace_reads() as read_trace:
                output = entry(**kwargs)
                _tag_origin(output, Path(attestation.isolation_root).resolve())
                row["output"] = json.loads(json.dumps(output, default=_json, allow_nan=False))
        row["status"] = "completed"
    except BaseException as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        # Startup failures have no bound destination; they must not create one.
        if "record_path" in locals():
            row["read_trace"] = read_trace if "read_trace" in locals() else []
            row["attestation"] = attestation.as_row()
            with closing(sqlite3.connect(record_path)) as conn:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS isolated_business_entries(entry_id TEXT PRIMARY KEY, body TEXT NOT NULL);
                    CREATE TRIGGER IF NOT EXISTS entries_no_update BEFORE UPDATE ON isolated_business_entries
                    BEGIN SELECT RAISE(ABORT, 'business entry evidence is append-only'); END;
                    CREATE TRIGGER IF NOT EXISTS entries_no_delete BEFORE DELETE ON isolated_business_entries
                    BEGIN SELECT RAISE(ABORT, 'business entry evidence is append-only'); END;
                """)
                conn.execute("INSERT INTO isolated_business_entries VALUES (?,?)", (identity, json.dumps(row)))
                conn.commit()
    return IsolatedEntryResult(identity, output, row["attestation"], str(record_path))
