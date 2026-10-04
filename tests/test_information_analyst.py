"""Phase D task group 4: 信息分析师——新建角色、迁移与简报契约。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


def _store():
    from ats.memory.store import TradingMemory

    return TradingMemory(":memory:")


def _brief_payload(**over):
    payload = {
        "entity": "COHR",
        "headline": "NVDA raises CapEx guidance",
        "summary": "上游资本开支指引上调",
        "relevance": "medium",
        "sources": ["rss:SemiAnalysis"],
        "fact_changes": ["NVDA 上调本季资本开支指引区间"],
        "impact_candidates": ["supply_chain"],
        "entities": ["COHR"],
        "confidence": 0.6,
        "freshness": "published 2026-09-23",
        "unverified": ["指引落地节奏待核验"],
        "event_time": NOW.isoformat(),
        "published_at": NOW.isoformat(),
        "extracted_at": NOW.isoformat(),
        "cluster_key": "evt-test",
        "source_count": 1,
        "independent_sources": 1,
    }
    payload.update(over)
    return payload


# --- 4.1 包内无 provider 导入、守卫扫描干净 --------------------------------------- #

def test_information_package_has_no_provider_imports():
    from pathlib import Path

    from ats.workflow.architecture_guards import PROVIDER_MODULES, scan_agents

    pkg = Path("src/ats/agents/information")
    for py in pkg.glob("*.py"):
        text = py.read_text(encoding="utf-8")
        for mod in PROVIDER_MODULES:
            # 顶层导入与函数内导入都算：字符串级检查 + 守卫 AST 扫描双保险
            assert f"from {mod}" not in text and f"import {mod}" not in text, \
                f"{py.name} imports provider module {mod}"
    violations = [v for v in scan_agents()
                  if v.module.startswith("src/ats/agents/information")]
    assert violations == []


def test_information_package_never_fetches_news_or_urls():
    from pathlib import Path

    pkg = Path("src/ats/agents/information")
    for py in pkg.glob("*.py"):
        text = py.read_text(encoding="utf-8")
        assert "fetch_news" not in text, f"{py.name} fetches news directly"
        assert "fetch_article_text" not in text, f"{py.name} fetches urls directly"


# --- 4.6 六要素校验 ---------------------------------------------------------------- #

def test_brief_missing_fact_changes_is_rejected_and_nothing_persisted():
    from ats.agent.task_projection import EnvelopeValidationError
    from ats.agent.task_projection import ProjectionScope

    store = _store()
    bad = _brief_payload(fact_changes=[])
    with pytest.raises(EnvelopeValidationError):
        from ats.agents.information.briefs import publish_information_brief

        publish_information_brief(store, bad,
                                  scope=ProjectionScope(kind="entity", id="COHR"),
                                  as_of=NOW.isoformat())
    assert store.task_projection_envelopes(agent_role="information_brief") == []


def test_brief_missing_unverified_or_confidence_is_rejected():
    from ats.agent.task_projection import EnvelopeValidationError
    from ats.agent.task_projection import ProjectionScope
    from ats.agents.information.briefs import publish_information_brief

    store = _store()
    scope = ProjectionScope(kind="entity", id="COHR")
    with pytest.raises(EnvelopeValidationError):
        publish_information_brief(store, _brief_payload(unverified=[]),
                                  scope=scope, as_of=NOW.isoformat())
    with pytest.raises(EnvelopeValidationError):
        publish_information_brief(store, _brief_payload(confidence=None),
                                  scope=scope, as_of=NOW.isoformat())
    assert store.task_projection_envelopes(agent_role="information_brief") == []


# --- 4.7 建议拦截 ------------------------------------------------------------------ #

@pytest.mark.parametrize("text", [
    "建议增持 COHR",
    "target position 5% of NAV",
    "analysts recommend buy",
    "建议目标仓位 10%",
])
def test_directional_advice_is_intercepted(text):
    from ats.agent.task_projection import EnvelopeValidationError
    from ats.agent.task_projection import ProjectionScope
    from ats.agents.information.briefs import publish_information_brief

    store = _store()
    with pytest.raises(EnvelopeValidationError):
        publish_information_brief(store, _brief_payload(summary=text),
                                  scope=ProjectionScope(kind="entity", id="COHR"),
                                  as_of=NOW.isoformat())
    assert store.task_projection_envelopes(agent_role="information_brief") == []


def test_neutral_wording_with_english_substring_passes():
    """'additionally' 不该被 'add' 误伤——词表只按词边界匹配英文。"""
    from ats.agent.task_projection import ProjectionScope
    from ats.agents.information.briefs import publish_information_brief

    store = _store()
    pid = publish_information_brief(
        store, _brief_payload(summary="Additionally, guidance was raised"),
        scope=ProjectionScope(kind="entity", id="COHR"), as_of=NOW.isoformat())
    assert pid


# --- 4.8 幂等：同一文档版本复用，新版本产出第二条并引用前一条 ------------------------ #

def test_same_document_version_reuses_brief_and_new_version_supersedes():
    from ats.agent.task_projection import ProjectionScope
    from ats.agents.information.briefs import publish_information_brief

    store = _store()
    scope = ProjectionScope(kind="entity", id="doc-v1")
    pid1 = publish_information_brief(store, _brief_payload(),
                                     scope=scope, as_of=NOW.isoformat())
    # 同一文档版本再处理：内容哈希相同 → 同一 projection_id，不新增行
    pid_again = publish_information_brief(store, _brief_payload(),
                                          scope=scope, as_of=NOW.isoformat())
    rows = store.task_projection_envelopes(agent_role="information_brief")
    assert pid_again == pid1
    assert len(rows) == 1

    # 文档出新版本：新投影引用前一版本，旧投影保留
    pid2 = publish_information_brief(
        store, _brief_payload(summary="updated guidance detail"),
        scope=ProjectionScope(kind="entity", id="doc-v1"),
        as_of=NOW.isoformat(), supersedes_projection_id=pid1)
    rows = store.task_projection_envelopes(agent_role="information_brief")
    assert len(rows) == 2
    assert {r["projection_id"] for r in rows} == {pid1, pid2}
    new_row = next(r for r in rows if r["projection_id"] == pid2)
    assert new_row["supersedes_projection_id"] == pid1


# --- 4.10 三类时间与按发布时间判时效 ------------------------------------------------ #

def test_late_ingested_document_is_not_new_by_publish_time():
    from ats.agents.information.briefs import is_new_by_publish

    cutoff = NOW - timedelta(days=1)
    # 早发布、晚入库：3 天前发布、今天才入库 → 不算本期新增
    late = {"published_at": (NOW - timedelta(days=3)).isoformat(),
            "extracted_at": NOW.isoformat()}
    assert not is_new_by_publish(late, cutoff)
    # 本期发布 → 新增
    fresh = {"published_at": NOW.isoformat()}
    assert is_new_by_publish(fresh, cutoff)
    # 无发布时间 → 保守按非本期处理
    assert not is_new_by_publish({}, cutoff)


def test_clocks_record_all_three_times():
    from ats.agents.information.briefs import clocks_for

    clocks = clocks_for({"published_at": (NOW - timedelta(hours=2)).isoformat()},
                        extracted_at=NOW)
    assert clocks["event_time"] == (NOW - timedelta(hours=2)).isoformat()
    assert clocks["published_at"] == (NOW - timedelta(hours=2)).isoformat()
    assert clocks["extracted_at"] == NOW.isoformat()


# --- 4.11 同源聚类 ----------------------------------------------------------------- #

def test_same_event_clusters_and_confidence_not_raised_by_count():
    from ats.agents.information.briefs import cluster_key, cluster_summary

    k1 = cluster_key("NVDA raises CapEx guidance for FY27")
    k2 = cluster_key("NVDA raises CapEx guidance for FY27")   # 同事件同题
    k3 = cluster_key("Completely different story about optics")
    assert k1 == k2 and k1 != k3

    four_same_source = [
        {"source": "wire:YH", "confidence": 0.6},
        {"source": "wire:YH", "confidence": 0.5},
        {"source": "wire:YH", "confidence": 0.55},
        {"source": "wire:YH", "confidence": 0.65},
    ]
    stats = cluster_summary(four_same_source)
    assert stats["document_count"] == 4
    assert stats["independent_sources"] == 1      # 4 篇同源 ≠ 4 个独立信源
    assert stats["confidence"] == 0.65            # 取单文档最大值，不因数量上调


# --- 4.2/4.4 抽取与监控链路不改写 dossier ------------------------------------------- #

def test_monitor_chain_leaves_dossier_untouched_and_publishes_brief(monkeypatch):
    from ats.agents.pead import monitor
    from ats.data import news as news_src
    from ats.schemas.news import NewsItem

    store = _store()
    import ats.memory as memory_mod

    monkeypatch.setattr(memory_mod, "get_store", lambda: store)

    fresh = [NewsItem(id="n1", source="finnhub", headline="NVDA raises CapEx",
                      published_at=NOW)]
    monkeypatch.setattr(news_src, "fetch_news",
                        lambda sym, since, until=None, consumer="pead_monitor": fresh)
    monkeypatch.setattr("ats.agents.information.documents.run_structured", lambda *a, **k: None)
    # LLM 失败路径：不产出简报、绝不写 dossier
    upd = monitor.run("COHR", use_llm=True, lookback_days=7)
    assert upd.symbol == "COHR"
    assert store.get_dossier("COHR", "Q FY2026") is None  # 无 dossier 创建
    # use_llm=False 路径：只登记事件
    upd2 = monitor.run("COHR", use_llm=False)
    assert upd2.materiality == 0.0
    assert store.count_events("COHR") == 1


def test_document_pass_publishes_brief_without_dossier_write(monkeypatch):
    from ats.agent.task_projection import ProjectionScope
    from ats.agents.information import documents
    from ats.agents.pead.outputs import ContextUpdateView
    from ats.schemas.news import ContextUpdate

    store = _store()
    view = ContextUpdateView(materiality=0.8, event_summary="capex up",
                             narrative_delta="upstream demand stronger",
                             expectation_changes=[])
    monkeypatch.setattr(documents, "run_structured", lambda *a, **k: view)
    # 预置一条已准入事件（发布时间在窗口内）
    from ats.schemas.news import NewsItem

    store.append_events("COHR", [NewsItem(
        id="n1", source="finnhub", headline="NVDA raises CapEx",
        published_at=NOW, url="", summary="")])
    upd = documents.run_document_pass(store, "COHR", cfg={}, fresh=[],
                                      use_llm=True, lookback_days=7)
    assert isinstance(upd, ContextUpdate) and upd.materiality == 0.8
    rows = store.task_projection_envelopes(agent_role="information_brief")
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["fact_changes"] and payload["unverified"]
    # dossier 未被创建/修改
    assert store.get_dossier("COHR", "Q FY2026") is None
    _ = ProjectionScope


# --- 4.3 迁移后 enrich 不发起取数 --------------------------------------------------- #

def test_enrich_reads_only_admitted_bodies(monkeypatch):
    from ats.agents.information import triage as info_triage
    from ats.schemas.news import NewsItem

    store = _store()
    items = [NewsItem(id="n1", source="finnhub", headline="h", published_at=NOW,
                      url="https://example.com/a")]

    # 已准入正文（≥800 字符）→ 返回
    monkeypatch.setattr("ats.data.document_assets.read_external",
                        lambda ext_id, store=None: ({"document_id": "d1"}, "正文" * 500))
    got = info_triage.enrich(items, max_items=3, max_chars=1000, store=store)
    assert len(got) == 1 and got[0][1].startswith("正文")
    # 未准入正文 → 跳过，绝不取数
    monkeypatch.setattr("ats.data.document_assets.read_external",
                        lambda ext_id, store=None: (None, ""))
    assert info_triage.enrich(items, max_items=3, max_chars=1000, store=store) == []


# --- 4.5 digest 产出可查回为投影 ----------------------------------------------------- #

def test_digest_publication_produces_queryable_briefs():
    from ats.agents.information.digest import publish_digest_briefs

    store = _store()
    per = {"COHR": {"events": [{"source": "finnhub", "headline": "CapEx up",
                                "published_at": NOW.isoformat(),
                                "triage_score": 0.8}],
                    "insights": [{"direction": "bullish", "impact_path": "direct",
                                  "summary": "upstream demand", "confidence": 0.7}],
                    "delta": ""}}
    n = publish_digest_briefs(store, per, cutoff=NOW - timedelta(hours=24),
                              min_triage=0.35)
    assert n == 1
    rows = store.task_projection_envelopes(agent_role="information_brief")
    assert len(rows) == 1 and rows[0]["payload"]["entity"] == "COHR"
    assert rows[0]["payload"]["fact_changes"]


# --- 4.9 独立入口独立终结 ------------------------------------------------------------ #

def test_information_pass_terminates_independently(monkeypatch):
    """单独运行不读取其他分析师投影、不产生基本面或决策记录。"""
    import ats.config as config_mod
    from ats.agents.information import entry

    store = _store()
    real_global = config_mod.load_pead_global
    monkeypatch.setattr(config_mod, "load_pead_global",
                        lambda: {**real_global(), "targets": ["COHR"]})
    monkeypatch.setattr("ats.memory.get_store", lambda: store)
    monkeypatch.setattr(entry, "_brief_count",
                        lambda store_: len(store_.task_projection_envelopes(agent_role="information_brief")))
    # 抽取与文档识别都打桩为空跑（窗口内无准入材料）
    monkeypatch.setattr("ats.agents.information.extract.run", lambda **k: [])
    monkeypatch.setattr("ats.agents.information.documents.run_document_pass",
                        lambda store_, sym, cfg, fresh, use_llm, lookback_days=7:
                        _noop_update(sym))
    summary = entry.run_information_pass(use_llm=False, symbols=["COHR"],
                                         ingest_research=False)
    assert summary["targets"] == ["COHR"]
    # 无基本面 dossier、无决策记录产生
    assert store.get_dossier("COHR", "Q FY2026") is None
    assert store.task_projection_envelopes(agent_role="fundamental_expectation_update") == []
    assert store.task_projection_envelopes(agent_role="fundamental_event_review") == []


def _noop_update(sym):
    from ats.schemas.news import ContextUpdate

    return ContextUpdate(symbol=sym, as_of=NOW, materiality=0.0, event_summary="none")
