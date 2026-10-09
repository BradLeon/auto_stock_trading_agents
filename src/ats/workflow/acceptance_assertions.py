"""Read-only new-entry requirement assertions (not a cutover/signoff checker).

Expected schedule and criteria come exclusively from the pre-run matrix. Legacy
comparisons are deliberately absent. Records supplied here must be durably bound
by the report/runner; those separate gates cannot be inferred from this result.
"""
from __future__ import annotations

import json
import hashlib
import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .shadow_matrix import MatrixError


def validate_criteria(criteria, assertions):
    allowed = {a["id"] for a in assertions if a["required"]}
    if not isinstance(criteria, dict) or not set(criteria) <= allowed:
        raise MatrixError("criteria must address applicable fixed assertions")
    for rows in criteria.values():
        if not isinstance(rows, list) or not rows:
            raise MatrixError("nonempty pre-run criteria required")
        for row in rows:
            if not isinstance(row, dict) or set(row) != {"path", "op", "value"}:
                raise MatrixError("criterion requires path/op/value")
            if not isinstance(row["path"], str) or not row["path"] or any(
                    not p or p.startswith("_") for p in row["path"].split(".")):
                raise MatrixError("invalid criterion path")
            if row["op"] not in {"eq", "in", "range", "contains", "nonempty"}:
                raise MatrixError("unknown criterion operator")
            value = row["value"]
            if row["op"] in {"in", "contains"} and (not isinstance(value, list) or not value):
                raise MatrixError("criterion requires nonempty list")
            if row["op"] == "nonempty" and value is not True:
                raise MatrixError("nonempty criterion requires true")
            if row["op"] == "range" and (not isinstance(value, list) or len(value) != 2 or
                    any(type(v) not in {int, float} or not math.isfinite(v) for v in value) or
                    value[0] > value[1]):
                raise MatrixError("finite ordered numeric bounds required")
    try:
        return json.loads(json.dumps(criteria, allow_nan=False))
    except (ValueError, TypeError) as exc:
        raise MatrixError("criteria must be finite JSON") from exc


def check_criteria(actual, criteria):
    for condition in criteria:
        value = actual
        for part in condition["path"].split("."):
            if not isinstance(value, dict) or part not in value:
                raise ValueError("criterion field missing: " + condition["path"])
            value = value[part]
        wanted, op = condition["value"], condition["op"]
        if op == "eq":
            ok = value == wanted and (type(value) is type(wanted) if isinstance(value,bool) or isinstance(wanted,bool) else True)
        elif op == "in":
            ok = value in wanted
        elif op == "range":
            ok = type(value) in {float,int} and math.isfinite(value) and wanted[0] <= value <= wanted[1]
        elif op == "contains":
            ok = isinstance(value,(list,dict,str)) and all(v in value for v in wanted)
        else:
            ok = bool(value)
        if not ok:
            raise ValueError("pre-run criterion failed: " + condition["path"])


class Untested(ValueError):
    """No resolvable actual measurement; distinct from a measured violation."""


@dataclass(frozen=True)
class AssertionResult:
    assertion_id: str
    dimension: str
    required: bool
    status: str
    expected: dict
    actual: dict
    refs: tuple[str, ...]
    reason: str = ""

    def as_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class AssertionRun:
    matrix_hash: str
    results: tuple[AssertionResult, ...]

    @property
    def passed(self):
        return bool(self.results) and all(r.status == "passed" for r in self.results if r.required)

    def as_dict(self):
        return {"matrix_hash": self.matrix_hash, "passed": self.passed,
                "results": [r.as_dict() for r in self.results]}


def _time(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("evidence time requires timezone")
    return result


def _plan(body):
    from ..agent.task_projection import ProjectionScope
    from .phase_e import build_plan
    from .run_contracts import TriggerContext
    raw = body["plan"]
    return build_plan(requested_tasks=raw["requested_tasks"], scope=ProjectionScope.model_validate(raw["request_scope"]),
        trigger=TriggerContext.model_validate(raw["trigger"]), as_of=raw["as_of"], run_id=raw["run_id"],
        profile_id=raw["profile_id"], enter_decision_cycle=raw["enter_decision_cycle"],
        task_inputs=raw["task_inputs"], config_dir=raw["config_root"] or None)


def _inputs(matrix, evidence):
    from .acceptance_matrix import check_inputs
    from . import shadow_inputs as si
    value = evidence.get("inputs")
    reads = evidence.get("consumed_reads")
    if value is None or reads is None:
        raise Untested("frozen inputs or actual consumed reads missing")
    value.validate()
    if not reads:
        raise Untested("no actual consumed inputs")
    if not isinstance(reads,(list,tuple)) or any(not isinstance(key,str) or not key for key in reads):
        raise ValueError("actual consumed reads require nonempty request identities")
    from .shadow_replay import digest
    fixed = {digest(row["request"]): row for row in value.reads}
    model = value.contents.get(si.MODEL_CONFIG,{})
    if not isinstance(model,dict) or not isinstance(model.get("rows",{}),dict):
        raise ValueError("fixed model configuration/response records malformed")
    model_rows = model.get("rows", {})
    if not set(reads) <= set(fixed) | set(model_rows):
        raise ValueError("actual read not in frozen data/model inputs")
    runtime = {row["surface"] for key, row in fixed.items() if key in reads and
               row["surface"] in {si.MARKET_RUNTIME, si.ACCOUNT_STATE, si.HISTORY_STATE}}
    check_inputs(matrix, value.packet, actual_runtime_surfaces=runtime)
    return {"input_hash": value.input_hash(), "consumed_reads": sorted(reads)}, ["input:" + value.input_hash()]


def _run(body, evidence):
    store, actual = evidence.get("store"), evidence.get("dispatch")
    if store is None or actual is None:
        raise Untested("actual workflow store/result missing")
    run = store.conn.execute("SELECT * FROM workflow_runs WHERE run_id=?", (body["plan"]["run_id"],)).fetchone()
    if run is None:
        raise Untested("durable workflow run missing")
    if run["plan_hash"] != body["plan_hash"] or actual.get("run_id") != run["run_id"]:
        raise ValueError("actual workflow differs from independent fixed plan")
    return store, actual


def _schedule(body, evidence):
    store, actual = _run(body, evidence)
    state = evidence.get("schedule")
    if state is None:
        raise Untested("actual trigger claim/publication evidence missing")
    rows = actual.get("outcomes", [])
    expected = {t["instance_key"] for t in body["plan"]["tasks"]}
    if len(rows) != len(expected) or {r["instance_key"] for r in rows} != expected:
        raise ValueError("new schedule omitted or duplicated fixed task instances")
    claims = state.get("claims", [])
    refs = []
    for item in body["expected_schedule"]:
        hits = [row for row in claims if row["key"] == item["key"]]
        if len(hits) != 1 or hits[0]["status"] != "complete":
            raise ValueError("required new trigger missing/incomplete: " + item["key"])
        claim = hits[0]
        if claim["owner"] != "dispatcher" or json.loads(claim["identity"]) != item["identity"]:
            raise ValueError("trigger identity/owner differs")
        output = next(row for row in rows if row["instance_key"] == item["instance_key"])
        if output["status"] != "succeeded" or not output["projection_refs"] or not set(
                output["projection_refs"]) <= set(json.loads(claim["result_refs"])):
            raise ValueError("required trigger has no matching actual publication")
        publications = [json.loads(r["payload"]).get("ref") for r in state.get("history", [])
                        if r["key"] == item["key"] and r["action"] == "publication"]
        if any(publications.count(ref) > 1 for ref in output["projection_refs"]):
            raise ValueError("duplicate publication for required trigger")
        if any(json.loads(r["payload"]).get("generation") != claim["generation"]
               for r in state.get("history",[]) if r["key"] == item["key"] and r["action"] == "publication"
               and json.loads(r["payload"]).get("ref") in output["projection_refs"]):
            raise ValueError("required publication from stale claim generation")
        refs.extend(output["projection_refs"])
    return {"expected_instances": sorted(expected), "missing": 0, "duplicate_publications": 0}, refs


def _analysis(body, assertion, evidence):
    from ..agent.task_projection import content_hash, reuse_decision, validate_payload
    from ..agents.chief.assemble import _envelope_from_row
    from .dispatcher import Dispatcher
    from .phase_e import TASK_ROLE
    store, actual = _run(body, evidence)
    plan = _plan(body)
    task = next(t for t in plan.tasks if "analysis." + t.instance_key == assertion["id"])
    selected = {}
    for current in plan.tasks:
        hits = [r for r in actual.get("outcomes", []) if r["instance_key"] == current.instance_key]
        if len(hits) != 1:
            if current == task or current.instance_key in task.dependencies:
                raise ValueError("required analysis outcome missing/duplicate: " + current.instance_key)
            continue
        out = hits[0]
        if out["status"] != "succeeded" or len(out["projection_refs"]) != 1:
            if current == task or current.instance_key in task.dependencies:
                raise ValueError("required analysis failed/no exact output: " + current.instance_key)
            continue
        row = store.conn.execute("SELECT * FROM task_projection_envelopes WHERE projection_id=?", (out["projection_refs"][0],)).fetchone()
        if row is None:
            raise Untested("projection ref unresolved: " + out["projection_refs"][0])
        decoded = dict(row)
        for field in ("payload", "input_refs", "data_vintage_refs"):
            if isinstance(decoded[field], str):
                decoded[field] = json.loads(decoded[field])
        env = _envelope_from_row(decoded, current.scope)
        if (out["task_id"] != current.task_id or out["scope"] != current.scope.model_dump(mode="json")
                or env.agent_role != TASK_ROLE[current.task_id] or row["scope_kind"] != current.scope.kind
                or row["scope_id"] != current.scope.id):
            raise ValueError("analysis role/scope differs")
        payload = validate_payload(env.agent_role, env.payload)
        scope_field = {"entity":"entity","sector":"sector","layer":"layer"}.get(current.scope.kind)
        if scope_field and env.payload.get(scope_field) != current.scope.id:
            raise ValueError("analysis payload entity/sector/layer differs from required scope")
        if (payload.schema_name != current.output_schema or env.content_hash != content_hash(
                role=env.agent_role, scope=env.scope, as_of=env.as_of, schema_name=env.schema_name,
                schema_version=env.schema_version, payload=payload, input_refs=env.input_refs,
                data_vintage_refs=env.data_vintage_refs)):
            raise ValueError("analysis schema/content integrity mismatch")
        at, source = _time(plan.as_of), _time(env.as_of)
        ok, reason = reuse_decision(env, scope=current.scope, schema_name=current.output_schema,
                                    schema_version="v1", at=at)
        from .phase_e import FRESHNESS_SECONDS
        if not ok or source > at or (at-source).total_seconds() > FRESHNESS_SECONDS[current.task_id]:
            raise ValueError("analysis stale/invalid: " + reason)
        attempt = store.conn.execute("SELECT * FROM agent_runs WHERE run_id=? AND task_instance_key=? ORDER BY attempt_no DESC LIMIT 1", (plan.run_id,current.instance_key)).fetchone()
        if not attempt or attempt["status"] != "succeeded" or json.loads(attempt["projection_refs_json"]) != [env.projection_id]:
            raise ValueError("durable actual task attempt differs")
        original = store.conn.execute("SELECT * FROM agent_runs WHERE agent_run_id=?", (env.agent_run_id,)).fetchone()
        if (not original or original["status"] != "succeeded" or original["run_id"] != env.workflow_run_id
                or original["task_id"] != current.task_id or original["task_instance_key"] != current.instance_key
                or (not out.get("reused") and env.workflow_run_id != plan.run_id)):
            raise ValueError("projection producer attempt unresolved/failed")
        selected[current.instance_key] = env
    env = selected[task.instance_key]
    parents = {key: selected[key] for key in task.dependencies}
    wanted, vintages, _ = Dispatcher._task_inputs(None, plan, task, parents)
    if not set(wanted) <= set(env.input_refs) or not set(vintages) <= set(env.data_vintage_refs):
        raise ValueError("analysis fixed input/dependency/vintage lineage differs")
    return {"payload": env.payload, "as_of": env.as_of, "content_hash": env.content_hash,
            "input_refs": env.input_refs, "data_vintage_refs": env.data_vintage_refs}, [env.projection_id, env.agent_run_id]


def _revision(evidence):
    from ..decision.repository import DecisionAuditRepository
    from ..decision.hashing import decision_hash
    store, cycle_id = evidence.get("store"), evidence.get("cycle_id")
    if store is None or not cycle_id:
        raise Untested("actual decision store/cycle missing")
    repo = DecisionAuditRepository(store)
    rev = repo.latest_revision(cycle_id)
    if rev is None:
        raise Untested("actual decision revision missing")
    orders = json.loads(rev["orders_json"])
    rationale = rev["rationale"]
    if rationale.startswith("{"):
        try: rationale = json.loads(rationale)
        except ValueError: pass
    if not orders or rev["decision_hash"] != decision_hash(orders=orders, rationale=rationale,
            input_refs=json.loads(rev["input_refs"] or "null")):
        raise ValueError("empty or corrupted full revision")
    return repo, dict(rev), orders


def audit_risk(evidence):
    from ..schemas.risk import DecisionRiskReview
    repo, rev, orders = _revision(evidence)
    row = repo.effective_review(rev["cycle_id"], rev["revision_no"], rev["decision_hash"])
    full = evidence.get("risk_review")
    if row is None or full is None:
        raise Untested("actual review/ref/full per-order result missing")
    review = DecisionRiskReview.model_validate(full)
    if review.basis is None or review.basis.missing_bindings():
        raise ValueError("risk required input basis incomplete")
    for field, value in review.basis.model_dump().items():
        if row[field] != value:
            raise ValueError("actual risk basis differs from stored review")
    for field, column in (("violations","violations_json"), ("allowed_boundary","allowed_boundary_json"),
                          ("before_metrics","before_metrics_json"), ("after_metrics","after_metrics_json")):
        if review.model_dump(mode="json")[field] != json.loads(row[column] or "{}"):
            raise ValueError("full risk result differs from stored review: " + field)
    expected_orders = sorted((d["symbol"],d["action"]) for d in orders)
    if sorted((d.symbol,d.action) for d in review.order_verdicts) != expected_orders or review.verdict != row["verdict"]:
        raise ValueError("risk verdict/order coverage differs from full revision")
    if review.verdict == "approved" and (any(d.verdict != "approved" for d in review.order_verdicts)
            or any(v.severity == "hard" for v in review.violations)):
        raise ValueError("risk approval contradicts hard violations/order verdicts")
    if review.verdict == "rejected" and not review.violations:
        raise ValueError("risk rejection has no structured counterproposal reason")
    boundary = review.allowed_boundary
    sizes = [boundary.max_additional_notional, *boundary.per_symbol_max_notional.values(),
             *(d.max_allowed_notional for d in review.order_verdicts)]
    if any(v is not None and (not math.isfinite(v) or v < 0) for v in sizes):
        raise ValueError("risk counterproposal contains invalid numeric limits")
    actual = {**review.model_dump(mode="json"), "orders": orders, "decision_hash": rev["decision_hash"]}
    if len({d["symbol"] for d in orders}) == len(orders):
        actual["orders_by_symbol"] = {d["symbol"]:d for d in orders}
    json.dumps(actual, allow_nan=False)
    return actual, [rev["decision_hash"], row["review_id"]]


def audit_approval(evidence):
    repo, rev, _ = _revision(evidence)
    review = repo.effective_review(rev["cycle_id"],rev["revision_no"],rev["decision_hash"])
    rows = repo.conn.execute("SELECT * FROM boss_approvals WHERE cycle_id=? ORDER BY created_at DESC,approval_id DESC",(rev["cycle_id"],)).fetchall()
    if not rows:
        raise Untested("actual human approval missing")
    row = dict(rows[0])
    if (row["decision_hash"] != rev["decision_hash"] or row["revision_no"] != rev["revision_no"]
            or review is None
            or not row["reviewer"] or not row["channel"] or row["channel"] in {"auto","automatic"}
            or _time(row["created_at"]) < _time(review["created_at"])
            or (row["decision"] == "approved" and review["verdict"] != "approved")):
        raise ValueError("approval not human/exact/current reviewed revision")
    if row["decision"] == "approved":
        effective = repo.effective_approval(rev["cycle_id"],rev["revision_no"],rev["decision_hash"])
        if effective is None or effective["approval_id"] != row["approval_id"]:
            raise ValueError("approval outside current effective review round")
    return row, [row["approval_id"], review["review_id"], rev["decision_hash"]]


def audit_attribution(evidence):
    from .isolation import verified_isolation_root
    root = verified_isolation_root()
    store = evidence.get("store")
    if root is None or store is None or not Path(store.path).resolve().is_relative_to(Path(root).resolve()):
        raise ValueError("trading attribution requires verified isolated ledger")
    repo, rev, orders = _revision(evidence)
    approval, refs = audit_approval(evidence)
    if approval["decision"] != "approved":
        raise ValueError("executed attribution has no approval")
    cycle = repo.get_cycle(rev["cycle_id"])
    snapshot = json.loads(cycle["research_snapshot"] or "null")
    if not snapshot or not snapshot.get("items") or any(not i.get("projection_id") or not i.get("content_hash") for i in snapshot["items"]):
        raise ValueError("executed research snapshot refs missing")
    for item in snapshot["items"]:
        row = store.conn.execute("SELECT content_hash FROM task_projection_envelopes WHERE projection_id=?",(item["projection_id"],)).fetchone()
        if not row or row[0] != item["content_hash"]:
            raise ValueError("executed research snapshot ref unresolved/changed")
        refs.append(item["projection_id"])
    receipts, retried = evidence.get("receipts"), evidence.get("retry_receipts")
    if not receipts or retried is None:
        raise Untested("nonempty actual intent/receipt and retry measurement missing")
    receipts = [r for r in receipts if r["cycle_id"] == rev["cycle_id"] and r["revision_no"] == rev["revision_no"]]
    retries = [r for r in retried if r["cycle_id"] == rev["cycle_id"] and r["revision_no"] == rev["revision_no"]]
    if not receipts:
        raise Untested("no actual receipts for selected full revision")
    if receipts != retries or len({r["intent_id"] for r in receipts}) != len(receipts):
        raise ValueError("retry changed receipts or duplicate submission")
    trades = [dict(r) for r in store.conn.execute("SELECT * FROM trades WHERE cycle_id=? AND revision_no=?", (rev["cycle_id"],rev["revision_no"]))]
    if len(trades) != len(orders) or len(receipts) != len(orders):
        raise ValueError("full approved orders/receipt/ledger coverage differs")
    for seq, order in enumerate(orders):
        hit = [r for r in receipts if r["sequence"] == seq]
        trade = [r for r in trades if len(hit) == 1 and r["order_id"] == hit[0]["broker_order_id"]
                 and r["order_ref"] == hit[0]["order_ref"]]
        if len(hit) != 1 or len(trade) != 1:
            raise ValueError("intent/sequence duplicated or missing")
        receipt, row = hit[0], trade[0]
        from ..broker.ibkr import IBKRBroker
        from ..broker.ibkr import order_ref
        from ..schemas.decision import TradeDecision
        payload = {"decision":TradeDecision.model_validate(order).model_dump(mode="json"),
                   "qty":row["qty"], "account":receipt["account"],
                   "chain":{"decision_hash":rev["decision_hash"],"approval_id":approval["approval_id"]}}
        expected_payload = hashlib.sha256(json.dumps(payload,sort_keys=True,allow_nan=False).encode()).hexdigest()
        if (not receipt["payload_hash"] or not receipt["route_id"] or receipt["generation"] < 1
                or receipt["intent_id"] != IBKRBroker._intent_id(rev["cycle_id"],rev["revision_no"],seq)
                or receipt["order_ref"] != order_ref(rev["cycle_id"],rev["revision_no"],seq,order["symbol"])
                or (row["order_seq"] is not None and row["order_seq"] != seq)
                or receipt["payload_hash"] != expected_payload
                or not receipt["account"] or receipt["environment"] != "paper"
                or receipt["status"] not in {"submitted","partial","filled"}
                or receipt["broker_order_id"] != row["order_id"] or receipt["order_ref"] != row["order_ref"]
                or row["decision_hash"] != rev["decision_hash"] or row["approval_id"] != approval["approval_id"]
                or row["symbol"] != order["symbol"] or row["action"] != order["action"]
                or row["qty"] != order["qty"] or row["order_type"] != order["order_type"]
                or row["limit_price"] != order["limit_price"]):
            raise ValueError("actual receipt/order/full approved instruction differs")
        refs.extend([receipt["intent_id"],row["order_id"]])
    fills = [dict(r) for r in store.conn.execute("SELECT * FROM fills WHERE cycle_id=? AND decision_hash=?",(rev["cycle_id"],rev["decision_hash"]))]
    if not fills:
        raise Untested("actual Clerk fills/attribution not measured")
    if len({f["exec_id"] for f in fills}) != len(fills) or any(f["origin"] != "system" or f["approval_id"] != approval["approval_id"] or
            f["order_id"] not in {r["order_id"] for r in trades} for f in fills):
        raise ValueError("Clerk fill attribution/idempotence differs")
    for trade in trades:
        hits = [f for f in fills if f["order_id"] == trade["order_id"]]
        quantity = sum(f["shares"] for f in hits)
        if any(f["symbol"] != trade["symbol"] or f["shares"] <= 0 or f["price"] <= 0 for f in hits) or quantity > trade["qty"] or quantity != trade["filled_qty"]:
            raise ValueError("Clerk fill quantity/symbol differs from ledger")
        if quantity and not math.isclose(sum(f["shares"]*f["price"] for f in hits)/quantity,trade["avg_fill_price"],rel_tol=1e-9):
            raise ValueError("Clerk weighted fill price differs")
    refs.extend(f["exec_id"] for f in fills)
    routes = {(r["route_id"],r["generation"],r["account"],r["environment"]) for r in receipts}
    if len(routes) != 1:
        raise ValueError("executed full revision spans different routes/accounts")
    route_id, generation, account, environment = next(iter(routes))
    return {"receipts": receipts,"trades": trades,"fills": fills,"retry_unchanged": True,
            "route": {"route_id":route_id,"generation":generation,"account":account,"environment":environment}}, refs


def evaluate(matrix, evidence):
    """Execute every frozen assertion; no override/legacy acceptance argument.

    Criteria extend mandatory structural checks; they cannot disable them. Missing
    evidence remains untested, including a claimed passed flag without records.
    """
    from .acceptance_matrix import restore
    matrix = restore(matrix.as_row())
    results = []
    for assertion in matrix.body["assertions"]:
        actual, refs, reason = {}, [], ""
        expected = {"contract": assertion["expected"], "criteria": assertion["criteria"]}
        if not assertion["required"]:
            status, reason = "not-applicable", assertion["reason"]
        else:
            try:
                dimension = assertion["dimension"]
                if dimension == "inputs": actual, refs = _inputs(matrix,evidence)
                elif dimension == "schedule": actual, refs = _schedule(matrix.body,evidence)
                elif dimension == "analysis": actual, refs = _analysis(matrix.body,assertion,evidence)
                else:
                    if not assertion["criteria"]:
                        raise Untested("risk/approval/trading scenario expectations not fixed before run")
                    actual, refs = {"risk":audit_risk,"approval":audit_approval,"attribution":audit_attribution}[dimension](evidence)
                check_criteria(actual,assertion["criteria"])
                status = "passed"
            except Untested as exc:
                status, reason = "untested", str(exc)
            except sqlite3.Error as exc:
                status, reason = "untested", "actual records unreadable: " + str(exc)
            except (ValueError, TypeError, KeyError) as exc:
                status, reason = "failed", str(exc)
            if status != "passed" and not actual:
                actual = {"observed_dispatch": evidence.get("dispatch"), "cycle_id": evidence.get("cycle_id"),
                          "record_resolution_error": reason}
                refs = [ref for outcome in (evidence.get("dispatch") or {}).get("outcomes",[]) for ref in outcome.get("projection_refs",[])]
                if assertion["dimension"] == "schedule":
                    actual["observed_schedule"] = evidence.get("schedule")
                if assertion["dimension"] in {"risk","approval","attribution"} and evidence.get("store") and evidence.get("cycle_id"):
                    from ..decision.repository import DecisionAuditRepository
                    try:
                        actual["observed_chain"] = DecisionAuditRepository(evidence["store"]).read_chain(evidence["cycle_id"])
                        refs.extend(r["decision_hash"] for r in actual["observed_chain"]["revisions"])
                    except sqlite3.Error:
                        pass
        results.append(AssertionResult(assertion["id"],assertion["dimension"],assertion["required"],
            status,expected,actual,tuple(refs),reason))
    return AssertionRun(matrix.matrix_hash,tuple(results))
