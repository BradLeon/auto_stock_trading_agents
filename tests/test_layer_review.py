"""层级分析师：配置结论、两类议题的定向、护栏、以及失败降级。

这里锁住的核心区分是**「本层无命题」与「证据缺失」不是一回事**：前者是配置缺口
（该建的命题没建），后者是证据缺口（本季没人发声）。两者都给标配 + 低 confidence，
但混为一谈会让配置缺口被当成「行业没消息」，从而永远不被发现。
"""

from datetime import datetime, timezone

import pytest

from ats.agents.sector import layer_review
from ats.agents.sector.outputs import LayerVerdictView
from ats.schemas.sector import LayerBasket, BasketRow, LayerVerdict, SectorConfig, SectorLayer

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


# --------------------------------------------------------------------------- #
# 配置结论的解析与钳制
# --------------------------------------------------------------------------- #
def test_unknown_allocation_falls_back_to_flat():
    v = layer_review._to_verdict(_layer(), _view(allocation="梭哈", confidence=0.9),
                                 has_claims=True, cross_ok=True)
    assert v.allocation == "标配"


@pytest.mark.parametrize("raw,expected", [(-1.0, 0.0), (0.55, 0.55), (7.0, 1.0)])
def test_confidence_is_clamped(raw, expected):
    v = layer_review._to_verdict(_layer(), _view(allocation="超配", confidence=raw),
                                 has_claims=True, cross_ok=True)
    assert v.confidence == expected


def test_a_layer_without_claims_cannot_report_high_confidence():
    # 在**代码里**封顶，不是在提示词里客气地要求：一个没有命题支撑却很自信的结论，
    # 形状上正好就是会去推动预算的那种。
    v = layer_review._to_verdict(_layer(claims=[]), _view(allocation="超配", confidence=0.95),
                                 has_claims=False, cross_ok=True)
    assert v.confidence <= layer_review.BLIND_CONFIDENCE_CAP
    assert v.has_claims is False


def test_name_calls_outside_the_layer_are_dropped():
    v = layer_review._to_verdict(
        _layer(cohort_extra=["TSM"]),
        _view(name_calls=[{"symbol": "MU", "stance": "增持"},
                          {"symbol": "TSM", "stance": "持有"},     # cohort_extra，保留
                          {"symbol": "AAPL", "stance": "减持"}]),  # 不在本层，丢弃
        has_claims=True, cross_ok=True)
    assert {c.symbol for c in v.name_calls} == {"MU", "TSM"}


def test_unknown_stance_falls_back_to_hold():
    v = layer_review._to_verdict(_layer(), _view(name_calls=[{"symbol": "MU", "stance": "抄底"}]),
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
    prior = LayerVerdict(layer_key="L6_memory", as_of=NOW, allocation="超配",
                         confidence=0.7, reversal_triggers=["售罄表述消失", "出现降价指引"])
    ctx = layer_review.build_context(_cfg(layer), layer, prior=prior)
    assert "上一轮" in ctx and "逐条说明是否已被触发" in ctx
    assert "售罄表述消失" in ctx and "出现降价指引" in ctx


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
# 失败降级
# --------------------------------------------------------------------------- #
def test_failure_carries_the_previous_verdict_forward_and_says_so(monkeypatch):
    layer = _layer()
    prior = LayerVerdict(layer_key="L6_memory", as_of=NOW, allocation="超配", confidence=0.7,
                         rationale="上一轮的理由")
    monkeypatch.setattr("ats.agents.sector.layer_review.run_structured",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr("ats.agents.sector.assemble.layer_evidence_blocks",
                        lambda cfg, ly, a=None: ("", ""))
    v, ok = layer_review.run(_cfg(layer), layer, prior=prior)
    assert ok is False                       # 调用方据此决定不落库
    assert v.allocation == "超配"             # 沿用，而不是悄悄变成一次新的标配
    assert "本轮评审失败" in v.rationale


def test_failure_without_a_prior_refuses_to_guess(monkeypatch):
    layer = _layer()
    monkeypatch.setattr("ats.agents.sector.layer_review.run_structured",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr("ats.agents.sector.assemble.layer_evidence_blocks",
                        lambda cfg, ly, a=None: ("", ""))
    v, ok = layer_review.run(_cfg(layer), layer)
    assert ok is False and v.confidence == 0.0
    assert "取不到判断" in v.rationale       # 取不到 ≠ 判断为中性


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
    monkeypatch.setattr("ats.agents.sector.layer_review.run",
                        lambda *a, **k: (layer_review._fallback(a[1], None, True, False), False))

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
    monkeypatch.setattr(layer_review, "run",
                        lambda *a, **k: (layer_review._fallback(a[1], None, True, False), False))

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
        return layer_review._fallback(layer, None, True, False), False

    monkeypatch.setattr("ats.agents.sector.assemble.layer_assessments",
                        lambda cfg, layer, as_of=None, **_kwargs: fake)
    monkeypatch.setattr(layer_review, "run", _run)

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
        return layer_review._fallback(layer, None, True, True), True

    def _fake_build_bundle(cfg_, review, *, assessments_by_layer):
        calls["build_bundle"] = (cfg_, review, assessments_by_layer)
        return {"fake": "bundle"}

    def _fake_write_html(bundle, folder):
        calls["write_html"] = (bundle, folder)
        return "/tmp/whatever/fake.html"

    monkeypatch.setattr("ats.agents.sector.assemble.layer_assessments",
                        lambda cfg, layer, as_of=None, **_kwargs: [])
    monkeypatch.setattr(layer_review, "run", _run)
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
    monkeypatch.setattr(layer_review, "run",
                        lambda cfg_, layer, **kw: (layer_review._fallback(layer, None, True, True), True))
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
                 assessments=None):
        seen["basket"] = basket
        return layer_review._fallback(layer, None, True, False), False

    monkeypatch.setattr(layer_review, "run", _capture)   # review.py 内是延迟 import
    sector_review._run_layered("ai_hardware", one, get_store(),
                               use_llm=False, live_data=True)
    assert seen["basket"] is basket        # 拿到的是 basket 本身，不是元组
