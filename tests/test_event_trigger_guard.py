"""The Dispatcher's calendar path must honour the operator's paused event triggers
(`event_triggers` in config/workflow/phase_e_schedules.yaml)."""
from __future__ import annotations

import pytest

from ats.workflow import ownership

_NAMES = {"pead_event_triggers": "earnings", "macro_sector_event_triggers": "macro_sector"}


def _stages(monkeypatch, **switches):
    current = ownership.event_trigger_switches()
    current.update({_NAMES[key]: value for key, value in switches.items()})
    monkeypatch.setattr(ownership, "event_trigger_switches", lambda **kwargs: dict(current))


@pytest.mark.parametrize("workflow", ["macro-review", "sector-review", "layer-review"])
def test_macro_and_sector_workflows_follow_macro_sector_event_triggers(monkeypatch, workflow):
    _stages(monkeypatch, macro_sector_event_triggers=False, pead_event_triggers=True)
    assert ownership.event_trigger_enabled(workflow) is False
    _stages(monkeypatch, macro_sector_event_triggers=True)
    assert ownership.event_trigger_enabled(workflow) is True


@pytest.mark.parametrize("workflow", ["information-brief", "fundamental-routine", "fundamental-event"])
def test_pead_workflows_follow_pead_event_triggers(monkeypatch, workflow):
    _stages(monkeypatch, pead_event_triggers=False, macro_sector_event_triggers=True)
    assert ownership.event_trigger_enabled(workflow) is False
    _stages(monkeypatch, pead_event_triggers=True)
    assert ownership.event_trigger_enabled(workflow) is True


def test_technical_has_no_event_route_and_is_never_blocked(monkeypatch):
    _stages(monkeypatch, macro_sector_event_triggers=False, pead_event_triggers=False)
    assert ownership.event_trigger_enabled("technical-review") is True


def test_the_shipped_policy_keeps_macro_sector_events_paused_and_pead_events_on():
    assert ownership.event_trigger_switches() == {"earnings": True, "macro_sector": False}


def test_a_paused_trigger_family_never_reaches_run_owned_workflow(monkeypatch):
    from types import SimpleNamespace

    import ats.workflow.routing as routing

    route = SimpleNamespace(event_id="fomc:1", event_version="1", scope_resolver="portfolio", route_kind="fomc:release",
                            event_state="released", workflow_ids=["macro-review"])
    monkeypatch.setattr(routing, "resolve_event_route", lambda *a, **k: route)
    store = SimpleNamespace(latest_events=lambda limit=0: [{"event_id": "fomc:1", "event_version": "1", "quality_status": "ok"}])
    monkeypatch.setattr(ownership, "owner_mode", lambda *a, **k: "dispatcher")
    monkeypatch.setattr(ownership, "run_owned_workflow",
                        lambda *a, **k: pytest.fail("a paused event trigger must not start a workflow"))
    _stages(monkeypatch, macro_sector_event_triggers=False)
    out = ownership.dispatch_calendar_event({"event_id": "fomc:1", "payload": {}}, admitted_materials=[],
                                            calendar_store=store)
    assert out == [{"workflow_id": "macro-review", "owner": "dispatcher", "status": "event_trigger_paused"}]
