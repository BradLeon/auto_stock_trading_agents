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


def test_prep_after_score_does_not_discard_the_score():
    """Found via KLAC 2026-07-29: a same-day-amc routing bug meant prep never ran
    before the print, so a post-hoc `pead prep KLAC` backfill was needed AFTER score
    had already persisted actuals/scorecard/decision_summary. prep_persist rebuilt
    the dossier from scratch and silently wiped all three. This is the regression
    test for the fix — prep run after score must preserve the score, not erase it."""
    from ats.memory import get_store

    score_app, score_state, score_cfg = _run("score")
    score_result = score_app.invoke(score_state, config=score_cfg)
    fiscal_label = score_result["fiscal_label"]
    scored = get_store().get_dossier("COHR", fiscal_label)
    assert scored.phase == "score" and scored.actuals is not None

    prep_app, prep_state, prep_cfg = _run("prep")
    prep_app.invoke(prep_state, config=prep_cfg)

    after_prep = get_store().get_dossier("COHR", fiscal_label)
    assert after_prep.phase == "score"                        # not regressed to "prep"
    assert after_prep.actuals is not None                     # not wiped
    assert after_prep.decision_summary == scored.decision_summary
    assert after_prep.expectation_set is not None              # prep content DID land


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


def test_platform_score_reads_event_bound_documents_without_legacy_fetch(monkeypatch):
    """Platform PEAD scores immutable documents; it must not silently re-scrape them."""
    from ats.data.products.unstructured import EarningsDocument, EarningsDocumentPackage
    from ats.graph import pead
    from ats.schemas.fundamentals import FundamentalData

    transcript = EarningsDocument(
        role="earnings_transcript", document_id="NVDA:Q2 FY2027:earnings_transcript",
        version_id="transcript-v1", source="defeatbeta", source_url="", published_at="",
        title="NVDA Q2 FY2027 transcript", text="FULL TRANSCRIPT",
    )
    release = EarningsDocument(
        role="earnings_release", document_id="NVDA:Q2 FY2027:company_release",
        version_id="release-v1", source="sec", source_url="https://sec/release",
        published_at="2026-08-26", title="NVDA release", text="FULL RELEASE",
    )
    package = EarningsDocumentPackage(
        entity="NVDA", period="Q2 FY2027", documents=(release, transcript),
        repository="PlatformUnstructuredRepository")
    state = PeadState(symbol="NVDA", phase="score", as_of=NOW, live_data=True,
                      use_broker=False, config=__import__("ats.config", fromlist=["load_pead_config"]).load_pead_config("NVDA"))
    state.earnings_date = "2026-11-17"  # stale next-event date carried from prep
    monkeypatch.setenv("ATS_STRUCTURED_PEAD_GRAPH_MODE", "platform")
    monkeypatch.setattr("ats.data.products.unstructured.platform_earnings_document_package",
                        lambda **_kwargs: package)
    monkeypatch.setattr("ats.data.fundamentals.fetch", lambda *_args, **_kwargs: FundamentalData(symbol="NVDA", as_of=NOW))
    monkeypatch.setattr("ats.data.transcript.fetch", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy transcript fetch")))
    monkeypatch.setattr("ats.data.transcript.extract_body", lambda text, _src: (text, ""))
    monkeypatch.setattr("ats.data.transcript.looks_like_transcript", lambda _text: (True, "ok"))
    monkeypatch.setattr("ats.data.fiscal.verify_transcript", lambda *_args: (True, "period ok"))

    out = pead.score_fetch(state)

    assert out["transcript_text"] == "FULL TRANSCRIPT"
    assert out["documents_text"] == "### NVDA release\nFULL RELEASE"
    assert out["transcript_resolved_source"].startswith("platform:defeatbeta:transcript-v1")
    assert [row["document_id"] for row in out["document_lineage"]] == [
        "NVDA:Q2 FY2027:company_release", "NVDA:Q2 FY2027:earnings_transcript"]
    assert out["earnings_date"] == "2026-08-26"


def test_cli_score_report_uses_recommendation_hint_fields(capsys):
    from ats.runtime.cli import _pead_report
    from ats.schemas.pead import PeadRecommendation

    _pead_report("NVDA", "score", {
        "decisions": [PeadRecommendation(symbol="NVDA", action="BUY", notional_hint=12_000)],
        "decision_band": "test",
    })

    assert "建议 BUY NVDA $12,000" in capsys.readouterr().out
