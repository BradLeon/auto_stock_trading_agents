"""PEAD graph wiring — prep + score phases end-to-end (offline, no-llm, hermetic)."""

from datetime import datetime, timezone

from ats.graph.checkpoint import get_checkpointer
from ats.graph.pead import build_pead_graph
from ats.graph.pead_state import PeadState
from ats.schemas.decision import BossApproval

NOW = datetime.now(timezone.utc)


def _run(phase, **extra):
    app = build_pead_graph(checkpointer=get_checkpointer(persist=False))
    state = PeadState(symbol="COHR", phase=phase, as_of=NOW, dry_run=True,
                      use_llm=False, use_broker=False, live_data=False, **extra)
    cfg = {"configurable": {"thread_id": f"t-{phase}"}}
    return app, state, cfg


def test_prep_phase_persists_dossier():
    app, state, cfg = _run("prep")
    result = app.invoke(state, config=cfg)
    assert "__interrupt__" not in result          # prep never asks for approval
    assert result["expectation_set"] is not None
    assert result["config"].symbol == "COHR"

    from ats.memory import get_store

    d = get_store().get_dossier("COHR", result["fiscal_label"])
    assert d is not None and d.phase == "prep"


def test_prep_continues_accumulated_monitor_narrative():
    """prep must NOT reset to the seed — it inherits the monitor's living narrative."""
    from ats.config import load_pead_config
    from ats.memory import get_store
    from ats.schemas.pead import ExpectationSet, PeadDossier

    cfg = load_pead_config("COHR")
    accumulated = ("core thesis\n\n[update 2026-07-02] Meta excess-compute admission — demand risk"
                   "\n  · [hyperscaler_capex_demand] downgrade conviction")
    get_store().save_dossier(PeadDossier(
        symbol="COHR", fiscal_label=cfg.fiscal_label, phase="prep", updated_at=NOW,
        expectation_set=ExpectationSet(symbol="COHR", fiscal_label=cfg.fiscal_label,
                                       as_of=NOW, narrative=accumulated)))

    app, state, cfgg = _run("prep")
    result = app.invoke(state, config=cfgg)
    # Offline prep carries the accumulated narrative forward instead of the seed.
    assert "Meta excess-compute admission" in result["expectation_set"].narrative
    assert result["expectation_set"].narrative != cfg.narrative_seed


def test_score_decision_does_not_trim_unrelated_holdings():
    # A single-name PEAD decision must not force-trim other portfolio names that
    # happen to be over the position cap (e.g. a cash-parked SHV).
    from ats.config import load_pead_config
    from ats.graph import pead
    from ats.graph.pead_state import PeadState
    from ats.schemas.pead import Scorecard
    from ats.schemas.portfolio import Position, PortfolioSnapshot

    pf = PortfolioSnapshot(as_of=NOW, net_liquidation=100000, positions=[
        Position(symbol="SHV", qty=900, avg_cost=110, market_price=110,
                 market_value=99000, weight=0.99)])  # 99% in SHV -> over cap
    state = PeadState(symbol="COHR", phase="score", as_of=NOW, use_broker=False,
                      config=load_pead_config("COHR"), portfolio=pf,
                      scorecard=Scorecard(symbol="COHR", as_of=NOW, total=0.33, threshold=1.5,
                                          lines=[], band="中性观望"))
    out = pead.score_decision(state)
    assert all(d.symbol != "SHV" for d in out["decisions"])   # no leaked SHV trim


def test_score_phase_completes_without_interrupt():
    """v0.2: score produces a recommendation dossier; the Chief makes the trade call."""
    app, state, cfg = _run("score")
    result = app.invoke(state, config=cfg)
    assert "__interrupt__" not in result           # no HITL pause in the score branch anymore
    # no-llm => zero scorecard => no trade recommended, but completes cleanly.
    assert result["scorecard"].total == 0.0

    from ats.memory import get_store

    d = get_store().get_dossier("COHR", result["fiscal_label"])
    assert d is not None and d.phase == "score"
    assert result.get("decision_band", "") in d.decision_summary   # 建议入档
