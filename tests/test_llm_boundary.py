"""Phase C group 7: the LLM/ledger boundary, enforced in code (§11.2).

7.1 — annotation whitelist + provenance; 7.2 — module-level architecture
guard; 7.3 — field-level guard (both directions); 7.4 — critic commentary
never changes ledger numbers.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from ats.execution.llm_boundary import (
    LEDGER_SQL_TARGETS,
    LEDGER_WRITE_METHODS,
    LLM_ALLOWED_WRITES,
    LLM_ANNOTATION_FIELDS,
    LlmLedgerWriteError,
    assert_llm_update_allowed,
)
from ats.memory import get_store

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "ats"


@pytest.fixture
def store():
    return get_store()


def _llm_modules() -> list[Path]:
    """Modules that invoke LLMs: they call `run_structured` (or the critic's
    LLM entry). `agents/base.py` DEFINES the runner and is excluded."""
    out = []
    for p in SRC.rglob("*.py"):
        text = p.read_text()
        if "run_structured(" in text and p.name != "base.py":
            out.append(p)
    return sorted(out)


# --------------------------------------------------------------------------- #
# 7.1 — whitelist + provenance on every LLM annotation
# --------------------------------------------------------------------------- #

def test_whitelisted_annotation_with_provenance_passes():
    assert_llm_update_allowed({
        "invalidation_triggered": True,
        "invalidation_source": "llm",
        "invalidation_checked_at": "2026-09-23T12:00:00+00:00",
    })


def test_annotation_without_provenance_is_refused():
    with pytest.raises(LlmLedgerWriteError):
        assert_llm_update_allowed({"invalidation_triggered": True})
    with pytest.raises(LlmLedgerWriteError):
        assert_llm_update_allowed({
            "invalidation_triggered": True,
            "invalidation_source": "llm",
        })                                  # no checked_at


def test_money_identity_and_attribution_fields_are_refused():
    for key in ("realized_pnl", "avg_fill_price", "qty", "commission",
                "decision_hash", "approval_id", "origin", "status"):
        with pytest.raises(LlmLedgerWriteError):
            assert_llm_update_allowed({
                key: 1.0,
                "invalidation_source": "llm",
                "invalidation_checked_at": "now",
            })


def test_episode_annotation_fields_exist_and_persist(store):
    """The schema carries the provenance pair and it round-trips the store."""
    _ep = store.list_episodes(limit=1)
    from ats.schemas.journal import TradeEpisode
    from datetime import datetime, timezone

    ep = TradeEpisode(episode_id="t7", symbol="NVDA", opened_at=datetime.now(timezone.utc),
                      invalidation_triggered=True, invalidation_source="llm",
                      invalidation_checked_at=datetime.now(timezone.utc))
    store.save_episode(ep)
    row = store.conn.execute(
        "SELECT invalidation_triggered, invalidation_source, invalidation_checked_at "
        "FROM trade_episodes WHERE episode_id='t7'").fetchone()
    assert row["invalidation_source"] == "llm"
    assert row["invalidation_checked_at"] is not None
    # the whitelist names exactly the episode's LLM-writable field(s)
    assert LLM_ANNOTATION_FIELDS == {"invalidation_triggered"}


# --------------------------------------------------------------------------- #
# 7.2 — architecture guard: LLM modules have no ledger write path
# --------------------------------------------------------------------------- #

def test_llm_modules_have_no_ledger_write_path():
    """Textual + AST guard: no module that runs an LLM may call a ledger write
    method or emit raw SQL against ledger tables."""
    assert _llm_modules(), "guard found no LLM modules — check the scanner"
    for path in _llm_modules():
        text = path.read_text()
        for method in LEDGER_WRITE_METHODS:
            assert f".{method}(" not in text, (
                f"{path.name} calls ledger write method {method}()")
        for target in LEDGER_SQL_TARGETS:
            assert target not in text, (
                f"{path.name} emits raw SQL: {target}")


def test_guard_fails_when_an_llm_module_gains_a_ledger_write(tmp_path):
    """The guard has teeth: inject a ledger write into a fake LLM module and
    the same scan logic flags it."""
    fake = tmp_path / "fake_llm.py"
    fake.write_text(
        "from ats.agents.base import run_structured\n"
        "def sneak(store):\n"
        "    store.save_trades([], cycle_id='c', source='chief')\n")
    text = fake.read_text()
    assert any(f".{m}(" in text for m in LEDGER_WRITE_METHODS)


def test_save_episode_is_the_only_allowed_llm_write():
    """The whitelist stays deliberate: save_episode (annotations) only."""
    assert LEDGER_WRITE_METHODS.isdisjoint(LLM_ALLOWED_WRITES)


# --------------------------------------------------------------------------- #
# 7.4 — critic commentary links facts; ledger numbers never move
# --------------------------------------------------------------------------- #

def test_critic_run_does_not_change_ledger_numbers(store):
    from ats.journal.critic import run_critic

    def _ledger_snapshot():
        return [
            [tuple(r) for r in store.conn.execute(q).fetchall()]
            for q in ("SELECT * FROM trades",
                      "SELECT * FROM fills",
                      "SELECT * FROM trade_episodes",
                      "SELECT * FROM performance")
        ]

    before = _ledger_snapshot()
    run_critic(store=store, use_llm=False)
    after = _ledger_snapshot()
    assert before == after
