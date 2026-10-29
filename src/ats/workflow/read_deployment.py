"""Formal read deployment adapters: immutable business scope, actual projections.

The manifest carries an explicit joint request and compatibility receipts. No
fixture adapter or global switch is accepted by this entry. Each independently
authorized identity is a separate dependency-closed coordinator operation.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from . import cutover as plane
from . import joint_cutover as joint
from . import read_recovery as recovery
from . import scoped_routes as scopes


def requests(batch):
    if "read_request" not in batch.scope:
        raise plane.CutoverError("exact business read_request required; product-list batches cannot migrate")
    request = joint._validate(batch.scope["read_request"])
    if request["batch_id"] != batch.batch_id or batch.batch_class not in {"research_read", "schedule"}:
        raise plane.CutoverError("explicit research-read or schedule request required")
    identities = {scopes.RouteIdentity(**m["identity"]).key: scopes.RouteIdentity(**m["identity"])
                  for m in request["members"]}
    if not identities or {i.consumer_id for i in identities.values()} != set(batch.consumers):
        raise plane.CutoverError("request consumers differ from declared batch")
    for who in identities.values():
        from .cutover_routing import _consumer_contract
        contract = _consumer_contract(who.consumer_id)
        if (who.scope != batch.qualification_scope or who.domain_id != contract["domain_id"]
                or who.contract_version != contract["contract_version"]):
            raise plane.CutoverError("request business scope/contract mismatch")
    if any(m["boundary"] not in {plane.PROJECTION_READ, plane.ANALYST_OUTPUT,
            plane.DISPATCHER_SCHEDULE} or m["route"] not in {"target", "disabled"}
            for m in request["members"]):
        raise plane.CutoverError("read request cannot move trading/approval or implicit old target")
    anchor = plane.DISPATCHER_SCHEDULE if batch.batch_class == 'schedule' else plane.PROJECTION_READ
    if not all(any(m["boundary"] == anchor and m["identity"] == i.as_row()
                   for m in request["members"]) for i in identities.values()):
        raise plane.CutoverError("each group requires its explicit governing boundary member")
    # A shared schedule owner cannot be split into independent consumer groups.
    if request["schedules"] and len(identities) != 1:
        raise plane.CutoverError("shared schedule requires one explicit dependency-closed group")
    return [(who, {**request, "batch_id": request["batch_id"] + ":" + key,
                  "members": [m for m in request["members"] if m["identity"] == who.as_row()]})
            for key, who in identities.items()]


def authorize(batch, auth, *, actor):
    from .read_cutover import assert_authorised
    groups = requests(batch)
    for who, request in groups:
        assert_authorised(auth, consumer_id=who.consumer_id)
    for who, request in groups:
        joint.record_authorization(request, reference=auth.reference + ":" + who.key,
            valid_until=auth.valid_until, actor=actor, reason=auth.note or "explicit read deployment")


def projection_check(identity, *, refs, fundamental_mode=""):
    """Resolve fixed references through the same schema/lineage reader as runtime.

All declared scopes must exist. Sector additionally requires every configured
layer. Chief uses the complete configured research snapshot, not presence counts.
"""
    from ..agent.task_projection import ProjectionScope
    from ..memory import get_store
    from .consumer_reads import read_projection
    from .runtime_reads import consumer_entities

    if not isinstance(refs, list) or not refs:
        raise plane.CutoverError("actual projection references required")
    if consumer_entities(identity.consumer_id, identity.scope["kind"], identity.scope["id"]) != identity.scope["entities"]:
        raise plane.CutoverError("projection entities differ from actual business scope")
    store = get_store()
    # A persistent deployment scope is not a data cutoff. Check projections at
    # the actual deployment check time; subsequent reads bind their own as_of.
    point = (datetime.now(UTC) if identity.scope.get("time_policy") == "per_invocation"
             else datetime.fromisoformat(identity.scope["time_range"]["end"]))
    selected = []
    for ref in refs:
        if not ref.get("projection_id") or not ref.get("content_hash"):
            raise plane.CutoverError("projection id/hash required")
        scope = ProjectionScope(kind=ref["scope_kind"], id=ref["scope_id"])
        if scope.kind == "entity" and scope.id not in identity.scope["entities"]:
            raise plane.CutoverError("projection escapes business entities")
        row = read_projection(store, consumer=identity.consumer_id, role=ref["role"], scope=scope,
            at=point, projection_id=ref["projection_id"], content_hash=ref["content_hash"])
        if row is None or not row.get("input_refs"):
            raise plane.CutoverError("required projection missing/unusable or without lineage")
        selected.append((ref["role"], scope.key))
    required = []
    if identity.consumer_id == "sector":
        from ..config import load_sector_config
        required = [("sector_allocation", "sector:" + identity.scope["id"])]
        required += [("layer_analysis", "layer:" + layer.key)
                     for layer in load_sector_config(identity.scope["id"]).layers]
    elif identity.consumer_id == "chief":
        from ..agents.chief.assemble import build_chief_snapshot, projection_query_plan
        if fundamental_mode not in {"routine", "event", "per_target"}:
            raise plane.CutoverError("Chief deployment requires an explicit Fundamental mode")
        if fundamental_mode == "per_target":
            # Each target under the mode its fixed reference actually uses (D3-3).
            fundamental = ("fundamental_expectation_update", "fundamental_event_review")
            used = {role: {ref["scope_id"] for ref in refs if ref["role"] == role} for role in fundamental}
            plan = {key: ({**item, "scopes": [s for s in item["scopes"] if s.id in used[key]]}
                          if key in fundamental else item)
                    for key, item in projection_query_plan().items()}
            plan = {key: item for key, item in plan.items() if item["scopes"]}
        else:
            omitted = ("fundamental_event_review" if fundamental_mode == "routine"
                       else "fundamental_expectation_update")
            plan = {key: item for key, item in projection_query_plan().items() if key != omitted}
        snapshot, detail = build_chief_snapshot(store, at=point, projection_manifest=refs, query_plan=plan)
        if not snapshot.complete or any(d["status"] != "fresh" for d in detail):
            raise plane.CutoverError("required Chief projection scopes unavailable")
    else:
        roles = {"technical": "technical_review", "layer": "layer_analysis",
                 "information": "information_brief", "macro": "macro_review"}
        role = roles.get(identity.consumer_id)
        if identity.consumer_id == "fundamental":
            found = {r for r, _ in selected} & {"fundamental_event_review", "fundamental_expectation_update"}
            if len(found) != 1:
                raise plane.CutoverError("explicit single Fundamental mode required")
            role = found.pop()
        if not role:
            raise plane.CutoverError("no actual projection adapter for consumer")
        required = [(role, identity.scope["kind"] + ":" +
                     ("" if identity.scope["kind"] == "portfolio" else identity.scope["id"]))]
    if not set(required) <= set(selected):
        raise plane.CutoverError("required projection dependency scope omitted")
    return {"available": True, "checked": True, "identity": identity.as_row(), "refs": refs}


def gate_adapters(batch, who, request, auth, *, report_checker, qualification_reader,
                  projection_checker, path):
    from .business_replay_inputs import implementation_hashes
    from .read_cutover import assert_authorised
    from .shadow_reports import require_batch_report

    def authorization(member, workflow):
        assert_authorised(auth, consumer_id=who.consumer_id)
        result = {"member": member, "workflow": workflow, "valid": True,
                  "reference": auth.reference + ":" + who.key}
        joint._authorization(result, request, path)
        return result

    def qualification(identity):
        result = qualification_reader(identity.consumer_id, identity.scope)
        return {**result, "identity": identity.as_row()}

    def report(identity):
        adapted = SimpleNamespace(shadow_report_id=batch.shadow_report_id,
            scope=identity.scope, batch_class=batch.batch_class,
            consumers=(identity.consumer_id,), required_surfaces=batch.required_surfaces)
        ok, problems = require_batch_report(adapted, report_checker)
        if not ok:
            raise plane.CutoverError("; ".join(problems))
        return {"identity": identity.as_row(), "valid": True, "reference": batch.shadow_report_id,
                "batch_class": batch.batch_class, "required_surfaces": list(batch.required_surfaces)}

    def fallback(identity):
        policy = recovery.current_policy(identity, path=path)
        if policy is None or policy["stopped"]:
            raise plane.CutoverError("registered current read recovery strategy required")
        recovery._proof(identity, policy, path=path)
        return {"identity": identity.as_row(), "valid": True, "reference": policy["reference"]}

    def compatibility(workflow, identity, routes):
        artifact = batch.scope["compatibility_proofs"][identity.key]
        raw = Path(artifact["reference"]).read_bytes()
        proof = json.loads(raw)
        if (hashlib.sha256(raw).hexdigest() != artifact["sha256"]
                or proof["identity"] != identity.as_row() or proof["workflow"] != workflow
                or proof["implementation"] != implementation_hashes()
                or proof["routes"] != routes
                or datetime.fromisoformat(proof["valid_until"]) <= datetime.now(UTC)
                or proof["report_id"] != batch.shadow_report_id):
            raise plane.CutoverError("compatibility proof scope/routes/expiry/implementation drift")
        if not proof.get("edges"):
            raise plane.CutoverError("explicit dependency compatibility edges required")
        # Disabled actions still need authorization and compatibility, but they
        # must remain possible when the very inputs being stopped are invalid.
        if any(m["route"] == "target" for m in request["members"]):
            if projection_checker is None:
                raise plane.CutoverError("actual projection checker required")
            verdict = projection_checker(identity,
                refs=batch.scope["projection_refs"][identity.key],
                fundamental_mode=batch.scope.get("fundamental_mode", ""))
            if (verdict.get("available") is not True or verdict.get("checked") is not True
                    or verdict.get("identity") != identity.as_row() or not verdict.get("refs")):
                raise plane.CutoverError("projection not actually checked for this identity")
        return {**proof, "valid": True, "reference": artifact["reference"]}

    return {"authorization": authorization, "qualification": qualification, "report": report,
            "fallback": fallback, "compatibility": compatibility}
