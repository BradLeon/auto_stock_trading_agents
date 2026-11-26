"""Collection resilience (2026-11-26): same-day catch-up, IBKR no-callback breaker with an actionable alert,
Yahoo fallback without batch human review, and the Chief told which inputs are stale."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import yaml

from ats.data import persistent_queue, refresh

NOW = datetime(2026, 11, 25, 22, 0, tzinfo=timezone.utc)        # 17:00 New York, after the 16:30 IBKR job
JOB_ID = "ibkr_news_daily"
FAILED = {"status": "partial", "deferred": 0, "discovery": {"status": "succeeded", "failed_slices": ["NVDA"]}}


class _Queue:
    packets: list[dict] = []
    refs: list[str] = []

    def __init__(self, *a, **k):
        pass

    def enqueue(self, **kwargs):
        return f"task-{kwargs['trigger_ref']}", True

    def run_one(self, **kwargs):
        packet = type(self).packets.pop(0)
        return {"stdout": packet, "reported_status": packet["status"], "exit_code": 0,
                "result": {"stdout_sha256": "x"}}

    def record_lineage(self, *a, **k):
        pass


@pytest.fixture
def tick(tmp_path, monkeypatch):
    _Queue.packets, _Queue.refs = [], []
    monkeypatch.setattr(persistent_queue, "PersistentIngestionQueue", _Queue)
    monkeypatch.setattr(refresh, "_unstructured_references",
                        lambda db, packet, job: {"runs": ["run-1"], "checks": [], "raw": [], "admission": [],
                                                 "published": list(_Queue.refs)})
    plan = refresh.load_plan()
    plan = replace(plan, jobs=tuple(j for j in plan.jobs if j.id == JOB_ID), ledger_path=tmp_path / "l.sqlite")
    clock = {"now": NOW}
    monkeypatch.setattr(refresh, "_utc_now", lambda: clock["now"])

    def run(packet=None, at=NOW):
        clock["now"] = at
        _Queue.packets = [packet] if packet else []
        return refresh.run_tick(plan, now=at, data_db=tmp_path / "missing.sqlite")[0]

    return run


# --------------------------------------------------------------------------- 2a catch-up
def test_catch_up_is_configured_for_source_jobs_only():
    jobs = {j.id: j for j in refresh.load_plan().jobs}
    assert (jobs[JOB_ID].catch_up_attempts, jobs[JOB_ID].catch_up_interval_seconds) == (6, 3600)
    assert jobs["us_census_btos_weekly"].catch_up_attempts == 6                  # structured
    assert all(j.catch_up_attempts == 0 for j in jobs.values() if j.domain == "derived")   # model calls


def test_a_failed_window_is_retried_hourly_after_the_ordinary_retries(tick):
    assert tick(FAILED)["attempt"] == 1
    assert tick(FAILED, at=NOW + timedelta(minutes=3))["attempt"] == 2           # backoff 120 s
    assert tick(at=NOW + timedelta(minutes=30))["status"] == "already_claimed"   # catch-up waits an hour
    t = NOW + timedelta(minutes=3)
    for attempt in range(3, 9):                                                  # six catch-up runs
        t += timedelta(minutes=61)
        assert tick(FAILED, at=t)["attempt"] == attempt
    assert tick(at=t + timedelta(minutes=61))["status"] == "already_claimed"     # then it stops


def test_catch_up_success_ends_the_retries(tick):
    tick(FAILED)
    tick(FAILED, at=NOW + timedelta(minutes=3))
    _Queue.refs = ["doc-1"]
    ok = tick({"status": "succeeded", "discovery": {"status": "succeeded"}}, at=NOW + timedelta(minutes=65))
    assert (ok["status"], ok["attempt"]) == ("succeeded", 3)
    assert tick(at=NOW + timedelta(minutes=130))["status"] == "already_claimed"


# --------------------------------------------------------------------------- 1b / 2b status
def test_review_held_items_do_not_hide_published_articles(tick):
    _Queue.refs = ["doc-1"]
    row = tick({"status": "pending_human_review", "discovery": {"status": "succeeded"}})
    assert (row["status"], row["reason"]) == ("succeeded", "partial_review_held_back")


def test_review_held_items_with_nothing_published_stay_discovery_only(tick):
    row = tick({"status": "pending_human_review", "discovery": {"status": "succeeded"}})
    assert (row["status"], row["reason"]) == ("discovery_only", "human_review_required")


def test_no_callback_breaker_fails_the_run_even_when_yahoo_published(tick):
    _Queue.refs = ["yahoo-doc"]
    row = tick({"status": "partial", "discovery": {"status": "succeeded", "failed_slices": ["NVDA"],
                                                   "breaker": "historical_news_no_callback"}})
    assert (row["status"], row["reason"]) == ("failed", "ibkr_historical_news_no_callback")


# --------------------------------------------------------------------------- 2b breaker in discovery
class _FakeIB:
    pass


def _discover(monkeypatch, answers):
    from ats.data.articles import ibkr_news

    calls = []
    monkeypatch.setattr(ibkr_news, "_client", lambda: _FakeIB())
    monkeypatch.setattr(ibkr_news, "_news_providers", lambda ib, **k: (["DJ-N"], {"attempts": 1}))

    def qualify(ib, contract):
        contract.conId = 1

    def historical(ib, con_id, codes, start, end, limit, diagnostic=None):
        calls.append(start)
        return answers.pop(0) if answers else None

    monkeypatch.setattr(ibkr_news, "_qualify", qualify)
    monkeypatch.setattr(ibkr_news, "_historical_news", historical)
    refs, meta = ibkr_news.discover_with_status(symbols=["NVDA", "AMD", "TSM"], pages=3, lookback_days=9, now=NOW)
    return calls, meta


def test_discovery_stops_after_three_requests_without_any_callback(monkeypatch):
    calls, meta = _discover(monkeypatch, [])
    assert len(calls) == 3 and meta["breaker"] == "historical_news_no_callback"
    assert {s["symbol"] for s in meta["failed_slices"]} == {"NVDA", "AMD", "TSM"}   # Yahoo covers them all


def test_one_callback_keeps_the_sweep_going(monkeypatch):
    calls, meta = _discover(monkeypatch, [[]])          # the first slice answers (zero headlines)
    assert len(calls) == 9 and "breaker" not in meta


def test_the_alert_says_what_to_do():
    from ats.runtime import collection_alerts as ca

    row = SimpleNamespace(job_id=JOB_ID, light="yellow", light_reason="最近一次运行失败", last_status="failed",
                          last_reason="ibkr_historical_news_no_callback", last_at=None)
    assert "请完整退出 TWS（Cmd+Q）后重开并登录" in ca._line(row)


# --------------------------------------------------------------------------- 1a Yahoo
def test_yahoo_fallback_no_longer_waits_for_batch_review():
    from ats.config import REPO_ROOT

    raw = yaml.safe_load((REPO_ROOT / "config" / "data" / "unstructured.yaml").read_text(encoding="utf-8"))
    sources = raw.get("sources") or raw
    policy = sources["yfinance_live_news"]["policy"]
    assert policy["release_requires_human_title_url_review"] is False
    assert policy["require_entity_verified"] is True and policy["minimum_body_chars"] == 800   # per-article gates


# --------------------------------------------------------------------------- 2c Chief told what is stale
def _row(job_id, light, reason="", at=None):
    return SimpleNamespace(job_id=job_id, enabled=True, light=light, light_reason=reason,
                           data_time=SimpleNamespace(kind="入库", at=at, period=""))


def test_stale_inputs_are_listed_worst_first_and_green_is_left_out():
    from ats.runtime import input_freshness as f

    rows = [_row("ibkr_news_daily", "yellow", "最近一次运行失败", NOW - timedelta(days=2)),
            _row("company_financials_weekly.SKHY", "red", "从未正常运行"),
            _row("fred_daily", "green")]
    stale = f.stale_sources(now=NOW, rows=rows)
    assert [s.job_id for s in stale] == ["company_financials_weekly.SKHY", "ibkr_news_daily"]
    text = f.block(stale, now=NOW)
    assert "[异常] company_financials_weekly.SKHY" in text and "没有可用的数据时间" in text
    assert "最新数据 入库 11-23（2.0 天前）" in text

    sent = []
    assert f.notify("chief-x", stale, now=NOW, push=lambda t, b: sent.append((t, b)))
    assert sent[0][0] == "首席决策输入滞后：2 个采集点（1 个异常）"


def test_the_report_opens_with_the_stale_inputs():
    from ats.agents.chief.decide import ChiefResult
    from ats.agents.chief.report import render

    context = "Chief decision context.\n\n## 数据新鲜度（采集异常）\n以下 1 个…\n- [注意] ibkr_news_daily：x\n\n## 组合账本状态\n…"
    text = render(ChiefResult(cycle_id="c", as_of=NOW, summary="s", context_text=context))
    assert text.index("输入数据滞后") < text.index("（无行动")
    assert "> - [注意] ibkr_news_daily：x" in text
