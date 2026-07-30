"""Stage E — the critic agent. Everything here is plumbing around one core
guarantee: samples below n_min never reach the LLM (a spy that raises on call is
the enforcement, not just an assertion on the output), and `open_position` /
insufficient categories never carry a hypothesis.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ats.journal import calibration as cal
from ats.journal import critic
from ats.journal.outputs import FindingItemView, ProposedChangeView
from ats.memory import get_store
from ats.schemas.journal import EvidenceBlock, JournalEntry, TradeEpisode

NOW = datetime(2026, 7, 23, tzinfo=timezone.utc)


@pytest.fixture
def store():
    return get_store()


def _boom(*a, **k):
    raise AssertionError("run_structured must not be called")


def _episode(**kw):
    base = dict(episode_id="e1", symbol="GOOG", direction="long", status="closed",
               origin="system", basis_source="observed_fills",
               opened_at=NOW, closed_at=NOW, primary_entry_id="c1:GOOG:open",
               setup="pead_event")
    return TradeEpisode(**{**base, **kw})


def _entry(**kw):
    base = dict(entry_id="c1:GOOG:open", cycle_id="c1", as_of=NOW, symbol="GOOG", action="buy")
    return JournalEntry(**{**base, **kw})


def _view(**kw):
    base = dict(hypothesis="可能是因为 X", falsifier="若下季度 Y 再次出现则假设成立")
    return FindingItemView(**{**base, **kw})


# --------------------------------------------------------------------------- #
# insufficient categories never call the LLM
# --------------------------------------------------------------------------- #
def test_insufficient_category_skips_the_llm_entirely(store, monkeypatch):
    monkeypatch.setattr(critic, "run_structured", _boom)
    findings = critic.run_critic(store=store, period_label="2026Q3", use_llm=True)
    stat_findings = [f for f in findings if f.category != "open_position"]
    assert stat_findings   # got the 6 stat categories
    assert all(not f.n_sufficient for f in stat_findings)
    assert all(not f.hypothesis and not f.falsifier for f in stat_findings)


def test_llm_finding_for_category_direct_insufficient_path(store, monkeypatch):
    monkeypatch.setattr(critic, "run_structured", _boom)
    block = EvidenceBlock(question="q", table=[{"a": 1}], n_closed=2, n_open=0, n_min=10)
    f = critic._llm_finding_for_category("holding", [block], [], "2026Q3", use_llm=True)
    assert f.n_sufficient is False
    assert f.finding_id == "holding:2026Q3"


# --------------------------------------------------------------------------- #
# sufficient category calls the LLM and threads the result through
# --------------------------------------------------------------------------- #
def test_sufficient_category_calls_llm_and_fills_hypothesis(store, monkeypatch):
    captured = {}

    def fake(role, schema, context, *, skill_slug=None):
        captured["role"], captured["skill_slug"], captured["context"] = role, skill_slug, context
        return _view()

    monkeypatch.setattr(critic, "run_structured", fake)
    block = EvidenceBlock(question="q", table=[{"a": 1}], n_closed=12, n_open=0, n_min=10)
    f = critic._llm_finding_for_category("risk_gate", [block], [], "2026Q3", use_llm=True)
    assert f.n_sufficient is True
    assert f.hypothesis == "可能是因为 X"
    assert f.falsifier
    assert captured["role"] == "critic" and captured["skill_slug"] == "trade-journal-critic"


def test_proposed_change_passes_through_end_to_end(store, monkeypatch):
    view = _view(proposed_change=ProposedChangeView(
        target="config", locator="config/pead/GOOG.yaml: long_threshold",
        current="1.2", proposed="1.0", expected_effect="已观测样本中多捕获 3 次"))
    monkeypatch.setattr(critic, "run_structured", lambda *a, **k: view)
    block = EvidenceBlock(question="q", table=[{"a": 1}], n_closed=12, n_open=0, n_min=10)
    f = critic._llm_finding_for_category("risk_gate", [block], [], "2026Q3", use_llm=True)
    assert f.proposed_change is not None
    assert f.proposed_change.locator == "config/pead/GOOG.yaml: long_threshold"
    assert f.proposed_change.proposed == "1.0"


def test_llm_failure_degrades_to_a_sufficient_but_hypothesis_free_finding(store, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(critic, "run_structured", boom)
    block = EvidenceBlock(question="q", table=[{"a": 1}], n_closed=12, n_open=0, n_min=10)
    f = critic._llm_finding_for_category("risk_gate", [block], [], "2026Q3", use_llm=True)
    assert f.n_sufficient is True   # the DATA was sufficient — the call just failed
    assert f.hypothesis == "" and f.falsifier == ""


def test_use_llm_false_never_calls_the_model_even_if_sufficient(store, monkeypatch):
    monkeypatch.setattr(critic, "run_structured", _boom)
    block = EvidenceBlock(question="q", table=[{"a": 1}], n_closed=12, n_open=0, n_min=10)
    f = critic._llm_finding_for_category("risk_gate", [block], [], "2026Q3", use_llm=False)
    assert f.n_sufficient is False
    assert "--no-llm" in f.observation


# --------------------------------------------------------------------------- #
# open_position: deterministic, never touches the LLM
# --------------------------------------------------------------------------- #
def test_open_position_finding_per_flagged_episode(store):
    store.save_episode(_episode(episode_id="e1", status="open", closed_at=None,
                               invalidation_triggered=True))
    store.save_episode(_episode(episode_id="e2", symbol="TSM", status="open", closed_at=None,
                               horizon_overdue_days=7))
    store.save_episode(_episode(episode_id="e3", symbol="ASML", status="open", closed_at=None))
    findings = critic._open_position_findings(store)
    assert {f.finding_id for f in findings} == {"open_position:e1", "open_position:e2"}
    for f in findings:
        assert f.n == 1 and f.n_sufficient is True
        assert f.hypothesis == "" and f.falsifier == ""


def test_open_position_ignores_closed_episodes(store):
    store.save_episode(_episode(episode_id="e1", invalidation_triggered=True))   # status=closed
    assert critic._open_position_findings(store) == []


# --------------------------------------------------------------------------- #
# cases: resolved from counterexample IDs, NOT blind (unlike B3)
# --------------------------------------------------------------------------- #
def test_select_cases_resolves_episode_ids_and_skips_others(store):
    store.save_episode(_episode(episode_id="e1", realized_pnl=500.0))
    block_with_episode = EvidenceBlock(question="q1", table=[], counterexamples=["e1"])
    block_with_prediction = EvidenceBlock(question="q2", table=[], counterexamples=["p1-not-an-episode"])
    cases = critic._select_cases(store, [block_with_episode, block_with_prediction])
    assert len(cases) == 1
    assert cases[0].episode.episode_id == "e1"


def test_select_cases_are_not_blinded(store):
    """The deliberate opposite of B3's invalidation.py: critic is retrospective, so
    outcome fields must survive into the case the LLM sees."""
    store.save_episode(_episode(episode_id="e1", realized_pnl=1234.5, r_multiple=2.0))
    block = EvidenceBlock(question="q", table=[], counterexamples=["e1"])
    cases = critic._select_cases(store, [block])
    assert cases[0].episode.realized_pnl == pytest.approx(1234.5)
    assert cases[0].episode.r_multiple == pytest.approx(2.0)


def test_select_cases_respects_cap(store):
    for i in range(6):
        store.save_episode(_episode(episode_id=f"e{i}", symbol=f"S{i}",
                                    primary_entry_id=f"c{i}:S{i}:open"))
    block = EvidenceBlock(question="q", table=[], counterexamples=[f"e{i}" for i in range(6)])
    assert len(critic._select_cases(store, [block], cap=3)) == 3


# --------------------------------------------------------------------------- #
# new evidence blocks (execution / evidence) — hand-verified
# --------------------------------------------------------------------------- #
def test_execution_quality_block_splits_by_slippage_threshold(store):
    store.save_journal_entry(_entry(entry_id="c1:GOOG:open", slippage_bps=50.0))
    store.save_episode(_episode(episode_id="e1", primary_entry_id="c1:GOOG:open", realized_pnl=-200.0))
    store.save_journal_entry(_entry(entry_id="c2:TSM:open", symbol="TSM", slippage_bps=5.0))
    store.save_episode(_episode(episode_id="e2", symbol="TSM", primary_entry_id="c2:TSM:open",
                               realized_pnl=300.0))
    block = critic._execution_quality_block(store)
    by_group = {row["分组"]: row for row in block.table}
    assert by_group["滑点≥20bp"]["n"] == 1 and by_group["滑点≥20bp"]["均值盈亏$"] == pytest.approx(-200.0)
    assert by_group["滑点较低"]["均值盈亏$"] == pytest.approx(300.0)
    assert block.n_closed == 2


def test_evidence_quality_block_splits_by_transcript(store):
    store.save_journal_entry(_entry(entry_id="c1:GOOG:open", ev_has_transcript=True))
    store.save_episode(_episode(episode_id="e1", primary_entry_id="c1:GOOG:open", realized_pnl=100.0))
    store.save_journal_entry(_entry(entry_id="c2:TSM:open", symbol="TSM", ev_has_transcript=False))
    store.save_episode(_episode(episode_id="e2", symbol="TSM", primary_entry_id="c2:TSM:open",
                               realized_pnl=-50.0))
    block = critic._evidence_quality_block(store)
    by_group = {row["分组"]: row for row in block.table}
    assert by_group["有纪要"]["均值盈亏$"] == pytest.approx(100.0)
    assert by_group["缺纪要（凭发布稿打分）"]["均值盈亏$"] == pytest.approx(-50.0)


# --------------------------------------------------------------------------- #
# two-column layout: decision vs outcome, stable order, no pnl-based sorting
# --------------------------------------------------------------------------- #
def test_render_splits_findings_into_decision_and_outcome_columns():
    findings = [
        critic.CriticFinding(finding_id="risk_gate:q", category="risk_gate",
                             observation="o1", n=1, n_sufficient=False),
        critic.CriticFinding(finding_id="calibration:q", category="calibration",
                             observation="o2", n=1, n_sufficient=False),
        critic.CriticFinding(finding_id="open_position:e1", category="open_position",
                             observation="GOOG 失效", n=1, n_sufficient=True),
    ]
    out = critic.render_reflection(findings, "2026Q3")
    decision_section = out.split("## 决策质量")[1].split("## 结果质量")[0]
    outcome_section = out.split("## 结果质量")[1]
    assert "risk_gate" in decision_section and "calibration" not in decision_section
    assert "calibration" in outcome_section and "risk_gate" not in outcome_section
    assert "GOOG 失效" in out.split("## 当前需要处理")[1].split("## 决策质量")[0]


def test_insufficient_finding_never_renders_hypothesis_text():
    f = critic.CriticFinding(finding_id="x:q", category="risk_gate", observation="o",
                             n=2, n_sufficient=False, hypothesis="不应该出现",
                             falsifier="也不应该出现")
    out = critic.render_reflection([f], "2026Q3")
    assert "不应该出现" not in out
    assert "样本不足" in out


# --------------------------------------------------------------------------- #
# misc
# --------------------------------------------------------------------------- #
def test_quarter_label():
    from datetime import date

    assert critic._quarter_label(date(2026, 1, 15)) == "2026Q1"
    assert critic._quarter_label(date(2026, 12, 31)) == "2026Q4"


def test_build_brief_assembles_all_blocks_and_cases(store):
    store.save_episode(_episode(episode_id="e1"))
    brief = critic.build_brief(store, "2026Q3")
    assert brief.period == "2026Q3"
    assert len(brief.blocks) == 9   # 4 calibration + 1 holding + 1 risk_gate + 1 human_gate + execution + evidence


def test_run_critic_use_llm_false_is_fully_llm_free(store, monkeypatch):
    monkeypatch.setattr(critic, "run_structured", _boom)
    findings = critic.run_critic(store=store, use_llm=False)
    assert findings   # still produces (hypothesis-free) findings for all 6 categories
    assert all(not f.hypothesis for f in findings)


def test_run_is_safe_with_no_data(store):
    assert critic.run(use_llm=False) == 0
