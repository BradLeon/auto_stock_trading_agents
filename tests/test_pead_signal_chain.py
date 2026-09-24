"""Cross-ticker signal-chain read-through (Phase D task 5.10).

跨标的信号只以中性事实进入：已报实际值取自 fundamentals 数据产品，近期事实
变化取自该标的的 InformationBrief 投影。上游评审结论（decision_summary）、
Scorecard 分档与自由文本指引是另一个标的的基本面观点，一律不读。

Motivating case: TSM's CoWoS capacity read matters greatly for NVDA's supply
thesis — but what crosses the boundary is TSM's reported EPS/revenue and its
InformationBrief fact list, not TSM's scored verdict.
"""

from datetime import datetime, timedelta, timezone

from ats.agents.pead.prep import _peer_line
from ats.agent.task_projection import ProjectionScope, build_envelope
from ats.data.fundamentals import (FinancialStatements, FundamentalData,
                                   StatementMetric)
from ats.graph.pead import _peer_report
from ats.schemas.pead import PeadDossier

NOW = datetime.now(timezone.utc)


def _fund(symbol, *, period="2026-06-30", with_lines=True):
    lines = []
    if with_lines:
        lines = [
            StatementMetric(label="Diluted EPS", value=7.58, yoy=28.5, unit="$"),
            StatementMetric(label="Revenue", value=9326.0, yoy=21.3, unit="$M"),
        ]
    return FundamentalData(
        symbol=symbol, as_of=NOW,
        statements=FinancialStatements(period=period, lines=lines))


def _seed_envelope(store, symbol, fact_changes):
    env = build_envelope(
        role="information_brief",
        payload={"entity": symbol, "headline": "上游订单走强", "summary": "指引上修",
                 "relevance": "high", "sources": ["rss:test"],
                 "fact_changes": fact_changes, "impact_candidates": ["demand"],
                 "entities": [symbol], "confidence": 0.7, "freshness": "today",
                 "unverified": []},
        scope=ProjectionScope(kind="entity", id=symbol),
        as_of=NOW.isoformat(timespec="seconds"),
        valid_until=(NOW + timedelta(hours=48)).isoformat(timespec="seconds"),
        data_vintage_refs=["dataset@2026-09-23"])
    store.save_task_projection_envelope(env)


def test_peer_report_surfaces_neutral_reported_facts(monkeypatch):
    """已报实际值来自 fundamentals 数据产品；上游 dossier 的观点字段一律缺席。"""
    from ats.memory import get_store

    monkeypatch.setattr("ats.data.fundamentals.fetch",
                        lambda symbol, **k: _fund(symbol))
    _seed_envelope(get_store(), "TSM", ["CoWoS 产能翻倍、交期缩短"])

    rep = _peer_report("TSM")
    assert rep["reported"] is True
    assert rep["peer_fiscal"] == "2026-06-30"
    assert rep["peer_reported_eps"] == 7.58 and rep["peer_reported_eps_yoy"] == 28.5
    assert rep["peer_reported_revenue"] == 9326.0
    assert rep["peer_brief_facts"] == ["CoWoS 产能翻倍、交期缩短"]
    # 上游观点不得跨标的进入：band / 指引 / 决策摘要没有读取入口。
    assert "peer_band" not in rep
    assert "peer_guidance" not in rep
    assert "peer_decision" not in rep


def test_peer_report_stays_unreported_without_neutral_data(monkeypatch):
    """数据产品不可得 + 无简报 → reported=False（无论该标的有无 prep dossier）。"""
    from ats.memory import get_store

    def _boom(symbol, **k):
        raise RuntimeError("data product unavailable")

    monkeypatch.setattr("ats.data.fundamentals.fetch", _boom)
    # prep 阶段的 dossier 不是已发布的基本面读取，不构成信号链输入。
    get_store().save_dossier(PeadDossier(
        symbol="ASML", fiscal_label="Q FY2026", phase="prep", updated_at=NOW))
    assert _peer_report("ASML") == {"reported": False}
    assert _peer_report("UNSEEN") == {"reported": False}


def test_peer_report_ignores_dossier_opinion_even_when_data_exists(monkeypatch):
    """fundamentals 与 dossier 同时存在：报告仍只含中性事实。"""
    from ats.memory import get_store

    monkeypatch.setattr("ats.data.fundamentals.fetch",
                        lambda symbol, **k: _fund(symbol))
    get_store().save_dossier(PeadDossier(
        symbol="TSM", fiscal_label="Q FY2026", phase="score", updated_at=NOW,
        decision_summary="总分 1.6 越过门槛，链条净支持，建议做多 read-through 标的"))
    rep = _peer_report("TSM")
    assert rep["reported"] is True and "peer_decision" not in rep


def test_peer_line_renders_reported_facts_and_brief_only():
    row = {"symbol": "TSM", "role": "upstream", "price_chg_pct": 3.1,
           "earnings_date": None, "reported": True, "peer_fiscal": "2026-06-30",
           "peer_reported_eps": 7.58, "peer_reported_eps_yoy": 28.5,
           "peer_reported_revenue": 9326.0, "peer_reported_revenue_yoy": 21.3,
           "peer_brief_facts": ["CoWoS 产能翻倍"]}
    line = _peer_line(row)
    assert "【已发布财报 2026-06-30】" in line
    assert "已报 EPS: 7.58" in line
    assert "已报营收: 9326.0" in line
    assert "简报事实: CoWoS 产能翻倍" in line
    # 观点字段不渲染
    assert "band=" not in line and "决策" not in line


def test_peer_line_plain_when_not_reported():
    row = {"symbol": "AMD", "role": "peer", "price_chg_pct": -1.2,
           "earnings_date": None, "reported": False}
    line = _peer_line(row)
    assert "已发布财报" not in line
    assert "reported=False" in line
