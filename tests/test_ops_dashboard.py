"""ops-observability-dashboard: queries over throwaway stores, pages, local-only routes, catalog coverage."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ats.config import REPO_ROOT
from ats.runtime.dashboard import pages, queries

NOW = datetime(2026, 11, 19, 3, 0, tzinfo=timezone.utc)          # Wed 11-18 22:00 New York


def _db(path: Path, ddl: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(ddl)
    return conn


@pytest.fixture
def var(tmp_path, monkeypatch) -> Path:
    v = tmp_path / "var"
    v.mkdir()
    monkeypatch.setenv("ATS_DASHBOARD_VAR", str(v))
    return v


def _paths(var: Path, workflow_config: Path | None = None) -> queries.DashboardPaths:
    p = queries.DashboardPaths.from_env()
    if workflow_config is not None:
        p = queries.DashboardPaths(p.ats, p.data, p.refresh, p.dispatch, p.schedules, workflow_config)
    return p


# --------------------------------------------------------------------------- #
# lights
# --------------------------------------------------------------------------- #
def test_light_thresholds_follow_slo():
    assert queries.light_for(True, 4, NOW - timedelta(days=1), NOW) == "green"
    assert queries.light_for(True, 4, NOW - timedelta(days=3), NOW) == "yellow"
    assert queries.light_for(True, 4, NOW - timedelta(days=5), NOW) == "red"
    assert queries.light_for(True, 4, None, NOW) == "red"
    assert queries.light_for(False, 4, None, NOW) == "grey"
    assert queries.worst(["green", "yellow", "grey"]) == "yellow"


# --------------------------------------------------------------------------- #
# data sources
# --------------------------------------------------------------------------- #
def test_source_rows_three_clocks_and_no_change_counts_as_healthy(var):
    ref = _db(var / "data_refresh.sqlite", """
        CREATE TABLE refresh_runs (trigger_key TEXT, job_id TEXT, window_date TEXT, status TEXT, reason_code TEXT,
          claimed_at TEXT, started_at TEXT, completed_at TEXT, attempts INTEGER, exit_code INTEGER);
        CREATE TABLE refresh_attempts (trigger_key TEXT, attempt_no INTEGER, started_at TEXT, completed_at TEXT,
          status TEXT, exit_code INTEGER, stdout_sha256 TEXT, reason_code TEXT);""")
    rows = [
        # SEC daily: last new data 40 days ago, checked every day since -> green ("between prints is normal")
        ("company_financials_sec_daily.NVDA", "2026-10-10", "succeeded", "", "2026-10-10T08:00:00+00:00"),
        ("company_financials_sec_daily.NVDA", "2026-11-18", "no_change", "", "2026-11-18T08:00:00+00:00"),
        # IBKR news: healthy 11-16, then two failures -> 2.2 days on a 3-day SLO = yellow, 2 consecutive failures
        ("ibkr_news_daily", "2026-11-16", "succeeded", "partial_budget_deferred", "2026-11-16T22:25:00+00:00"),
        ("ibkr_news_daily", "2026-11-17", "failed", "article_source_partial", "2026-11-17T23:46:00+00:00"),
        ("ibkr_news_daily", "2026-11-18", "failed", "article_source_partial", "2026-11-18T23:12:00+00:00"),
    ]
    ref.executemany("INSERT INTO refresh_runs (trigger_key, job_id, window_date, status, reason_code, completed_at) "
                    "VALUES (?,?,?,?,?,?)", [(f"k{i}", *r) for i, r in enumerate(rows)])
    ref.commit()
    data = _db(var / "data.sqlite", """
        CREATE TABLE data_documents (document_id TEXT, source TEXT, published_at TEXT, fetched_at TEXT);
        CREATE TABLE structured_ingestion_runs (source_id TEXT, dataset_id TEXT, completed_at TEXT, accepted INTEGER);""")
    data.executemany("INSERT INTO data_documents VALUES (?,?,?,?)", [
        ("a", "ibkr_news", "2026-11-16", "2026-11-16T22:24:57+00:00"),
        ("b", "ibkr_news", "", "2026-11-17T07:00:00+08:00"),          # +08:00 == 11-16 23:00 UTC, still older
    ])
    data.execute("INSERT INTO structured_ingestion_runs VALUES ('sec_companyfacts','company_financials',"
                 "'2026-11-16T09:21:18+00:00', 3)")
    data.commit()
    by_id = {r.job_id: r for r in queries.source_rows(_paths(var), NOW)}
    sec = by_id["company_financials_sec_daily.NVDA"]
    assert sec.light == "green" and sec.last_status == "no_change"
    assert sec.last_new_at.date().isoformat() == "2026-10-10"
    assert (sec.data_time.kind, sec.data_time.at.date().isoformat()) == ("入库", "2026-11-16")
    news = by_id["ibkr_news_daily"]
    assert news.light == "yellow" and news.consecutive_failures == 2 and news.last_reason == "article_source_partial"
    assert (news.data_time.kind, news.data_time.at.astimezone(timezone.utc).date().isoformat()) == ("采集", "2026-11-16")
    assert news.history[-1] == ("2026-11-18", "failed")
    # a job with no runs in the window and no healthy run at all is red, never silently green
    assert by_id["tw_ic_exports_monthly"].light == "red"
    groups = {g.group: g for g in queries.source_groups(list(by_id.values()))}
    assert groups["company_financials_sec_daily"].expanded


# --------------------------------------------------------------------------- #
# workflows
# --------------------------------------------------------------------------- #
def _workflow_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "wf"
    cfg.mkdir()
    (cfg / "research_schedules.yaml").write_text(
        "version: '1'\nworkflows:\n  technical-review:\n    enabled: true\n"
        "    cron: {day_of_week: mon-fri, hour: 8, minute: 30, timezone: America/New_York}\n", encoding="utf-8")
    (cfg / "trade_schedules.yaml").write_text("version: '1'\nworkflows: {}\n", encoding="utf-8")
    return cfg


def test_grid_marks_missed_paused_and_holiday(var, tmp_path):
    ats = _db(var / "ats.sqlite", """
        CREATE TABLE workflow_runs (run_id TEXT, request_json TEXT, status TEXT, started_at TEXT, created_at TEXT,
          ended_at TEXT, result_json TEXT);
        CREATE TABLE decision_cycles (cycle_id TEXT, status TEXT, created_at TEXT, research_snapshot TEXT);""")
    req = json.dumps({"requested_tasks": ["technical-review"]})
    ats.executemany("INSERT INTO workflow_runs (run_id, request_json, status, started_at) VALUES (?,?,?,?)", [
        ("r1", req, "incomplete", "2026-11-18T13:30:00+00:00"),
        ("r2", req, "complete", "2026-11-18T13:45:00+00:00"),
        ("r3", req, "complete", "2026-11-12T13:30:00+00:00"),
    ])
    ats.commit()
    disp = _db(var / "phase_f_dispatch.sqlite", """
        CREATE TABLE schedule_runtime_claims (workflow TEXT, status TEXT, identity TEXT, updated_at TEXT);
        CREATE TABLE schedule_owner_log (seq INTEGER, workflow TEXT, action TEXT, at TEXT);""")
    disp.executemany("INSERT INTO schedule_owner_log VALUES (?,?,?,?)", [
        (1, "technical-review", "rollback", "2026-11-16T00:00:00+00:00"),
        (2, "technical-review", "switch", "2026-11-17T00:00:00+00:00")])
    disp.commit()
    paths = _paths(var, _workflow_config(tmp_path))
    days, rows = queries.workflow_grid(paths, NOW, days=14)
    cells = {c.day.isoformat(): c for c in rows[0].cells}
    assert days[-1].isoformat() == "2026-11-18"
    assert (cells["2026-11-18"].state, cells["2026-11-18"].count) == ("incomplete", 2)   # the worse state wins
    assert cells["2026-11-12"].state == "complete"
    assert cells["2026-11-17"].state == "missed"       # scheduled weekday, trading day, no run
    assert cells["2026-11-16"].state == "paused"       # inside a pause window
    assert cells["2026-11-15"].state == "none"         # Sunday: not scheduled
    assert cells["2026-11-11"].state == "missed"       # Veterans Day: NYSE is open
    html = pages.workflows_page(days, rows, {"workflows": {}}, [], NOW)
    assert "缺" in html and "/dashboard/workflows/run/r3" in html            # one run: straight to it
    assert "/dashboard/workflows/technical-review/2026-11-18" in html          # two runs: the day's list
    runs = queries.runs_on_day(paths, "technical-review",
                               datetime(2026, 11, 18).date(), NOW)
    assert [r["run_id"] for r in runs] == ["r1", "r2"]


# --------------------------------------------------------------------------- #
# lineage agrees with the Chief report
# --------------------------------------------------------------------------- #
def test_lineage_uses_the_chief_report_resolver(var):
    from ats.agents.provenance import cite_projection

    pid, fake = "a" * 32, "f" * 32
    item = {"projection_id": pid, "agent_role": "technical_review", "scope_id": "LITE",
            "as_of": "2026-11-18T13:30:00+00:00", "task_id": "technical_review"}
    ats = _db(var / "ats.sqlite", """
        CREATE TABLE decision_cycles (cycle_id TEXT, status TEXT, created_at TEXT, research_snapshot TEXT);
        CREATE TABLE decision_revisions (cycle_id TEXT, revision_no INTEGER, orders_json TEXT, rationale TEXT,
          created_at TEXT);
        CREATE TABLE task_projection_envelopes (projection_id TEXT, agent_role TEXT, scope_id TEXT, as_of TEXT,
          input_refs TEXT, data_vintage_refs TEXT);""")
    ats.execute("INSERT INTO decision_cycles VALUES (?,?,?,?)", ("chief-x", "manual_review", "2026-11-18T14:45:00+00:00",
                json.dumps({"built_at": "2026-11-18T14:45:00+00:00", "items": [item]})))
    ats.execute("INSERT INTO decision_revisions VALUES (?,?,?,?,?)", (
        "chief-x", 1, json.dumps([{"action": "sell", "symbol": "LITE", "qty": 10, "references": [pid, fake]}]),
        json.dumps({"summary": "s", "reference_audit": {"unknown": [fake]}}), "2026-11-18T14:49:00+00:00"))
    ats.execute("INSERT INTO task_projection_envelopes VALUES (?,?,?,?,?,?)", (
        pid, "technical_review", "LITE", item["as_of"], "[]", json.dumps(["market-session:LITE:2026-11-18:before-close"])))
    ats.commit()
    d = queries.lineage(_paths(var), "chief-x")
    order = d["decisions"][0]
    assert order.label == "sell LITE 10 股"
    assert order.children[0].label == cite_projection(item, datetime(2026, 11, 18, 14, 45, tzinfo=timezone.utc))
    assert order.children[0].children[0].label == "行情 LITE · 2026-11-18 收盘前"
    assert order.children[1].unknown
    html = pages.lineage_page(d, NOW)
    assert "1 个引用不在本轮快照中" in html


# --------------------------------------------------------------------------- #
# routes: every page renders on empty stores; tunnelled requests are refused
# --------------------------------------------------------------------------- #
def _client():
    from fastapi.testclient import TestClient

    from ats.runtime.server import build_app

    return TestClient(build_app(), base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))


@pytest.mark.parametrize("path", ["/dashboard", "/dashboard/sources", "/dashboard/sources?only=issues",
                                  "/dashboard/sources/macro_series_daily", "/dashboard/workflows",
                                  "/dashboard/lineage"])
def test_pages_render_on_empty_stores(var, path):
    r = _client().get(path)
    assert r.status_code == 200, r.text[:500]
    assert "运维看板" in r.text and 'http-equiv="refresh" content="60"' in r.text


def test_unknown_ids_are_404(var):
    c = _client()
    assert c.get("/dashboard/workflows/technical-review/2026-11-18").status_code == 200
    assert c.get("/dashboard/workflows/run/nope").status_code == 404
    assert c.get("/dashboard/lineage/nope").status_code == 404


def test_dashboard_refuses_tunnelled_and_remote_requests(var):
    c = _client()
    assert c.get("/dashboard", headers={"cf-ray": "abc"}).status_code == 403
    assert c.get("/dashboard", headers={"x-forwarded-for": "1.2.3.4"}).status_code == 403
    assert c.get("/dashboard", headers={"host": "abc.trycloudflare.com"}).status_code == 403
    from fastapi.testclient import TestClient

    from ats.runtime.server import build_app

    remote = TestClient(build_app(), base_url="http://127.0.0.1:8000", client=("10.0.0.5", 50000))
    assert remote.get("/dashboard").status_code == 403
    # the approval webhook is untouched by the guard
    assert c.get("/health", headers={"cf-ray": "abc"}).status_code == 200


# --------------------------------------------------------------------------- #
# Chinese notes
# --------------------------------------------------------------------------- #
def test_every_enabled_job_and_workflow_has_a_note():
    import yaml

    from ats.data.refresh import load_plan
    from ats.workflow.planning import TASK_ROLE

    catalog = queries.load_catalog()
    groups = {j.id[: -len(j.entity) - 1] if j.entity else j.id for j in load_plan().jobs if j.enabled}
    missing = sorted(g for g in groups if g not in catalog["jobs"] or catalog["jobs"][g].missing)
    assert not missing, f"config/ops/catalog_zh.yaml lacks notes for jobs: {missing}"
    workflows = set(TASK_ROLE)
    for name in ("research_schedules.yaml", "trade_schedules.yaml"):
        raw = yaml.safe_load((REPO_ROOT / "config" / "workflow" / name).read_text(encoding="utf-8"))
        workflows |= {w for w, b in (raw.get("workflows") or {}).items() if b.get("enabled")}
    missing = sorted(w for w in workflows if w not in catalog["workflows"] or catalog["workflows"][w].missing)
    assert not missing, f"config/ops/catalog_zh.yaml lacks notes for workflows: {missing}"


def test_note_renders_as_click_to_open_and_missing_is_flagged():
    n = queries.Note(label="台湾集成电路出口", what="HS8542", source="台湾财政部", used_by="层分析", if_broken="缺证人")
    html = pages.note(n)
    assert "<details" in html and "<b>是什么：</b>HS8542" in html and "<b>坏了影响：</b>缺证人" in html
    assert "缺说明" in pages.note(None) and "缺说明" in pages.note(queries.Note())
