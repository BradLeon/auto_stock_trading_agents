"""Group 8 boundary tests: acquisition out of agents/, provider-direct reads retired.

8.1–8.5 pin the migration itself: no module under `src/ats/agents/` imports a
provider or writes raw assets, and the data-product entries actually route to the
underlying reads.
8.6–8.7 pin the exception lifecycle: the Phase D batch is gone, and any future
exception must declare a removal phase that has not expired.
The publish tests pin the macro/technical projection seam the chief snapshot reads.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ats.workflow import architecture_guards as guards
from ats.workflow.architecture_guards import (
    FIRST_BATCH_EXCEPTIONS,
    ExceptionEntry,
    ExceptionPhaseError,
    scan_agents,
    scan_module,
)

import pytest


def _agent_module(relative: str):
    from ats.config import REPO_ROOT

    return REPO_ROOT / relative


# --- 8.1 采集侧迁出 ----------------------------------------------------------- #

def test_no_agent_module_imports_a_provider_or_writes_raw_assets():
    """The whole agents tree is clean against an EMPTY exception list."""
    assert scan_agents() == []


def test_observer_acquisition_delegates_to_the_data_layer(monkeypatch):
    """observer.fetch_document / fetch_release are thin delegates now."""
    from ats.agents.evidence import observer
    from ats.data.collection import evidence as collection

    called = {}
    monkeypatch.setattr(collection, "fetch_document",
                        lambda *a, **k: called.setdefault("doc", True) and ("T", "src", ""))
    monkeypatch.setattr(collection, "fetch_release",
                        lambda *a, **k: called.setdefault("rel", True) and ("R", "sec", ""))
    assert observer.fetch_document("MU") == ("T", "src", "")
    assert observer.fetch_release("MU", report_date="2026-08-01") == ("R", "sec", "")
    assert set(called) == {"doc", "rel"}


def test_source_rank_lives_in_the_data_layer_and_is_still_reachable():
    """The tier classification moved with the acquisition code; the viz re-export
    must keep working (`agents/sector/viz.py` imports it from observer)."""
    from ats.agents.evidence import observer
    from ats.data.collection import evidence as collection

    assert observer.source_rank is collection.source_rank
    assert collection.source_rank("defeatbeta:...") == collection.RANK_KEYED
    assert collection.source_rank("manual") == collection.RANK_MANUAL


# --- 8.2–8.5 各角色模块无 provider 导入，入口真实路由 -------------------------- #

def test_macro_modules_have_no_provider_imports():
    for name in ("src/ats/agents/macro/assemble.py", "src/ats/agents/macro/review.py"):
        assert scan_module(_agent_module(name)) == [], name


def test_sector_modules_have_no_provider_imports():
    names = (
        "src/ats/agents/sector/assemble.py", "src/ats/agents/sector/cross_section.py",
        "src/ats/agents/sector/kb_perturb.py", "src/ats/agents/sector/structure.py",
        "src/ats/agents/sector/review.py", "src/ats/agents/layer/layer_review.py",
    )
    for name in names:
        assert scan_module(_agent_module(name)) == [], name


def test_pead_modules_have_no_provider_imports():
    from pathlib import Path

    import ats.agents.pead as pead

    for path in sorted(Path(pead.__path__[0]).glob("*.py")):
        assert scan_module(path) == [], path.name


def test_technical_modules_have_no_provider_imports():
    assert scan_module(_agent_module("src/ats/agents/technical/review.py")) == []


def test_macro_inputs_route_to_the_underlying_reads(monkeypatch):
    from ats.data.products import macro_inputs

    monkeypatch.setattr("ats.data.regional.fetch",
                        lambda *, consumer: ("snap", consumer))
    monkeypatch.setattr("ats.data.factset.fetch_macro_material", lambda *, products=None: 7)
    monkeypatch.setattr("ats.data.websearch.search_news", lambda *a, **k: ["hit"])

    snap = macro_inputs.regional_monthly("macro_agent")
    assert snap == ("snap", "macro_agent")
    assert macro_inputs.factset_macro_material() == 7
    assert macro_inputs.search_news("q") == ["hit"]


def test_sector_inputs_route_to_the_underlying_reads(monkeypatch):
    from ats.data.products import sector_inputs

    monkeypatch.setattr("ats.data.industry.fetch_notes", lambda: [("n", "t")])
    monkeypatch.setattr("ats.data.industry.criteria_spans", lambda text: [(0, 1)])
    monkeypatch.setattr("ats.data.sector_snapshot.fetch_prices",
                        lambda symbols, *, period="1y": {"MU": [1.0]})
    monkeypatch.setattr("ats.data.consensus.fetch", lambda s, *, consumer: {"pt": s})
    monkeypatch.setattr("ats.data.fundamentals.fetch_constituent_financials",
                        lambda s, **k: {"pe": 11})

    assert sector_inputs.industry_notes() == [("n", "t")]
    assert sector_inputs.industry_criteria_spans("x") == [(0, 1)]
    assert sector_inputs.sector_prices(["MU"]) == {"MU": [1.0]}
    assert sector_inputs.consensus_for("MU") == {"pt": "MU"}
    assert sector_inputs.constituent_financials("MU") == {"pe": 11}


def test_news_and_market_inputs_route_to_the_underlying_reads(monkeypatch):
    from ats.data.products import market_inputs, news_inputs

    monkeypatch.setattr("ats.data.news.fetch_news",
                        lambda symbol, since, until=None, *, consumer: [symbol])
    monkeypatch.setattr("ats.data.base.yf_symbol", lambda s: s.upper())
    assert news_inputs.monitor_news("MU", "since") == ["MU"]
    assert market_inputs.yf_symbol("mu") == "MU"


def test_fiscal_tools_are_the_pure_functions():
    from ats.data.fiscal import canonical_tag as underlying
    from ats.data.products import fiscal_tools

    assert fiscal_tools.canonical_tag is underlying
    assert fiscal_tools.parse_label("FY2026 Q2") == (2026, 2)


# --- 8.6 例外清退 -------------------------------------------------------------- #

def test_the_phase_d_exception_batch_is_retired():
    assert FIRST_BATCH_EXCEPTIONS == ()


def test_the_guard_runs_clean_on_the_current_tree():
    assert scan_agents() == []


# --- 8.7 例外必须声明收敛阶段 --------------------------------------------------- #

def test_an_exception_without_a_phase_fails_validation():
    entry = ExceptionEntry(module="src/ats/agents/x.py", target="ats.data.y",
                           reason="declared without a phase")
    with pytest.raises(ExceptionPhaseError, match="no removal phase"):
        guards.validate_exceptions((entry,), active_phase="Phase D")


def test_an_exception_with_an_expired_phase_fails_validation():
    entry = ExceptionEntry(module="src/ats/agents/x.py", target="ats.data.y",
                           reason="stale", phase="Phase C")
    with pytest.raises(ExceptionPhaseError, match="already expired"):
        guards.validate_exceptions((entry,), active_phase="Phase D")


def test_an_exception_with_a_future_phase_passes_validation():
    entry = ExceptionEntry(module="src/ats/agents/x.py", target="ats.data.y",
                           reason="moved to Phase E", phase="Phase E")
    guards.validate_exceptions((entry,), active_phase="Phase D")


def test_an_unknown_phase_string_is_rejected():
    entry = ExceptionEntry(module="src/ats/agents/x.py", target="ats.data.y",
                           reason="typo", phase="phase e")
    with pytest.raises(ExceptionPhaseError, match="unknown removal phase"):
        guards.validate_exceptions((entry,), active_phase="Phase D")


def test_scan_agents_rejects_an_invalid_declared_exception(monkeypatch):
    entry = ExceptionEntry(module="src/ats/agents/x.py", target="ats.data.y",
                           reason="no phase", phase="")
    monkeypatch.setattr(guards, "FIRST_BATCH_EXCEPTIONS", (entry,))
    with pytest.raises(ExceptionPhaseError):
        scan_agents()


# --- 投影发布点（主理人快照的 macro/technical 两类输入） ------------------------- #

class _FakeStore:
    def __init__(self):
        self.envelopes = []

    def save_task_projection_envelope(self, envelope):
        self.envelopes.append(envelope)
        return True


def test_macro_review_publishes_a_portfolio_scope_projection():
    from ats.agents.macro.review import _publish_projection
    from ats.schemas.macro_strategy import IndicatorReading, MacroReview

    review = MacroReview(
        name="macro", as_of=datetime(2026, 9, 24, tzinfo=timezone.utc),
        quadrant="goldilocks", quadrant_state="confirmed",
        regime="增长稳、通胀降温",
        summary="金发姑娘格局延续",
        indicators=[IndicatorReading(key="dgs10", label="10Y", level=4.1, unit="pct")])
    store = _FakeStore()
    _publish_projection(store, review)
    assert len(store.envelopes) == 1
    env = store.envelopes[0]
    assert env.agent_role == "macro_review"
    assert env.scope.kind == "portfolio"
    assert env.payload["regime"] == "risk_on"
    assert env.payload["indicators"] == ["10Y=4.1"]


def test_macro_placeholder_review_is_not_published():
    from ats.agents.macro.review import _publish_projection
    from ats.schemas.macro_strategy import MacroReview

    review = MacroReview(name="macro", as_of=datetime(2026, 9, 24, tzinfo=timezone.utc),
                         regime="(no-llm)")
    store = _FakeStore()
    _publish_projection(store, review)
    assert store.envelopes == []


def test_technical_review_publishes_per_entity_projections():
    from ats.agents.technical.review import _publish_projection
    from ats.schemas.technical import TechnicalReading, TechnicalReview

    now = datetime(2026, 9, 24, tzinfo=timezone.utc)
    review = TechnicalReview(name="technical", as_of=now, readings=[
        TechnicalReading(symbol="MU", score=6, close=100.0, sma200=90.0, bars=250),
        TechnicalReading(symbol="NVDA", score=2, bars=250),
        TechnicalReading(symbol="SKHY", score=4, stale=True, bars=3,
                         note="历史不足"),
    ])
    store = _FakeStore()
    published = _publish_projection(store, review)
    assert published == 2
    by_entity = {env.scope.id: env for env in store.envelopes}
    assert set(by_entity) == {"MU", "NVDA"}
    assert by_entity["MU"].payload["signal"] == "bullish"
    assert by_entity["MU"].payload["levels"]["close"] == 100.0
    assert by_entity["NVDA"].payload["signal"] == "bearish"
    assert all(env.agent_role == "technical_review" for env in store.envelopes)
    assert all(env.scope.kind == "entity" for env in store.envelopes)
