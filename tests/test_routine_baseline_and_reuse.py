"""Routine-mode PEAD baseline, financial summary and Dispatcher data-vintage reuse (Phase F 10.6/10.8,
user rulings 2026-10-16). Calendar, graph and Information reads are explicit fakes."""
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from ats.agents.fundamental import inputs as fin
from ats.agents.fundamental import routine
from ats.schemas.pead import Expectation, ExpectationSet, PeadDossier

NOW = datetime(2026, 10, 16, 14, 0, tzinfo=UTC)                       # 10:00 ET


def calendar(monkeypatch, *, day, hour="amc", label="Q3 FY2027", before=3):
    from ats.data.runtime import earnings as cal

    monkeypatch.setattr(cal, "next_earnings", lambda s: {"date": day, "hour": hour, "quarter": 3, "year": 2027})
    monkeypatch.setattr(cal, "resolve_fiscal_label", lambda s, p, config_label="": (label, "derived"))
    monkeypatch.setattr("ats.config.load_pead_global", lambda: {"schedule": {"prep_days_before": before}})


@pytest.mark.parametrize("day,hour,open_,passed", [
    (date(2026, 10, 19), "amc", True, False),      # 3 days out
    (date(2026, 10, 20), "amc", False, False),     # 4 days out: too early
    (date(2026, 10, 16), "amc", True, False),      # same-day after-close print: still before the release
    (date(2026, 10, 16), "bmo", False, True),      # same-day before-open print: already released
])
def test_the_window_is_counted_in_days_before_the_next_print(monkeypatch, day, hour, open_, passed):
    calendar(monkeypatch, day=day, hour=hour)
    window = routine.real_baseline_window("nvda", now=NOW)
    assert (window["open"], window["cutoff_passed"], window["label"]) == (open_, passed, "Q3 FY2027")


def test_no_upcoming_print_means_no_window(monkeypatch):
    from ats.data.runtime import earnings as cal

    monkeypatch.setattr(cal, "next_earnings", lambda s: None)
    assert routine.real_baseline_window("NVDA", now=NOW)["label"] == ""


class Store:
    def __init__(self, dossier=None):
        self.dossiers, self.envelopes = ({"Q3 FY2027": dossier} if dossier else {}), []

    def get_dossier(self, symbol, label):
        return self.dossiers.get(label)

    def task_projection_envelopes(self, **kw):
        return [{"payload": e.payload} for e in self.envelopes]

    def save_task_projection_envelope(self, envelope):
        self.envelopes.append(envelope)


def dossier(narrative="NVDA baseline"):
    es = ExpectationSet(symbol="NVDA", fiscal_label="Q3 FY2027", as_of=NOW, narrative=narrative,
                        focus_ranking=["data center"], consensus_eps=2.47,
                        expectations=[Expectation(dim_key="datacenter_rev", neutral="ok")])
    return PeadDossier(symbol="NVDA", fiscal_label="Q3 FY2027", updated_at=NOW, expectation_set=es)


@pytest.fixture
def world(monkeypatch):
    state = SimpleNamespace(window={"label": "Q3 FY2027", "open": True, "days_to": 3, "date": "2026-11-18",
                                    "session": "amc", "cutoff_passed": False}, builds=[])
    monkeypatch.setattr(routine, "baseline_window", lambda s, now=None: {"symbol": s, **state.window})
    monkeypatch.setattr(routine, "_brief_facts", lambda store, s: (["fact A", "fact B"], "brief-1"))

    class App:
        def __init__(self, store):
            self.store = store

        def invoke(self, pead_state, config):
            state.builds.append(pead_state)
            self.store.dossiers["Q3 FY2027"] = dossier(f"build {len(state.builds)}")

    state.app = App
    return state


def run(world, store, monkeypatch, **extra):
    monkeypatch.setattr("ats.graph.pead.build_pead_graph", lambda checkpointer=None: world.app(store))
    return routine.ensure_baseline(store, SimpleNamespace(symbol="nvda", use_llm=True, extra=extra))


def test_inside_the_window_without_a_baseline_it_builds_one_from_the_latest_information(world, monkeypatch):
    store = Store()
    out = run(world, store, monkeypatch)
    assert out["baseline"] == "built" and out["reason"].startswith("财报前 3 天")
    built = world.builds[0]
    assert built.phase == "prep" and built.fiscal_label == "Q3 FY2027" and built.information_facts == ["fact A", "fact B"]
    assert built.earnings_date == "2026-11-18"
    payload = store.envelopes[0].payload
    assert payload["driver"].startswith("[基线 v1] Q3 FY2027") and payload["metric"] == "narrative"
    assert payload["new_value"] is None and "brief-1" in store.envelopes[0].input_refs


def test_an_existing_baseline_is_not_rebuilt_and_outside_the_window_nothing_happens(world, monkeypatch):
    assert run(world, Store(dossier()), monkeypatch)["baseline"] == "exists"
    world.window["open"] = False
    assert run(world, Store(), monkeypatch)["baseline"] == "outside_window"
    assert world.builds == []


def test_a_manual_refresh_rebuilds_and_publishes_the_next_version(world, monkeypatch):
    store = Store(dossier())
    store.envelopes.append(SimpleNamespace(payload={"period": "Q3 FY2027", "driver": "[基线 v1] old"}))
    world.window["open"] = False                                            # manual works outside the window
    out = run(world, store, monkeypatch, refresh_baseline=True)
    assert out["baseline"] == "built" and out["reason"] == "手动刷新"
    assert store.envelopes[-1].payload["driver"].startswith("[基线 v2]")


def test_after_the_print_the_baseline_is_frozen_even_on_manual_refresh(world, monkeypatch):
    world.window.update(cutoff_passed=True, open=False)
    assert run(world, Store(dossier()), monkeypatch, refresh_baseline=True)["baseline"] == "frozen_after_print"
    assert world.builds == []


def test_the_financial_summary_keeps_the_latest_value_per_period_and_separates_bases():
    rows = [
        {"metric_id": "financial.revenue.gaap", "period": "2026-06-30", "period_end": "2026-06-30", "value": "1",
         "known_at": "2026-07-01", "unit": "USD", "period_basis": "quarter"},
        {"metric_id": "financial.revenue.gaap", "period": "2026-06-30", "period_end": "2026-06-30", "value": "2",
         "known_at": "2026-08-01", "unit": "USD", "period_basis": "quarter"},
        {"metric_id": "financial.revenue.gaap", "period": "2026-06-30", "period_end": "2026-06-30", "value": "9e9",
         "known_at": "2026-08-01", "unit": "USD", "period_basis": "annual"},
        *[{"metric_id": "financial.capex.gaap", "period": f"20{y}-12-31", "period_end": f"20{y}-12-31",
           "value": "5", "known_at": "x", "unit": "USD", "period_basis": "annual"} for y in range(10, 26)],
    ]
    text = fin.financial_summary({"entity": "X", "rows": rows, "as_of": "t"}, periods=8)
    assert "[USD, quarter]: 2026-06-30=2" in text and "[USD, annual]: 2026-06-30=9.000B" in text
    capex = next(line for line in text.splitlines() if "capex" in line)
    assert capex.count("=") == 8 and "2025-12-31" in capex and "2017-12-31" not in capex
    assert len(fin.financial_summary({"rows": rows}, max_chars=50)) < 80


def test_dispatcher_takes_data_vintage_from_the_resolver_only_when_the_plan_has_none():
    from ats.workflow.dispatcher import Dispatcher

    task = SimpleNamespace(task_id="information-brief", instance_key="dispatcher:information-brief:entity:NVDA",
                           scope=SimpleNamespace(key="entity:NVDA"))
    plan = SimpleNamespace(task_inputs={}, source_config_hashes={})
    seen = Dispatcher(vintage_resolver=lambda p, t, i: ["documents:NVDA:2@abc"])._task_inputs(plan, task, {})
    assert seen[1] == ["documents:NVDA:2@abc"]
    given = SimpleNamespace(task_inputs={"data_vintage_refs": ["fixed"]}, source_config_hashes={})
    assert Dispatcher(vintage_resolver=lambda p, t, i: ["other"])._task_inputs(given, task, {})[1] == ["fixed"]

    def broken(p, t, i):
        raise RuntimeError("products down")
    assert Dispatcher(vintage_resolver=broken)._task_inputs(plan, task, {})[1] == []
    assert Dispatcher()._task_inputs(plan, task, {})[1] == []                # tests/legacy: unchanged


def test_only_a_manual_trigger_can_ask_the_routine_adapter_to_rebuild_the_baseline(monkeypatch):
    from ats.workflow import dispatcher

    seen = []
    monkeypatch.setattr("ats.agents.fundamental.entry.run_fundamental_pass", lambda request: seen.append(request) or {})
    monkeypatch.setattr(dispatcher, "_information_dependency_refs", lambda c: [])
    for kind in ("manual", "schedule"):
        context = SimpleNamespace(task=SimpleNamespace(scope=SimpleNamespace(id="NVDA")),
                                  task_inputs={"refresh_baseline": True, "use_llm": False},
                                  trigger=SimpleNamespace(kind=kind), data_vintage_refs=[])
        dispatcher._fundamental_routine_adapter(context)
    assert [r.extra["refresh_baseline"] for r in seen] == [True, False]


def _envelope(input_refs, vintage):
    from ats.agent.task_projection import ProjectionScope, build_envelope

    payload = {"entity": "NVDA", "headline": "h", "summary": "s", "relevance": "low", "sources": ["x"],
               "fact_changes": ["a"], "entities": ["NVDA"]}
    return build_envelope(role="information_brief", payload=payload, scope=ProjectionScope(kind="entity", id="NVDA"),
                          as_of="2026-10-16T00:00:00+00:00", input_refs=input_refs, data_vintage_refs=vintage)


def test_reuse_requires_the_plan_refs_but_tolerates_the_refs_an_adapter_added():
    from ats.workflow.dispatcher import Dispatcher

    task = SimpleNamespace(task_id="information-brief", scope=SimpleNamespace(key="entity:NVDA"))
    env = _envelope(["config:x@1", "doc-a@v1"], ["documents:NVDA:1@abc"])
    check = lambda plan_refs, vintage: Dispatcher._projection_reuse_check(
        env, task, plan_refs, vintage, "2026-10-16T01:00:00+00:00", 72 * 3600, "InformationBrief")
    assert check(["config:x@1"], ["documents:NVDA:1@abc"])[1] != "input_refs_changed"
    assert check(["config:y@2"], ["documents:NVDA:1@abc"]) == (False, "input_refs_changed")
    assert check(["config:x@1"], ["documents:NVDA:2@def"])[1] == "data_vintage_changed"


def _routine_rows(agent_run="A", n=2, input_refs=("brief-1", "config:x@1"), vintage=("financials:NVDA:3@abc",)):
    from ats.agent.task_projection import ProjectionScope, build_envelope
    from ats.workflow.evaluation_clock import now

    stamp = now(UTC).isoformat(timespec="seconds")
    rows = []
    for i in range(n):
        env = build_envelope(
            role="fundamental_expectation_update", scope=ProjectionScope(kind="entity", id="NVDA"), as_of=stamp,
            payload={"entity": "NVDA", "metric": "narrative", "period": "Q3 FY2027", "previous_value": None,
                     "new_value": None, "driver": f"[确认] fact {i}"},
            input_refs=list(input_refs), data_vintage_refs=list(vintage), agent_run_id=agent_run)
        row = env.model_dump(mode="json")
        row.update(scope_kind="entity", scope_id="NVDA")
        rows.append(row)
    return rows


class Memory:
    def __init__(self, rows, refs, status="succeeded"):
        self.rows, self.refs, self.status = rows, refs, status
        self.conn = SimpleNamespace(execute=lambda sql, args: SimpleNamespace(fetchone=lambda: {
            "status": self.status, "projection_refs_json": __import__("json").dumps(self.refs)}))

    def task_projection_envelopes(self, **kw):
        return self.rows


def _select(memory, vintage=("financials:NVDA:3@abc",)):
    from ats.workflow.dispatcher import Dispatcher

    task = SimpleNamespace(task_id="fundamental-routine", scope=SimpleNamespace(key="entity:NVDA", kind="entity", id="NVDA"))
    from ats.workflow.evaluation_clock import now

    return Dispatcher()._select_reusable(memory, task, ["brief-1", "config:x@1"], list(vintage),
                                         now(UTC).isoformat(timespec="seconds"))


def test_a_complete_routine_bundle_is_reused_as_a_whole_when_every_member_still_validates():
    rows = _routine_rows(n=2)
    reused, detail = _select(Memory(rows, [r["projection_id"] for r in rows]))
    assert detail["reason"] == "reusable_bundle" and {e.projection_id for e in reused} == {r["projection_id"] for r in rows}


def test_a_routine_bundle_with_a_missing_member_or_changed_vintage_is_recomputed():
    rows = _routine_rows(n=2)
    refs = [r["projection_id"] for r in rows] + ["gone"]
    assert _select(Memory(rows, refs))[0] is None
    assert _select(Memory(rows, [r["projection_id"] for r in rows]), vintage=("financials:NVDA:4@def",))[1]["decision"] == "recompute"


# --- target period reaches the prompts and the Scorecard (2026-10-16, MSFT baseline said Q4 FY2026) ------------
def _state(label="Q1 FY2027", date="2026-10-28", **kw):
    from ats.graph.pead_state import PeadState

    return PeadState(symbol="MSFT", phase=kw.pop("phase", "prep"), as_of=NOW, fiscal_label=label,
                     earnings_date=date, use_llm=True, **kw)


def test_the_runs_target_period_replaces_the_stale_hand_written_config_label():
    from ats.graph import pead

    out = pead.load(_state())
    assert out["fiscal_label"] == "Q1 FY2027" and out["config"].fiscal_label == "Q1 FY2027"
    legacy = pead.load(_state(label=""))                       # no target period: config label as before
    assert legacy["config"].fiscal_label == legacy["fiscal_label"] != "Q1 FY2027"


def test_the_prep_prompts_name_the_target_print_its_date_and_that_it_has_not_happened(monkeypatch):
    from ats.agents.pead import prep
    from ats.graph import pead

    seen = []
    monkeypatch.setattr(prep, "run_structured", lambda role, schema, ctx, skill_slug=None: seen.append(ctx) or schema())
    state = _state()
    cfg = pead.load(state)["config"]
    note = pead.target_event_note(state)
    prep.framework(cfg, "FIN", {}, event_note=note)
    prep.narrative(cfg, "FIN", {}, event_note=note)
    from ats.agents.pead.outputs import NarrativeView

    prep.expectations(cfg, NarrativeView(narrative="n"), "FIN", {}, event_note=note)
    assert len(seen) >= 3
    for ctx in seen:
        assert "Q1 FY2027" in ctx and "Q4 FY2026" not in ctx
        assert "2026-10-28" in ctx and "2026-10-16" in ctx and "尚未发布" in ctx


def test_without_a_known_release_date_the_note_still_says_the_print_has_not_happened():
    from ats.graph import pead

    note = pead.target_event_note(_state(date=""))
    assert "Q1 FY2027" in note and "预计发布日" not in note and "尚未发布" in note


def test_event_scoring_stamps_the_scorecard_with_the_run_period_not_the_config_one():
    from ats.graph import pead

    cfg = pead.load(_state(label="Q3 FY2026", phase="score"))["config"]
    assert cfg.fiscal_label == "Q3 FY2026"


# --- routine classification: own role, one retry, visible fallback ------------------------------------------
BASELINE = {"symbol": "MSFT", "fiscal_label": "Q1 FY2027", "narrative": "n", "metrics": {}, "dim_keys": ["x"],
            "kpi_words": set()}


def _view(labels):
    from ats.agents.fundamental.outputs import ClassificationItem, ClassificationView

    return ClassificationView(items=[ClassificationItem(index=i, classification=c, reason="r") for i, c in enumerate(labels)])


def test_classification_uses_its_own_role_with_a_bounded_answer(monkeypatch):
    seen = {}

    def fake(role, schema, ctx, skill_slug=None):
        seen.update(role=role, ctx=ctx)
        return _view(["确认", "新增"])
    monkeypatch.setattr("ats.agents.base.run_structured", fake)
    labels, source = routine.classify_changes_with_source(["a", "b"], BASELINE)
    assert (labels, source) == (["确认", "新增"], "llm") and seen["role"] == "fundamental_classifier"
    assert "exactly 2 items" in seen["ctx"] and "at most 30 characters" in seen["ctx"]


def test_a_truncated_answer_is_retried_once_then_falls_back_to_the_rules(monkeypatch):
    calls = []

    def fake(role, schema, ctx, skill_slug=None):
        calls.append(role)
        raise ValueError("Could not parse response content as the length limit was reached")
    monkeypatch.setattr("ats.agents.base.run_structured", fake)
    labels, source = routine.classify_changes_with_source(["a", "b"], BASELINE)
    assert source == "rules_fallback" and len(labels) == 2 and len(calls) == routine.CLASSIFIER_ATTEMPTS == 2


def test_a_second_attempt_that_succeeds_is_used_and_a_wrong_item_count_counts_as_failure(monkeypatch):
    answers = iter([RuntimeError("length"), _view(["确认"])])

    def fake(role, schema, ctx, skill_slug=None):
        value = next(answers)
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr("ats.agents.base.run_structured", fake)
    assert routine.classify_changes_with_source(["a"], BASELINE) == (["确认"], "llm")
    monkeypatch.setattr("ats.agents.base.run_structured", lambda *a, **k: _view(["确认"]))
    assert routine.classify_changes_with_source(["a", "b"], BASELINE)[1] == "rules_fallback"


def test_choosing_no_model_is_not_a_fallback_and_carries_no_marker(monkeypatch):
    monkeypatch.setattr("ats.agents.base.run_structured", lambda *a, **k: pytest.fail("no model expected"))
    labels, source = routine.classify_changes_with_source(["a"], BASELINE, use_llm=False)
    assert source == "rules" and routine.classify_changes(["a"], BASELINE, use_llm=False) == labels


def test_only_a_fallback_marks_the_driver():
    plain = routine.build_expectation_updates(["fact"], ["新增"], BASELINE)[0]["driver"]
    marked = routine.build_expectation_updates(["fact"], ["新增"], BASELINE, note=routine.RULES_FALLBACK_NOTE)[0]["driver"]
    assert plain == "[新增] fact" and marked == "[新增] fact｜分类器：规则回退"



@pytest.mark.parametrize("utc,expected", [
    ("2026-10-16T14:00:00+00:00", "market-session:NVDA:2026-10-16:before-close"),   # 10:00 ET
    ("2026-10-16T20:30:00+00:00", "market-session:NVDA:2026-10-16:after-close"),    # 16:30 ET
    ("2026-10-17T02:00:00+00:00", "market-session:NVDA:2026-10-16:after-close"),    # 22:00 ET, same session
])
def test_technical_reuses_within_one_new_york_session_side(utc, expected):
    from ats.workflow.dispatcher import default_data_vintage

    task = SimpleNamespace(task_id="technical-review", scope=SimpleNamespace(kind="entity", id="nvda"))
    assert default_data_vintage(SimpleNamespace(as_of=utc, run_id="r"), task, {}) == [expected]
