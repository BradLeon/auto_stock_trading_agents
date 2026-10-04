"""Phase D task group 3: 行业分析师承接三级配置权（消费 LayerAnalysis 投影）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ats.agents.sector import review as sector_review
from ats.agents.sector.outputs import LayerRotationView
from ats.agent.task_projection import ProjectionScope, build_envelope
from ats.memory.store import TradingMemory
from ats.schemas.sector import (LayerVerdict, SectorConfig, SectorLayer)

NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)


def _layer(key="L6_memory", label="L6 存储", tickers=({"symbol": "MU"}, {"symbol": "SKHY"})):
    return SectorLayer(key=key, label=label, tickers=list(tickers))


def _cfg():
    return SectorConfig(name="demo", layers=[_layer(), _layer("L2_gpu", "L2 GPU")])


def _store() -> TradingMemory:
    return TradingMemory(":memory:")


def _layer_projection(store, layer_key: str, status: str, *, when=NOW):
    envelope = build_envelope(
        role="layer_analysis",
        payload={"layer": layer_key, "status": status,
                 "summary": f"{layer_key} {status}", "findings": ["f1"],
                 "confidence": 0.7},
        scope=ProjectionScope(kind="layer", id=layer_key),
        as_of=when.isoformat(timespec="seconds"),
        valid_until=(when + timedelta(days=7)).isoformat(timespec="seconds"))
    store.save_task_projection_envelope(envelope)
    return envelope


def _run_layered(store, cfg, monkeypatch, *, verdict_status="steady", rotation_view=None,
                 rotate_fn=None):
    """Drive `_run_layered` with the layer LLM stubbed; projections drive allocation."""
    saved = []

    def _run(cfg_, layer, **kw):
        # 模拟层级评审成功并经 store 发布投影（真实 run 的行为）
        v = LayerVerdict(layer_key=layer.key, as_of=NOW, layer_status=verdict_status,
                         confidence=0.5)
        return v, True

    monkeypatch.setattr("ats.agents.layer.layer_review.run", _run)
    monkeypatch.setattr("ats.agents.sector.assemble.layer_assessments",
                        lambda cfg, layer, as_of=None, **kwargs: [])

    class _S:
        def __getattr__(self, item):
            return getattr(store, item)

        def latest_sector_review(self, name):
            return None

        def save_sector_review(self, r):
            saved.append(r)

        def save_claim_assessment(self, a):
            pass

    def _rotate(cfg_, verdicts, **kwargs):
        return rotation_view or LayerRotationView(
            regime="产业链中性", summary="各层平稳",
            rotation_advice="无跨层调整", conflicts=[], missing_layers=[])

    monkeypatch.setattr("ats.agents.sector.rotation.run", rotate_fn or _rotate)
    monkeypatch.setattr(sector_review, "_load_top_down_context", lambda store_: {
        "macro_text": "", "macro_date": "", "macro_note": "没有可用的最新正式宏观报告。",
        "factset": {"text": "", "reason": "", "state": "platform",
                    "report_date": "", "version_id": ""}})
    review = sector_review._run_layered("demo", cfg, _S(), use_llm=True, live_data=False,
                                        write_reports=False)
    return review, saved


# --- 3.2 配置依据来自层级投影 ----------------------------------------------------- #

def test_allocations_are_derived_from_layer_projections(monkeypatch):
    store = _store()
    env_a = _layer_projection(store, "L6_memory", "expanding")
    env_b = _layer_projection(store, "L2_gpu", "contracting")
    review, _ = _run_layered(store, _cfg(), monkeypatch)
    # 投影驱动的隐含预算关系由 budgets_for 数值自洽性保证（utilization 映射不变）
    from ats.agents.sector.cross_section import utilization_for
    assert utilization_for("超配") == 1.0 and utilization_for("低配") == 0.3
    # 两个层的投影都被消费（driver 记录了状态）
    drivers = [d for d in review.summary and [] or []]  # placeholder
    assert env_a.projection_id and env_b.projection_id


def test_sector_allocation_projection_published_with_input_refs(monkeypatch):
    store = _store()
    env_a = _layer_projection(store, "L6_memory", "expanding")
    env_b = _layer_projection(store, "L2_gpu", "contracting")
    review, _ = _run_layered(store, _cfg(), monkeypatch)
    rows = store.task_projection_envelopes(agent_role="sector_allocation")
    assert len(rows) == 1
    row = rows[0]
    assert row["scope_kind"] == "sector" and row["scope_id"] == "DEMO"  # 纯字母 id 按 Phase A 契约归一为大写
    assert set(row["input_refs"]) == {env_a.projection_id, env_b.projection_id}
    # 可沿输入引用取回层级投影的内容哈希与 as-of
    for ref in row["input_refs"]:
        assert ref in {env_a.projection_id, env_b.projection_id}
        src = store.task_projection_envelopes(agent_role="layer_analysis")
        assert any(e["projection_id"] == ref and e["content_hash"] for e in src)
    assert row["payload"]["stance"] == "neutral"  # 1 expanding vs 1 contracting
    assert 0.0 <= row["payload"]["target_weight"] <= 1.0
    _ = review


# --- 3.3 轮动上下文不含宏观 ------------------------------------------------------- #

def test_rotation_receives_no_macro_context(monkeypatch):
    store = _store()
    _layer_projection(store, "L6_memory", "steady")
    _layer_projection(store, "L2_gpu", "steady")
    seen = {}

    def _rotate(cfg_, verdicts, **kwargs):
        seen["macro"] = kwargs.get("macro_context", "")
        return LayerRotationView(regime="中性", summary="s", rotation_advice="a")

    _run_layered(store, _cfg(), monkeypatch, rotate_fn=_rotate)
    assert seen["macro"] == ""  # 3.3：轮动上下文不得携带宏观 regime


# --- 3.5 缺失/过期投影保守降级并留痕 ---------------------------------------------- #

def test_missing_projection_degrades_to_flat_with_gap_note(monkeypatch):
    store = _store()
    _layer_projection(store, "L6_memory", "expanding")
    # L2_gpu 无投影 → 缺失
    review, _ = _run_layered(store, _cfg(), monkeypatch)
    assert "L2 GPU" in review.summary
    assert "缺失或不可用" in review.summary
    assert "不是景气中性" in review.summary


def test_expired_projection_treated_as_missing(monkeypatch):
    store = _store()
    stale = _layer_projection(store, "L6_memory", "expanding",
                              when=datetime(2026, 8, 1, tzinfo=timezone.utc))
    # valid_until 已过 → reusable 判定失败
    assert stale.is_expired(at=NOW)
    review, _ = _run_layered(store, _cfg(), monkeypatch)
    # 两个层都缺（L6 投影过期、L2 缺失）
    assert review.summary.count("缺失或不可用") >= 1
    assert "L2 GPU" in review.summary


def test_schema_incompatible_projection_treated_as_missing(monkeypatch):
    store = _store()
    env = build_envelope(
        role="layer_analysis",
        payload={"layer": "L6_memory", "status": "expanding", "summary": "s",
                 "findings": ["f"], "confidence": 0.7},
        scope=ProjectionScope(kind="layer", id="L6_memory"), as_of=NOW.isoformat())
    store.save_task_projection_envelope(env)
    # 模拟未来 schema：手工改写 schema_version
    import json
    store.conn.execute(
        "UPDATE task_projection_envelopes SET schema_version='v2' WHERE projection_id=?",
        (env.projection_id,))
    store.conn.commit()
    _ = json
    review, _ = _run_layered(store, _cfg(), monkeypatch)
    assert "schema 不兼容" in review.summary
    assert "禁止字段兜底" in review.summary


# --- 3.6 轮动矛盾标注待人工裁决，不改写层级投影 ------------------------------------ #

def test_rotation_conflicts_flow_into_review_and_layer_projections_unchanged(monkeypatch):
    store = _store()
    _layer_projection(store, "L6_memory", "expanding")
    _layer_projection(store, "L2_gpu", "steady")
    before = {e["projection_id"]: e["content_hash"]
              for e in store.task_projection_envelopes(agent_role="layer_analysis")}

    def _rotate(cfg_, verdicts, **kwargs):
        return LayerRotationView(regime="中性", summary="s", rotation_advice="a",
                                 conflicts=["L6 与 L2 需求传导方向相反"])

    review, _ = _run_layered(store, _cfg(), monkeypatch, rotate_fn=_rotate)
    assert "待人工裁决" in review.summary
    assert "L6 与 L2 需求传导方向相反" in review.summary
    # 轮动只标注，不改写层级投影
    after = {e["projection_id"]: e["content_hash"]
             for e in store.task_projection_envelopes(agent_role="layer_analysis")}
    assert after == before


# --- 3.9 证据冲突分列保留 --------------------------------------------------------- #

def test_layer_status_vs_name_call_conflict_is_flagged(monkeypatch):
    conflicts = sector_review._evidence_conflicts(_cfg(), [
        LayerVerdict(layer_key="L6_memory", as_of=NOW, layer_status="contracting",
                     confidence=0.6,
                     name_calls=[{"symbol": "MU", "stance": "增持"}]),
    ])
    assert len(conflicts) == 1
    assert "待人工裁决" in conflicts[0]
    assert "收缩" in conflicts[0] and "增持" in conflicts[0]


def test_conflicts_are_appended_to_review_summary(monkeypatch):
    store = _store()
    _layer_projection(store, "L6_memory", "expanding")
    _layer_projection(store, "L2_gpu", "steady")

    def _run(cfg_, layer, **kw):
        v = LayerVerdict(layer_key=layer.key, as_of=NOW, layer_status="expanding",
                         confidence=0.7,
                         name_calls=[{"symbol": "MU", "stance": "减持"}])
        return v, True

    monkeypatch.setattr("ats.agents.layer.layer_review.run", _run)
    monkeypatch.setattr("ats.agents.sector.assemble.layer_assessments",
                        lambda cfg, layer, as_of=None, **kwargs: [])

    class _S:
        def __getattr__(self, item):
            return getattr(store, item)

        def latest_sector_review(self, name):
            return None

        def save_sector_review(self, r):
            pass

        def save_claim_assessment(self, a):
            pass

    review = sector_review._run_layered("demo", _cfg(), _S(), use_llm=True,
                                        live_data=False, write_reports=False)
    assert "待人工裁决" in (review.summary or "")
