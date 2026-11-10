"""Actual isolated research -> decision -> execution -> recovery acceptance."""
import json
from datetime import UTC, datetime, timedelta

import pytest
from test_phase_f_research_gate import business_inputs, research_isolation, decision_run
from test_phase_f_safe_reads import seed_performance
from test_phase_f_trader_a import quote

from ats.execution import broker_write_guard as guard
from ats.execution.clerk import clerk_run
from ats.execution.route_registry import install_route
from ats.execution.simulation import simulated_execution
from ats.graph import chief
from ats.graph.chief_state import ChiefDecisionState
from ats.runtime.cli import run_decision_graph
from ats.schemas.decision import BossApproval
from ats.schemas.portfolio import PortfolioSnapshot
from ats.workflow.consumer_reads import trace_reads, read_projection
from ats.agent.task_projection import ProjectionScope


@pytest.mark.parametrize("future_seconds", [0,60])
def test_broker_snapshot_generated_during_request_uses_receipt_clock(future_seconds):
    from ats.data.runtime.broker import portfolio_snapshot
    class Broker:
        def get_portfolio(self):
            return PortfolioSnapshot(as_of=datetime.now(UTC)+timedelta(seconds=future_seconds),
                                     net_liquidation=1e6,cash=1e6,daily_pnl=0,gross_exposure=0,positions=[])
    packet=portfolio_snapshot(Broker())
    assert packet["status"]==("unavailable" if future_seconds else "complete")


def test_actual_opinion_edges_and_published_lineage(business_inputs, isolated, record_property):
    import os, sqlite3, hashlib
    def fact_hashes():
        with sqlite3.connect(os.environ["ATS_DATA_DB_PATH"]) as conn:
            tables=[r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    if r[0].startswith("data_") and "processing" not in r[0]]
            return {t:hashlib.sha256(json.dumps(sorted(map(str,conn.execute('SELECT * FROM "'+t+'"')))).encode()).hexdigest()
                    for t in tables}
    original=fact_hashes()
    with trace_reads() as reads:
        result=decision_run(business_inputs, isolated)
    assert result.complete and result.decision_cycle_ready, result.as_dict()
    assert fact_hashes()==original
    allowed={("layer","sector"),("information","fundamental")}
    edges=[r for r in reads if r.get("api")=="task_projection" and r["status"]=="complete"
           and r["consumer"] in {"sector","fundamental"}]
    assert {(r["producer"],r["consumer"]) for r in edges}==allowed
    published=[e for o in result.outcomes for e in o.projections]
    by_id={e.projection_id:e for e in published}
    for r in edges:
        assert r["refs"] and r["content_hash"]==by_id[r["refs"][0]].content_hash
        children=[e for e in published if e.agent_role=={
            "sector":"sector_allocation", "fundamental":"fundamental_expectation_update"}[r["consumer"]]]
        assert any(r["refs"][0] in e.input_refs for e in children)
    with trace_reads() as refused, pytest.raises(PermissionError, match="undeclared"):
        read_projection(isolated,consumer="fundamental",role="macro_review",scope=ProjectionScope(kind="portfolio"))
    assert refused[0]["status"]=="blocked"
    record_property("actual_reads",json.dumps(reads))
    record_property("published",json.dumps([e.model_dump(mode="json") for e in published]))
    record_property("unchanged_shared_facts",json.dumps(original))


def test_candidate_record_cannot_become_published_information(business_inputs, isolated, record_property):
    from ats.data.admission import CandidateDocument, admit
    from ats.data.stores.unstructured import get_platform_unstructured_store
    from ats.agents.information import assemble
    from ats.workflow.runtime_reads import bind_read
    from test_phase_f_safe_reads import scope
    repo=get_platform_unstructured_store()
    candidate=CandidateDocument(expected_entity="COHR",claimed_entity="NVDA",
        target_period="2026Q3",claimed_period="2026Q3",
        expected_semantic="article",claimed_semantic="article", text="Unreviewed rival guidance "*100,
        source="ibkr_news",source_url="fixture:quarantined-rival",external_id="quarantined-rival",
        published_at=(datetime.now(UTC)-timedelta(seconds=1)).isoformat(),completeness="full",min_chars=1)
    outcome=admit(candidate,store=repo)
    assert outcome.validation.status=="quarantined"
    saved=repo.document_candidates(status="quarantined")
    assert any(r["candidate_id"]==candidate.candidate_id for r in saved)
    with bind_read(scope("information",entity=True)):
        rows=assemble.admitted_events(isolated,"COHR",cutoff=datetime.now(UTC)-timedelta(days=1))
    assert rows and all(r["publication_id"] and r["version_id"] for r in rows)
    assert all(r["source_url"]!=candidate.source_url for r in rows)
    record_property("actual_candidate",json.dumps(saved,default=str))
    record_property("admitted_inputs",json.dumps(rows))
    repo.close()
