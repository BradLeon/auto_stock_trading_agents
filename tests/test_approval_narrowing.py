"""Phase B task group 6: approval narrowing + persistent callback idempotency.

6.1 the verdict is approve/reject of the EXACT revision; modifications become
    rejection notes and never reach the executor.
6.2 a review/approval binds one revision hash — any substantive change means a
    NEW revision, and the old review/approval no longer binds.
6.4 callbacks on terminal cycles / superseded revisions / dead reviews are
    rejected; duplicates dedup against the persistent record.
6.5 the full approval row lands in `boss_approvals` bound to the revision.
"""

from datetime import datetime, timezone

from ats.decision.repository import (DecisionAuditRepository,
                                     approval_idempotency_key)
from ats.graph.chief import narrow_approval
from ats.memory.store import TradingMemory
from ats.schemas.decision import BossApproval, TradeDecision

NOW = datetime.now(timezone.utc)


def _repo(tmp_path) -> DecisionAuditRepository:
    return DecisionAuditRepository(TradingMemory(tmp_path / "g6.sqlite"))


def _order(notional: float) -> dict:
    return {"symbol": "NVDA", "action": "buy", "notional_usd": notional}


def _approved_revision(repo, cycle_id: str, notional: float = 1_000.0):
    """Cycle with one revision + a binding approved review."""
    repo.create_cycle(cycle_id=cycle_id, trigger_source="manual")
    rev = repo.append_revision(cycle_id=cycle_id, orders=[_order(notional)],
                               rationale="v1")
    repo.record_review(
        review_id=f"{cycle_id}:r{rev['revision_no']}:review",
        cycle_id=cycle_id, revision_no=rev["revision_no"],
        decision_hash=rev["decision_hash"], ruleset_version="ruleset-1",
        portfolio_snapshot_id="pf:1", market_as_of=NOW.isoformat(),
        verdict="approved")
    return rev


# --- 6.1 approval input narrowing ---------------------------------------------- #

def test_clean_verdict_passes_through_unchanged():
    a = BossApproval(status="approved", reviewer="boss", channel="feishu-card")
    out = narrow_approval(a)
    assert out.status == "approved" and out.comment == ""


def test_modified_with_overrides_becomes_rejection_with_note():
    d = TradeDecision(symbol="NVDA", action="buy", notional_usd=10_000, rationale="r")
    a = BossApproval(status="modified", overrides=[d], reviewer="boss")
    out = narrow_approval(a)
    assert out.status == "rejected"
    assert "修改" in out.comment


def test_submitted_quantities_become_rejection_executor_gets_nothing():
    """The card approves a $25k order; the Boss 'approves' but edits the size —
    recorded as a rejection whose note keeps the modification for audit, while
    the trader (which reads only the review-approved revision) places nothing."""
    d = TradeDecision(symbol="NVDA", action="buy", notional_usd=40_000, rationale="r")
    a = BossApproval(status="modified", overrides=[d], reviewer="boss")
    out = narrow_approval(a)
    assert out.status == "rejected"
    assert "修改" in out.comment
    assert out.overrides[0].notional_usd == 40_000   # kept as the recorded note,
    # never as executable state: trader uses approved_decisions, not overrides.


def test_symbol_filter_is_not_partial_approval():
    a = BossApproval(status="approved", approved_symbols=["NVDA"], reviewer="boss")
    out = narrow_approval(a)
    assert out.status == "rejected"
    assert "筛选" in out.comment


def test_rejection_with_comment_is_preserved():
    a = BossApproval(status="rejected", comment="too concentrated", reviewer="boss")
    assert narrow_approval(a) is a


# --- 6.2 field change invalidates review + approval ------------------------------ #

def test_field_change_invalidates_review_and_approval(tmp_path):
    repo = _repo(tmp_path)
    rev1 = _approved_revision(repo, "c1")
    repo.record_approval(
        approval_id="ap1", cycle_id="c1", revision_no=rev1["revision_no"],
        decision_hash=rev1["decision_hash"], decision="approved", reviewer="boss",
        idempotency_key=approval_idempotency_key(
            "c1", rev1["revision_no"], rev1["decision_hash"], "test"))

    # Chief revises: a substantive change is a NEW revision with a new hash.
    rev2 = repo.append_revision(cycle_id="c1", orders=[_order(500.0)],
                                rationale="v2")
    assert rev2["revision_no"] == 2
    assert rev2["decision_hash"] != rev1["decision_hash"]

    assert repo.effective_review("c1", rev2["revision_no"],
                                 rev2["decision_hash"]) is None
    assert repo.effective_approval("c1", rev2["revision_no"],
                                   rev2["decision_hash"]) is None
    # ... while the originals still bind the revision they were made on.
    assert repo.effective_review("c1", rev1["revision_no"],
                                 rev1["decision_hash"]) is not None
    assert repo.effective_approval("c1", rev1["revision_no"],
                                   rev1["decision_hash"]) is not None


# --- 6.4 callback admission guard ------------------------------------------------ #

def test_guard_allows_legacy_cycle_without_audit_row(tmp_path):
    repo = _repo(tmp_path)
    ok, reason, deduped = repo.validate_callback("no-such-cycle")
    assert ok and not deduped


def test_guard_dedupes_terminal_cycle(tmp_path):
    repo = _repo(tmp_path)
    rev = _approved_revision(repo, "c-term")
    repo.record_approval(
        approval_id="ap", cycle_id="c-term", revision_no=rev["revision_no"],
        decision_hash=rev["decision_hash"], decision="approved", reviewer="boss",
        idempotency_key=approval_idempotency_key(
            "c-term", rev["revision_no"], rev["decision_hash"], "feishu-card"))
    repo.transition("c-term", to_status="pending_approval", actor="risk_gate",
                    revision_no=rev["revision_no"])
    repo.transition("c-term", to_status="executed", actor="trader")
    ok, reason, deduped = repo.validate_callback("c-term", channel="feishu-card")
    assert not ok and deduped and "terminal" in reason


def test_guard_rejects_callback_on_superseded_revision(tmp_path):
    repo = _repo(tmp_path)
    rev1 = _approved_revision(repo, "c-old")
    repo.append_revision(cycle_id="c-old", orders=[_order(500.0)], rationale="v2")
    ok, reason, deduped = repo.validate_callback(
        "c-old", decision_hash=rev1["decision_hash"], channel="feishu-card")
    assert not ok and not deduped and "superseded" in reason


def test_guard_rejects_callback_when_review_no_longer_binds(tmp_path):
    """rev1 reviewed+approved; chief revises to rev2 (no review yet) — a callback
    without a stale binding still hits the dead-review check."""
    repo = _repo(tmp_path)
    _approved_revision(repo, "c-dead")
    repo.append_revision(cycle_id="c-dead", orders=[_order(500.0)], rationale="v2")
    ok, reason, deduped = repo.validate_callback("c-dead", channel="feishu-card")
    assert not ok and not deduped and "review" in reason


def test_guard_allows_pending_current_revision(tmp_path):
    repo = _repo(tmp_path)
    rev = _approved_revision(repo, "c-ok")
    repo.transition("c-ok", to_status="pending_approval", actor="risk_gate",
                    revision_no=rev["revision_no"])
    ok, reason, deduped = repo.validate_callback("c-ok", channel="feishu-card")
    assert ok


def test_guard_dedupes_recorded_approval_on_current_revision(tmp_path):
    repo = _repo(tmp_path)
    rev = _approved_revision(repo, "c-dup")
    repo.record_approval(
        approval_id="ap", cycle_id="c-dup", revision_no=rev["revision_no"],
        decision_hash=rev["decision_hash"], decision="approved", reviewer="boss",
        idempotency_key=approval_idempotency_key(
            "c-dup", rev["revision_no"], rev["decision_hash"], "feishu-card"))
    ok, reason, deduped = repo.validate_callback("c-dup", channel="feishu-card")
    assert not ok and deduped and "already handled" in reason


# --- 6.5 full approval record via the graph ---------------------------------------- #

def test_graph_run_records_full_approval_row(broker, approve_all, monkeypatch):
    """End-to-end: approve through the graph and read the approval back from
    `boss_approvals`, bound to the executed revision."""
    from ats.memory import get_store
    from ats.runtime.cli import run_decision_graph
    from ats.graph.chief_state import ChiefDecisionState
    from ats.decision.repository import DecisionAuditRepository

    state = ChiefDecisionState(
        cycle_id="chief-g6-appr", as_of=NOW, source="chief", decide=False,
        dry_run=False,
        seed_decisions=[TradeDecision(symbol="NVDA", action="buy", qty=1,
                                      rationale="r")])
    run_decision_graph(state, channel=approve_all)

    repo = DecisionAuditRepository(get_store())
    chain = repo.read_chain("chief-g6-appr")
    assert len(chain["approvals"]) == 1
    row = chain["approvals"][0]
    assert row["decision"] == "approved"
    assert row["revision_no"] == chain["revisions"][-1]["revision_no"]
    assert row["decision_hash"] == chain["revisions"][-1]["decision_hash"]
    assert row["idempotency_key"]                    # persistent dedup key present
    assert row["approval_id"] == "chief-g6-appr:r1:approval:r1"
