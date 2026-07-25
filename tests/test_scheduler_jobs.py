"""Job registration: the daily cycle plus one job per PEAD score window."""

from __future__ import annotations

import pytest

from ats.runtime import scheduler


class FakeScheduler:
    """Records add_job calls instead of running them."""

    def __init__(self, *a, **kw):
        self.init_kwargs = kw
        self.jobs: list[dict] = []
        self.started = False

    def add_job(self, fn, trigger, *, id, misfire_grace_time=None, **kw):
        self.jobs.append({"fn": fn, "trigger": trigger, "id": id,
                          "misfire_grace_time": misfire_grace_time})

    def start(self):
        self.started = True
        raise SystemExit          # don't block the test


@pytest.fixture
def registered(monkeypatch):
    import apscheduler.schedulers.blocking as blocking

    holder = {}

    def factory(*a, **kw):
        holder["sched"] = FakeScheduler(*a, **kw)
        return holder["sched"]

    monkeypatch.setattr(blocking, "BlockingScheduler", factory)
    scheduler.start(dry_run=True)
    return holder["sched"]


def test_registers_daily_cycle_and_both_score_windows(registered):
    ids = [j["id"] for j in registered.jobs]
    assert ids == ["daily_cycle", "pead_score_amc", "pead_score_bmo"]


def test_windows_fire_at_the_configured_times(registered):
    by_id = {j["id"]: j["trigger"] for j in registered.jobs}
    fields = {i: {f.name: str(f) for f in t.fields} for i, t in by_id.items()}

    assert (fields["pead_score_amc"]["hour"], fields["pead_score_amc"]["minute"]) == ("20", "0")
    assert (fields["pead_score_bmo"]["hour"], fields["pead_score_bmo"]["minute"]) == ("11", "0")
    for i in by_id:
        assert fields[i]["day_of_week"] == "mon-fri"


def test_each_window_job_is_bound_to_its_own_window(registered, monkeypatch):
    """A bare `lambda: f(name)` in the loop would give every job the LAST name —
    the classic late-binding trap, and it would silently run 'bmo' twice."""
    called: list[str] = []
    monkeypatch.setattr(scheduler, "pead_score_window",
                        lambda w, **kw: called.append(w))

    for job in registered.jobs:
        if job["id"].startswith("pead_score_"):
            job["fn"]()
    assert sorted(called) == ["amc", "bmo"]


def test_jobs_are_serialized_by_a_single_worker(registered):
    """The jobs share one sqlite connection, so they must not run concurrently."""
    executor = registered.init_kwargs.get("executors", {}).get("default")
    assert executor is not None
    assert executor.__class__.__name__ == "ThreadPoolExecutor"
    assert executor._pool._max_workers == 1


def test_window_flag_runs_one_window_and_exits(monkeypatch):
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(scheduler, "pead_score_window",
                        lambda w, **kw: calls.append((w, kw.get("dry_run"))))
    monkeypatch.setattr(scheduler, "_daily", lambda **kw: pytest.fail("should not run daily"))

    scheduler.start(dry_run=True, window="amc")
    assert calls == [("amc", True)]


def test_bad_window_time_is_skipped_not_fatal(monkeypatch):
    """A typo in score_windows must not take the whole scheduler down."""
    from ats import config

    real = config.load_pead_global

    def broken():
        cfg = dict(real())
        cfg["schedule"] = {**cfg["schedule"], "score_windows": {"amc": "oops", "bmo": "11:00"}}
        return cfg

    monkeypatch.setattr(config, "load_pead_global", broken)
    import apscheduler.schedulers.blocking as blocking

    holder = {}
    monkeypatch.setattr(blocking, "BlockingScheduler",
                        lambda *a, **kw: holder.setdefault("s", FakeScheduler(*a, **kw)))
    scheduler.start(dry_run=True)
    assert [j["id"] for j in holder["s"].jobs] == ["daily_cycle", "pead_score_bmo"]


# --------------------------------------------------------------------------- #
# Staged-rollout guard
# --------------------------------------------------------------------------- #
def test_live_daemon_still_gets_dry_run_windows(monkeypatch):
    """The resident daemon runs `ats schedule --live`. Until score_windows_live is
    turned on, the windows must force dry-run — otherwise registering them would
    immediately place market orders raised at 20:00 ET into the next open."""
    seen: list[bool] = []
    monkeypatch.setattr(scheduler, "is_trading_session", lambda *a, **k: True)
    monkeypatch.setattr(scheduler, "_score_plan", lambda *a, **k: (False, "no print"))

    from ats import config

    real = config.load_pead_global

    def cfg_with(live: bool):
        c = dict(real())
        c["schedule"] = {**c["schedule"], "score_windows_live": live}
        c["targets"] = []
        return c

    monkeypatch.setattr(config, "load_pead_global", lambda: cfg_with(False))
    monkeypatch.setattr(scheduler.log, "info",
                        lambda msg, *a: seen.append("forcing dry-run" in str(msg)))
    scheduler.pead_score_window("amc", dry_run=False)
    assert any(seen), "expected the dry-run override to be logged"


def test_windows_go_live_only_when_explicitly_enabled(monkeypatch):
    monkeypatch.setattr(scheduler, "is_trading_session", lambda *a, **k: True)
    from ats import config

    real = config.load_pead_global

    def cfg():
        c = dict(real())
        c["schedule"] = {**c["schedule"], "score_windows_live": True}
        c["targets"] = []
        return c

    monkeypatch.setattr(config, "load_pead_global", cfg)
    msgs: list[str] = []
    monkeypatch.setattr(scheduler.log, "info", lambda msg, *a: msgs.append(str(msg)))
    scheduler.pead_score_window("amc", dry_run=False)
    assert not any("forcing dry-run" in m for m in msgs)
