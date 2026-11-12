"""Chief 统一决策 — context assembly, no-llm stub, store round-trip, execute wiring
(hermetic; no network/LLM/TWS)."""

import pytest
from datetime import datetime, timedelta, timezone


from ats.agents.chief import decide
from ats.memory import get_store
from ats.schemas.decision import TradeDecision
from ats.schemas.macro_strategy import MacroReview, SectorTilt
from ats.schemas.pead import ExpectationSet, PeadDossier, Scorecard
from ats.schemas.risk import RiskReview
from ats.schemas.sector import CompanyCall, SectorReview

pytestmark = pytest.mark.usefixtures("open_decision_read_gate")


NOW = datetime.now(timezone.utc)


def _seed_store():
    from ats.config import load_pead_config

    store = get_store()
    cfg = load_pead_config("COHR")
    store.save_dossier(PeadDossier(
        symbol="COHR", fiscal_label=cfg.fiscal_label, phase="score", updated_at=NOW,
        expectation_set=ExpectationSet(symbol="COHR", fiscal_label=cfg.fiscal_label,
                                       as_of=NOW, narrative="thesis narrative tail"),
        scorecard=Scorecard(symbol="COHR", as_of=NOW, lines=[], total=1.8, threshold=1.5,
                            band="超预期"),
        decision_summary="超预期 | 建议: buy COHR $10,000"))
    store.save_sector_review(SectorReview(
        sector="ai_hardware", as_of=NOW, regime="L5 是瓶颈",
        company_calls=[CompanyCall(symbol="COHR", layer="L3", stance="增持",
                                   conviction=0.6, rationale="财报超预期")]))
    store.save_macro_review(MacroReview(
        name="macro", as_of=NOW, regime="neutral 晚周期", rate_path="维持",
        sector_tilts=[SectorTilt(sector="半导体", stance="中性")]))
    store.save_risk_review(RiskReview(as_of=NOW, risk_state="normal"))
    return store












def test_decide_no_llm_zero_decisions():
    result = decide.from_context("ctx", cycle_id="chief-test", as_of=NOW, use_llm=False)
    assert result.decisions == [] and result.cycle_id.startswith("chief-")


def test_decide_llm_failure_degrades():
    import ats.agents.chief.decide as d

    def boom(*a, **k):
        raise RuntimeError("down")

    orig = d.run_structured
    d.run_structured = boom
    try:
        result = decide.from_context("ctx", cycle_id="chief-test", as_of=NOW, use_llm=True)
        assert result.decisions == [] and "fallback" in result.summary
    finally:
        d.run_structured = orig


def test_chief_run_store_roundtrip():
    """`last_chief_run` reads the decision audit: the cycle's current revision and its summary."""
    from ats.decision.repository import DecisionAuditRepository

    store = get_store()
    repo = DecisionAuditRepository(store)
    repo.create_cycle(cycle_id="chief-20260705-1", trigger_source="manual")
    repo.append_revision(cycle_id="chief-20260705-1", rationale="test run",
                         orders=[{"symbol": "COHR", "action": "buy", "notional_usd": 5000,
                                  "rationale": "r"}])
    run = store.last_chief_run()
    assert run["cycle_id"] == "chief-20260705-1" and run["manager_summary"] == "test run"
    assert run["decisions"][0]["symbol"] == "COHR"


def test_run_chief_cli_executes_when_decisions(monkeypatch, tmp_path, broker):
    """run_chief -> decision graph: chief run persisted + dry-run trade log with source=chief.

    The decide path requires a complete research snapshot whose projections carry the
    frozen lineage (see tests/phase_d_frozen_harness.py), so the test publishes one
    projection per category/scope of a pinned one-name universe. It runs inside the
    isolated environment the `broker` fixture enters, as the other decision-path tests do:
    a production-mode run needs an authorized read route, which a hermetic test does not have.
    """
    from phase_d_frozen_harness import freeze_config, publish_with_lineage

    from ats.agents.chief.decide import ChiefResult
    from ats.agent.task_projection import ProjectionScope, build_envelope
    from ats.graph.chief_state import ChiefDecisionState
    from ats.runtime import cli

    freeze_config(tmp_path, monkeypatch)
    now = datetime.now(timezone.utc) - timedelta(seconds=2)
    valid = (now + timedelta(hours=48)).isoformat(timespec="seconds")
    rows = {}

    def publish(name, role, payload, scope):
        rows[name] = build_envelope(
            role=role, payload=payload, scope=scope,
            as_of=now.isoformat(timespec="seconds"), valid_until=valid,
            data_vintage_refs=["dataset@test"])

    for key in ("L1_app", "L3_optics"):
        publish(f"layer:{key}", "layer_analysis",
                {"layer": key, "status": "steady", "summary": "持平",
                 "findings": ["读数平稳"], "confidence": 0.5},
                ProjectionScope(kind="layer", id=key))
    publish("brief", "information_brief",
            {"entity": "COHR", "headline": "无重大变化", "summary": "例行简报",
             "relevance": "medium", "sources": ["rss:test"],
             "fact_changes": ["无"], "impact_candidates": ["none"],
             "entities": ["COHR"], "confidence": 0.4, "freshness": "today",
             "unverified": []},
            ProjectionScope(kind="entity", id="COHR"))
    publish("fund", "fundamental_expectation_update",
            {"entity": "COHR", "metric": "gross_margin", "period": "Q3 FY2026",
             "previous_value": 0.25, "new_value": 0.26, "driver": "例行读数"},
            ProjectionScope(kind="entity", id="COHR"))
    publish("sector", "sector_allocation",
            {"sector": "ai_hardware", "stance": "neutral", "target_weight": 0.3,
             "rationale": "标配", "drivers": []},
            ProjectionScope(kind="sector", id="ai_hardware"))
    publish("macro", "macro_review",
            {"regime": "transition", "summary": "利率维持", "indicators": ["10Y"]},
            ProjectionScope(kind="portfolio"))
    publish("tech", "technical_review",
            {"entity": "COHR", "signal": "neutral", "summary": "区间", "levels": {}},
            ProjectionScope(kind="entity", id="COHR"))
    publish_with_lineage(get_store(), rows, ChiefDecisionState(
        cycle_id="chief-cli", as_of=now, source="chief", decide=True,
        fundamental_mode="routine", use_llm=False, use_broker=False))

    monkeypatch.setattr(
        "ats.agents.chief.decide.from_context",
        lambda text, *, cycle_id, as_of, use_llm=True: ChiefResult(
            cycle_id=cycle_id, as_of=as_of, summary="s",
            decisions=[TradeDecision(symbol="COHR", action="buy", notional_usd=1000)]))
    monkeypatch.setattr("ats.trader.execute._last_price", lambda s: 100.0)
    # The decision path sizes and prices orders from a portfolio snapshot and refuses without
    # one ("portfolio_snapshot_unavailable"), so the run is not offline; the `broker` fixture
    # supplies a fresh snapshot and a no-network transport.
    cli.run_chief(execute=True, dry_run=True, auto=True, offline=False)
    run = get_store().last_chief_run()
    assert run["decisions"][0]["symbol"] == "COHR"
    row = get_store().recent_trades("COHR")[0]
    assert row["source"] == "chief" and row["status"] == "cancelled"
