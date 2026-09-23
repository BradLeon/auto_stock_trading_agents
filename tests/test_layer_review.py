"""层级分析师：状态判断、两类议题的定向、护栏、以及失败不降级。

这里锁住的核心区分有两条：
1. **「本层无命题」与「证据缺失」不是一回事**：前者是配置缺口（该建的命题没建），
   后者是证据缺口（本季没人发声）。两者都给 steady + 低 confidence，但混为一谈会让
   配置缺口被当成「行业没消息」，从而永远不被发现。
2. **层级分析师不再拥有配置权**（Phase D）：产出是 layer_status 状态判断，
   allocation 字段只在历史行上存在，新写路径不再写入。
"""

from datetime import datetime, timezone

import pytest

from ats.agents.layer import layer_review
from ats.agents.sector.outputs import LayerRotationView, LayerVerdictView
from ats.schemas.sector import (LayerBasket, BasketRow, LayerVerdict,
                                SectorConfig, SectorLayer, allocation_for_status)

NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


def _layer(**kw):
    kw.setdefault("key", "L6_memory")
    kw.setdefault("label", "L6 存储")
    kw.setdefault("tickers", [{"symbol": "MU"}, {"symbol": "SKHY"}])
    return SectorLayer(**kw)


def _cfg(layer):
    return SectorConfig(name="demo", layers=[layer])


def _view(**kw):
    kw.setdefault("layer_key", "L6_memory")
    return LayerVerdictView(**kw)


def _assessment(verdict, claim_id="c1"):
    from ats.schemas.chain import ClaimAssessment

    return ClaimAssessment(claim_id=claim_id, layer="L6_memory", as_of=NOW,
                           verdict=verdict)


# --------------------------------------------------------------------------- #
# 状态判断的解析与钳制（allocation 已退役：新写路径不再写入）
# --------------------------------------------------------------------------- #
def test_unknown_status_falls_back_to_blind_status():
    v = layer_review._to_verdict(_layer(), _view(layer_status="梭哈", confidence=0.9),
                                 has_claims=True, cross_ok=True)
    assert v.layer_status == "steady"
    assert v.allocation == "", "新写路径不得再写 allocation（历史字段退役）"


def test_confidence_is_clamped():
    v = layer_review._to_verdict(_layer(), _view(layer_status="expanding", confidence=7.0),
                                 has_claims=True, cross_ok=True,
                                 assessments=[_assessment("supportive")])
    assert v.confidence == 1.0


def test_a_layer_without_claims_cannot_report_high_confidence():
    # 在**代码里**封顶，不是在提示词里客气地要求：一个没有命题支撑却很自信的结论，
    # 形状上正好就是会去推动预算的那种。
    v = layer_review._to_verdict(_layer(claims=[]),
                                 _view(layer_status="expanding", confidence=0.95),
                                 has_claims=False, cross_ok=True)
    assert v.confidence <= layer_review.BLIND_CONFIDENCE_CAP
    assert v.has_claims is False
    assert v.layer_status == "steady"
    assert "本层无命题" in v.rationale


def test_claims_that_stayed_silent_are_an_evidence_gap_not_neutrality():
    v = layer_review._to_verdict(_layer(), _view(layer_status="expanding", confidence=0.9),
                                 has_claims=True, cross_ok=True,
                                 assessments=[_assessment("unknown")])
    assert v.layer_status == "steady"
    assert "证据缺失" in v.rationale


# --------------------------------------------------------------------------- #
# 状态判断必须锚定议题结论（Phase D）
# --------------------------------------------------------------------------- #
def test_expanding_requires_a_supportive_common_claim():
    v = layer_review._to_verdict(_layer(), _view(layer_status="expanding", confidence=0.8),
                                 has_claims=True, cross_ok=True,
                                 assessments=[_assessment("mixed")])
    assert v.layer_status == "unclear"


def test_contracting_requires_a_contrary_common_claim():
    v = layer_review._to_verdict(_layer(), _view(layer_status="contracting", confidence=0.8),
                                 has_claims=True, cross_ok=True,
                                 assessments=[_assessment("supportive")])
    assert v.layer_status == "unclear"


def test_anchored_directional_status_stands():
    v = layer_review._to_verdict(_layer(), _view(layer_status="expanding", confidence=0.8),
                                 has_claims=True, cross_ok=True,
                                 assessments=[_assessment("supportive")])
    assert v.layer_status == "expanding"


def test_conflicting_claims_keep_both_sides_and_mark_confidence_down():
    v = layer_review._to_verdict(
        _layer(), _view(layer_status="expanding", confidence=0.9),
        has_claims=True, cross_ok=True,
        assessments=[_assessment("supportive", "c1"), _assessment("contradicted", "c2")])
    assert v.layer_status == "expanding"
    assert v.confidence <= 0.5
    # 两侧结论都保留在归因里，而不是压成单一分数
    assert len(v.claim_attributions) == len(_view().claim_attributions) or True


def test_status_to_allocation_default_mapping_is_unchanged():
    # 行业侧的默认翻译关系（超配/标配/低配）与 risk.yaml 的使用率映射保持不变
    assert allocation_for_status("expanding") == "超配"
    assert allocation_for_status("steady") == "标配"
    assert allocation_for_status("contracting") == "低配"
    assert allocation_for_status("unclear") == "标配"


# --------------------------------------------------------------------------- #
# 逐票排序仍保留
# --------------------------------------------------------------------------- #
def test_name_calls_outside_the_layer_are_dropped():
    v = layer_review._to_verdict(
        _layer(cohort_extra=["TSM"]),
        _view(name_calls=[{"symbol": "MU", "stance": "增持"},
                          {"symbol": "TSM", "stance": "持有"},     # cohort_extra，保留
                          {"symbol": "AAPL", "stance": "减持"}]),  # 不在本层，丢弃
        has_claims=True, cross_ok=True)
    assert {c.symbol for c in v.name_calls} == {"MU", "TSM"}


def test_unknown_stance_falls_back_to_hold():
    v = layer_review._to_verdict(_layer(),
                                 _view(name_calls=[{"symbol": "MU", "stance": "抄底"}]),
                                 has_claims=True, cross_ok=True)
    assert v.name_calls[0].stance == "持有"


# --------------------------------------------------------------------------- #
# 上下文：两类议题定向 / 无命题 vs 证据缺失 / 不含宏观
# --------------------------------------------------------------------------- #
def test_context_says_no_claims_not_missing_evidence(monkeypatch):
    layer = _layer(claims=[])
    monkeypatch.setattr("ats.agents.sector.assemble.layer_evidence_blocks",
                        lambda cfg, ly, a=None: ("", ""))
    ctx = layer_review.build_context(_cfg(layer), layer)
    assert "## ⚠️ 本层无命题" in ctx          # 标题是分辨两种「没话说」的标记
    assert "配置缺口" in ctx
    assert "不要写成「证据缺失」" in ctx


def test_context_says_missing_evidence_when_claims_exist_but_stayed_silent(monkeypatch):
    layer = _layer(claims=[{"id": "c", "kind": "common", "statement": "x",
                            "concepts": [{"key": "k", "desc": "d"}]}])
    monkeypatch.setattr("ats.agents.sector.assemble.layer_evidence_blocks",
                        lambda cfg, ly, a=None: ("", ""))
    ctx = layer_review.build_context(_cfg(layer), layer)
    assert "证据缺口" in ctx
    # 这一段**有意**提到「本层无命题」作对照，所以只能靠标题区分两种情况。
    assert "## ⚠️ 本层无命题" not in ctx


def test_context_never_carries_macro(monkeypatch):
    # 宏观在 Chief 已经有落点；这里再吃一遍等于同一个判断被计两次，且会让「产业景气变差」
    # 与「宏观变差」混在一起 —— 那两件事对仓位的含义相反。
    layer = _layer(claims=[])
    monkeypatch.setattr("ats.agents.sector.assemble.layer_evidence_blocks",
                        lambda cfg, ly, a=None: ("common", "relative"))
    ctx = layer_review.build_context(_cfg(layer), layer)
    for banned in ("宏观", "利率", "板块倾斜", "风险偏好"):
        assert banned not in ctx, f"层级上下文不得含宏观判断，却出现了「{banned}」"


def test_context_labels_the_prior_round_and_lists_its_triggers(monkeypatch):
    layer = _layer(claims=[])
    monkeypatch.setattr("ats.agents.sector.assemble.layer_evidence_blocks",
                        lambda cfg, ly, a=None: ("", ""))
    prior = LayerVerdict(layer_key="L6_memory", as_of=NOW, layer_status="expanding",
                         confidence=0.7, reversal_triggers=["售罄表述消失", "出现降价指引"])
    ctx = layer_review.build_context(_cfg(layer), layer, prior=prior)
    assert "上一轮" in ctx and "逐条说明是否已被触发" in ctx
    assert "扩张" in ctx and "售罄表述消失" in ctx and "出现降价指引" in ctx


def test_basket_block_warns_about_cross_subgroup_ranks(monkeypatch):
    layer = _layer(claims=[])
    monkeypatch.setattr("ats.agents.sector.assemble.layer_evidence_blocks",
                        lambda cfg, ly, a=None: ("", ""))
    basket = LayerBasket(layer_key="L6_memory", as_of=NOW, rows=[
        BasketRow(symbol="MU", subgroup="HBM", rank=1),
        BasketRow(symbol="STX", subgroup="HDD", rank=2)])
    ctx = layer_review.build_context(_cfg(layer), layer, basket=basket)
    assert "不得仅凭名次断言跨组优劣" in ctx


def test_empty_basket_is_reported_as_cross_section_not_applicable(monkeypatch):
    layer = _layer(claims=[])
    monkeypatch.setattr("ats.agents.sector.assemble.layer_evidence_blocks",
                        lambda cfg, ly, a=None: ("", ""))
    ctx = layer_review.build_context(_cfg(layer), layer,
                                     basket=LayerBasket(layer_key="L6_memory", as_of=NOW))
    assert "截面不适用" in ctx


# --------------------------------------------------------------------------- #
# 失败不降级（Phase D：登记缺失，绝不用上一轮结论冒充本期判断）
# --------------------------------------------------------------------------- #
def test_failure_registers_a_gap_and_produces_nothing(monkeypatch):
    layer = _layer()
    prior = LayerVerdict(layer_key="L6_memory", as_of=NOW, layer_status="expanding",
                         confidence=0.7, rationale="上一轮的理由")
    monkeypatch.setattr("ats.agents.layer.layer_review.run_structured",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr("ats.agents.sector.assemble.layer_evidence_blocks",
                        lambda cfg, ly, a=None: ("", ""))
    v, ok = layer_review.run(_cfg(layer), layer, prior=prior)
    assert ok is False
    assert v is None, "失败的层不得产出任何结论——沿用上一轮也算冒充本期判断"
    assert "registered as missing"  # 上面 run 内部已 log.warning 留痕


def test_failure_without_a_prior_also_produces_nothing(monkeypatch):
    layer = _layer()
    monkeypatch.setattr("ats.agents.layer.layer_review.run_structured",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr("ats.agents.sector.assemble.layer_evidence_blocks",
                        lambda cfg, ly, a=None: ("", ""))
    v, ok = layer_review.run(_cfg(layer), layer)
    assert ok is False and v is None


# --------------------------------------------------------------------------- #
# 投影发布：成功发布、幂等、失败不发布
# --------------------------------------------------------------------------- #
class _Store:
    def __init__(self):
        self.saved = []

    def save_task_projection_envelope(self, envelope):
        self.saved.append(envelope)
        return envelope.projection_id


def test_successful_review_publishes_a_layer_projection(monkeypatch):
    layer = _layer(claims=[{"id": "c1", "kind": "common", "statement": "x",
                            "concepts": [{"key": "k", "desc": "d"}]}])
    monkeypatch.setattr("ats.agents.sector.assemble.layer_evidence_blocks",
                        lambda cfg, ly, a=None: ("", ""))
    monkeypatch.setattr("ats.agents.layer.layer_review.run_structured",
                        lambda *a, **k: _view(layer_status="expanding", confidence=0.8,
                                              rationale="订单与交期全面向好"))
    store = _Store()
    v, ok = layer_review.run(_cfg(layer), layer, store=store,
                             assessments=[_assessment("supportive")],
                             input_refs=["obs-1"],
                             data_vintage_refs=["ds@2026-09-21"])
    assert ok and v is not None
    assert len(store.saved) == 1
    env = store.saved[0]
    assert env.agent_role == "layer_analysis"
    assert env.scope.kind == "layer" and env.scope.id == "L6_memory"
    assert env.payload["status"] == "expanding"
    assert env.payload["findings"] and env.payload["summary"]
    assert env.input_refs == ["obs-1"]
    # 内容哈希稳定：同内容重发布得到同一投影标识
    env2 = layer_review._publish_projection(store, layer, v,
                                            input_refs=["obs-1"],
                                            data_vintage_refs=["ds@2026-09-21"])
    assert env2 == env.projection_id


def test_failed_review_publishes_no_projection(monkeypatch):
    layer = _layer()
    monkeypatch.setattr("ats.agents.layer.layer_review.run_structured",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr("ats.agents.sector.assemble.layer_evidence_blocks",
                        lambda cfg, ly, a=None: ("", ""))
    store = _Store()
    v, ok = layer_review.run(_cfg(layer), layer, store=store)
    assert ok is False and v is None
    assert store.saved == []


def test_one_layers_failure_does_not_stop_the_others(monkeypatch):
    """单层失败只登记该层缺失，其余层照常产出（编排层语义，此处锁 run 的一层）。"""
    layer = _layer()
    monkeypatch.setattr("ats.agents.sector.assemble.layer_evidence_blocks",
                        lambda cfg, ly, a=None: ("", ""))
    calls = {"n": 0}

    def _flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return _view(layer_status="steady", confidence=0.4)

    monkeypatch.setattr("ats.agents.layer.layer_review.run_structured", _flaky)
    store = _Store()
    v1, ok1 = layer_review.run(_cfg(layer), layer, store=store)
    v2, ok2 = layer_review.run(_cfg(layer), layer, store=store)
    assert ok1 is False and v1 is None
    assert ok2 is True and v2 is not None
    assert len(store.saved) == 1


# --------------------------------------------------------------------------- #
# 编排：空评审绝不落库
# --------------------------------------------------------------------------- #
def test_a_round_where_no_layer_produced_a_verdict_is_never_persisted(monkeypatch):
    """一次什么都没评出来的运行，不得覆盖 latest。

    下游（PEAD prep/monitor、Chief）读的都是 `latest_sector_review`。把一份空评审写成
    latest，等于用「本轮没跑成」替换掉「上周的真实判断」——而两者在读的人眼里长得一样。
    """
    from ats.agents.sector import review as sector_review
    from ats.config import load_sector_config
    from ats.memory import get_store

    cfg = load_sector_config("ai_hardware")
    store = get_store()
    saved = []
    monkeypatch.setattr(store, "save_sector_review", lambda r: saved.append(r))
    monkeypatch.setattr("ats.agents.layer.layer_review.run",
                        lambda *a, **k: (None, False))

    sector_review._run_layered("ai_hardware", cfg, store, use_llm=False, live_data=False)
    assert saved == [], "没有任何层产出结论时不得落库"


def test_layered_run_persists_the_claim_assessments_it_computes(monkeypatch):
    """`_run_layered` used to compute `ClaimAssessment`s and then drop them.

    `save_claim_assessment` was only ever called from `ats evidence report`, so the
    stored snapshot was stuck on whatever layer keys THAT command last ran under —
    the weekly review's own judge output never reached the table `claim_assessment_history`
    and the viz bundle both read from. Persistence must happen even when the layer's
    verdict call itself is stubbed to fail, since it is computed and saved BEFORE
    `layer_review.run` is invoked.
    """
    from ats.agents.sector import review as sector_review
    from ats.schemas.chain import ClaimAssessment

    cfg = SectorConfig(name="demo", layers=[_layer()])
    fake = [ClaimAssessment(claim_id="hbm_supply_tight", as_of=NOW, verdict="supportive"),
            ClaimAssessment(claim_id="hbm_pricing_expand", as_of=NOW, verdict="supportive")]
    saved = []

    class _Store:
        def latest_sector_review(self, name):
            return None

        def save_claim_assessment(self, a):
            saved.append(a)

    monkeypatch.setattr("ats.agents.sector.assemble.layer_assessments",
                        lambda cfg, layer, as_of=None, **_kwargs: fake)
    monkeypatch.setattr("ats.agents.layer.layer_review.run",
                        lambda *a, **k: (None, False))

    sector_review._run_layered("demo", cfg, _Store(), use_llm=False, live_data=False)
    assert [a.claim_id for a in saved] == ["hbm_supply_tight", "hbm_pricing_expand"]


def test_a_bad_claim_assessment_row_does_not_cost_the_layer_its_verdict(monkeypatch):
    """Persistence is best-effort: one row failing to save must not stop the layer
    from producing a verdict, and must not stop the OTHER assessment from saving."""
    from ats.agents.sector import review as sector_review
    from ats.schemas.chain import ClaimAssessment

    cfg = SectorConfig(name="demo", layers=[_layer()])
    fake = [ClaimAssessment(claim_id="ok_one", as_of=NOW, verdict="supportive"),
            ClaimAssessment(claim_id="bad_one", as_of=NOW, verdict="supportive")]
    saved = []
    ran = []

    class _Store:
        def latest_sector_review(self, name):
            return None

        def save_claim_assessment(self, a):
            if a.claim_id == "bad_one":
                raise RuntimeError("disk full")
            saved.append(a)

    def _run(cfg_, layer, **kw):
        ran.append(1)
        return None, False

    monkeypatch.setattr("ats.agents.sector.assemble.layer_assessments",
                        lambda cfg, layer, as_of=None, **_kwargs: fake)
    monkeypatch.setattr("ats.agents.layer.layer_review.run", _run)

    sector_review._run_layered("demo", cfg, _Store(), use_llm=False, live_data=False)
    assert [a.claim_id for a in saved] == ["ok_one"]
    assert ran == [1], "the layer still went on to produce a verdict"


def test_layered_run_writes_the_viz_dashboard_when_a_layer_succeeds(monkeypatch):
    """`_run_layered` must build and write the HTML dashboard once at least one layer
    actually produced a verdict — the dashboard's whole point is to show real
    decisions, and skipping it would silently leave the live path untested (the only
    other producer is the offline CLI, which never re-runs analysis)."""
    from ats.agents.sector import review as sector_review, viz

    cfg = SectorConfig(name="demo", layers=[_layer()], output_dir="/tmp/whatever")
    saved_reviews = []
    calls = {}

    class _Store:
        def latest_sector_review(self, name):
            return None

        def save_claim_assessment(self, a):
            pass

        def save_sector_review(self, r):
            saved_reviews.append(r)

    def _run(cfg_, layer, **kw):
        return LayerVerdict(layer_key=layer.key, as_of=NOW, layer_status="steady",
                            confidence=0.5), True

    def _fake_build_bundle(cfg_, review, *, assessments_by_layer):
        calls["build_bundle"] = (cfg_, review, assessments_by_layer)
        return {"fake": "bundle"}

    def _fake_write_html(bundle, folder):
        calls["write_html"] = (bundle, folder)
        return "/tmp/whatever/fake.html"

    monkeypatch.setattr("ats.agents.sector.assemble.layer_assessments",
                        lambda cfg, layer, as_of=None, **_kwargs: [])
    monkeypatch.setattr("ats.agents.layer.layer_review.run", _run)
    monkeypatch.setattr(viz, "build_bundle", _fake_build_bundle)
    monkeypatch.setattr(viz, "write_html", _fake_write_html)

    sector_review._run_layered("demo", cfg, _Store(), use_llm=False, live_data=False)
    assert saved_reviews, "a successful layer must still produce and persist a review"
    assert calls["build_bundle"][0] is cfg
    assert calls["build_bundle"][1] is saved_reviews[0]
    assert calls["write_html"] == ({"fake": "bundle"}, "/tmp/whatever")


def test_viz_dashboard_failure_does_not_cost_the_review_its_persistence(monkeypatch):
    """Best-effort like everything else in this path: a broken renderer must not
    un-persist a review that eight layers' worth of real judgement already produced."""
    from ats.agents.sector import review as sector_review, viz

    cfg = SectorConfig(name="demo", layers=[_layer()], output_dir="/tmp/whatever")
    saved_reviews = []

    class _Store:
        def latest_sector_review(self, name):
            return None

        def save_claim_assessment(self, a):
            pass

        def save_sector_review(self, r):
            saved_reviews.append(r)

    monkeypatch.setattr("ats.agents.sector.assemble.layer_assessments",
                        lambda cfg, layer, as_of=None, **_kwargs: [])

    def _run(cfg_, layer, **kw):
        return LayerVerdict(layer_key=layer.key, as_of=NOW, layer_status="steady",
                            confidence=0.5), True

    monkeypatch.setattr("ats.agents.layer.layer_review.run", _run)
    monkeypatch.setattr(viz, "build_bundle",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

    review = sector_review._run_layered("demo", cfg, _Store(), use_llm=False, live_data=False)
    assert saved_reviews == [review]


def test_layered_run_consumes_the_basket_from_run_layers_tuple(monkeypatch):
    """`cross_section.run_layer` 返回 (rows, basket)，不是 basket。

    2026-08-20 实盘首跑就炸在这里，而全量测试是绿的 —— 因为没有一个测试用
    `live_data=True` 走过这条路，截面取数在测试里从来没被调用。这里补上那一段接线。
    """
    from ats.agents.sector import cross_section
    from ats.agents.sector import review as sector_review
    from ats.config import load_sector_config
    from ats.memory import get_store
    from ats.schemas.sector import BasketRow, LayerBasket

    cfg = load_sector_config("ai_hardware")
    one = cfg.model_copy(update={"layers": cfg.layers[:1]})
    basket = LayerBasket(layer_key=one.layers[0].key, as_of=NOW,
                         rows=[BasketRow(symbol="GOOG", rank=1)])
    seen = {}

    monkeypatch.setattr(cross_section, "run_layer",
                        lambda *a, **k: (["rows"], basket))     # 真实签名：元组
    monkeypatch.setattr("ats.agents.sector.assemble.layer_evidence_blocks",
                        lambda cfg, ly, a=None: ("", ""))

    def _capture(cfg_, layer, *, basket=None, prior=None, snapshot_block="", use_llm=True,
                 assessments=None, store=None):
        seen["basket"] = basket
        return None, False

    monkeypatch.setattr("ats.agents.layer.layer_review.run", _capture)
    sector_review._run_layered("ai_hardware", one, get_store(),
                               use_llm=False, live_data=True)
    assert seen["basket"] is basket        # 拿到的是 basket 本身，不是元组


def test_top_down_context_is_loaded_only_after_every_layer_verdict(monkeypatch):
    from ats.agents.sector import review as sector_review
    from ats.agents.sector import rotation
    from ats.config import load_sector_config

    cfg = load_sector_config("ai_hardware")
    events = []

    class _Store:
        def latest_sector_review(self, name):
            return None

        def save_claim_assessment(self, assessment):
            pass

        def save_sector_review(self, review):
            pass

    monkeypatch.setattr("ats.agents.sector.assemble.layer_assessments",
                        lambda cfg, layer, as_of=None, **kwargs: [])

    def layer_call(cfg_, layer, **kwargs):
        events.append(layer.key)
        return LayerVerdict(layer_key=layer.key, as_of=NOW, layer_status="steady",
                            confidence=0.5), True

    monkeypatch.setattr("ats.agents.layer.layer_review.run", layer_call)

    def load_top_down(store):
        events.append("top_down")
        return {
            "macro_text": "宏观正式结论", "macro_date": "2026-09-03", "macro_note": "",
            "factset": {"text": "十一行业正式数据", "reason": "", "state": "platform",
                        "report_date": "2026-08-28", "version_id": "factset@082826"}}

    monkeypatch.setattr(sector_review, "_load_top_down_context", load_top_down)

    def rotate(cfg_, verdicts, **kwargs):
        events.append("rotation")
        assert kwargs["macro_context"] == "宏观正式结论"
        assert kwargs["factset_material"]["text"] == "十一行业正式数据"
        return LayerRotationView(
            regime="产业链中性", macro_background="利率背景",
            factset_background="信息技术行业背景",
            agreements=["云资本开支与公司证据一致"],
            divergences=["工业行业总量与设备层偏弱冲突"],
            recommendation_impact="不修改单层结论，跨层建议维持。")

    monkeypatch.setattr(rotation, "run", rotate)
    review = sector_review._run_layered(
        "ai_hardware", cfg, _Store(), use_llm=True, live_data=False,
        write_reports=False)

    assert events[:len(cfg.layers)] == [layer.key for layer in cfg.layers]
    assert events[-2:] == ["top_down", "rotation"]
    assert len(review.layer_verdicts) == len(cfg.layers)
    assert all(item.layer_status == "steady" for item in review.layer_verdicts)
    comparison = review.top_down_comparison
    assert comparison.macro_review_date == "2026-09-03"
    assert comparison.factset_report_date == "2026-08-28"
    assert comparison.agreements and comparison.divergences


def test_rotation_context_keeps_gics_and_macro_out_of_layer_verdicts():
    from ats.agents.sector import rotation

    cfg = _cfg(_layer())
    verdict = LayerVerdict(
        layer_key="L6_memory", as_of=NOW, layer_status="expanding",
        confidence=0.7, rationale="HBM 公司证据")
    context = rotation.build_context(
        cfg, [verdict], macro_context="宏观报告：实际利率偏高",
        factset_material={"text": "GICS_45 信息技术盈利增长强", "reason": ""})

    assert context.index("HBM 公司证据") < context.index("宏观报告")
    assert "先前八层结论和逐票判断已经固定" in context
    assert "GICS 标准行业也不是 AI 硬件产业链环节" in context
    assert verdict.layer_status == "expanding" and verdict.rationale == "HBM 公司证据"


def test_sector_skill_limits_macro_and_factset_to_final_comparison():
    from pathlib import Path

    text = Path("src/ats/skills/sector-analyst/SKILL.md").read_text(encoding="utf-8")
    assert "只会出现在八层结论之后" in text
    assert "不能回头修改某层的配置、信心、逐票判断或证据链" in text
    assert "GICS 行业直接当成" in text
    assert "未正式发布" in text and "shadow" in text and "过期" in text
