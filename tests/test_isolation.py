"""Phase F 1.2 — the isolated run environment.

Isolation is only worth something if it cannot be half-installed. These tests
pin the two properties that make it structural rather than procedural: every
persistence surface is redirected (so an isolated run has no path to production
data), and the broker write prohibition is armed (so redirecting ledgers does
not leave a live broker reachable).
"""

import os
import re
from pathlib import Path

import pytest

from ats.config import REPO_ROOT
from ats.execution import broker_write_guard as guard
from ats.workflow import isolation


@pytest.fixture(autouse=True)
def _clean_guard():
    guard.reset_for_tests()
    yield
    guard.reset_for_tests()


def test_every_persistence_surface_is_redirected(tmp_path):
    """A surface missing from the table is a surface the run can still write to."""
    with isolation.isolated_run("iso-surfaces", root=tmp_path) as env:
        for var in isolation.PERSISTENCE_ENV_VARS:
            assert os.environ[var].startswith(str(tmp_path)), var
            assert Path(os.environ[var]).resolve() != Path(
                REPO_ROOT / "var" / "ats.sqlite").resolve()
        assert env.path_for("ATS_DB_PATH").name == "memory.sqlite"
        assert env.path_for("ATS_DATA_DB_PATH").name == "data.sqlite"


def test_broker_writes_are_prohibited_inside_the_run(tmp_path):
    """Redirecting ledgers does not stop a real order — the broker is not a DB."""
    with isolation.isolated_run("iso-broker", root=tmp_path):
        assert guard.is_prohibited() is True
        with pytest.raises(guard.BrokerWriteProhibited):
            guard.check_broker_write(operation="place_orders", caller="test")


def test_isolation_is_only_reported_when_both_halves_hold(tmp_path):
    """Paths redirected but writes allowed is the dangerous half-state: a run
    that believes it is a shadow while the broker is live."""
    with isolation.isolated_run("iso-both", root=tmp_path):
        assert isolation.isolation_active() is True

    # Same paths, prohibition removed -> no longer isolated.
    with isolation.isolated_run("iso-paths-only", root=tmp_path):
        guard._STATE.mode = "permitted"
        assert isolation.isolation_active() is False

    # Prohibition armed but paths untouched -> also not isolated.
    guard.prohibit_broker_writes()
    assert isolation.isolation_active() is False


def test_workflow_memory_reads_the_isolated_database(tmp_path):
    """The redirection must reach the cached connection, not just the env var.

    Both stores cache a connection per process; a redirection that leaves an
    open handle pointing at production would make every other assertion theatre.
    """
    from ats.memory import get_store

    with isolation.isolated_run("iso-memory", root=tmp_path):
        store = get_store()
        assert str(Path(store.path).resolve()).startswith(str(tmp_path.resolve()))


def test_data_layer_reads_the_isolated_database(tmp_path):
    from ats.data.runtime.repository import platform_data_db_path

    with isolation.isolated_run("iso-data", root=tmp_path):
        assert str(platform_data_db_path().resolve()).startswith(str(tmp_path.resolve()))


def test_an_isolated_run_cannot_write_production_trades(tmp_path):
    """The concrete claim: a trade logged in isolation is not in production."""
    from ats.memory import get_store
    from ats.schemas.memory import TradeLogEntry
    from datetime import datetime, timezone

    production = REPO_ROOT / "var" / "ats.sqlite"
    before = production.stat().st_size if production.exists() else None

    with isolation.isolated_run("iso-trades", root=tmp_path):
        get_store().save_trades(
            [TradeLogEntry(order_id="iso-1", cycle_id="iso-cycle", symbol="AMD",
                           action="buy", qty=1, status="filled",
                           submitted_at=datetime.now(timezone.utc))],
            cycle_id="iso-cycle", source="isolated", context="{}")

    after = production.stat().st_size if production.exists() else None
    assert before == after, "an isolated run grew the production trades database"


def test_no_production_approval_conclusion_is_produced(tmp_path):
    """A shadow run must not create approval records in production.

    An approval is the input to a real order, so an isolated run that wrote one
    would be manufacturing authorisation for a trade it may not place.
    """
    from ats.memory import get_store

    with isolation.isolated_run("iso-approval", root=tmp_path):
        approvals = get_store().conn.execute(
            "SELECT COUNT(*) FROM boss_approvals").fetchone()[0]
        revisions = get_store().conn.execute(
            "SELECT COUNT(*) FROM decision_revisions").fetchone()[0]

    assert approvals == 0
    assert revisions == 0


def test_environment_and_caches_are_restored_on_exit(tmp_path):
    """An isolation leak that outlives its run writes to the wrong database
    later, far from anything that would explain it."""
    original = {var: os.environ.get(var) for var in isolation.PERSISTENCE_ENV_VARS}
    was_prohibited = guard.is_prohibited()

    with isolation.isolated_run("iso-restore", root=tmp_path):
        pass

    for var, value in original.items():
        assert os.environ.get(var) == value, var
    assert os.environ.get("ATS_RUN_MODE") is None
    assert guard.is_prohibited() is was_prohibited


def test_environment_is_restored_even_when_the_run_raises(tmp_path):
    original = os.environ.get("ATS_DB_PATH")

    with pytest.raises(RuntimeError, match="boom"):
        with isolation.isolated_run("iso-boom", root=tmp_path):
            raise RuntimeError("boom")

    assert os.environ.get("ATS_DB_PATH") == original


def test_the_run_refuses_to_start_without_the_capability(tmp_path, monkeypatch):
    """A run that cannot prove writes are prohibited must not proceed."""
    monkeypatch.setattr(guard, "assert_broker_writes_prohibited",
                        lambda **_kwargs: (_ for _ in ()).throw(
                            guard.BrokerWriteProhibited(
                                "capability missing", reason_code="guard_missing",
                                refusal_id="")))
    with pytest.raises(guard.BrokerWriteProhibited):
        with isolation.isolated_run("iso-guard", root=tmp_path):
            pytest.fail("the run body must not execute")


def test_isolation_covers_every_known_persistence_env():
    """New persistence env vars must be declared here or isolation is a fiction.

    Derived from the source rather than a hand list, so adding
    `os.environ.get("ATS_SOMETHING_DB")` without declaring it fails this test.
    A var that looks like a path but is not a surface must be declared in
    NON_ISOLATED_ENV with a reason — silence is not an acceptable answer,
    because the reader cannot then tell an oversight from a decision.
    """
    pattern = re.compile(r"""os\.environ\.get\(\s*["'](ATS_[A-Z_]+)["']""")
    declared = set(isolation.PERSISTENCE_ENV_VARS)
    exempted = set(isolation.NON_ISOLATED_ENV)
    found: dict[str, str] = {}
    for path in (REPO_ROOT / "src" / "ats").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            name = match.group(1)
            if not (name.endswith(("_PATH", "_DB", "_ROOT", "_DIR")) or "_DB_" in name):
                continue
            found.setdefault(name, str(path.relative_to(REPO_ROOT)))

    undecided = {name: path for name, path in found.items()
                 if name not in declared and name not in exempted}
    assert not undecided, (
        "persistence env vars neither isolated nor explicitly exempted: "
        + ", ".join(f"{n} ({p})" for n, p in sorted(undecided.items())))
    for name in exempted:
        if name in isolation.NON_ISOLATED_ENV and not isolation.NON_ISOLATED_ENV[name]:
            raise AssertionError(f"{name} is exempted without a reason")


def test_two_isolated_runs_do_not_share_state(tmp_path):
    """Runs must not be able to see each other's ledgers."""
    from datetime import datetime, timezone

    from ats.memory import get_store
    from ats.schemas.memory import TradeLogEntry

    with isolation.isolated_run("iso-a", root=tmp_path / "a"):
        get_store().save_trades(
            [TradeLogEntry(order_id="a-1", cycle_id="c", symbol="AMD", action="buy",
                           qty=1, status="filled",
                           submitted_at=datetime.now(timezone.utc))],
            cycle_id="c", source="a", context="{}")
        first = str(get_store().path)

    with isolation.isolated_run("iso-b", root=tmp_path / "b"):
        second = str(get_store().path)
        assert get_store().conn.execute(
            "SELECT COUNT(*) FROM trades").fetchone()[0] == 0

    assert first != second
