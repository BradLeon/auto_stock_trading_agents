import copy
import hashlib

import pytest

from ats.agent.task_projection import ProjectionScope
from ats.workflow import acceptance_matrix as mx
from ats.workflow import shadow_inputs as si
from ats.workflow.phase_e import build_plan
from ats.workflow.run_contracts import TriggerContext
from ats.workflow.scoped_routes import RouteIdentity, canonical_json
from ats.workflow.shadow_matrix import MatrixError

POINT = "2026-10-09T00:00:00+00:00"


def plan(tasks=("macro-review",), decision=False):
    return build_plan(requested_tasks=tasks, scope=ProjectionScope(kind="sector", id="ai_hardware"),
        trigger=TriggerContext(kind="manual", trigger_id="requirements"), as_of=POINT,
        run_id="requirements", profile_id="ai_hardware", enter_decision_cycle=decision,
        task_inputs={"macro-review": {"live_data": False, "use_llm": False}})


def matrix(tasks=("macro-review",), batch_class="research_read", **kwargs):
    identity = RouteIdentity("research", "chief", "v1", {"kind": "sector", "id": "ai_hardware",
        "entities": ["COHR"], "time_range": {"start": POINT, "end": POINT}})
    return mx.freeze(plan(tasks, batch_class != "research_read"), workflow_id="analysis",
                     identity=identity, batch_class=batch_class, **kwargs)


def packet(value):
    body = value.body
    surfaces = {s: "captured" if d != si.NOT_APPLICABLE else si.NOT_APPLICABLE
                for s, d in body["input_fixing"].items()}
    surfaces[si.LOGICAL_EVAL_TIME] = POINT
    return si.ShadowInputPacket("new-only", "chief", body["batch_class"],
                                surfaces=surfaces, scope=body["identity"]["scope"])


def test_persistent_research_needs_no_old_or_trading_evidence():
    value = matrix(model_execution=False)
    assert mx.restore(value.as_row()) == value
    assert value.body["legacy_required"] is False
    assert not any(a["required"] for a in value.body["assertions"]
                   if a["dimension"] in {"risk", "approval", "attribution"})
    assert mx.check_inputs(value, packet(value))


def test_runtime_research_is_fixed_even_when_caller_omits_it():
    value = matrix(("technical-review",))
    assert value.body["input_fixing"][si.MARKET_RUNTIME] != si.NOT_APPLICABLE
    fixed = packet(value)
    fixed.surfaces[si.MARKET_RUNTIME] = si.NOT_APPLICABLE
    with pytest.raises(MatrixError, match="not fixed"):
        mx.check_inputs(value, fixed)
    value = matrix(model_execution=False)
    with pytest.raises(MatrixError, match="runtime consumption omitted"):
        mx.check_inputs(value, packet(value), actual_runtime_surfaces=[si.MARKET_RUNTIME])


@pytest.mark.parametrize("mode", ["event", "routine"])
def test_decision_and_trading_fix_selected_mode_and_all_other_categories(mode, record_property):
    value = matrix(("sector-review", "fundamental-" + mode, "macro-review", "technical-review"),
                   batch_class="trading")
    assert mx.restore(value.as_row()) == value
    assert value.body["selected_mode"] == mode
    tasks = value.body["plan"]["tasks"]
    assert all(t["task_id"] != "fundamental-" + ("routine" if mode == "event" else "event") for t in tasks)
    assert {"layer-review", "information-brief", "sector-review", "macro-review", "technical-review"} <= {
        t["task_id"] for t in tasks}
    assert all(a["required"] for a in value.body["assertions"])
    assert mx.check_inputs(value, packet(value))
    record_property("fixed_requirement_matrix", canonical_json(value.as_row()))


def test_dependency_omission_rehashed_still_refuses():
    row = matrix(("fundamental-routine",)).as_row()
    row["plan"]["tasks"] = [t for t in row["plan"]["tasks"] if t["task_id"] != "information-brief"]
    row["matrix_hash"] = hashlib.sha256(canonical_json(
        {k: v for k, v in row.items() if k != "matrix_hash"}).encode()).hexdigest()
    with pytest.raises(MatrixError):
        mx.restore(row)


def test_model_enabled_task_cannot_declare_model_inputs_away():
    with pytest.raises(MatrixError, match="model input cannot be omitted"):
        matrix(("information-brief",), model_execution=False)


def test_rehashing_cannot_make_mandatory_assertion_not_applicable():
    row = matrix().as_row()
    row["assertions"][0]["required"] = False
    row["matrix_hash"] = hashlib.sha256(canonical_json(
        {k: v for k, v in row.items() if k != "matrix_hash"}).encode()).hexdigest()
    with pytest.raises(MatrixError, match="invalid or drifted"):
        mx.restore(row)


def test_scope_cannot_drift_and_returned_body_cannot_mutate_fixed_matrix():
    value = matrix()
    mutable = value.body
    mutable["identity"]["scope"]["entities"].append("NVDA")
    assert value.body["identity"]["scope"]["entities"] == ["COHR"]
    changed = copy.deepcopy(packet(value))
    changed.scope["entities"] = ["NVDA"]
    with pytest.raises(MatrixError, match="scope/class/time"):
        mx.check_inputs(value, changed)
