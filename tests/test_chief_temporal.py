"""Score consumption bookkeeping: a PEAD score is marked consumed once per earnings (hermetic).

The Chief context no longer renders the PEAD dossier or the recent-actions block (task 2.1); what it
shows of recent decisions and their execution status is covered in test_chief_context.py."""

from datetime import datetime, timezone

from ats.memory import get_store
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




def test_consumption_idempotent_and_per_earnings():
    store = get_store()
    store.mark_score_consumed("GOOG", "Q2 2026", "c1")
    store.mark_score_consumed("GOOG", "Q2 2026", "c2")     # idempotent
    assert store.is_score_consumed("GOOG", "Q2 2026")
    assert not store.is_score_consumed("GOOG", "Q3 2026")  # a new earnings is actionable again




