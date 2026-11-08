"""Phase F 7.1–7.6 — isolated intake verification for the ten roles.

Most of these tests are about things that must FAIL or must NOT be claimable,
because the failure mode this module exists to prevent is a verification that
passes while proving nothing:

- an entry point that was never reached reported as "no bypass found";
- an isolated run quoted as proof that the production ledger is complete;
- a summary of "everything checked out" from a run that checked nothing;
- a restart that silently continues with an empty projection.

The one place a PASS is the interesting result is the real scan of the ten roles,
which is pinned here so a future change cannot quietly widen the forbidden set
into meaninglessness or narrow it into ignoring a real bypass.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from ats.agent.task_projection import ProjectionScope, build_envelope
from ats.execution import broker_write_guard as guard
from ats.workflow import intake_verification as iv

NOW = datetime(2026, 10, 6, 8, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _clean_guard():
    guard.reset_for_tests()
    yield
    guard.reset_for_tests()


# --------------------------------------------------------------------------- #
# 7.1 — the isolated acceptance entry
# --------------------------------------------------------------------------- #

def test_verification_starts_without_production_qualification(tmp_path):
    """The whole reason this module exists.

    `F.0.2` measured 0/10 eligible. If the entry point that produces the missing
    evidence required that evidence, qualification would be unobtainable rather
    than merely un-obtained.
    """
    with iv.isolated_verification("intake-no-qual", root=tmp_path / "isolated") as attestation:
        assert attestation.qualification_required is False
        assert attestation.production_ledger_integrity_proven is False


def test_the_entry_point_does_not_ask_assurance(tmp_path, monkeypatch):
    """A verification that called `qualification()` to start would deadlock.

    Patched to raise so that any call from inside the context manager is a hard
    failure rather than a passing test.
    """
    from ats.data import assurance

    def _refuse(**_kwargs):
        raise AssertionError("intake verification must not gate on qualification")

    monkeypatch.setattr(assurance, "qualification", _refuse)
    with iv.isolated_verification("intake-no-gate", root=tmp_path / "isolated"):
        pass


def test_verification_runs_under_the_process_write_prohibition(tmp_path):
    """Redirecting ledgers does not stop a real order — the broker is not a DB."""
    with iv.isolated_verification("intake-guard", root=tmp_path / "isolated"):
        assert guard.is_prohibited() is True
        with pytest.raises(guard.BrokerWriteProhibited):
            guard.check_broker_write(operation="place_orders", caller="intake")


def test_every_persistence_surface_is_redirected_during_verification(tmp_path):
    """A surface the verification can still write to is a surface it can corrupt."""
    from ats.workflow import isolation

    with iv.isolated_verification("intake-surfaces", root=tmp_path / "isolated") as attestation:
        for var in isolation.PERSISTENCE_ENV_VARS:
            import os

            assert os.environ[var].startswith(str(tmp_path)), var
        assert set(attestation.surfaces_redirected) == set(
            isolation.PERSISTENCE_ENV_VARS)


def test_verification_refuses_to_start_without_the_capability(tmp_path, monkeypatch):
    """A run that cannot prove writes are prohibited must not proceed."""
    monkeypatch.setattr(
        guard, "assert_broker_writes_prohibited",
        lambda **_kwargs: (_ for _ in ()).throw(guard.BrokerWriteProhibited(
            "capability missing", reason_code="guard_missing", refusal_id="")))
    with pytest.raises(guard.BrokerWriteProhibited):
        with iv.isolated_verification("intake-noguard", root=tmp_path / "isolated"):
            pytest.fail("the verification body must not execute")


def test_a_production_side_effect_invalidates_the_run(tmp_path, monkeypatch):
    """`7.1` second case, and the half that is easy to get wrong.

    A production write must make the acceptance INVALID, not merely be logged. A
    verification whose entire value is "I changed nothing" cannot be cited as
    evidence if it changed something — and a leak discovered at the end of a long
    run is exactly the one nobody goes looking for.
    """
    reads = iter([{"ats.sqlite:trades": 4}, {"ats.sqlite:trades": 5}])
    monkeypatch.setattr(iv, "_production_fingerprint", lambda: next(reads))

    with pytest.raises(iv.ProductionSideEffect) as excinfo:
        with iv.isolated_verification("intake-leak", root=tmp_path / "isolated"):
            pass

    assert "ats.sqlite:trades" in str(excinfo.value)
    assert "invalid" in str(excinfo.value)


def test_no_production_approval_is_produced_by_a_verification(tmp_path):
    """An approval is the input to a real order; a verification must not mint one."""
    from ats.memory import get_store

    with iv.isolated_verification("intake-approval", root=tmp_path / "isolated"):
        store = get_store()
        assert store.conn.execute(
            "SELECT COUNT(*) FROM boss_approvals").fetchone()[0] == 0
        assert store.conn.execute(
            "SELECT COUNT(*) FROM decision_revisions").fetchone()[0] == 0


def test_clearing_a_side_effect_requires_explicit_confirmation():
    """A cleanup that deletes ledger rows on its own initiative is a second way
    to corrupt the ledger it exists to protect."""
    with pytest.raises(iv.IntakeVerificationError, match="explicit"):
        iv.clear_production_side_effect("ats.sqlite:trades")


def test_a_confirmed_clear_reports_the_target_without_deleting():
    plan = iv.clear_production_side_effect("ats.sqlite:trades", confirm=True)
    assert plan["table"] == "trades"
    assert plan["cleared"] is False, "deletion is an operator action, not a side effect"


def test_an_isolated_result_cannot_authorise_a_trade(tmp_path):
    """`7.1` third case / `7.6`: the path working is not ledger integrity."""
    with iv.isolated_verification("intake-not-tradable", root=tmp_path / "isolated") as att:
        with pytest.raises(iv.IntakeVerificationError) as excinfo:
            iv.assert_isolated_result_not_tradable(att, consumer_id="trader")

    message = str(excinfo.value)
    assert "trader" in message
    assert "production reading qualification" in message


def test_the_attestation_states_what_it_does_not_prove(tmp_path):
    with iv.isolated_verification("intake-attest", root=tmp_path / "isolated") as att:
        pass
    row = att.as_row()
    assert row["production_ledger_integrity_proven"] is False
    assert row["qualification_required"] is False
    assert row["broker_write_prohibited"] is True
    assert row["production_side_effects"] == []


def test_an_admitted_document_is_not_a_candidate():
    assert iv.check_candidate_material(
        consumer_id="information", document_refs=["doc-1@v3"],
        admitted_refs=["doc-1@v3", "doc-2@v1"]) == []


def test_a_candidate_presented_as_published_is_flagged():
    """The check is by REFERENCE, not by a status flag the caller passes — the
    refs are what the analysis actually used."""
    violations = iv.check_candidate_material(
        consumer_id="information", document_refs=["doc-1@v3", "doc-9"],
        admitted_refs=["doc-1@v3"])
    assert len(violations) == 1
    assert violations[0].kind == "candidate_material_as_published"
    assert violations[0].location == "doc-9"


def test_an_opinion_written_as_a_shared_fact_is_flagged():
    """Once an opinion is a fact row, later readers cannot tell it apart from
    evidence and the reasoning that produced it is gone."""
    violations = iv.check_opinion_writeback(
        consumer_id="layer",
        statements=[{"table": "evidence_facts", "values": {"layer": "expanding"}}])
    assert len(violations) == 1
    assert violations[0].kind == "opinion_writeback"


def test_writing_a_projection_is_not_writeback():
    assert iv.check_opinion_writeback(
        consumer_id="layer",
        statements=[{"table": "task_projection_envelopes"}]) == []


def test_every_neutral_fact_table_named_in_the_project_exists_as_a_table_name():
    """The writeback check is only as good as the table list behind it."""
    from ats.memory.store import _SCHEMA

    known = set()
    for line in _SCHEMA.splitlines():
        stripped = line.strip()
        if stripped.startswith("CREATE TABLE IF NOT EXISTS "):
            # The trailing name carries a space before the opening paren, so a
            # plain split left "evidence_facts " and matched nothing.
            known.add(stripped.split("CREATE TABLE IF NOT EXISTS ")[1]
                      .split("(")[0].strip())
    # `data_evidence_facts` and friends live in the data layer, not in Workflow
    # memory; the rest must be real Workflow-memory tables.
    memory_tables = {t for t in iv.NEUTRAL_FACT_TABLES if t in known}
    assert memory_tables, "no neutral-fact table name matches a real table"
    assert {"evidence_facts", "measurement_points"} <= memory_tables


# --------------------------------------------------------------------------- #
# 7.4 — required-input blocking and restart resolvability
# --------------------------------------------------------------------------- #

# A payload per agent role. `build_envelope` validates against the role's own
# schema, so a generic dict is rejected — which is the point of the contract, and
# why these cannot all share one fixture.
ROLE_PAYLOADS: dict[str, dict] = {
    "layer_analysis": {"layer": "L4_interconnect", "status": "expanding",
                       "summary": "orders up", "findings": ["b2b 1.4"],
                       "confidence": 0.8},
    "information_brief": {"entity": "MU", "headline": "指引上修",
                          "summary": "上游订单走强", "relevance": "high",
                          "sources": ["rss:test"]},
    "sector_allocation": {"sector": "ai_hardware", "stance": "overweight",
                          "target_weight": 0.2, "rationale": "景气上行"},
    "fundamental_expectation_update": {"entity": "MU", "metric": "HBM4 ASP",
                                       "period": "FY27", "new_value": 210.0,
                                       "driver": "供给收缩"},
    "fundamental_event_review": {"entity": "MU", "event": "earnings",
                                 "period": "FY26Q3", "direction": 1,
                                 "magnitude": 0.05,
                                 "falsifiable_conditions": ["下季低于区间即证伪"]},
    "macro_review": {"regime": "risk_on", "summary": "流动性宽松",
                     "indicators": ["10y real yield"]},
    "technical_review": {"entity": "MU", "signal": "neutral", "summary": "区间震荡",
                         "levels": {"support": 300.0}},
}


def _envelope(role, scope, **extra):
    payload = ROLE_PAYLOADS.get(role)
    if payload is None:
        raise AssertionError(f"no schema-valid payload declared for role {role!r}")
    return build_envelope(
        role=role, payload=payload, scope=scope,
        as_of=NOW.isoformat(timespec="seconds"),
        valid_until=(NOW + timedelta(hours=48)).isoformat(timespec="seconds"),
        data_vintage_refs=["dataset@2026-10-05"], **extra)


def _complete_projections(registry):
    from ats.workflow.run_contracts import ROLE_TO_CATEGORY

    out = {}
    for task_id in registry.task_ids():
        role = registry.spec(task_id).agent_role
        out[task_id] = _envelope(role, ProjectionScope(kind="portfolio"))
    return out


def test_a_complete_run_may_enter_the_decision_cycle():
    from ats.workflow.run_contracts import default_registry

    registry = default_registry()
    result = iv.verify_required_input_blocking(
        at=NOW, registry=registry, projections=_complete_projections(registry),
        scope={"kind": "portfolio"})
    assert result["complete"] is True
    assert result["decision_cycle_entered"] is True
    assert result["verified"] is True


def test_a_missing_required_analysis_blocks_the_cycle():
    """`7.4`: the requirement is a refusal that NAMES the gap, not a gap report
    that exists. An incomplete snapshot must never open a cycle."""
    from ats.workflow.run_contracts import default_registry

    registry = default_registry()
    projections = _complete_projections(registry)
    projections.pop("technical_review")

    result = iv.verify_required_input_blocking(
        at=NOW, registry=registry, projections=projections, scope={"kind": "portfolio"})

    assert result["complete"] is False
    assert result["decision_cycle_entered"] is False
    assert result["verified"] is False
    assert any(gap["task_id"] == "technical_review" for gap in result["gaps"])
    assert "technical_review" in result["refusal_message"]


def test_an_empty_projection_set_blocks_and_names_every_category():
    from ats.workflow.run_contracts import default_registry

    registry = default_registry()
    result = iv.verify_required_input_blocking(
        at=NOW, registry=registry, projections={}, scope={"kind": "portfolio"})
    assert result["complete"] is False
    assert result["decision_cycle_entered"] is False
    assert {gap["category"] for gap in result["gaps"]} >= {
        "layer_analysis", "information_brief", "sector_allocation",
        "fundamental_analysis", "macro_review", "technical_review"}


def test_a_missing_analysis_is_reported_by_name_not_as_a_count():
    from ats.workflow.run_contracts import default_registry

    registry = default_registry()
    projections = _complete_projections(registry)
    # BOTH fundamental modes absent. Nulling only one leaves the category
    # satisfied — the contract judges per CATEGORY, since the fundamental analyst
    # runs in exactly one mode per cycle and demanding both would leave every
    # cycle permanently incomplete. That is why this test removes both.
    projections["fundamental_expectation_update"] = None
    projections["fundamental_event_review"] = None

    outcome = iv.verify_required_input_blocking(
        at=NOW, registry=registry, projections=projections, scope={"kind": "portfolio"})

    assert outcome["complete"] is False
    categories = {gap["category"] for gap in outcome["gaps"]}
    assert "fundamental_analysis" in categories
    # Every gap names the task it is about — a count would leave the reader
    # nothing to act on.
    assert all(gap["task_id"] for gap in outcome["gaps"])
    assert "fundamental_expectation_update" in outcome["refusal_message"]


@pytest.mark.parametrize("mode,missing",[("routine", "fundamental_expectation_update"),
                                         ("event", "fundamental_event_review")])
def test_selected_mode_failure_cannot_be_replaced_by_the_other(mode, missing):
    from ats.workflow.run_contracts import default_registry
    registry = default_registry()
    projections = _complete_projections(registry)
    projections[missing] = None
    outcome = iv.verify_required_input_blocking(at=NOW, registry=registry,
        projections=projections, scope={"kind": "portfolio"}, fundamental_mode=mode)
    assert not outcome["complete"] and not outcome["run_contract_complete"]
    assert not outcome["decision_cycle_entered"] and not outcome["verified"]
    assert outcome["contract_disagreements"] == []
    assert missing in outcome["refusal_message"]


def test_unselected_event_does_not_block_routine():
    from ats.workflow.run_contracts import default_registry
    registry = default_registry()
    projections = _complete_projections(registry)
    projections["fundamental_event_review"] = None
    result = iv.verify_required_input_blocking(at=NOW, registry=registry,
        projections=projections, scope={"kind":"portfolio"}, fundamental_mode="routine")
    assert result["verified"] and result["run_contract_complete"]
    assert "fundamental_event_review" not in result["selected_tasks"]


def test_a_fully_consistent_run_reports_no_disagreement():
    from ats.workflow.run_contracts import default_registry

    registry = default_registry()
    outcome = iv.verify_required_input_blocking(
        at=NOW, registry=registry, projections=_complete_projections(registry),
        scope={"kind": "portfolio"})
    assert outcome["contract_disagreements"] == []
    assert outcome["verified"] is True


# --- restart ---------------------------------------------------------------- #

def _store_with_projections(tmp_path):
    from ats.memory.store import TradingMemory

    store = TradingMemory(str(tmp_path / "restart.sqlite"))
    envelope = _envelope("macro_review", ProjectionScope(kind="portfolio"))
    store.save_task_projection_envelope(envelope)
    store.conn.commit()
    return str(tmp_path / "restart.sqlite"), envelope


def test_a_projection_resolves_in_a_fresh_store(tmp_path):
    path, envelope = _store_with_projections(tmp_path)

    verdict = iv.verify_restart_resolvability(
        lambda: _open_store(path), [envelope.projection_id])

    assert verdict.resolvable is True
    assert verdict.resolved == [envelope.projection_id]
    assert verdict.unresolved == []


def test_an_unresolvable_projection_fails_rather_than_defaulting(tmp_path):
    """The quiet failure this catches: after a restart the projection is gone,
    and a caller that reads 'no opinion yet' continues with a default snapshot."""
    path, _ = _store_with_projections(tmp_path)
    verdict = iv.verify_restart_resolvability(lambda: _open_store(path), ["no-such-id"])

    assert verdict.resolvable is False
    assert verdict.unresolved[0]["projection_id"] == "no-such-id"
    assert "not found" in verdict.unresolved[0]["reason"]


def test_a_projection_whose_content_hash_moved_is_not_the_same_projection(tmp_path):
    """A projection that resolves to different content IS a different
    projection, even though the id still matches."""
    path, envelope = _store_with_projections(tmp_path)
    verdict = iv.verify_restart_resolvability(
        lambda: _open_store(path), [envelope.projection_id],
        expect_hash={envelope.projection_id: "0" * 64})

    assert verdict.resolvable is False
    assert "content hash drifted" in verdict.unresolved[0]["reason"]


def test_an_unopenable_store_is_unresolvable_not_an_error(tmp_path):
    """'I could not check' and 'the check found nothing' must never read alike."""
    def _explode():
        raise sqlite3.OperationalError("database is locked")

    verdict = iv.verify_restart_resolvability(_explode, ["p1"])
    assert verdict.resolvable is False
    assert "could not be reopened" in verdict.unresolved[0]["reason"]


def test_an_unpublished_projection_is_not_resolvable(tmp_path):
    from ats.memory.store import TradingMemory

    path = str(tmp_path / "failed.sqlite")
    store = TradingMemory(path)
    envelope = _envelope("macro_review", ProjectionScope(kind="portfolio"))
    envelope = envelope.model_copy(update={"status": "failed"})
    store.save_task_projection_envelope(envelope)
    store.conn.commit()

    verdict = iv.verify_restart_resolvability(lambda: _open_store(path),
                                              [envelope.projection_id])
    assert verdict.resolvable is False
    assert "not published" in verdict.unresolved[0]["reason"]


def _open_store(path: str):
    from ats.memory.store import TradingMemory

    return TradingMemory(path)


# --------------------------------------------------------------------------- #
# 7.5 — the decision and execution chain
# --------------------------------------------------------------------------- #

@pytest.fixture
def chain_repo(tmp_path):
    """A cycle with two review rounds and an approval on the latest revision."""
    from ats.decision.repository import DecisionAuditRepository
    from ats.decision.snapshot import build_research_snapshot
    from ats.memory.store import TradingMemory
    from ats.workflow.run_contracts import default_registry

    store = TradingMemory(str(tmp_path / "chain.sqlite"))
    repo = DecisionAuditRepository(store)
    registry = default_registry()
    projections = _complete_projections(registry)
    snapshot = build_research_snapshot(
        at=NOW, registry=registry, projections=projections,
        scope=ProjectionScope(kind="portfolio"))

    cycle_id = "chain-cycle"
    repo.create_cycle(cycle_id=cycle_id, trigger_source="intake",
                      research_snapshot=snapshot.to_payload())
    revision = repo.append_revision(
        cycle_id=cycle_id,
        orders=[{"symbol": "MU", "action": "buy", "qty": 100}],
        rationale="chain verification")
    binding = dict(decision_hash=revision["decision_hash"],
                   ruleset_version="risk-v1", portfolio_snapshot_id="pf-1",
                   market_as_of=NOW.isoformat(timespec="seconds"))
    repo.record_review(review_id="rr-1:r1", cycle_id=cycle_id,
                       revision_no=revision["revision_no"],
                       verdict="revise", **binding)
    repo.record_review(review_id="rr-2:r2", cycle_id=cycle_id,
                       revision_no=revision["revision_no"],
                       verdict="approved", **binding)
    repo.record_approval(
        approval_id="ap-1:r2", cycle_id=cycle_id,
        revision_no=revision["revision_no"],
        decision_hash=revision["decision_hash"], decision="approved",
        reviewer="tester", idempotency_key="k-1")
    return repo, store, cycle_id, snapshot


def test_every_chain_link_is_reported_individually(chain_repo):
    """A cutover decision needs to know WHICH link is missing; 'the chain is not
    bound' sends the reader back to the start."""
    repo, store, cycle_id, snapshot = chain_repo
    result = iv.verify_decision_chain(repo=repo, cycle_id=cycle_id,
                                      snapshot=snapshot, store=store)

    names = {binding.name for binding in result.bindings}
    assert names == {"research_snapshot", "decision_revision", "risk_review",
                     "boss_approval", "execution_authorization",
                     "internal_state", "order_fill_channel"}
    for binding in result.bindings:
        assert binding.detail, binding.name


def test_the_chain_is_complete_when_every_link_is_bound(chain_repo):
    repo, store, cycle_id, snapshot = chain_repo
    result = iv.verify_decision_chain(repo=repo, cycle_id=cycle_id,
                                      snapshot=snapshot, store=store)
    bound = {b.name: b.bound for b in result.bindings}
    for name in ("research_snapshot", "decision_revision", "risk_review",
                 "boss_approval"):
        assert bound[name] is True, name


def test_more_than_one_review_round_is_detected(chain_repo):
    """A single review is the easy path; the loop exists because a revision after
    review invalidates it."""
    repo, store, cycle_id, snapshot = chain_repo
    result = iv.verify_decision_chain(repo=repo, cycle_id=cycle_id,
                                      snapshot=snapshot, store=store)
    assert result.rounds == 2
    assert result.multi_round is True


def test_an_unbound_chain_reports_the_missing_link_rather_than_a_boolean(chain_repo):
    repo, store, cycle_id, snapshot = chain_repo
    result = iv.verify_decision_chain(repo=repo, cycle_id="no-such-cycle",
                                      snapshot=snapshot, store=store)
    unbound = [b.name for b in result.bindings if not b.bound]
    assert "decision_revision" in unbound
    assert "boss_approval" in unbound
    assert result.complete is False


def test_a_broker_write_attempt_is_recorded_as_a_refusal(tmp_path):
    """`7.5`: the chain is verified without broker write permission, so the
    evidence that the prohibition held is the refusal."""
    from ats.decision.repository import DecisionAuditRepository
    from ats.memory.store import TradingMemory

    store = TradingMemory(str(tmp_path / "refusal.sqlite"))
    repo = DecisionAuditRepository(store)
    with iv.isolated_verification("chain-refusal", root=tmp_path / "isolated"):
        with pytest.raises(guard.BrokerWriteProhibited):
            guard.check_broker_write(operation="place_orders", caller="chain")
        result = iv.verify_decision_chain(
            repo=repo, cycle_id="none", snapshot=None, store=store,
            broker_write_attempted=True)

    assert result.broker_write_attempts == 1
    assert result.broker_refusals >= 1


def test_repeated_recovery_must_not_duplicate_the_ledger(tmp_path):
    """A recovery is exactly what gets re-run, because nobody watches a process
    crash and resume. Comparing the LEDGER, not the return value, because the
    ledger is what downstream readers see."""
    rows = [("fill-1",)]
    calls = {"count": 0}

    def _recover():
        calls["count"] += 1
        if calls["count"] > 1:
            rows.append(("fill-2",))       # a non-idempotent recovery

    outcome = iv.verify_recovery_idempotence(action=_recover,
                                             ledger_reader=lambda: list(rows))
    assert outcome["idempotent"] is False
    assert outcome["rows_after_first"] == 1
    assert outcome["rows_after_second"] == 2
    assert "double-count" in outcome["reason"]


def test_an_idempotent_recovery_is_reported_as_such():
    rows = [("fill-1",)]

    outcome = iv.verify_recovery_idempotence(action=lambda: None,
                                             ledger_reader=lambda: list(rows))
    assert outcome["idempotent"] is True
    assert outcome["reason"] == ""


def test_a_late_fill_stops_a_switch(tmp_path):
    """The asymmetry: absorbing a late fill is always correct, letting a switch
    continue past one is not — it proves the broker was still working after local
    state looked settled."""
    from ats.execution import order_disposition

    outcome = order_disposition.late_fill_disposition(
        {"order_id": "o1", "status": "submitted",
         "first_submitted_at": "2026-10-01T13:30:00+00:00", "symbol": "MU"},
        {"exec_id": "e1", "symbol": "MU", "side": "BOT", "shares": 10,
         "price": 100.0, "time": "2026-10-06T14:00:00+00:00", "order_id": "o1"})
    assert outcome.late is True
    assert outcome.stop_switch is True


def test_performance_rebuild_is_verified_when_a_period_is_supplied(chain_repo):
    repo, store, cycle_id, snapshot = chain_repo
    result = iv.verify_decision_chain(repo=repo, cycle_id=cycle_id,
                                      snapshot=snapshot, store=store,
                                      rebuild_period="2026-10")
    assert result.performance_rebuilt is True


def test_performance_rebuild_is_skipped_without_a_period(chain_repo):
    repo, store, cycle_id, snapshot = chain_repo
    result = iv.verify_decision_chain(repo=repo, cycle_id=cycle_id,
                                      snapshot=snapshot, store=store)
    assert result.performance_rebuilt is False


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

def test_the_report_counts_per_consumer_rather_than_saying_ok():
    records = [iv.scan_consumer_access(c) for c in iv.TEN_CONSUMERS]
    report = iv.render_report(records)

    assert "# 十角色接入核验" in report
    for consumer_id in iv.TEN_CONSUMERS:
        assert f"`{consumer_id}`" in report
    assert "否" in report, "a report that only says pass is the failure mode"


def test_the_report_states_that_isolation_is_not_ledger_integrity(tmp_path):
    records = [iv.scan_consumer_access("macro")]
    with iv.isolated_verification("report-attest", root=tmp_path / "isolated") as attestation:
        pass
    report = iv.render_report(records, attestation=attestation)
    assert "隔离证明不是生产账本完整性证明" in report


def test_the_digest_is_stable_across_identical_runs():
    """Two runs that verified the same thing must digest the same, or 'the
    evidence matches' becomes a claim nobody can check."""
    first = [iv.scan_consumer_access(c) for c in iv.TEN_CONSUMERS]
    second = [iv.scan_consumer_access(c) for c in iv.TEN_CONSUMERS]
    assert iv.verification_digest(first) == iv.verification_digest(second)


def test_the_digest_changes_when_a_finding_appears():
    clean = [iv.scan_consumer_access("macro")]
    dirty = [iv.scan_consumer_access("macro"),
             iv.AccessRecord(consumer_id="macro", entry_point="x",
                             call_path=("ats.data.products",))]
    assert iv.verification_digest(clean) != iv.verification_digest(dirty)


def test_a_role_the_scan_never_reached_is_called_out_in_the_report():
    record = iv.AccessRecord(consumer_id="ghost", entry_point="ats.nope")
    report = iv.render_report([record])
    assert "未到达入口" in report
