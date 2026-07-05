"""Chief 统一决策 — context assembly, no-llm stub, store round-trip, execute wiring
(hermetic; no network/LLM/TWS)."""

from datetime import datetime, timezone

from ats.agents.chief import assemble, decide
from ats.memory import get_store
from ats.schemas.decision import TradeDecision
from ats.schemas.macro_strategy import MacroReview, SectorTilt
from ats.schemas.pead import ExpectationSet, PeadDossier, Scorecard
from ats.schemas.risk import RiskReview
from ats.schemas.sector import CompanyCall, SectorReview

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


def test_assemble_gathers_all_blocks():
    _seed_store()
    ctx = assemble.build(live_broker=False)
    text = ctx.as_context()
    assert "新鲜可行动" in text and "+1.80" in text           # fresh score dossier
    assert "建议: buy COHR" in text                            # PEAD recommendation
    assert "增持 COHR" in text                                 # sector company_call
    assert "neutral 晚周期" in text                            # macro regime
    assert "normal" in text                                    # risk state


def test_assemble_derisk_prepends_hard_instruction():
    store = get_store()
    store.save_risk_review(RiskReview(as_of=NOW, risk_state="derisk"))
    ctx = assemble.build(live_broker=False)
    assert "只允许减仓" in ctx.as_context()


def test_decide_no_llm_zero_decisions():
    result = decide.run(use_llm=False, live_broker=False)
    assert result.decisions == [] and result.cycle_id.startswith("chief-")


def test_decide_llm_failure_degrades():
    import ats.agents.chief.decide as d

    def boom(*a, **k):
        raise RuntimeError("down")

    orig = d.run_structured
    d.run_structured = boom
    try:
        result = decide.run(use_llm=True, live_broker=False)
        assert result.decisions == [] and "fallback" in result.summary
    finally:
        d.run_structured = orig


def test_chief_run_store_roundtrip():
    store = get_store()
    store.save_chief_run(cycle_id="chief-20260705-1", as_of=NOW, summary="test run",
                         decisions=[TradeDecision(symbol="COHR", action="buy",
                                                  notional_usd=5000, rationale="r")])
    run = store.last_chief_run()
    assert run["cycle_id"] == "chief-20260705-1"
    assert run["decisions"][0]["symbol"] == "COHR"


def test_run_chief_cli_executes_when_decisions(monkeypatch):
    """run_chief -> decisions -> trader.execute called with source=chief."""
    from ats.agents.chief.decide import ChiefResult
    from ats.runtime import cli

    calls = {}
    monkeypatch.setattr("ats.agents.chief.decide.run", lambda **k: ChiefResult(
        cycle_id="chief-x", as_of=NOW, summary="s",
        decisions=[TradeDecision(symbol="COHR", action="buy", notional_usd=1000)]))
    monkeypatch.setattr("ats.trader.execute.execute",
                        lambda decisions, **kw: calls.update(kw, n=len(decisions)) or [])
    cli.run_chief(execute=True, dry_run=True, auto=True, offline=True)
    assert calls["source"] == "chief" and calls["n"] == 1
