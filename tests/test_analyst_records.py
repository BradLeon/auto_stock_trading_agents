"""The three analysts' working records are read by their owners only (switch-trade r5 tasks 6.3 / 6.5).

`sector_reviews`, `macro_reviews` and `technical_reviews` hold each analyst's full conclusion; the Chief reads the
slimmer projection published in the same run. What retired (`legacy_read_models`) is anyone else reading these
tables as a compatibility read model. These tests keep it that way.
"""

from __future__ import annotations

from pathlib import Path

from ats.workflow import retirement_clearance as rc


def _module(root: Path, relative: str, body: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_no_module_reads_an_analyst_record_it_does_not_own() -> None:
    bad = rc.analyst_record_violations()
    assert not bad, "\n".join(f"{r.location} ({r.function}) {r.table}: {why}" for r, why in bad)


def test_the_owners_really_do_read_their_records() -> None:
    """The allow-list is not vacuous: each analyst still reads its own table (the reason the tables stay)."""
    tables_read = {(r.table, r.location.split("/")[3])
                   for base in ("sector", "macro", "technical")
                   for path in sorted(Path(f"src/ats/agents/{base}").glob("*.py"))
                   for r in rc.scan_analyst_record_reads(path.as_posix())}
    assert ("sector_reviews", "sector") in tables_read
    assert ("macro_reviews", "macro") in tables_read
    assert ("technical_reviews", "technical") in tables_read


def test_a_non_owner_read_is_reported_whatever_way_it_is_written(tmp_path: Path) -> None:
    _module(tmp_path, "src/ats/agents/chief/assemble.py",
            "def f(store):\n    return store.latest_macro_review('macro')\n")
    _module(tmp_path, "src/ats/risk/assess.py",
            "Q = 'SELECT payload FROM technical_reviews ORDER BY created_at DESC'\n")
    _module(tmp_path, "src/ats/channel/context.py",
            "from ..agents.sector import context\n")
    _module(tmp_path, "src/ats/agents/sector/rotation.py",          # sector reading macro: cross-analyst
            "def g(store):\n    return store.latest_macro_review('macro')\n")
    _module(tmp_path, "src/ats/runtime/cli.py",                     # a CLI command of another analyst
            "def macro_show(store):\n    return store.latest_sector_review('ai')\n"
            "def technical_show(store):\n    return store.latest_technical_review('technical')\n")
    _module(tmp_path, "src/ats/agents/macro/review.py",             # the owner: allowed
            "def h(store):\n    return store.latest_macro_review_before('macro', None)\n")
    found = {(r.location.rsplit(":", 1)[0], r.table) for r, _ in rc.analyst_record_violations(tmp_path)}
    assert found == {
        ("src/ats/agents/chief/assemble.py", "macro_reviews"),
        ("src/ats/risk/assess.py", "technical_reviews"),
        ("src/ats/channel/context.py", "sector_reviews"),
        ("src/ats/agents/sector/rotation.py", "macro_reviews"),
        ("src/ats/runtime/cli.py", "sector_reviews"),
    }


def test_legacy_read_models_consumers_judge_zero_only_on_owner_reads(monkeypatch) -> None:
    verdicts = {v.consumer: v for v in rc.verify_consumer_zero(
        "legacy_read_models", ["runtime.cli", "channel.context"], scan=lambda role: None)}
    assert verdicts["channel.context"].verdict == rc.ZERO
    assert verdicts["runtime.cli"].verdict == rc.ZERO
    assert "macro_show:macro_reviews" in verdicts["runtime.cli"].evidence["owner_reads"]

    real = rc.scan_analyst_record_reads
    monkeypatch.setattr(rc, "scan_analyst_record_reads", lambda relative, base=None: [
        *real(relative, base),
        rc.AnalystRecordRead("macro_reviews", "store_call", f"{relative}:1", "approval_show")])
    verdicts = {v.consumer: v for v in rc.verify_consumer_zero(
        "legacy_read_models", ["runtime.cli", "channel.context"], scan=lambda role: None)}
    assert verdicts["runtime.cli"].verdict == rc.NOT_ZERO
    assert verdicts["channel.context"].verdict == rc.NOT_ZERO


def test_the_three_records_are_live_and_have_no_tombstone() -> None:
    from ats.workflow import legacy_retirement as mod

    registry = mod.load_registry()
    for table in ("sector_reviews", "macro_reviews", "technical_reviews"):
        assert table in registry.active(), table
        assert registry.tombstone(table) is None, table


def test_legacy_read_models_is_retired_and_its_gate_says_so() -> None:
    from ats.workflow import legacy_retirement as mod

    registry = mod.load_registry()
    stone = registry.tombstone("legacy_read_models")      # retired on 2026-11-07 (r5 tasks 6.3 / 6.5)
    assert stone is not None and stone.status == "retired" and not stone.consumers
    assert registry.read_gate("legacy_read_models") == ("retired", mod.REASON_RETIRED)
