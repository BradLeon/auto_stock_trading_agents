"""Chief 统一决策 — context assembly, no-llm stub, store round-trip, execute wiring
(hermetic; no network/LLM/TWS)."""

from datetime import datetime, timezone

from ats.agents.chief import assemble, decide
from ats.memory import get_store
from ats.schemas.decision import TradeDecision
from ats.schemas.macro_strategy import MacroReview, SectorTilt
from ats.schemas.pead import ExpectationSet, PeadDossier, Scorecard
from ats.schemas.risk import RiskDirective, RiskReview
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


def test_assemble_gathers_all_blocks(monkeypatch):
    # Pin the PEAD target universe so operational config/pead.yaml changes don't
    # break this test (it seeds + asserts on a COHR dossier).
    import ats.config as _config

    real = _config.load_pead_global
    monkeypatch.setattr(_config, "load_pead_global",
                        lambda: {**real(), "targets": ["COHR"]})
    _seed_store()
    ctx = assemble.build(live_broker=False)
    text = ctx.as_context()
    assert "新鲜可行动" in text and "+1.80" in text           # fresh score dossier
    assert "建议: buy COHR" in text                            # PEAD recommendation
    assert "增持 COHR" in text                                 # sector company_call
    assert "neutral 晚周期" in text                            # macro regime
    assert "normal" in text                                    # risk state


def test_sector_block_renders_layer_boom_scores():
    """Per-layer 景气 must reach the chief.

    Regression: the block read a `layer_views` attribute SectorReview never had, so
    the loop silently never ran — the weekly engine scored all six chain layers and
    the chief saw none of it.
    """
    from ats.schemas.sector import LayerAssessment

    get_store().save_sector_review(SectorReview(
        sector="ai_hardware", as_of=NOW, regime="L5 是瓶颈",
        layers=[LayerAssessment(key="L5_fab", label="L5 芯片制造", boom_score=82.0,
                                signal="bullish", supply_demand="紧张：HBM 仍是最大瓶颈")]))
    text = assemble._sector_block(set())
    assert "L5 芯片制造" in text and "景气82" in text
    assert "bullish" in text and "HBM 仍是最大瓶颈" in text


def test_pead_block_renders_signal_chain_summary():
    """Cross-ticker read-through belongs on the decision desk, not only in Obsidian."""
    import ats.config as _config

    real = _config.load_pead_global
    _config.load_pead_global = lambda: {**real(), "targets": ["COHR"]}
    try:
        cfg = _config.load_pead_config("COHR")
        get_store().save_dossier(PeadDossier(
            symbol="COHR", fiscal_label=cfg.fiscal_label, phase="prep", updated_at=NOW,
            signal_chain_summary="上游 TSM CoWoS 产能扩张，对本标的供给上限构成支持"))
        assert "信号链: 上游 TSM CoWoS 产能扩张" in assemble._pead_block(set(), None)
    finally:
        _config.load_pead_global = real


def test_assemble_derisk_prepends_hard_instruction():
    store = get_store()
    store.save_risk_review(RiskReview(as_of=NOW, risk_state="derisk"))
    ctx = assemble.build(live_broker=False)
    assert "只允许减仓" in ctx.as_context()


def test_assemble_exposes_compact_risk_directive():
    store = get_store()
    store.save_risk_review(RiskReview(
        as_of=NOW, risk_state="caution",
        directive=RiskDirective(
            state="REPAIR_ONLY", can_increase_risk=False,
            allowed_actions=["reduce", "hedge_if_verified_improving"],
            blocked_entities=["SK_HYNIX"], blocked_layers=["L5_fab"],
            required_repairs=["L5制造层 32%→30%"])))

    text = assemble.build(live_broker=False).as_context()

    assert "RiskDirective=REPAIR_ONLY" in text
    assert "可增加风险=False" in text
    assert "SK_HYNIX" in text and "L5_fab" in text


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
    """run_chief -> decision graph: chief run persisted + dry-run trade log with source=chief."""
    from ats.agents.chief.decide import ChiefResult
    from ats.runtime import cli

    monkeypatch.setattr(
        "ats.agents.chief.decide.from_context",
        lambda text, *, cycle_id, as_of, use_llm=True: ChiefResult(
            cycle_id=cycle_id, as_of=as_of, summary="s",
            decisions=[TradeDecision(symbol="COHR", action="buy", notional_usd=1000)]))
    monkeypatch.setattr("ats.trader.execute._last_price", lambda s: 100.0)
    cli.run_chief(execute=True, dry_run=True, auto=True, offline=True)
    run = get_store().last_chief_run()
    assert run["decisions"][0]["symbol"] == "COHR"
    row = get_store().recent_trades("COHR")[0]
    assert row["source"] == "chief" and row["status"] == "cancelled"
