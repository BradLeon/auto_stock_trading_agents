"""Event calendar — loader validation (hermetic)."""

from datetime import date

import pytest



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
