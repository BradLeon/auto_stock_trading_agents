"""A newly listed company before its first report is stated uncovered by Fundamental (ant-data-onboarding 4.3)."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ats.agent.task_projection import ProjectionScope, build_envelope
from ats.agents.chief import assemble
from ats.agents.fundamental import entry, routine
from ats.data.stores.schedule_calendar import ScheduleCalendarStore
from ats.memory.store import TradingMemory
from ats.schemas.pead import Actuals, Expectation, ExpectationSet, PeadDossier
from ats.workflow.dispatcher import Dispatcher
from ats.workflow.read_scope import ReadUnavailable

SCOPE = ProjectionScope(kind="entity", id="ANT")
PLAN = {"fundamental_expectation_update": {"role": "fundamental_expectation_update", "scopes": [SCOPE]}}
LABEL = "Q4 FY2026"


@pytest.fixture
def store(monkeypatch):
    memory = TradingMemory(":memory:")
    monkeypatch.setattr("ats.memory.get_store", lambda: memory)
    return memory


def run(symbol="ANT"):
    return entry.run_fundamental_pass(entry.routine_request(symbol, trigger="information_brief_update", use_llm=False))


def seed_dossier(store, *, actuals, baseline, symbol="ANT"):
    now = datetime.now(UTC)
    expectations = ExpectationSet(symbol=symbol, fiscal_label=LABEL, as_of=now, narrative="baseline",
                                  expectations=[Expectation(dim_key="arr_added", neutral="ok")])
    store.save_dossier(PeadDossier(
        symbol=symbol, fiscal_label=LABEL, updated_at=now, expectation_set=expectations if baseline else None,
        actuals=Actuals(symbol=symbol, fiscal_label=LABEL, as_of=now) if actuals else None))


def seed_brief(store, symbol="ANT"):
    payload = {"entity": symbol, "headline": "读数", "summary": "一条读数", "relevance": "high", "sources": ["rss:test"],
               "fact_changes": ["arr_added 新增年化收入读数 12"], "impact_candidates": ["revenue"], "entities": [symbol],
               "confidence": 0.6, "freshness": "published now", "unverified": ["待核验"]}
    store.save_task_projection_envelope(build_envelope(
        role="information_brief", payload=payload, scope=ProjectionScope(kind="entity", id=symbol),
        as_of=datetime.now(UTC).isoformat()))


def scan(store):
    return assemble.scan_projection_scopes(store, query_plan=PLAN)


def latest(store, symbol="ANT"):
    return store.task_projection_envelopes(agent_role="fundamental_expectation_update", scope_kind="entity",
                                           scope_id=symbol, limit=1)[0]


@pytest.mark.parametrize("with_brief", [False, True])
def test_before_the_first_report_routine_publishes_a_stated_gap(store, with_brief):
    if with_brief:
        seed_brief(store)
    out = run()
    assert out["coverage"] == "no_earnings_history" and len(out["published"]) == 1
    payload = latest(store)["payload"]
    assert payload["coverage"] == {"status": "uncovered", "reason": "no_earnings_history", "expected_report_date": ""}
    assert "无财报历史" in payload["driver"] and "未知" in payload["driver"]
    assert [(row["status"], row["reason"]) for row in scan(store)] == [("uncovered", "uncovered")]


def test_the_gap_names_a_published_next_earnings_date(store):
    day = datetime.now(UTC).date() + timedelta(days=70)
    ScheduleCalendarStore().register_earnings_event(
        "ANT", fiscal_year=2026, fiscal_quarter=4, event_date=day, actor="tester", reason="公司公告",
        evidence={"url": "https://example.test/announcement"})
    run()
    payload = latest(store)["payload"]
    assert payload["coverage"]["expected_report_date"] == day.isoformat() and day.isoformat() in payload["driver"]


def test_a_baseline_built_before_the_first_report_does_not_make_the_company_covered(store):
    seed_dossier(store, actuals=False, baseline=True)
    seed_brief(store)
    assert run()["coverage"] == "no_earnings_history"
    assert {row["status"] for row in scan(store)} == {"uncovered"}


def test_a_reported_company_without_a_baseline_stays_stated_uncovered_instead_of_failing(store):
    seed_dossier(store, actuals=True, baseline=False)
    seed_brief(store)
    assert run()["coverage"] == "no_baseline"
    assert "预期基线尚未建立" in latest(store)["payload"]["driver"]


def test_first_report_and_baseline_switch_to_normal_coverage_without_any_setting(store):
    seed_brief(store)
    assert run()["coverage"] == "no_earnings_history"
    seed_dossier(store, actuals=True, baseline=True)
    out = run()
    assert "coverage" not in out and out["published"]
    assert "coverage" not in latest(store)["payload"]
    assert "fresh" in {row["status"] for row in scan(store)}


def test_missing_financials_state_the_gap_only_for_a_new_listing(store, monkeypatch):
    def unavailable(*_args):
        raise ReadUnavailable("fundamental financial input unavailable: no_coverage")

    monkeypatch.setattr(routine, "ensure_baseline", unavailable)
    assert run()["baseline"]["baseline"] == "input_unavailable"
    with pytest.raises(ReadUnavailable):
        run("NVDA")


def test_an_established_company_without_a_baseline_still_fails(store):
    seed_brief(store, "NVDA")
    with pytest.raises(RuntimeError, match="baseline"):
        run("NVDA")


def test_the_decision_treats_the_company_as_uncovered_even_when_the_same_run_built_a_baseline(store, monkeypatch):
    from ats.workflow import decision_requirements

    stamp = datetime.now(UTC).isoformat(timespec="seconds")

    def save(entity, driver, **extra):
        envelope = build_envelope(
            role="fundamental_expectation_update", scope=ProjectionScope(kind="entity", id=entity), as_of=stamp,
            agent_run_id=f"run-{entity}", payload={"entity": entity, "metric": "narrative", "period": LABEL,
                                                   "previous_value": None, "new_value": None, "driver": driver, **extra})
        store.save_task_projection_envelope(envelope)
        return envelope.projection_id

    refs = {"ANT": [save("ANT", "[基线 v1] 财报前预期基线"),
                    save("ANT", "基本面未覆盖", coverage={"status": "uncovered", "reason": "no_earnings_history"})],
            "NVDA": [save("NVDA", "[确认] 读数")]}
    monkeypatch.setattr(decision_requirements, "routine_producer_refs", lambda s, row, scope: refs[scope.id])
    assert [row["status"] for row in scan(store)] == ["uncovered", "uncovered"]
    modes, uncovered, _ = decision_requirements.fundamental_coverage(
        store, targets=["ANT", "NVDA"], at=datetime.now(UTC), event_cycle=False)
    assert uncovered == ["ANT"] and modes == {"NVDA": "fundamental-routine"}


def test_a_stated_gap_is_never_reused_so_coverage_can_start_on_the_next_run(store):
    run()
    row = latest(store)
    from ats.agent.task_projection import TaskProjectionEnvelope

    envelope = TaskProjectionEnvelope.model_validate(
        {key: value for key, value in row.items() if key in TaskProjectionEnvelope.model_fields}
        | {"scope": SCOPE})
    task = SimpleNamespace(task_id="fundamental-routine", scope=SimpleNamespace(key="entity:ANT"))
    assert Dispatcher._projection_reuse_check(
        envelope, task, [], [], datetime.now(UTC).isoformat(), 72 * 3600,
        "FundamentalExpectationUpdate") == (False, "coverage_gap_recompute")
