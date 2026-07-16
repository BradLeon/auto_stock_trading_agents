"""Cross-ticker signal-chain read-through: a peer's scored dossier (guidance /
capacity + decision) must flow into the target's prep signal-chain analysis.

Motivating case: TSM's CoWoS capacity read matters greatly for NVDA's supply
thesis, so once TSM is scored, NVDA's prep must see reported=True + TSM guidance.
"""

from datetime import datetime, timezone

from ats.agents.pead.prep import _peer_line
from ats.graph.pead import _peer_report
from ats.schemas.pead import Actuals, PeadDossier, Scorecard

NOW = datetime.now(timezone.utc)


def _seed_scored_tsm():
    from ats.memory import get_store

    get_store().save_dossier(PeadDossier(
        symbol="TSM", fiscal_label="Q FY2026", phase="score", updated_at=NOW,
        actuals=Actuals(symbol="TSM", fiscal_label="Q FY2026", as_of=NOW,
                        guidance="次季营收指引中枢上修；CoWoS 产能翻倍、交期缩短，AI 加速需求能见度延伸至 2027"),
        scorecard=Scorecard(symbol="TSM", as_of=NOW, total=1.6, threshold=1.2, band="强劲超预期"),
        decision_summary="总分 1.6 越过门槛，链条净支持，建议做多 read-through 标的"))


def test_peer_report_surfaces_scored_dossier():
    _seed_scored_tsm()
    rep = _peer_report("TSM")
    assert rep["reported"] is True
    assert rep["peer_fiscal"] == "Q FY2026"
    assert rep["peer_band"] == "强劲超预期"
    assert "CoWoS 产能翻倍" in rep["peer_guidance"]
    assert "净支持" in rep["peer_decision"]


def test_peer_report_empty_when_only_prep_exists():
    from ats.memory import get_store

    # A prep-phase dossier is NOT a reported fundamental read -> must stay reported=False.
    get_store().save_dossier(PeadDossier(
        symbol="ASML", fiscal_label="Q FY2026", phase="prep", updated_at=NOW))
    assert _peer_report("ASML") == {}
    assert _peer_report("UNSEEN") == {}


def test_peer_line_renders_reported_guidance():
    _seed_scored_tsm()
    row = {"symbol": "TSM", "role": "upstream", "price_chg_pct": 3.1,
           "earnings_date": None, **_peer_report("TSM")}
    line = _peer_line(row)
    assert "【已发布财报 Q FY2026" in line
    assert "band=强劲超预期" in line
    assert "CoWoS 产能翻倍" in line


def test_peer_line_plain_when_not_reported():
    row = {"symbol": "AMD", "role": "peer", "price_chg_pct": -1.2,
           "earnings_date": None, "reported": False}
    line = _peer_line(row)
    assert "已发布财报" not in line
    assert "reported=False" in line
