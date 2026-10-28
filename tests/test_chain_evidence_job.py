"""Chain evidence moved into the data refresh controller (2026-10-16).

The legacy PEAD score window used to be the only thing that extracted evidence; when it was
switched off nothing collected any. These tests pin: the per-entity job's outcomes, the
controller's per-entity expansion of `entities:`, and how derived-job packets are classified.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
import yaml


# --------------------------------------------------------------------------- #
# observe_entity
# --------------------------------------------------------------------------- #
class _Store:
    def __init__(self, extracted=()):
        self.extracted = set(extracted)

    def has_observations_for_document(self, doc_id):
        return doc_id in self.extracted


def _wire(monkeypatch, *, print_=None, confirmed=(True, "已公布"), text="body", note="",
          result=None, asset=None):
    from ats.agents.evidence import observer
    from ats.data import earnings_calendar
    from ats.data.pipelines.unstructured import documents
    from ats.data.products import earnings

    calls = {"fetch": 0, "observe": 0}
    monkeypatch.setattr(earnings_calendar, "last_print", lambda *a, **k: print_)
    monkeypatch.setattr(earnings, "confirm_reported", lambda *a, **k: confirmed)

    def fetch(*a, **k):
        calls["fetch"] += 1
        return text, "defeatbeta", note

    def observe(sym, doc_id, body, **k):
        calls["observe"] += 1
        return result or {"symbol": sym, "saved": 3, "new": 3, "failure": ""}

    monkeypatch.setattr(observer, "fetch_document", fetch)
    monkeypatch.setattr(observer, "observe_document", observe)
    monkeypatch.setattr(documents, "identify", lambda *a, **k: asset)
    return calls


def _print(day=date(2026, 10, 15)):
    return SimpleNamespace(date=day, reported=True)


def test_new_confirmed_print_is_extracted(monkeypatch):
    from ats.runtime.evidence_observe import observe_entity

    calls = _wire(monkeypatch, print_=_print())
    packet = observe_entity("TSM", store=_Store())
    assert packet["status"] == "succeeded" and packet["new"] == 3
    assert packet["document_id"] == "TSM:20261015"
    assert calls == {"fetch": 1, "observe": 1}


def test_no_print_in_window_is_a_quiet_no_change(monkeypatch):
    from ats.runtime.evidence_observe import observe_entity

    calls = _wire(monkeypatch, print_=None)
    packet = observe_entity("SKHY", store=_Store())
    assert (packet["status"], packet["reason"]) == ("no_change", "no_print_in_window")
    assert calls == {"fetch": 0, "observe": 0}


def test_unconfirmed_release_is_not_read(monkeypatch):
    # Without double confirmation last quarter's filing would be read as new.
    from ats.runtime.evidence_observe import observe_entity

    calls = _wire(monkeypatch, print_=_print(), confirmed=(False, "no 8-K yet"))
    packet = observe_entity("TSM", store=_Store())
    assert (packet["status"], packet["reason"]) == ("no_change", "release_not_confirmed")
    assert calls["fetch"] == 0


def test_a_document_already_extracted_is_never_fetched_or_sent_to_the_model_again(monkeypatch):
    from ats.runtime.evidence_observe import observe_entity

    calls = _wire(monkeypatch, print_=_print())
    packet = observe_entity("TSM", store=_Store(extracted={"TSM:20261015"}))
    assert (packet["status"], packet["reason"]) == ("no_change", "already_extracted")
    assert calls == {"fetch": 0, "observe": 0}


def test_same_body_under_another_id_is_not_extracted_twice(monkeypatch):
    from ats.runtime.evidence_observe import observe_entity

    calls = _wire(monkeypatch, print_=_print(), asset={"document_id": "asset:abc"})
    packet = observe_entity("TSM", store=_Store(extracted={"asset:abc"}))
    assert packet["reason"] == "already_extracted" and calls["observe"] == 0


def test_missing_verified_document_is_a_gap_not_a_failure(monkeypatch):
    from ats.runtime.evidence_observe import observe_entity

    calls = _wire(monkeypatch, print_=_print(), text="", note="报告期核对未通过")
    packet = observe_entity("TSM", store=_Store())
    assert (packet["status"], packet["reason"]) == ("no_change", "no_verified_document")
    assert packet["note"] == "报告期核对未通过" and calls["observe"] == 0


def test_extraction_failure_is_failed_so_the_next_window_retries(monkeypatch):
    from ats.runtime.evidence_observe import observe_entity

    _wire(monkeypatch, print_=_print(),
          result={"symbol": "TSM", "saved": 0, "new": 0, "failure": "truncated"})
    packet = observe_entity("TSM", store=_Store())
    assert (packet["status"], packet["reason"]) == ("failed", "extraction_failed")


def test_dry_run_neither_fetches_nor_calls_the_model(monkeypatch):
    from ats.runtime.evidence_observe import observe_entity

    calls = _wire(monkeypatch, print_=_print())
    packet = observe_entity("TSM", dry_run=True, store=_Store())
    assert packet["reason"] == "would_extract" and calls == {"fetch": 0, "observe": 0}


def test_calendar_outage_fails_instead_of_looking_like_no_print(monkeypatch):
    from ats.data import earnings_calendar
    from ats.runtime.evidence_observe import observe_entity

    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(earnings_calendar, "last_print", boom)
    assert observe_entity("TSM", store=_Store())["status"] == "failed"


# --------------------------------------------------------------------------- #
# Controller plan: per-entity expansion and derived jobs
# --------------------------------------------------------------------------- #
def _plan(tmp_path, jobs: dict, *, section="derived"):
    from ats.data.refresh import load_plan

    path = tmp_path / "schedules.yaml"
    path.write_text(yaml.safe_dump({
        "version": 1,
        "controller": {"owner": "launchd:com.ats.data-refresh", "ledger": str(tmp_path / "ledger.sqlite"),
                       "default_enabled": False},
        section: {"default_timezone": "UTC", "jobs": jobs}}))
    return load_plan(path)


def _derived_job(**over):
    body = {"enabled": True, "source": "chain_evidence_observer", "entities": ["TSM", "MU"],
            "schedule": "daily", "at": "22:00", "timezone": "UTC",
            "command": "ats data evidence-observe --entity {entity} --lookback-days 7",
            "freshness_slo_days": 3, "request_budget": {"requests_per_run": 8}}
    body.update(over)
    return body


def test_entities_expand_to_one_job_per_name(tmp_path):
    plan = _plan(tmp_path, {"chain": _derived_job()})
    assert [j.id for j in plan.jobs] == ["chain.TSM", "chain.MU"]
    assert plan.jobs[0].command == ("ats", "data", "evidence-observe", "--entity", "TSM",
                                    "--lookback-days", "7")
    assert {j.entity for j in plan.jobs} == {"TSM", "MU"}


def test_a_derived_job_without_a_request_budget_is_refused(tmp_path):
    with pytest.raises(ValueError, match="request_budget"):
        _plan(tmp_path, {"chain": _derived_job(request_budget={})})


def test_derived_jobs_may_only_run_the_allowed_actions(tmp_path):
    with pytest.raises(ValueError, match="allowed ats data action"):
        _plan(tmp_path, {"chain": _derived_job(command="ats data purge-source --source x")})


def test_the_shipped_schedule_loads_with_a_declared_budget_per_entity():
    from ats.data.refresh import load_plan

    jobs = [j for j in load_plan().jobs if j.id.startswith("chain_evidence_daily.")]
    assert jobs and all(j.domain == "derived" and j.request_budget >= 1 for j in jobs)


# --------------------------------------------------------------------------- #
# Controller tick: derived packets are classified from their own result
# --------------------------------------------------------------------------- #
class _FakeQueue:
    packet: dict = {}
    code = 0

    def __init__(self, *a, **k):
        pass

    def enqueue(self, **kwargs):
        return "task-1", True

    def run_one(self, **kwargs):
        return {"stdout": self.packet, "reported_status": self.packet.get("status", ""),
                "exit_code": self.code, "result": {"stdout_sha256": "x"}}

    def record_lineage(self, *a, **k):
        pass


def _tick(tmp_path, monkeypatch, packet, code=0):
    from ats.data import persistent_queue, refresh

    _FakeQueue.packet, _FakeQueue.code = packet, code
    monkeypatch.setattr(persistent_queue, "PersistentIngestionQueue", _FakeQueue)
    plan = _plan(tmp_path, {"chain": _derived_job(entities=["TSM"])})
    now = datetime(2026, 10, 16, 23, 0, tzinfo=timezone.utc)
    return refresh.run_tick(plan, now=now, data_db=tmp_path / "missing.sqlite")


def test_a_derived_no_change_keeps_the_reason(tmp_path, monkeypatch):
    out = _tick(tmp_path, monkeypatch, {"status": "no_change", "reason": "already_extracted"})
    assert (out[0]["status"], out[0]["reason"]) == ("no_change", "already_extracted")


def test_a_derived_success_is_recorded_as_succeeded(tmp_path, monkeypatch):
    out = _tick(tmp_path, monkeypatch, {"status": "succeeded", "new": 5})
    assert out[0]["status"] == "succeeded"


def test_a_derived_failure_is_failed_and_carries_the_reason(tmp_path, monkeypatch):
    out = _tick(tmp_path, monkeypatch, {"status": "failed", "reason": "extraction_failed"}, code=1)
    assert (out[0]["status"], out[0]["reason"]) == ("failed", "extraction_failed")


def test_a_disabled_derived_job_does_not_run(tmp_path, monkeypatch):
    from ats.data import persistent_queue, refresh

    monkeypatch.setattr(persistent_queue, "PersistentIngestionQueue", _FakeQueue)
    plan = _plan(tmp_path, {"chain": _derived_job(enabled=False, entities=["TSM"])})
    out = refresh.run_tick(plan, now=datetime(2026, 10, 16, 23, 0, tzinfo=timezone.utc),
                           data_db=tmp_path / "missing.sqlite")
    assert out[0]["status"] == "disabled"


# --------------------------------------------------------------------------- #
# Multi-entity structured jobs (company financials)
# --------------------------------------------------------------------------- #
def test_entity_sources_pick_the_provider_per_name(tmp_path):
    body = {"enabled": True, "source": "defeatbeta_stock_statement", "entities": ["GOOG", "MU"],
            "entity_sources": {"MU": "yfinance_financials"}, "datasets": ["company_financials"],
            "schedule": "weekly", "weekday": "saturday", "at": "06:30", "timezone": "UTC",
            "command": "ats data ingest --source {source} --dataset company_financials --entity {entity}",
            "freshness_slo_days": 10}
    plan = _plan(tmp_path, {"fin": body}, section="structured")
    by_id = {j.id: j for j in plan.jobs}
    assert by_id["fin.GOOG"].sources == ("defeatbeta_stock_statement",)
    assert by_id["fin.MU"].sources == ("yfinance_financials",)
    assert by_id["fin.MU"].command[3:5] == ("--source", "yfinance_financials")
    assert by_id["fin.MU"].command[-1] == "MU"


def test_one_failing_name_does_not_block_the_others(tmp_path, monkeypatch):
    from ats.data import persistent_queue, refresh

    class Queue(_FakeQueue):
        def run_one(self, **kwargs):
            self.__class__.calls = getattr(self.__class__, "calls", 0) + 1
            if self.__class__.calls == 1:
                return {"stdout": {"status": "failed", "reason": "extraction_failed"},
                        "reported_status": "failed", "exit_code": 1, "result": {}}
            return {"stdout": {"status": "no_change", "reason": "already_extracted"},
                    "reported_status": "no_change", "exit_code": 0, "result": {}}

    monkeypatch.setattr(persistent_queue, "PersistentIngestionQueue", Queue)
    plan = _plan(tmp_path, {"chain": _derived_job(entities=["TSM", "MU"])})
    out = refresh.run_tick(plan, now=datetime(2026, 10, 16, 23, 0, tzinfo=timezone.utc),
                           data_db=tmp_path / "missing.sqlite")
    assert [(r["job"], r["status"]) for r in out] == [("chain.TSM", "failed"), ("chain.MU", "no_change")]


def test_the_shipped_financials_job_expands_per_entity_with_a_declared_budget():
    from ats.data.refresh import load_plan

    jobs = [j for j in load_plan().jobs if j.id.startswith("company_financials_weekly.")]
    assert jobs and all(j.entity and j.command[-1] == j.entity for j in jobs)
    assert all(j.enabled for j in jobs)            # enabled by the user on 2026-10-17
