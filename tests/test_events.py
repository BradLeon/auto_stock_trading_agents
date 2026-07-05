"""Event calendar — loader validation + scheduler event triggers (hermetic)."""

from datetime import date

import pytest

from ats.runtime import scheduler


def test_load_events_missing_file(monkeypatch, tmp_path):
    monkeypatch.setenv("ATS_CONFIG_DIR", str(tmp_path))
    from ats.config import load_events

    assert load_events() == []


def test_load_events_parses_and_validates(monkeypatch, tmp_path):
    monkeypatch.setenv("ATS_CONFIG_DIR", str(tmp_path))
    (tmp_path / "events.yaml").write_text(
        "events:\n"
        "  - {date: 2026-07-29, kind: fomc, label: FOMC, triggers: [macro]}\n"
        "  - {date: 2026-09-09, kind: industry_conf, label: ECOC, "
        "triggers: ['sector:ai_hardware', 'pead:COHR']}\n", encoding="utf-8")
    from ats.config import load_events

    events = load_events()
    assert len(events) == 2
    assert events[0].kind == "fomc" and events[0].date == date(2026, 7, 29)
    assert events[1].triggers == ["sector:ai_hardware", "pead:COHR"]


def test_load_events_rejects_bad_kind(monkeypatch, tmp_path):
    monkeypatch.setenv("ATS_CONFIG_DIR", str(tmp_path))
    (tmp_path / "events.yaml").write_text(
        "events:\n  - {date: 2026-07-29, kind: alien_invasion, label: X, triggers: [macro]}\n",
        encoding="utf-8")
    from ats.config import load_events

    with pytest.raises(Exception):
        load_events()


def test_event_triggers_fire_on_matching_date(monkeypatch, tmp_path):
    monkeypatch.setenv("ATS_CONFIG_DIR", str(tmp_path))
    (tmp_path / "pead.yaml").write_text("targets: []\n", encoding="utf-8")
    (tmp_path / "events.yaml").write_text(
        "events:\n"
        "  - {date: 2026-07-29, kind: fomc, label: FOMC, triggers: [macro]}\n"
        "  - {date: 2026-07-29, kind: industry_conf, label: ECOC, "
        "triggers: ['sector:ai_hardware', 'pead:COHR']}\n"
        "  - {date: 2026-08-01, kind: cpi, label: CPI, triggers: [macro]}\n", encoding="utf-8")

    fired = []
    monkeypatch.setattr("ats.runtime.cli.run_macro_review",
                        lambda name, **k: fired.append(f"macro:{name}"))
    monkeypatch.setattr("ats.runtime.cli.run_sector_review",
                        lambda name, **k: fired.append(f"sector:{name}"))
    monkeypatch.setattr("ats.runtime.cli.run_pead_monitor",
                        lambda sym, **k: fired.append(f"pead:{sym}"))
    monkeypatch.setattr(scheduler, "_today", lambda: date(2026, 7, 29))

    labels = scheduler._event_triggers()
    assert fired == ["macro:macro", "sector:ai_hardware", "pead:COHR"]   # 8/1 CPI not fired
    assert len(labels) == 3


def test_event_triggers_noop_on_other_days(monkeypatch, tmp_path):
    monkeypatch.setenv("ATS_CONFIG_DIR", str(tmp_path))
    (tmp_path / "events.yaml").write_text(
        "events:\n  - {date: 2026-07-29, kind: fomc, label: FOMC, triggers: [macro]}\n",
        encoding="utf-8")
    monkeypatch.setattr(scheduler, "_today", lambda: date(2026, 7, 30))
    assert scheduler._event_triggers() == []
