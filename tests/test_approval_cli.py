"""tasks 2.5 — `ats approval` and the Boss's three answers, driven through the real decision graph.

The decision run leaves its cycle at the approval interrupt in a persistent checkpoint (the pending
channel; the isolated run owns the checkpoint file), and each answer resumes it from a separate call,
as the command line does.
"""

from __future__ import annotations

import pytest
from test_phase_d_e2e import (NOW, _an_active_trade_route, _chief_llm, _seed, _state,  # noqa: F401
                              e2e_env)

from ats.channel.pending_channel import PendingApprovalChannel
from ats.decision.repository import DecisionAuditRepository
from ats.runtime import approval_cli
from ats.runtime.cli import run_decision_graph

pytestmark = pytest.mark.usefixtures("open_decision_read_gate")

CYCLE = "phase-d-e2e"


@pytest.fixture
def sent(monkeypatch):
    """Capture every one-way message; a messenger outage is simulated by `fail`."""
    box = {"messages": [], "fail": False}

    class _Messenger:
        def push(self, msg):
            if box["fail"]:
                raise RuntimeError("messenger down")
            box["messages"].append(msg)

    monkeypatch.setattr("ats.channel.get_channel", lambda kind=None: _Messenger())
    return box


@pytest.fixture
def waiting(e2e_env, broker, monkeypatch, tmp_path, sent):
    """A cycle left waiting for the Boss, with its checkpoint on disk."""
    _seed(e2e_env)
    _chief_llm(monkeypatch)
    result = run_decision_graph(_state(), channel=PendingApprovalChannel())
    assert "__interrupt__" in result
    repo = DecisionAuditRepository(e2e_env)
    return repo, repo.latest_revision(CYCLE)


def _hash(rev, n=12):
    return rev["decision_hash"][:n]


def test_pending_lists_a_risk_approved_cycle_with_its_hash_prefix(waiting, capsys):
    repo, rev = waiting
    assert [r["cycle_id"] for r in approval_cli.pending_cycles(repo)] == [CYCLE]
    approval_cli.pending()
    out = capsys.readouterr().out
    assert CYCLE in out and _hash(rev) in out


def test_pending_never_lists_a_cycle_that_did_not_pass_risk(e2e_env, broker):
    repo = DecisionAuditRepository(e2e_env)
    repo.create_cycle(cycle_id="blocked-1", trigger_source="chief")
    rev = repo.append_revision(cycle_id="blocked-1", rationale="r", orders=[
        {"symbol": "COHR", "action": "buy", "notional_usd": 5000, "rationale": "r"}])
    repo.record_review(review_id="blocked-1:r1:review:r1", cycle_id="blocked-1", revision_no=1,
                       decision_hash=rev["decision_hash"], ruleset_version="v", portfolio_snapshot_id="pf:x",
                       market_as_of="x", verdict="rejected")
    repo.transition("blocked-1", to_status="pending_approval", actor="test")
    assert approval_cli.pending_cycles(repo) == []


def test_a_short_or_wrong_hash_is_refused_and_names_the_current_one(waiting, capsys):
    repo, rev = waiting
    assert approval_cli.approve(CYCLE, _hash(rev, 8)) == 1
    assert "至少 12 位" in capsys.readouterr().out
    assert approval_cli.approve(CYCLE, "f" * 12) == 1
    assert _hash(rev) in capsys.readouterr().out
    assert repo.get_cycle(CYCLE)["status"] == "pending_approval"


def test_a_superseded_cycle_cannot_be_approved(waiting, capsys):
    repo, rev = waiting
    from ats.decision.snapshot import supersede_cycle

    supersede_cycle(repo, CYCLE, reason="newer cycle", actor="test")
    assert approval_cli.approve(CYCLE, _hash(rev)) == 1
    assert "取代" in capsys.readouterr().out


def test_approving_ends_in_a_terminal_status_and_a_second_approve_returns_the_first_result(waiting, capsys):
    repo, rev = waiting
    assert approval_cli.approve(CYCLE, _hash(rev)) == 0
    status = repo.get_cycle(CYCLE)["status"]
    assert status in {"executed", "approved_not_executed"}
    capsys.readouterr()
    assert approval_cli.approve(CYCLE, _hash(rev)) == 0
    assert "已批准过" in capsys.readouterr().out
    assert repo.conn.execute("SELECT COUNT(*) FROM boss_approvals WHERE cycle_id = ?", (CYCLE,)).fetchone()[0] == 1


def test_a_finished_cycle_refuses_a_different_answer(waiting, capsys):
    repo, rev = waiting
    approval_cli.approve(CYCLE, _hash(rev))
    capsys.readouterr()
    assert approval_cli.reject(CYCLE, _hash(rev), note="late") == 1
    assert "已终结" in capsys.readouterr().out


def test_reject_ends_the_cycle_and_keeps_the_note(waiting):
    repo, rev = waiting
    assert approval_cli.reject(CYCLE, _hash(rev), note="仓位偏大，今天不做") == 0
    assert repo.get_cycle(CYCLE)["status"] == "approval_rejected"
    row = repo.conn.execute("SELECT * FROM boss_approvals WHERE cycle_id = ?", (CYCLE,)).fetchone()
    assert row["decision"] == "rejected" and row["comment"] == "仓位偏大，今天不做"


def test_approval_refreshes_the_snapshot_and_needs_no_second_human_approval(waiting, capsys):
    """The snapshot the review rests on is older than the 60 seconds by the time a person answers."""
    repo, rev = waiting
    assert approval_cli.approve(CYCLE, _hash(rev)) == 0
    out = capsys.readouterr().out
    assert "重审通过" in out
    reviews = repo.conn.execute("SELECT review_id FROM decision_risk_reviews WHERE cycle_id = ? ORDER BY created_at",
                                (CYCLE,)).fetchall()
    assert len(reviews) == 2 and reviews[-1]["review_id"].endswith(":r2")      # same revision, new review
    assert len(repo.list_revisions(CYCLE)) == 1                                # no new revision
    approvals = repo.conn.execute("SELECT * FROM boss_approvals WHERE cycle_id = ?", (CYCLE,)).fetchall()
    assert len(approvals) == 1 and approvals[0]["approval_id"].endswith(":approval:r2")
    assert repo.get_cycle(CYCLE)["status"] in {"executed", "approved_not_executed"}


def test_a_different_re_review_refuses_the_approval_and_leaves_the_cycle_waiting(waiting, monkeypatch, capsys):
    repo, rev = waiting
    from ats.schemas.risk import DecisionRiskReview, OrderRiskVerdict

    def blocked(decisions, pf, **kw):
        return DecisionRiskReview(
            verdict="rejected", notes=["单票上限现在被突破"],
            order_verdicts=[OrderRiskVerdict(symbol=d.symbol, action=d.action, verdict="rejected",
                                             reasons=["limit"]) for d in decisions])

    monkeypatch.setattr("ats.risk.checks.review_revision", blocked)
    assert approval_cli.approve(CYCLE, _hash(rev)) == 1
    out = capsys.readouterr().out
    assert "结论不同" in out and "revise" in out and "reject" in out
    assert repo.get_cycle(CYCLE)["status"] == "pending_approval"
    assert repo.conn.execute("SELECT COUNT(*) FROM boss_approvals WHERE cycle_id = ?", (CYCLE,)).fetchone()[0] == 0


def test_revise_makes_a_new_revision_that_is_re_risked_and_comes_back_for_approval(waiting, monkeypatch, sent, capsys):
    repo, rev = waiting
    seen = {}

    def revised(context_text, *, prior_summary, prior_decisions, note, cycle_id, as_of, use_llm=True):
        from ats.agents.chief.decide import ChiefResult
        from ats.schemas.decision import TradeDecision

        seen["note"] = note
        return ChiefResult(cycle_id=cycle_id, as_of=as_of, summary="按意见减半",
                           decisions=[TradeDecision(symbol="COHR", action="buy", notional_usd=5_000,
                                                    conviction=0.7, rationale="按 Boss 意见减半")])

    monkeypatch.setattr("ats.agents.chief.decide.revise_with_note", revised)
    sent["messages"].clear()
    assert approval_cli.revise(CYCLE, _hash(rev), note="金额减半") == 0
    assert seen["note"] == "金额减半"
    revisions = repo.list_revisions(CYCLE)
    assert [r["revision_no"] for r in revisions] == [1, 2] and revisions[1]["parent_revision_no"] == 1
    assert repo.get_cycle(CYCLE)["status"] == "pending_approval"
    latest = repo.latest_revision(CYCLE)
    assert repo.effective_review(CYCLE, 2, latest["decision_hash"])["verdict"] == "approved"   # re-risked
    assert [r["cycle_id"] for r in approval_cli.pending_cycles(repo)] == [CYCLE]
    assert any("待审批" in m.title for m in sent["messages"])                                    # asked again
    # the old revision's hash no longer answers
    capsys.readouterr()
    assert approval_cli.approve(CYCLE, _hash(rev)) == 1 and "hash 不匹配" in capsys.readouterr().out
    assert approval_cli.approve(CYCLE, _hash(latest)) == 0


def test_revise_needs_a_note_and_is_capped_at_two(waiting, monkeypatch, capsys):
    repo, rev = waiting
    assert approval_cli.revise(CYCLE, _hash(rev), note="  ") == 1
    from ats.agents.chief.decide import ChiefResult
    from ats.schemas.decision import TradeDecision

    counter = {"n": 0}

    def revised(context_text, *, prior_summary, prior_decisions, note, cycle_id, as_of, use_llm=True):
        counter["n"] += 1
        return ChiefResult(cycle_id=cycle_id, as_of=as_of, summary="改",
                           decisions=[TradeDecision(symbol="COHR", action="buy",
                                                    notional_usd=9_000 - 1_000 * counter["n"], conviction=0.7,
                                                    rationale="改")])

    monkeypatch.setattr("ats.agents.chief.decide.revise_with_note", revised)
    for _ in range(2):
        current = repo.latest_revision(CYCLE)
        assert approval_cli.revise(CYCLE, _hash(current), note="再改") == 0
    capsys.readouterr()
    current = repo.latest_revision(CYCLE)
    assert approval_cli.revise(CYCLE, _hash(current), note="第三次") == 1
    assert "已用完 2 次" in capsys.readouterr().out
    assert counter["n"] == 2                                                     # no third model request
    assert approval_cli.approve(CYCLE, _hash(current)) == 0                      # approve is still open


def test_the_reminder_has_no_link_and_a_messenger_outage_does_not_fail_the_cycle(
        e2e_env, broker, monkeypatch, tmp_path, sent):
    _seed(e2e_env)
    _chief_llm(monkeypatch)
    sent["fail"] = True                                                          # messenger down
    result = run_decision_graph(_state(), channel=PendingApprovalChannel())
    assert "__interrupt__" in result and sent["messages"] == []
    sent["fail"] = False
    from ats.channel.pending_channel import pending_summary, reminder_text
    from ats.schemas.channel import ApprovalRequest

    req = ApprovalRequest.model_validate(result["__interrupt__"][0].value)
    title, body = reminder_text(req.cycle_id, req.revision_no, pending_summary(req))
    assert "http" not in title + body and "://" not in title + body
    assert f"ats approval show {CYCLE}" in body and "COHR" in body


def test_a_new_cycle_supersedes_the_waiting_one_and_says_so_once(waiting, sent):
    repo, rev = waiting
    sent["messages"].clear()
    assert approval_cli.supersede_pending("chief-next") == [CYCLE]
    assert repo.get_cycle(CYCLE)["status"] == "superseded"
    assert [m.title for m in sent["messages"]] == [f"已过期未审批：{CYCLE}"]
    assert approval_cli.supersede_pending("chief-next") == []                     # no second reminder


def test_show_says_the_approval_will_not_place_an_order_and_why(waiting, monkeypatch, capsys):
    repo, rev = waiting
    monkeypatch.setattr("ats.trader.execute.execution_locked", lambda: True)   # production: no isolated transport
    assert approval_cli.show(CYCLE) == 0
    out = capsys.readouterr().out
    assert rev["decision_hash"] in out and "风控审查" in out and "研究快照引用" in out
    assert "批准后不会下单" in out and "实盘锁定" in out


def test_show_says_nothing_about_a_lock_when_an_approval_would_place_the_order(waiting, capsys):
    repo, rev = waiting
    assert approval_cli.show(CYCLE) == 0
    assert "批准后不会下单" not in capsys.readouterr().out


# --- the foreground prompt of `ats chief run` ------------------------------------------------------- #
def _answers(monkeypatch, *lines):
    it = iter(lines)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(it))


def _request(used=0):
    from ats.schemas.channel import ApprovalRequest

    return ApprovalRequest(cycle_id="c", as_of=NOW, boss_revisions=used)


def test_foreground_prompt_offers_only_approve_revise_reject(monkeypatch):
    from ats.channel.cli_channel import CLIChannel

    _answers(monkeypatch, "s COHR", "x COHR", "revise", "revise 金额减半")
    verdict = CLIChannel()._prompt(_request())
    assert verdict.status == "revise" and verdict.comment == "金额减半"       # `s`/`x`/no-note revise were all refused
    assert not verdict.approved_symbols and not verdict.rejected_symbols


def test_foreground_prompt_refuses_a_third_revise(monkeypatch, capsys):
    from ats.channel.cli_channel import CLIChannel

    _answers(monkeypatch, "revise 再改", "a")
    assert CLIChannel()._prompt(_request(used=2)).status == "approved"
    assert "已用完 2 次" in capsys.readouterr().out


def test_a_revise_that_cannot_be_honoured_is_recorded_as_a_rejection():
    from ats.graph.chief import narrow_approval
    from ats.schemas.decision import BossApproval

    assert narrow_approval(BossApproval(status="revise", comment="改小")).status == "revise"
    no_note = narrow_approval(BossApproval(status="revise", comment=" "))
    assert no_note.status == "rejected" and "缺少修改意见" in no_note.comment
