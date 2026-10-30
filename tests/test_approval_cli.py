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
    out = capsys.readouterr().out
    assert "至少 12 位" in out and _hash(rev) in out            # a short prefix is told what the current one is
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


def test_show_folds_the_research_references_unless_asked_for_all_of_them(waiting, capsys):
    repo, rev = waiting
    assert approval_cli.show(CYCLE) == 0
    folded = capsys.readouterr().out
    assert "研究快照引用：共" in folded and "--full" in folded
    assert approval_cli.show(CYCLE, full=True) == 0
    full = capsys.readouterr().out
    assert "研究快照引用：\n" in full and len(full.splitlines()) > len(folded.splitlines())


def test_a_long_order_reason_is_cut_with_an_ellipsis_not_mid_sentence_silently():
    assert approval_cli._ellipsis("理由" * 100, 120).endswith("…")
    assert approval_cli._ellipsis("短理由", 120) == "短理由"


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


# --- answering in a terminal without typing the hash ------------------------------------------------ #
def _args(verb, **kw):
    from argparse import Namespace

    return Namespace(verb=verb, cycle=kw.get("cycle"), revision=kw.get("revision"), note=kw.get("note", ""),
                     actor="boss")


@pytest.fixture
def at_a_terminal(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)


def test_a_terminal_answer_picks_the_only_waiting_cycle_and_binds_the_hash_on_the_card(
        waiting, at_a_terminal, monkeypatch, capsys):
    repo, rev = waiting
    _answers(monkeypatch, "approve")
    assert approval_cli._dispatch(_args("approve")) == 0
    out = capsys.readouterr().out
    assert f"hash {_hash(rev)}" in out                                  # the card says which revision is answered
    assert repo.get_cycle(CYCLE)["status"] in {"approved_not_executed", "executed"}


def test_anything_but_the_verb_cancels_and_leaves_the_cycle_waiting(waiting, at_a_terminal, monkeypatch, capsys):
    repo, _ = waiting
    for typed in ("", "y", "yes", "APPROVE", "reject"):
        _answers(monkeypatch, typed)
        assert approval_cli._dispatch(_args("approve")) == 1, typed
    assert "已取消" in capsys.readouterr().out
    assert repo.get_cycle(CYCLE)["status"] == "pending_approval"
    assert repo.conn.execute("SELECT COUNT(*) FROM boss_approvals WHERE cycle_id = ?", (CYCLE,)).fetchone()[0] == 0


def test_without_a_terminal_the_hash_stays_mandatory(waiting, monkeypatch, capsys):
    repo, _ = waiting
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    assert approval_cli._dispatch(_args("approve")) == 1
    assert "必须写明周期和修订 hash" in capsys.readouterr().out
    assert repo.get_cycle(CYCLE)["status"] == "pending_approval"


def test_a_revision_without_its_cycle_is_refused(waiting, at_a_terminal, capsys):
    assert approval_cli._dispatch(_args("approve", revision="a" * 12)) == 1
    assert "同时给周期" in capsys.readouterr().out


def test_a_newer_revision_that_appears_after_reading_the_card_is_not_answered(
        waiting, at_a_terminal, monkeypatch, capsys):
    repo, rev = waiting
    box = {"typed": False}

    def typed(prompt=""):
        box["typed"] = True
        return "approve"

    monkeypatch.setattr("builtins.input", typed)
    real = DecisionAuditRepository.latest_revision

    def latest(self, cycle_id):
        row = real(self, cycle_id)
        if box["typed"] and row is not None:
            return {**dict(row), "decision_hash": "f" * 32, "revision_no": row["revision_no"] + 1}
        return row

    monkeypatch.setattr(DecisionAuditRepository, "latest_revision", latest)
    assert approval_cli._dispatch(_args("approve")) == 1
    assert "已不是最新" in capsys.readouterr().out
    assert repo.conn.execute("SELECT COUNT(*) FROM boss_approvals WHERE cycle_id = ?", (CYCLE,)).fetchone()[0] == 0


def test_a_terminal_revise_asks_for_the_note_and_a_blank_note_cancels(waiting, at_a_terminal, monkeypatch, capsys):
    repo, _ = waiting
    _answers(monkeypatch, "")
    assert approval_cli._dispatch(_args("revise")) == 1
    assert "已取消" in capsys.readouterr().out
    assert repo.conn.execute("SELECT COUNT(*) FROM boss_approvals WHERE cycle_id = ?", (CYCLE,)).fetchone()[0] == 0


def test_nothing_waiting_is_said_plainly(e2e_env, at_a_terminal, capsys):
    assert approval_cli._dispatch(_args("approve")) == 1
    assert "没有待审批的周期" in capsys.readouterr().out


# --- readable output -------------------------------------------------------------------------------- #
def test_the_noise_around_an_answer_is_dropped_or_folded_and_real_signals_are_counted(caplog, capsys):
    import logging

    caplog.set_level(logging.DEBUG)
    with approval_cli._quiet_noise():
        logging.getLogger("ats.consumer_reads").info("consumer_read {}")
        logging.getLogger("ats.consumer_reads").warning("consumer_read blocked")
        logging.getLogger("ib_async.wrapper").error("Error 300, reqId 4: Can't find EId with tickerId:4")
        for _ in range(3):
            logging.getLogger("ib_async.ib").error("completed orders request timed out")
        for symbol in ("AMZN", "DRAM", "GOOG"):
            logging.getLogger("ats.data").warning(
                "data source yf-light:%s failed after 2 attempts: SSL", symbol)
    shown = [r.getMessage() for r in caplog.records]
    assert "consumer_read {}" not in shown and "consumer_read blocked" in shown
    assert not any("Error 300" in m for m in shown)
    assert sum("completed orders" in m for m in shown) == 1             # said once, not per connection
    assert sum("yf-light" in m for m in shown) == 1
    out = capsys.readouterr().out
    assert "还有 2 次" in out and "TWS" in out and "行情源 yf-light" in out
    # leaving the command puts the loggers back
    logging.getLogger("ats.consumer_reads").info("consumer_read after")
    assert "consumer_read after" in [r.getMessage() for r in caplog.records]
