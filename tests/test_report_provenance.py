"""report-source-timestamps: citations carry code-resolved information times."""

from __future__ import annotations

from datetime import datetime, timezone

from ats.agents import provenance as pv

AT = datetime(2026, 11, 18, 14, 45, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# information time
# --------------------------------------------------------------------------- #
def test_info_time_prefers_publish_then_fetch_never_observed_at():
    obs = {"id": "o1", "document_id": "d1", "period": "2022-2028",
           "observed_at": "2026-11-18T09:00:00+00:00"}
    published = pv.info_time(obs, {"published_at": "2026-11-12T13:00:00+00:00",
                                   "fetched_at": "2026-11-12T22:30:00+00:00", "period": "DJ-N-1"})
    assert (published.kind, published.at.day) == ("发布", 12)
    fetched = pv.info_time(obs, {"published_at": "", "fetched_at": "2026-11-03T22:04:50+00:00",
                                 "period": "Q3 FY2026"})
    assert fetched.kind == "采集" and "发布时间未登记" in fetched.text()
    # an article's period is a forecast horizon, not a date: never shown as data period
    assert fetched.period == ""
    assert "11-18" not in published.text() + fetched.text()


def test_info_time_without_document_uses_date_in_id_then_period():
    in_id = pv.info_time({"document_id": "AMD:20260804", "period": "FY26Q2"}, None)
    assert in_id.kind == "文档日期" and in_id.at == datetime(2026, 8, 4, tzinfo=timezone.utc)
    series = pv.info_time({"document_id": "l1_x:2026-11_y", "period": "2026-11",
                           "observed_at": "2026-11-18T09:30:00+00:00"}, None)
    assert series.at is None and series.text() == "数据期 2026-11"
    unknown = pv.info_time({"document_id": "x", "observed_at": "2026-11-18T09:30:00+00:00"}, None)
    assert unknown.text() == "信息时间未知"


def test_series_document_shows_data_period_beside_fetch_time():
    t = pv.info_time({"document_id": "s"}, {"fetched_at": "2026-11-16T08:00:00+00:00",
                                           "period": "2026-04"})
    assert t.text() == "采集 11-16 08:00（发布时间未登记） · 数据期 2026-04"


class _Reader:
    def __init__(self, obs, docs):
        self.obs, self.docs = obs, docs

    def observations_by_id(self, ids):
        return {i: self.obs[i] for i in ids if i in self.obs}

    def documents_by_id(self, ids):
        return {i: self.docs[i] for i in ids if i in self.docs}


def test_stamp_cell_shows_newest_document_and_count():
    reader = _Reader(
        {"a": {"id": "a", "document_id": "d1"}, "b": {"id": "b", "document_id": "d2"},
         "c": {"id": "c", "document_id": "d2"}},
        {"d1": {"entity": "COHR", "doc_type": "release", "period": "Q1 FY2027",
                "published_at": "2026-11-04T21:00:00+00:00"},
         "d2": {"entity": "ORCL", "doc_type": "company_release", "period": "Q2 FY2027",
                "published_at": "2026-11-12T21:00:00+00:00"}})
    stamps = pv.observation_sources(["a", "b", "c"], reader)
    cell = pv.stamp_cell(["a", "b", "c"], stamps)
    assert cell.startswith("ORCL 公司公告 Q2 FY2027 · 发布 11-12 21:00") and cell.endswith("（等 2 份）")
    assert pv.stamp_cell(["zzz"], stamps) == "—（未找到来源）"
    assert pv.stamp_cell(["a"], None) == "—"


# --------------------------------------------------------------------------- #
# projections and vintages
# --------------------------------------------------------------------------- #
def test_cite_projection_age_and_freshness_flag():
    layer = {"agent_role": "layer_analysis", "scope_id": "L5_chip_design",
             "as_of": "2026-11-14T01:30:00+00:00"}
    assert pv.cite_projection(layer, AT) == "层分析·L5_chip_design · 截至 11-14 01:30 · 4.6 天前"
    old = dict(layer, as_of="2026-11-10T01:30:00+00:00")
    assert "⚠️超出 7 天窗口" in pv.cite_projection(old, AT)
    tech = {"agent_role": "technical_review", "scope_id": "LITE", "as_of": "2026-11-17T13:30:00+00:00"}
    assert "⚠️超出 24 小时窗口" in pv.cite_projection(tech, AT)


def test_every_vintage_prefix_in_use_is_described():
    refs = {
        "consensus:AAPL@2026-11-18T07:02:18.056128+00:00": "一致预期 AAPL · 采集 11-18 07:02",
        "consensus:ai_hardware@7bc15eb83eea05a3": "一致预期 ai_hardware · 采集时间未带",
        "market-session:AAPL:2026-11-18:before-close": "行情 AAPL · 2026-11-18 收盘前",
        "financials:AAPL:1554@7e33dc91a4d9c568": "财务数据 AAPL · 1554 行（未带时间）",
        "evidence:L8_equipment:1605@18b1b92e0e99ee9d": "产业链观测 L8_equipment · 1605 条（逐条时间见层分析日报）",
        "documents:NVDA:13@ca4f10896a16a19b": "文档 NVDA · 13 份（未带时间）",
        "knowledge:config/knowledge/资本开支链.md@f112a6bd9b48401b": "判据笔记 资本开支链.md",
        "macro-regional:tw_ic_exports:2026-10@2026-11-09T10:29:22.813526+00:00":
            "区域出口 tw_ic_exports · 数据期 2026-10 · 采集 11-09 10:29",
        "macro-series:25@e58fbe8ae791f6e3": "宏观序列 · 25 条（逐条截至见宏观日报）",
        "macro-news:133@1d50c20c26118a13": "宏观新闻 · 133 篇（未带时间）",
        "macro-factset:SP500:2026-10-30:research_article@6cba5517d6edb424": "FactSet SP500 · 2026-10-30 期",
        "factset:SP500:2026-10-30:research_article@6cba5517d6edb424:partial_sectors":
            "FactSet SP500 · 2026-10-30 期（部分行业）",
        "baseline-window:GOOG:Q3 FY2026:closed": "财报前基线窗口 GOOG Q3 FY2026（已关闭）",
        "observer-data:ai_work_adoption:2@2026-09-09T15:12:10": "观测器数据 ai_work_adoption · 采集 09-09 15:12",
    }
    for ref, want in refs.items():
        assert pv.describe_vintage_ref(ref) == want, ref
    for marker in ("layer-budget-bound:1", "forced-recompute:run-1", "baseline-refresh:run-2"):
        assert pv.describe_vintage_ref(marker) is None
    assert pv.describe_vintage_ref("brand-new:x:1").endswith("（未登记格式）")


# --------------------------------------------------------------------------- #
# Chief report and context
# --------------------------------------------------------------------------- #
def _result(**kw):
    from ats.agents.chief.decide import ChiefResult
    from ats.schemas.decision import TradeDecision

    layer_id, tech_id, fake = "9eebec25a6d7bc55f93c21748261dc53", "580b4d56a0d99902a6aaaaaaaaaaaaaa", \
        "a6a90ff1421241157e9919153af3e453"
    decision = TradeDecision(symbol="LITE", action="sell", qty=10, conviction=0.9, rationale="止损",
                             references=[tech_id, fake])
    citations = {
        layer_id: {"agent_role": "layer_analysis", "scope_id": "L6_optics",
                   "as_of": "2026-11-14T01:30:00+00:00", "vintages": ["产业链观测 L6 · 900 条"]},
        tech_id: {"agent_role": "technical_review", "scope_id": "LITE",
                  "as_of": "2026-11-18T13:30:00+00:00", "vintages": ["行情 LITE · 2026-11-18 收盘前"]},
    }
    reasoning = {"portfolio_stance": "derisk", "risks_and_invalidation": "",
                 "evidence_weighing": [{"source": "层分析", "said": "扩张", "use": "context",
                                        "why": "背景", "references": [layer_id]}]}
    return ChiefResult(cycle_id="chief-x", as_of=AT, summary="s", decisions=[decision],
                       reasoning=reasoning, citations=citations, **kw), layer_id, fake


def test_chief_report_resolves_references_and_lists_basis():
    from ats.agents.chief import report

    result, layer_id, fake = _result()
    text = report.render(result)
    assert "技术面·LITE · 截至 11-18 13:30 · 1.2 小时前" in text
    assert f"❓ 未知引用 {fake[:8]}" in text
    # 证据权衡 keeps its references now
    assert "| 层分析 | 扩张 | context | 背景 | 层分析·L6_optics · 截至 11-14 01:30 · 4.6 天前 |" in text
    basis = text.split("## 依据清单", 1)[1]
    assert "有 1 个引用不在本轮研究快照中" in basis
    assert f"`{layer_id}`" in basis and "    - 产业链观测 L6 · 900 条" in basis


def test_chief_report_without_index_keeps_short_ids():
    from ats.agents.chief import report

    result, _, _ = _result()
    text = report.render(result.model_copy(update={"citations": {}}))
    assert "580b4d56, a6a90ff1" in text and "## 依据清单" not in text


def test_chief_context_has_as_of_and_no_truncated_ids():
    from ats.agents.chief import assemble

    pid = "96d0e73a65bf532213898a0775e9ca47"
    detail = [{"task_id": "layer_analysis", "role": "layer_analysis", "scope": "layer:L1_app",
               "status": "fresh", "projection_id": pid, "content_hash": "h" * 64,
               "as_of": "2026-11-14T01:30:00+00:00", "payload": {"status": "expanding"}}]
    text = assemble.projection_context_block(detail, at=AT)
    assert pid[:18] + "…" not in text
    assert text.count(pid) == 1
    assert "最新截至 2026-11-14T01:30:00+00:00（距本轮 4.6 天）" in text
    assert "fresh · as_of=2026-11-14T01:30:00+00:00（距本轮 4.6 天） · " + pid in text


# --------------------------------------------------------------------------- #
# Fundamental report
# --------------------------------------------------------------------------- #
def test_consensus_stamp():
    from types import SimpleNamespace

    from ats.agents.pead.report import _consensus_stamp

    es = SimpleNamespace(consensus_as_of="2026-11-18T07:02:18+00:00", as_of=AT)
    assert _consensus_stamp(es) == "_共识快照采集于 2026-11-18 07:02 UTC（距本次运行 7.7 小时）。_"
    assert _consensus_stamp(SimpleNamespace(consensus_as_of="", as_of=AT)) == "_共识快照采集时间未登记。_"


def test_citation_index_reads_vintages_only_for_cited(monkeypatch):
    from ats.graph import chief as graph_chief
    from ats.graph.chief_state import ChiefDecisionState
    from ats.schemas.decision import TradeDecision

    cited, other = "a" * 32, "b" * 32
    asked: list[str] = []

    class Store:
        def get_task_projection(self, pid):
            asked.append(pid)
            return {"data_vintage_refs": ["market-session:LITE:2026-11-18:before-close", "layer-budget-bound:1"]}

    monkeypatch.setattr("ats.memory.get_store", lambda: Store())
    items = [{"projection_id": cited, "agent_role": "technical_review", "scope_id": "LITE",
              "as_of": "2026-11-18T13:30:00+00:00", "task_id": "technical_review"},
             {"projection_id": other, "agent_role": "macro_review", "scope_id": "",
              "as_of": "2026-11-14T01:00:00+00:00", "task_id": "macro_review"}]
    state = ChiefDecisionState(cycle_id="c", as_of=AT, research_snapshot={"items": items},
                               decisions=[TradeDecision(symbol="LITE", action="sell", qty=1,
                                                        references=[cited])])
    index = graph_chief._citation_index(state)
    assert set(index) == {cited, other} and asked == [cited]
    assert index[cited]["vintages"] == ["行情 LITE · 2026-11-18 收盘前"]
    assert index[cited]["as_of"] == "2026-11-18T13:30:00+00:00" and "vintages" not in index[other]
