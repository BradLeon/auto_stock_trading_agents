"""Layer consumer cutover (Phase F 10.5, 2026-10-17): read-only health check and data-vintage reference.

The evidence repository, the knowledge corpus and the sector config are explicit fakes: these tests prove what the
check treats as a failure and a warning, that it never goes through a consumer read gate, the memory store, a
provider or the network, and that the vintage changes exactly when an input of the review changes.
"""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ats.workflow import consumer_health as health
from ats.workflow import layer_sector_inputs as inp

NOW = datetime(2026, 10, 17, 1, 0, tzinfo=UTC)


class Claim:
    def __init__(self, witnesses):
        self._w = set(witnesses)
        self.witnesses, self.entities, self.concepts = [], [], []

    def expected_witnesses(self):
        return set(self._w)


def make_layer(key, witnesses=("AAA", "BBB"), notes=("n1.md",), claims=True):
    return SimpleNamespace(key=key, claims=[Claim(witnesses)] if claims else [],
                           structure_notes={f"note{i}": path for i, path in enumerate(notes)},
                           tickers=[SimpleNamespace(symbol=w) for w in witnesses], cohort_extra=[])


def obs(entity, n, *, days=3, **overrides):
    stamp = (NOW - timedelta(days=days)).isoformat()
    return [{**dict(id=f"{entity}-{i}", entity=entity, observed_at=stamp, document_id=f"doc-{entity}"),
             **overrides} for i in range(n)]


class Repo:
    def __init__(self, rows):
        self.rows, self.closed = rows, False

    def observations(self, *, entity, limit):
        value = self.rows.get(entity, [])
        if isinstance(value, Exception):
            raise value
        return value[:limit]

    def close(self):
        self.closed = True


@pytest.fixture
def world(monkeypatch):
    state = SimpleNamespace(
        layers=[make_layer("L1"), make_layer("L2", witnesses=("CCC",), notes=("n2.md",))],
        rows={"AAA": obs("AAA", 3), "BBB": obs("BBB", 2), "CCC": obs("CCC", 4)},
        packets=None)
    state.repo = Repo(state.rows)
    monkeypatch.setattr(inp, "configured_sectors", lambda: ["demo"])
    monkeypatch.setattr(inp, "load_sector", lambda name: SimpleNamespace(layers=state.layers, layer_by_key=lambda key: next(
        (l for l in state.layers if l.key == key), None)))
    monkeypatch.setattr(inp, "witness_entities", lambda layer: sorted(
        {w for claim in layer.claims for w in claim.expected_witnesses()}))
    monkeypatch.setattr(health, "_unstructured_repository", lambda: state.repo)

    def packet(paths):
        if state.packets is not None:
            return state.packets(paths)
        return {"status": "complete", "missing": [], "notes": [
            {"path": p, "content_hash": f"hash-{p}", "version_id": f"v-{p}"} for p in paths]}
    monkeypatch.setattr(inp, "knowledge_notes", packet)
    # The review's own read gate, the memory store and any provider are exactly what the check must not touch.
    monkeypatch.setattr("ats.memory.get_store", lambda: pytest.fail("health check must not open the memory store"))
    for name in ("ats.workflow.runtime_reads.gate_read", "ats.workflow.cutover_routing.read_route",
                 "ats.data.consumer_api.read_input"):
        monkeypatch.setattr(name, lambda *a, **k: pytest.fail("health check went through a consumer read gate"))
    monkeypatch.setattr("socket.socket.connect", lambda *a, **k: pytest.fail("health check used the network"))
    return state


def test_layer_is_healthy_when_every_layer_has_evidence_and_notes(world):
    result = health.check("layer", now=NOW)
    assert result["status"] == "ok" and result["problems"] == [] and result["warnings"] == []
    assert result["detail"]["L1"]["observations"] == 5 and result["detail"]["L2"]["observations"] == 4
    assert result["detail"]["L1"]["knowledge_notes"] == {"published": 1, "missing": []}
    assert world.repo.closed


def test_thin_layers_are_warnings_because_the_review_caps_a_blind_round_itself(world):
    world.layers.append(make_layer("L3", witnesses=(), notes=("n3.md",), claims=False))     # config gap
    world.layers.append(make_layer("L4", witnesses=("ZZZ",), notes=("n4.md",)))               # nobody answers
    world.rows["BBB"] = []                                                                    # one silent witness in L1
    result = health.check("layer", now=NOW)
    assert result["status"] == "ok"
    text = "\n".join(result["warnings"])
    assert "L3: no claims configured" in text and "L4: no observations for any of its 1 witnesses" in text
    assert "L1: no observations for witnesses BBB" in text


def test_old_evidence_is_a_warning_not_a_failure(world):
    world.rows["CCC"] = obs("CCC", 2, days=health.LAYER_EVIDENCE_STALE_DAYS + 30)
    result = health.check("layer", now=NOW)
    assert result["status"] == "ok" and any("L2: newest observation" in w for w in result["warnings"])


def test_no_observation_at_all_for_the_whole_sector_is_a_failure(world):
    world.rows.clear()
    result = health.check("layer", now=NOW)
    assert result["status"] == "failed" and any("no evidence observations for any witness" in p
                                                for p in result["problems"])


def test_an_observation_without_its_document_or_identity_is_a_failure(world):
    world.rows["AAA"] = obs("AAA", 2, document_id="", source_url="")
    world.rows["CCC"] = obs("CCC", 1, observed_at="")
    result = health.check("layer", now=NOW)
    assert result["status"] == "failed"
    assert any("L1: 2 observations without" in p for p in result["problems"])
    assert any("L2: 1 observations without" in p for p in result["problems"])


def test_an_unreadable_evidence_product_is_a_failure_for_that_layer_only_reported_once(world):
    world.rows["AAA"] = RuntimeError("database is locked")
    result = health.check("layer", now=NOW)
    assert result["status"] == "failed"
    assert [p for p in result["problems"] if "unreadable" in p] == [
        "L1: evidence observations unreadable (RuntimeError: database is locked)"]
    assert result["detail"]["L2"]["observations"] == 4          # the other layer is still described


def test_a_missing_note_is_a_warning_but_no_published_note_anywhere_is_a_failure(world):
    world.packets = lambda paths: {"status": "partial", "notes": [{"path": "n1.md"}] if "n1.md" in paths else [],
                                   "missing": [{"path": p, "reason": "not_published"} for p in paths if p != "n1.md"]}
    result = health.check("layer", now=NOW)
    assert result["status"] == "ok" and any("n2.md not_published" in w for w in result["warnings"])
    world.packets = lambda paths: {"status": "missing", "notes": [],
                                   "missing": [{"path": p, "reason": "not_published"} for p in paths]}
    result = health.check("layer", now=NOW)
    assert result["status"] == "failed" and any("knowledge notes is published" in p for p in result["problems"])


def test_an_unreadable_sector_config_or_one_without_layers_is_a_failure(world, monkeypatch):
    def broken(name):
        raise FileNotFoundError(name)
    monkeypatch.setattr(inp, "load_sector", broken)
    assert health.check("layer", now=NOW)["status"] == "failed"
    monkeypatch.setattr(inp, "load_sector", lambda name: SimpleNamespace(layers=[]))
    assert any("no layers configured" in p for p in health.check("layer", now=NOW)["problems"])


# --- data vintage -------------------------------------------------------------------------------------------
@pytest.fixture
def vintage(world, monkeypatch):
    monkeypatch.setattr("ats.data.stores.unstructured.get_platform_unstructured_repository", lambda: world.repo)
    return world


def test_the_vintage_names_the_observation_set_and_the_note_versions(vintage):
    refs = inp.layer_vintage("demo", "L1")
    assert refs[0].startswith("evidence:L1:5@") and "knowledge:n1.md@hash-n1.md" in refs
    assert vintage.repo.closed


def test_the_vintage_is_stable_and_changes_only_when_an_input_changes(vintage):
    first = inp.layer_vintage("demo", "L1")
    assert inp.layer_vintage("demo", "L1") == first
    # another layer's evidence is not this layer's input
    vintage.rows["CCC"] = obs("CCC", 9)
    assert inp.layer_vintage("demo", "L1") == first
    # new observation of a witness, or one superseded (it drops out of the newest-200 read): both move it
    vintage.rows["AAA"] = obs("AAA", 4)
    assert inp.layer_vintage("demo", "L1") != first
    vintage.rows["AAA"] = obs("AAA", 2)
    assert inp.layer_vintage("demo", "L1")[0].startswith("evidence:L1:4@")
    # order of rows is irrelevant
    vintage.rows["AAA"] = list(reversed(obs("AAA", 3)))
    assert inp.layer_vintage("demo", "L1") == first


def test_a_new_note_version_or_a_note_that_vanishes_changes_the_vintage(vintage):
    first = inp.layer_vintage("demo", "L1")
    vintage.packets = lambda paths: {"status": "complete", "missing": [], "notes": [
        {"path": p, "content_hash": "other", "version_id": "v2"} for p in paths]}
    assert inp.layer_vintage("demo", "L1") != first
    vintage.packets = lambda paths: {"status": "missing", "notes": [],
                                     "missing": [{"path": p, "reason": "not_published"} for p in paths]}
    assert any(ref.startswith("knowledge-missing:n1.md") for ref in inp.layer_vintage("demo", "L1"))


def test_a_layer_without_evidence_still_has_a_vintage_so_an_unchanged_blind_round_is_reused(vintage):
    vintage.layers.append(make_layer("L3", witnesses=(), notes=("n3.md",), claims=False))
    refs = inp.layer_vintage("demo", "L3")
    assert refs[0].startswith("evidence:L3:0@") and refs == inp.layer_vintage("demo", "L3")


def test_default_data_vintage_resolves_a_layer_task_from_a_sector_plan_without_network(vintage):
    from ats.workflow.dispatcher import default_data_vintage

    plan = SimpleNamespace(request_scope=SimpleNamespace(kind="sector", id="demo"), as_of="2026-10-17T01:00:00+00:00")
    task = SimpleNamespace(task_id="layer-review", scope=SimpleNamespace(kind="layer", id="L2"))
    assert default_data_vintage(plan, task, {})[0].startswith("evidence:L2:4@")
    layer_scoped = SimpleNamespace(request_scope=SimpleNamespace(kind="layer", id="L2"), as_of=plan.as_of)
    assert default_data_vintage(layer_scoped, task, {"sector": "demo"})[0].startswith("evidence:L2:4@")
    assert default_data_vintage(layer_scoped, task, {}) == []          # no sector named: no vintage, so recompute


# --- reuse through the Dispatcher's own selection ------------------------------------------------------------
class Memory:
    def __init__(self, rows):
        self.rows = rows
        self.conn = SimpleNamespace(execute=lambda sql, args: SimpleNamespace(fetchone=lambda: {
            "status": "succeeded", "projection_refs_json": __import__("json").dumps(
                [r["projection_id"] for r in rows])}))

    def task_projection_envelopes(self, **kwargs):
        return self.rows


def published_layer(vintage_refs, input_refs=("config:sectors/demo.yaml@1",), age_days=0):
    from ats.agent.task_projection import ProjectionScope, build_envelope
    from ats.workflow.evaluation_clock import now

    stamp = now(UTC).isoformat(timespec="seconds")
    env = build_envelope(role="layer_analysis", scope=ProjectionScope(kind="layer", id="L1"), as_of=stamp,
                         payload={"layer": "L1", "status": "steady", "summary": "s", "findings": ["f"],
                                  "confidence": 0.3},
                         input_refs=list(input_refs), data_vintage_refs=list(vintage_refs), agent_run_id="A",
                         created_at=(now(UTC) - timedelta(days=age_days)).isoformat(timespec="seconds"))
    row = env.model_dump(mode="json")
    row.update(scope_kind="layer", scope_id="L1")
    return row


def select(rows, vintage):
    from ats.workflow.dispatcher import Dispatcher
    from ats.workflow.evaluation_clock import now

    task = SimpleNamespace(task_id="layer-review", scope=SimpleNamespace(key="layer:L1", kind="layer", id="L1"))
    return Dispatcher()._select_reusable(Memory(rows), task, ["config:sectors/demo.yaml@1"], list(vintage),
                                         now(UTC).isoformat(timespec="seconds"))


def test_an_unchanged_vintage_reuses_the_published_layer_projection_and_a_changed_one_does_not(vintage):
    refs = inp.layer_vintage("demo", "L1")
    row = published_layer(refs)
    reused, detail = select([row], refs)
    assert detail["decision"] == "hit" and reused.projection_id == row["projection_id"]
    vintage.rows["AAA"] = obs("AAA", 4)
    assert select([row], inp.layer_vintage("demo", "L1"))[1]["decision"] == "recompute"


def test_a_layer_projection_older_than_the_weekly_window_is_not_reused_even_when_unchanged(vintage):
    from ats.workflow.planning import FRESHNESS_SECONDS

    assert FRESHNESS_SECONDS["layer-review"] == 7 * 86_400
    refs = inp.layer_vintage("demo", "L1")
    assert select([published_layer(refs, age_days=6)], refs)[1]["decision"] == "hit"
    assert select([published_layer(refs, age_days=8)], refs)[1]["reason"] == "expired"


def test_a_projection_without_a_vintage_is_never_reused(vintage):
    assert select([published_layer([])], [])[1] == {"decision": "recompute", "reason": "missing_data_vintage"}


# --- the single-layer entries judge the verdict against the claim verdicts the prompt shows -------------------
def _claim_layer():
    from ats.schemas.sector import SectorConfig, SectorLayer

    layer = SectorLayer(key="L6_memory", label="L6", tickers=[{"symbol": "MU"}],
                        claims=[{"id": "c1", "kind": "common", "statement": "x",
                                 "concepts": [{"key": "k", "desc": "d"}]}])
    return SectorConfig(name="demo", layers=[layer]), layer


def _stub_model(monkeypatch, calls, verdict):
    from ats.agents.sector.outputs import LayerVerdictView
    from ats.schemas.chain import ClaimAssessment

    def assessments(cfg, layer, **kwargs):
        calls.append(kwargs)
        if isinstance(verdict, Exception):
            raise verdict
        return [ClaimAssessment(claim_id="c1", layer="L6_memory", as_of=NOW, verdict=verdict)]
    monkeypatch.setattr("ats.agents.sector.assemble.layer_assessments", assessments)
    monkeypatch.setattr("ats.agents.layer.layer_review.run_structured", lambda *a, **k: LayerVerdictView(
        layer_key="L6_memory", layer_status="expanding", confidence=0.8, rationale="orders up"))


def test_a_caller_without_claim_verdicts_gets_them_computed_once_and_the_guards_see_them(monkeypatch):
    # Before 2026-10-17 this returned steady / 0.3 / "evidence missing" for every layer that had evidence.
    from ats.agents.layer import layer_review

    cfg, layer = _claim_layer()
    calls = []
    _stub_model(monkeypatch, calls, "supportive")
    verdict, ok = layer_review.run(cfg, layer)
    assert ok and verdict.layer_status == "expanding" and verdict.confidence == 0.8
    assert "证据缺失" not in verdict.rationale and len(calls) == 1      # one engine pass shared by prompt and guards


def test_the_guards_still_cap_a_round_whose_claims_said_nothing_or_whose_engine_failed(monkeypatch):
    from ats.agents.layer import layer_review

    cfg, layer = _claim_layer()
    for verdict in ("unknown", RuntimeError("ledger locked")):
        _stub_model(monkeypatch, [], verdict)
        result, ok = layer_review.run(cfg, layer)
        assert ok and result.layer_status == "steady" and result.confidence <= layer_review.BLIND_CONFIDENCE_CAP
        assert "证据缺失" in result.rationale


def test_a_caller_that_brings_its_own_verdicts_is_not_recomputed(monkeypatch):
    from ats.agents.layer import layer_review
    from ats.schemas.chain import ClaimAssessment

    cfg, layer = _claim_layer()
    calls = []
    _stub_model(monkeypatch, calls, "supportive")
    own = [ClaimAssessment(claim_id="c1", layer="L6_memory", as_of=NOW, verdict="contradicted")]
    result, _ = layer_review.run(cfg, layer, assessments=own)
    assert calls == [] and result.layer_status == "unclear"          # expanding without support is not allowed
