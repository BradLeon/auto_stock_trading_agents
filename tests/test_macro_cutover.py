"""Macro consumer cutover pieces (Phase F 10.5, 2026-10-17): the read-only health check, the data-vintage
reference that makes an unchanged input set reusable, and the owner switch for `macro-review`.

The products are explicit fakes; the check and the vintage are proved to read only (no memory store, no
provider) and to judge exactly the inputs the review reads.
"""
import json
from contextlib import closing
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from ats.data.products import macro_inputs
from ats.data.products.macro_series import MacroSeriesSet
from ats.data.sources.macro_series import all_series
from ats.workflow import consumer_health as health

NOW = datetime(2026, 10, 17, 3, 0, tzinfo=UTC)
SPECS = all_series()
DAILY = [k for k, v in SPECS.items() if v[4] == "daily"]


def series_set(*, keys=None, stale=(), gaps=None, known="2026-10-16T22:00:00+00:00"):
    keys = list(SPECS) if keys is None else list(keys)
    index = pd.to_datetime(["2026-10-15"])
    return MacroSeriesSet(
        series={k: pd.Series([1.0], index=index) for k in keys},
        known_at={k: known for k in keys},
        gaps=tuple(sorted(set(SPECS) - set(keys))) if gaps is None else gaps,
        stale=tuple((k, "2026-09-01", 46) for k in stale), as_of=NOW)


def news_doc(theme, i=0, *, published="2026-10-15T12:00:00+00:00", **overrides):
    values = dict(document_id=f"{theme}-{i}", version_id=f"v{i}", content_hash="h", source="macro_theme_news",
                  published_at=published)
    return SimpleNamespace(**{**values, **overrides})


THEMES = ("inflation", "growth")


def good_state(**changes):
    state = {
        "series": {"value": series_set(), "error": ""},
        "news": {"value": {"days": 21, "themes": {t: [news_doc(t)] for t in THEMES}}, "error": ""},
        "regional": {"value": [{"id": "tw_ic_exports", "label": "TW", "period": "2026-09", "known_at": "2026-10-08"},
                               {"id": "kr_semi_exports", "label": "KR", "period": "2026-09", "known_at": "2026-10-15"}],
                     "error": ""},
        "factset": {"value": {"version_id": "SP500:2026-10-09@x", "report_date": "2026-10-09", "age_days": 8,
                              "state": "platform", "freshness": "latest_released"}, "error": ""},
    }
    for name, value in changes.items():
        state[name] = value
    return state


@pytest.fixture
def check(monkeypatch):
    """Run the real `_macro` over a given stored-input state; products and memory/provider access are traps."""
    class Structured:
        closed = False

        def close(self):
            self.closed = True

    products = SimpleNamespace(structured=Structured(), unstructured=object())
    monkeypatch.setattr("ats.data.products.get_platform_data_products", lambda readonly=False: products)
    monkeypatch.setattr("ats.memory.get_store", lambda *a, **k: pytest.fail("health check touched the memory store"))
    monkeypatch.setattr("ats.data.macro.fetch", lambda *a, **k: pytest.fail("health check called a provider"))
    monkeypatch.setattr("ats.data.macro.fetch_series", lambda *a, **k: pytest.fail("health check called a provider"))

    def run(state):
        monkeypatch.setattr(macro_inputs, "stored_input_state", lambda p, *, as_of=None, **kw: state)
        result = health.check("macro", now=NOW)
        assert products.structured.closed
        return result
    return run


# --- health: what is a failure and what is a warning ------------------------------------------------------
def test_macro_is_healthy_when_every_input_is_present_and_fresh(check):
    result = check(good_state())
    assert result["status"] == "ok" and result["warnings"] == [] and result["consumer_id"] == "macro"
    assert result["detail"]["series"]["stored"] == len(SPECS)


def test_per_input_gaps_are_only_warnings(check):
    state = good_state(
        series={"value": series_set(keys=[k for k in SPECS if k != "gold"], stale=["core_pce"]), "error": ""},
        news={"value": {"days": 21, "themes": {"inflation": [news_doc("inflation")], "growth": []}}, "error": ""},
        regional={"value": [{"id": "tw_ic_exports", "label": "TW", "period": "2026-08", "known_at": "x"},
                            {"id": "kr_semi_exports", "label": "KR", "period": "", "known_at": ""}], "error": ""},
        factset={"value": {"version_id": "SP500:2026-09-25@x", "report_date": "2026-09-25", "age_days": 22,
                           "state": "platform", "freshness": "latest_released"}, "error": ""})
    result = check(state)
    assert result["status"] == "ok"
    text = " | ".join(result["warnings"])
    for expected in ("not stored: gold", "indicator core_pce stale", "theme growth: no admitted news",
                     "regional tw_ic_exports", "regional kr_semi_exports: no stored data", "FactSet index report"):
        assert expected in text


def test_unreadable_regional_or_factset_is_a_warning_because_the_review_treats_both_as_additive(check):
    result = check(good_state(regional={"value": None, "error": "OperationalError: locked"},
                              factset={"value": None, "error": "RuntimeError: no packet"}))
    assert result["status"] == "ok" and len(result["warnings"]) == 2


def test_an_old_regional_period_warns_only_after_its_threshold(check):
    def regional(period):
        return {"value": [{"id": "tw_ic_exports", "label": "TW", "period": period, "known_at": "x"}], "error": ""}
    # 2026-09 ends 09-30: 17 days before NOW; 2026-08 ends 08-31: 47 days before NOW (> 45)
    assert not [w for w in check(good_state(regional=regional("2026-09")))["warnings"] if "regional" in w]
    assert [w for w in check(good_state(regional=regional("2026-08")))["warnings"] if "47 days" in w]


@pytest.mark.parametrize("state,fragment", [
    (dict(series={"value": None, "error": "OperationalError: no such table"}), "indicator series unreadable"),
    (dict(series={"value": series_set(keys=[]), "error": ""}), "no indicator series stored"),
    (dict(series={"value": series_set(keys=list(SPECS)[:10]), "error": ""}), "only 10/25 indicator series"),
    (dict(series={"value": series_set(stale=DAILY), "error": ""}), "no daily indicator has a print"),
    (dict(news={"value": None, "error": "OperationalError: locked"}), "admitted macro news unreadable"),
    (dict(news={"value": {"days": 21, "themes": {t: [] for t in THEMES}}, "error": ""}), "no admitted macro news for any theme"),
    (dict(news={"value": {"days": 21, "themes": {"inflation": [news_doc("inflation", version_id="")]}}, "error": ""}),
     "without version/hash/source"),
])
def test_the_check_fails_only_when_the_review_could_not_be_done(check, state, fragment):
    result = check(good_state(**state))
    assert result["status"] == "failed" and any(fragment in p for p in result["problems"])


def test_a_stopped_collection_is_a_warning_with_the_threshold_written_down(check):
    old = series_set(known=(NOW - timedelta(days=health.MACRO_SERIES_COLLECTION_DAYS + 2)).isoformat())
    result = check(good_state(series={"value": old, "error": ""}))
    assert result["status"] == "ok" and any("may have stopped" in w for w in result["warnings"])
    assert (health.MACRO_SERIES_MIN_COVERAGE, health.MACRO_REGIONAL_STALE_DAYS, health.MACRO_FACTSET_STALE_DAYS) == (0.5, 45, 14)


# --- the stored-input reader --------------------------------------------------------------------------------
class FakeStructured:
    def __init__(self, rows=None, error=None):
        self.rows, self.error = rows or [], error

    def observations(self, **kw):
        if self.error:
            raise self.error
        return self.rows


class FakeProducts:
    def __init__(self, *, rows=None, error=None, regional=None, packet=None):
        self.structured = FakeStructured(rows, error)
        self.unstructured = object()
        self._regional, self._packet = regional, packet

    def metric_series(self, *, metric, **kw):
        if isinstance(self._regional, Exception):
            raise self._regional
        rows = (self._regional or {}).get(metric, [])
        return {"rows": rows}

    def earnings_insight_analysis_packet(self, *, as_of=None):
        if isinstance(self._packet, Exception):
            raise self._packet
        return self._packet


def obs(key, period, value, known_at):
    from ats.data.sources.macro_series import metric_id

    return {"metric_id": metric_id(key), "period": period, "value": value, "known_at": known_at}


def packet(version="SP500:2026-10-09@x", date_="2026-10-09", age=8):
    return SimpleNamespace(report=SimpleNamespace(version_id=version, report_date=date_),
                           status=SimpleNamespace(age_days=age, state="platform", freshness="latest_released"))


def test_the_reader_collects_each_input_and_one_failure_does_not_stop_the_others(monkeypatch):
    monkeypatch.setattr(macro_inputs, "admitted_news", lambda key, **kw: [news_doc(key)])
    products = FakeProducts(rows=[obs("vix", "2026-10-15", 16.5, "2026-10-16T22:00:00+00:00")],
                            regional={"regional.tw_ic_exports.value": [
                                {"period": "2026-08", "known_at": "2026-09-26"}, {"period": "2026-09", "known_at": "2026-10-08"}]},
                            packet=RuntimeError("no packet"))
    state = macro_inputs.stored_input_state(products, as_of=NOW, news_days=21, themes=["inflation"])
    assert set(state["series"]["value"].series) == {"vix"} and state["series"]["error"] == ""
    assert [d.document_id for d in state["news"]["value"]["themes"]["inflation"]] == ["inflation-0"]
    regional = {p["id"]: p for p in state["regional"]["value"]}
    assert regional["tw_ic_exports"]["period"] == "2026-09" and regional["kr_semi_exports"]["period"] == ""
    assert state["factset"]["value"] is None and "no packet" in state["factset"]["error"]
    failing = macro_inputs.stored_input_state(FakeProducts(error=OSError("disk")), as_of=NOW, news_days=21, themes=[])
    assert "disk" in failing["series"]["error"] and failing["news"]["error"] == ""


# --- data vintage: unchanged inputs are reused, any changed input is not ---------------------------------------
def vintage(state):
    return macro_inputs.input_vintage_refs(state)


def test_the_vintage_is_stable_for_unchanged_inputs_and_moves_with_each_input():
    base = vintage(good_state())
    assert base == vintage(good_state())
    new_print = series_set(known="2026-10-17T00:30:00+00:00")
    assert vintage(good_state(series={"value": new_print, "error": ""})) != base
    one_more = {"days": 21, "themes": {t: [news_doc(t)] for t in THEMES} | {"growth": [news_doc("growth"), news_doc("growth", 1)]}}
    assert vintage(good_state(news={"value": one_more, "error": ""})) != base
    revised = {"days": 21, "themes": {t: [news_doc(t, version_id="v9")] for t in THEMES}}
    assert vintage(good_state(news={"value": revised, "error": ""})) != base
    state = good_state()
    state["regional"]["value"][0]["period"] = "2026-10"
    assert vintage(state) != base
    state = good_state()
    state["factset"]["value"]["version_id"] = "SP500:2026-10-16@y"
    assert vintage(state) != base


def test_an_unreadable_input_never_looks_unchanged():
    assert vintage(good_state(series={"value": None, "error": "boom"})) != vintage(good_state())
    assert [r for r in vintage(good_state(series={"value": None, "error": "boom"})) if r.startswith("macro-series")] == []


def test_default_data_vintage_for_macro_reads_stored_inputs_only_and_needs_no_network(monkeypatch):
    from ats.workflow import dispatcher

    products = SimpleNamespace(structured=SimpleNamespace(close=lambda: None), unstructured=object())
    monkeypatch.setattr("ats.data.products.get_platform_data_products", lambda readonly=False: products)
    monkeypatch.setattr("ats.data.macro.fetch_series", lambda *a, **k: pytest.fail("provider call"))
    monkeypatch.setattr("ats.data.websearch.search_news", lambda *a, **k: pytest.fail("provider call"), raising=False)
    seen = {}
    state = good_state()

    def fake_state(p, *, as_of=None, **kw):
        seen["as_of"] = as_of
        return state
    monkeypatch.setattr(macro_inputs, "stored_input_state", fake_state)
    task = SimpleNamespace(task_id="macro-review", scope=SimpleNamespace(kind="portfolio", id=""))
    plan = SimpleNamespace(as_of="2026-10-17T03:00:00Z", run_id="r")
    refs = dispatcher.default_data_vintage(plan, task, {})
    assert refs == macro_inputs.input_vintage_refs(state) and seen["as_of"] == NOW
    assert any(r.startswith("macro-series:25@") for r in refs) and any(r.startswith("macro-news:2@") for r in refs)
    state["series"] = {"value": None, "error": "boom"}                       # unreadable series => recompute
    assert dispatcher.default_data_vintage(plan, task, {}) == []


def _macro_envelope(vintage_refs, *, created):
    from ats.agent.task_projection import ProjectionScope, build_envelope
    from ats.workflow.evaluation_clock import now

    env = build_envelope(role="macro_review", scope=ProjectionScope(kind="portfolio"),
                         as_of=(now(UTC) - timedelta(minutes=5)).isoformat(timespec="seconds"), agent_run_id="A", input_refs=["config:macro.yaml@1"],
                         data_vintage_refs=vintage_refs,
                         payload={"regime": "risk_on", "summary": "s", "indicators": ["VIX=16.5"]})
    return env.model_copy(update={"created_at": created})


def test_a_published_macro_review_is_reused_while_the_inputs_are_unchanged_and_within_a_week():
    from ats.workflow.dispatcher import Dispatcher
    from ats.workflow.evaluation_clock import now

    refs = vintage(good_state())
    task = SimpleNamespace(task_id="macro-review", scope=SimpleNamespace(key="portfolio", kind="portfolio", id=""))

    class Memory:
        def __init__(self, env):
            self.env = env
            self.conn = SimpleNamespace(execute=lambda sql, args: SimpleNamespace(
                fetchone=lambda: {"status": "succeeded", "projection_refs_json": "[]"}))

        def task_projection_envelopes(self, **kw):
            return [self.env.model_dump(mode="json") | {"scope_kind": "portfolio", "scope_id": ""}]

    def select(env, wanted):
        stamp = now(UTC).isoformat(timespec="seconds")
        return Dispatcher()._select_reusable(Memory(env), task, ["config:macro.yaml@1"], wanted, stamp)[1]["decision"]

    fresh = now(UTC).isoformat(timespec="seconds")
    assert select(_macro_envelope(refs, created=fresh), refs) == "hit"
    assert select(_macro_envelope(refs, created=fresh), vintage(good_state(
        series={"value": series_set(known="2026-10-17T00:30:00+00:00"), "error": ""}))) == "recompute"
    old = (now(UTC) - timedelta(days=8)).isoformat(timespec="seconds")
    assert select(_macro_envelope(refs, created=old), refs) == "recompute"
    assert select(_macro_envelope(refs, created=fresh), []) == "recompute"      # no vintage => never reused


def test_macro_freshness_is_the_weekly_cadence():
    from ats.workflow.phase_e import FRESHNESS_SECONDS

    assert FRESHNESS_SECONDS["macro-review"] == 7 * 86_400


# --- the owner switch for macro-review ---------------------------------------------------------------------------
def test_macro_review_owner_switch_needs_the_macro_read_route_and_a_healthy_macro_check(tmp_path):
    from ats.workflow import consumer_cutover as cc
    from ats.workflow import cutover_wiring as cw
    from ats.workflow import schedule_runtime as rt
    from ats.workflow.isolation import isolated_run

    ok = {"status": "ok", "problems": [], "warnings": []}
    asked = []

    def healthy(consumer):
        asked.append(consumer)
        return ok

    with isolated_run("macro-owner-switch", root=tmp_path / "iso") as environment:
        path = environment.path_for("ATS_CUTOVER_DB")
        cw.bootstrap_wired(path=path)
        args = dict(to_owner="dispatcher", actor="op", reason="drill", approval="user approved", health_check=healthy)
        with pytest.raises(rt.ScheduleAuthorityError, match="macro research read is not on the target route"):
            rt.owner_switch("macro-review", **args)
        cc.switch("macro", actor="op", approval="chat", stop_conditions="health fails", health=ok, path=path,
                  observe_until=(datetime.now(UTC) + timedelta(hours=72)).isoformat())
        entry = rt.owner_switch("macro-review", **args)
        assert entry["to_owner"] == "dispatcher" and asked == ["macro"]
        assert json.loads(rt.owner_log("macro-review")[0]["health"])["consumer"] == "macro"
        back = rt.owner_switch("macro-review", to_owner="legacy", actor="op", reason="drill back")
        assert back["action"] == "rollback" and back["to_generation"] == entry["to_generation"] + 1
