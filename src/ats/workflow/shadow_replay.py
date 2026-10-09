"""Recoverable, content-addressed inputs. Replay never consults a provider."""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from . import shadow_inputs as si


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode()).hexdigest()


def _isolated(path=None):
    from ..execution.broker_write_guard import assert_broker_writes_prohibited
    from .isolation import verified_isolation_root

    root = verified_isolation_root()
    if root is None:
        raise PermissionError("shadow_replay_requires_complete_isolation")
    assert_broker_writes_prohibited(operation="shadow_replay", caller="shadow_replay")
    if path is not None and not Path(path).resolve().is_relative_to(Path(root).resolve()):
        raise PermissionError("shadow_input_destination_outside_isolation")


@dataclass
class FrozenInputs:
    packet: si.ShadowInputPacket
    contents: dict = field(default_factory=dict)
    reads: list = field(default_factory=list)

    def body(self):
        return {"schema_version": "shadow-replay-v1", "packet": asdict(self.packet),
                "contents": self.contents, "reads": self.reads}

    def input_hash(self):
        return digest(self.body())

    def validate(self):
        if not self.packet.scope or not self.packet.consumer_id:
            raise si.IncompletePacketError("input scope/consumer required")
        self.packet.assert_usable_as_evidence()
        point = datetime.fromisoformat(self.packet.surfaces[si.LOGICAL_EVAL_TIME])
        if point.tzinfo is None:
            raise si.IncompletePacketError("logical_eval_time requires timezone")
        if set(self.packet.surfaces) != set(si.ALL_SURFACES):
            raise si.IncompletePacketError("every surface must be fixed or explicitly not_applicable")
        for name, expected in self.packet.surfaces.items():
            if name == si.LOGICAL_EVAL_TIME:
                continue
            if expected == si.NOT_APPLICABLE:
                if name in self.contents:
                    raise si.IncompletePacketError(f"{name}: captured but declared not_applicable")
            elif name not in self.contents or digest(self.contents[name]) != expected:
                raise si.IncompletePacketError(f"{name}: hash-only, missing content or content mismatch")
        if set(self.contents) - set(si.ALL_SURFACES):
            raise si.IncompletePacketError("unknown content surface")
        from . import shadow_matrix as mx

        if self.packet.batch_class in {mx.DECISION, mx.TRADING}:
            mx.assert_satisfies_matrix(self.packet)
        elif self.packet.batch_class != mx.RESEARCH_READ or not {
                si.PERSISTENT_REFS, si.PROJECTION_HASH} <= set(self.contents):
            raise si.IncompletePacketError("unknown class or missing research input")
        seen = set()
        from ..data.consumer_api import ConsumerInput

        for row in self.reads:
            key = digest(row["request"])
            if key in seen:
                raise si.IncompletePacketError("duplicate frozen read request")
            seen.add(key)
            packet = ConsumerInput.model_validate(row["input"])
            request = row["request"]
            if (packet.consumer != request["consumer"] or packet.product != request["product"]
                    or packet.scope != request["scope"]
                    or row["surface"] not in self.contents
                    or row["input"] not in self.contents[row["surface"]]):
                raise si.IncompletePacketError("frozen read content/request binding mismatch")
            if packet.product == "MARKET_DATA" and packet.scope.get("kind") == "execution_price":
                from ..data.runtime.execution_prices import ExecutionPrice

                quote = ExecutionPrice.model_validate(packet.payload)
                if not quote.source or any(t.tzinfo is None for t in (quote.source_as_of, quote.queried_at)):
                    raise si.IncompletePacketError("execution quote provenance missing")
                if request["stage"] not in {"preapproval_normalization", "approved_execution_check"} \
                        or packet.scope.get("purpose") != request["stage"]:
                    raise si.IncompletePacketError("execution quote phase mismatch")
        digest(self.body())  # Reject non-JSON/non-finite content rather than stringify it.


def freeze_inputs(*, run_id, consumer_id, batch_class, scope, logical_eval_time,
                  contents, reads=()):
    contents = copy.deepcopy(contents)
    surfaces = {name: digest(contents[name]) if name in contents else si.NOT_APPLICABLE
                for name in si.ALL_SURFACES if name != si.LOGICAL_EVAL_TIME}
    surfaces[si.LOGICAL_EVAL_TIME] = logical_eval_time
    value = FrozenInputs(si.ShadowInputPacket(run_id, consumer_id, batch_class,
                         surfaces=surfaces, scope=copy.deepcopy(scope)), contents, copy.deepcopy(list(reads)))
    value.validate()
    # The declaration matrix remains usable by legacy comparison tools; the
    # recoverable format additionally enforces full content and runtime reads.
    from . import shadow_matrix as mx

    if batch_class in {mx.DECISION, mx.TRADING}:
        mx.assert_satisfies_matrix(value.packet)
    elif batch_class == mx.RESEARCH_READ:
        required = {si.PERSISTENT_REFS, si.PROJECTION_HASH}
        if not required <= set(contents):
            raise si.IncompletePacketError("research persistent refs/projections missing")
    else:
        raise si.IncompletePacketError("unknown batch class")
    return value


class CaptureReads:
    """Wrap actual governed reads once, preserving payloads, refs and source times."""

    def __init__(self, reader=None):
        from ..data.consumer_api import read_input

        self.reader = reader or read_input
        self.reads = []

    def read_input(self, consumer, product, *, scope, stage, **kwargs):
        packet = self.reader(consumer, product, scope=scope, **kwargs)
        modes = {"persistent": si.PERSISTENT_REFS, "runtime": si.MARKET_RUNTIME,
                 "internal": si.HISTORY_STATE, "configuration": si.RULESET_VERSION,
                 "authorization": si.HISTORY_STATE}
        surface = si.ACCOUNT_STATE if product == "PORTFOLIO_DATA" else modes[packet.input_mode]
        request = {"consumer": consumer, "product": product, "scope": copy.deepcopy(scope), "stage": stage}
        row = {"request": request, "surface": surface, "input": packet.model_dump(mode="json")}
        prior = next((r for r in self.reads if r["request"] == request), None)
        if prior is not None and prior != row:
            raise si.IncompletePacketError("same phase/request drifted during capture")
        if prior is None:
            self.reads.append(row)
        return packet

    def contents(self):
        result = {}
        for row in self.reads:
            result.setdefault(row["surface"], []).append(copy.deepcopy(row["input"]))
        return result


def save_inputs(value, *, path):
    _isolated(path)
    value.validate()
    body = json.dumps(value.body(), sort_keys=True, ensure_ascii=False, allow_nan=False)
    identity = value.input_hash()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS shadow_replay_inputs(input_hash TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS replay_no_update BEFORE UPDATE ON shadow_replay_inputs
            BEGIN SELECT RAISE(ABORT, 'inputs are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS replay_no_delete BEFORE DELETE ON shadow_replay_inputs
            BEGIN SELECT RAISE(ABORT, 'inputs are append-only'); END;
        """)
        prior = conn.execute("SELECT body FROM shadow_replay_inputs WHERE input_hash=?", (identity,)).fetchone()
        if prior and prior[0] != body:
            raise si.IncompletePacketError("input hash collision")
        conn.execute("INSERT OR IGNORE INTO shadow_replay_inputs VALUES (?,?)", (identity, body))
        conn.commit()
    return identity


def load_inputs(identity, *, path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        row = conn.execute("SELECT body FROM shadow_replay_inputs WHERE input_hash=?", (identity,)).fetchone()
    if row is None:
        raise si.IncompletePacketError("frozen input not found")
    body = json.loads(row[0])
    if body.get("schema_version") != "shadow-replay-v1" or digest(body) != identity:
        raise si.IncompletePacketError("frozen input schema/hash mismatch")
    value = FrozenInputs(si.ShadowInputPacket(**body["packet"]), body["contents"], body["reads"])
    value.validate()
    return value


class ReplayReads:
    def __init__(self, value):
        value.validate()
        self.value = copy.deepcopy(value)
        self.used = []

    @property
    def logical_time(self):
        return datetime.fromisoformat(self.value.packet.surfaces[si.LOGICAL_EVAL_TIME])

    def surface(self, name):
        _isolated()
        if name not in self.value.contents:
            raise si.IncompletePacketError(f"unfixed replay surface:{name}")
        return copy.deepcopy(self.value.contents[name])

    def read_input(self, consumer, product, *, scope, stage, as_of=None):
        _isolated()
        from ..data.consumer_api import ConsumerInput, input_contract

        contract = input_contract(consumer, product)
        request = {"consumer": consumer, "product": product, "scope": scope, "stage": stage}
        row = next((r for r in self.value.reads if r["request"] == request), None)
        # Runtime stages use the recorded receipt clock, not the research as-of.
        # A different time is admitted only if actually captured by this tape.
        clocks = self.value.contents.get(si.MODEL_CONFIG, {}).get("rows", {})
        runtime_times = {r["result"].get("$datetime") for r in clocks.values()
                         if r["request"]["api"] == "evaluation_clock.now" and isinstance(r["result"], dict)}
        fixed_time = as_of is None or as_of == self.logical_time or (
            row is not None and row["surface"] in {si.MARKET_RUNTIME, si.ACCOUNT_STATE, si.HISTORY_STATE}
            and as_of.isoformat() in runtime_times)
        if row is None or not fixed_time:
            raise si.IncompletePacketError("uncaptured phase/scope/time; provider fallback forbidden")
        packet = ConsumerInput.model_validate(copy.deepcopy(row["input"]))
        if packet.contract_version != contract["contract_version"]:
            raise si.IncompletePacketError("replay consumer contract changed")
        self.used.append(digest(request))
        return packet

    def invoke(self, entry, *, run_id=None, path=None, **kwargs):
        """Inject identical governed read adapter/clock into either business path.

        Concrete new/legacy workflow adapters and run evidence are task 3.9.
        """
        _isolated(path if run_id is not None else None)
        if run_id is not None and path is None:
            raise ValueError("run recording requires explicit input store path")
        if run_id is not None:
            load_inputs(self.value.input_hash(), path=path)
        self.used = []
        output = entry(read_input=self.read_input, logical_time=self.logical_time, **kwargs)
        if run_id is not None:
            _isolated(path)
            if path is None:
                raise ValueError("run recording requires explicit input store path")
            source = Path(inspect.getsourcefile(entry)).resolve()
            row = {"run_id": run_id, "input_hash": self.value.input_hash(),
                   "logical_time": self.logical_time.isoformat(), "reads": sorted(self.used),
                   "entry": f"{entry.__module__}.{entry.__qualname__}",
                   "source": str(source), "source_hash": hashlib.sha256(source.read_bytes()).hexdigest(),
                   "output": output}
            with closing(sqlite3.connect(path)) as conn:
                # Require a durable input, not a caller's in-memory claim.
                load_inputs(self.value.input_hash(), path=path)
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS shadow_replay_runs(run_id TEXT PRIMARY KEY, body TEXT NOT NULL);
                    CREATE TRIGGER IF NOT EXISTS runs_no_update BEFORE UPDATE ON shadow_replay_runs
                    BEGIN SELECT RAISE(ABORT, 'runs are append-only'); END;
                    CREATE TRIGGER IF NOT EXISTS runs_no_delete BEFORE DELETE ON shadow_replay_runs
                    BEGIN SELECT RAISE(ABORT, 'runs are append-only'); END;
                """)
                conn.execute("INSERT INTO shadow_replay_runs VALUES (?,?)",
                             (run_id, json.dumps(row, sort_keys=True, allow_nan=False)))
                conn.commit()
        return output


def verify_execution_evidence(proof, *, report, scope):
    path = proof["input_store"]
    value = load_inputs(report.packet_hash, path=path)
    if value.packet.scope != scope:
        raise si.IncompletePacketError("paired execution input scope mismatch")
    ids = [proof["left_run_id"], proof["right_run_id"]]
    if not all(ids) or ids[0] == ids[1]:
        raise si.IncompletePacketError("distinct paired run IDs required")
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as conn:
        rows = [conn.execute("SELECT body FROM shadow_replay_runs WHERE run_id=?", (i,)).fetchone()
                for i in ids]
    if not all(rows):
        raise si.IncompletePacketError("paired run records missing")
    from ..config import REPO_ROOT

    runs = [json.loads(row[0]) for row in rows]
    if runs[0]["entry"] == runs[1]["entry"]:
        raise si.IncompletePacketError("distinct new/legacy business entries required")
    for run in runs:
        source = Path(run["source"])
        if (run["input_hash"] != report.packet_hash
                or run["logical_time"] != value.packet.surfaces[si.LOGICAL_EVAL_TIME]
                or not source.resolve().is_relative_to(REPO_ROOT / "src/ats")
                or hashlib.sha256(source.read_bytes()).hexdigest() != run["source_hash"]):
            raise si.IncompletePacketError("business execution input/clock/source mismatch")
        if run["output"].get("_packet") != asdict(value.packet):
            raise si.IncompletePacketError("business output is not bound to frozen packet")
    concrete = {"ats.workflow.paired_business.legacy_business", "ats.workflow.paired_business.dispatcher_business"}
    if {r["entry"] for r in runs} == concrete:
        from .business_replay_inputs import implementation_hashes
        if value.contents[si.MODEL_CONFIG]["dependency_hashes"] != implementation_hashes():
            raise si.IncompletePacketError("paired business dependency fingerprint drifted")
        allowed = set(value.contents[si.MODEL_CONFIG]["rows"])
        allowed.update(digest(row["request"]) for row in value.reads)
        for run in runs:
            if not run["reads"] or not set(run["reads"]) <= allowed:
                raise si.IncompletePacketError("paired executions consumed uncaptured inputs")
            output = run["output"]
            if not output.get("projections"):
                raise si.IncompletePacketError("paired business has no actual projections")
            side = Path(output["side_root"]).resolve()
            if not side.is_relative_to(Path(path).resolve().parent) or output["run_id"] != run["run_id"]:
                raise si.IncompletePacketError("business result scope/run destination mismatch")
            with closing(sqlite3.connect((side / "memory.sqlite").as_uri() + "?mode=ro", uri=True)) as db:
                for projection in output["projections"]:
                    row = db.execute("SELECT content_hash,payload FROM task_projection_envelopes WHERE projection_id=?",
                                     (projection["projection_id"],)).fetchone()
                    if not row or row[0] != projection["content_hash"] or json.loads(row[1]) != projection["payload"]:
                        raise si.IncompletePacketError("actual business projection missing or changed")
    elif not runs[0]["reads"] or runs[0]["reads"] != runs[1]["reads"]:
        raise si.IncompletePacketError("paired executions did not consume the same captured inputs")

    return value, runs
