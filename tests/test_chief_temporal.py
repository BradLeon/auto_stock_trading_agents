"""Chief temporal reasoning — a PEAD score is actionable ONCE (consumed after), and the
chief sees per-symbol recent decisions + execution status (hermetic; no network/LLM/TWS)."""

from datetime import datetime, timezone

from ats.agents.chief import assemble
from ats.memory import get_store
from ats.schemas.decision import TradeDecision
from ats.schemas.memory import TradeLogEntry
from ats.schemas.pead import ExpectationSet, PeadDossier, Scorecard

NOW = datetime.now(timezone.utc)


def _seed_fresh_score(store, sym="COHR"):
    from ats.config import load_pead_config

    label = load_pead_config(sym).fiscal_label
    store.save_dossier(PeadDossier(
        symbol=sym, fiscal_label=label, phase="score", updated_at=NOW,
        expectation_set=ExpectationSet(symbol=sym, fiscal_label=label, as_of=NOW, narrative="thesis"),
        scorecard=Scorecard(symbol=sym, as_of=NOW, lines=[], total=0.5, threshold=1.2, band="未达门槛"),
        decision_summary="未达门槛+持仓→分步减仓 24股"))
    return label


def _pin_targets(monkeypatch, syms=("COHR",)):
    import ats.config as cfg

    real = cfg.load_pead_global

    def patched():
        g = dict(real())
        g["targets"] = list(syms)
        return g

    monkeypatch.setattr(cfg, "load_pead_global", patched)


def test_score_actionable_once_then_consumed(monkeypatch):
    _pin_targets(monkeypatch)
    store = get_store()
    label = _seed_fresh_score(store)

    # 1st cycle: fresh + unconsumed -> actionable, the 分步减仓 instruction is shown
    ctx = assemble.ChiefContext(as_of=NOW)
    block = assemble._pead_block(set(), ctx)
    assert "新鲜可行动" in block and "分步减仓" in block
    assert ("COHR", label) in ctx.actionable_scores

    # the chief responds -> consume it
    store.mark_score_consumed("COHR", label, "chief-test")
    assert store.is_score_consumed("COHR", label)

    # 2nd cycle: consumed -> background only, no actionable, the 分步减仓 line is gone
    ctx2 = assemble.ChiefContext(as_of=NOW)
    block2 = assemble._pead_block(set(), ctx2)
    assert "已消费" in block2 and "分步减仓" not in block2
    assert ("COHR", label) not in ctx2.actionable_scores


def test_consumption_idempotent_and_per_earnings():
    store = get_store()
    store.mark_score_consumed("GOOG", "Q2 2026", "c1")
    store.mark_score_consumed("GOOG", "Q2 2026", "c2")     # idempotent
    assert store.is_score_consumed("GOOG", "Q2 2026")
    assert not store.is_score_consumed("GOOG", "Q3 2026")  # a new earnings is actionable again


def test_recent_actions_shows_execution_status():
    store = get_store()
    cid = "chief-20260724-100000"
    store.save_chief_run(cycle_id=cid, as_of=NOW, summary="", decisions=[
        TradeDecision(symbol="GOOG", action="trim", notional_usd=4000, rationale="r"),
        TradeDecision(symbol="ASML", action="trim", notional_usd=1500, rationale="r")])
    store.save_trades(
        [TradeLogEntry(order_id="1", cycle_id=cid, symbol="GOOG", action="trim", qty=13,
                       status="filled", submitted_at=NOW, rationale="r")],
        cycle_id=cid, source="chief", context="")

    block = assemble._recent_actions_block()
    assert "GOOG" in block and "已成交" in block          # decision + filled trade
    assert "ASML" in block and "待处理" in block          # decision, no trade → pending
