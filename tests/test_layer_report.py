"""层报告：一层一份、结论先行、证据可核对。

这份报告要同时服务两种人：下单的人只读第一节，核对的人往下读。所以**顺序是载荷**——
把证据放前面等于每周先读三千字才看到答案，把证据砍掉则第一节只是一个断言。
"""

from datetime import datetime, timezone

import pytest

from ats.agents.sector import report
from ats.schemas.chain import ClaimAssessment, ClusterJudgement, EntityReading
from ats.schemas.sector import (BasketRow, CandidateClaim, LayerBasket, LayerNameCall,
                                LayerVerdict, SectorConfig, SectorLayer)

NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)

COMMON = {"id": "supply_tight", "kind": "common", "statement": "供给持续紧张",
          "concepts": [{"key": "tightness", "desc": "紧张程度"}]}
RELATIVE = {"id": "share_split", "kind": "relative", "entities": ["MU", "SKHY"],
            "statement": "份额如何分布", "concepts": [{"key": "share", "desc": "份额"}]}


def _cfg(claims=(COMMON, RELATIVE)):
    return SectorConfig(name="demo", label="测试行业", output_dir="", layers=[
        SectorLayer(key="L6_memory", label="L6 存储", weight_cap=0.25,
                    tickers=[{"symbol": "MU", "subgroup": "HBM"},
                             {"symbol": "SKHY", "subgroup": "HBM"}],
                    claims=list(claims))])


def _verdict(**kw):
    kw.setdefault("layer_key", "L6_memory")
    kw.setdefault("as_of", NOW)
    kw.setdefault("allocation", "超配")
    kw.setdefault("confidence", 0.8)
    return LayerVerdict(**kw)


def _basket(**kw):
    kw.setdefault("layer_key", "L6_memory")
    kw.setdefault("as_of", NOW)
    kw.setdefault("layer_cap", 0.167)
    kw.setdefault("rows", [BasketRow(symbol="MU", subgroup="HBM", rank=1, weight=0.10),
                           BasketRow(symbol="SKHY", subgroup="HBM", rank=2, weight=0.067)])
    return LayerBasket(**kw)


def _common_assessment(**kw):
    kw.setdefault("claim_id", "supply_tight")
    kw.setdefault("as_of", NOW)
    kw.setdefault("verdict", "supportive")
    kw.setdefault("basis", "corroborated")
    kw.setdefault("witnesses_expected", 9)
    kw.setdefault("witnesses_reported", 8)
    kw.setdefault("evidence_clusters", 65)
    kw.setdefault("stance_classes", 3)
    kw.setdefault("support_score", 39.0)
    return ClaimAssessment(**kw)


# --------------------------------------------------------------------------- #
# 结论先行
# --------------------------------------------------------------------------- #
def test_conclusion_comes_before_the_evidence():
    md = report.render_layer(_verdict(), _cfg().layers[0], _cfg(), basket=_basket())
    assert md.index("## 一、本层结论") < md.index("## 二、议题证据链") < md.index("## 三、截面明细")


def test_first_section_is_enough_to_act_on():
    # 配置、预算、以及层内怎么分 —— 三样都要在第一节里。
    v = _verdict(cycle_position="中周期",
                 name_calls=[LayerNameCall(symbol="MU", subgroup="HBM", stance="增持")])
    md = report.render_layer(v, _cfg().layers[0], _cfg(), basket=_basket())
    first = md.split("## 二、")[0]
    assert "超配" in first and "16.7% NAV" in first and "中周期" in first
    assert "MU" in first and "增持" in first and "10.0%" in first     # 逐票权重也在第一节


def test_reversal_triggers_render_as_a_checklist():
    v = _verdict(reversal_triggers=["售罄表述消失", "出现降价指引"])
    first = report.render_layer(v, _cfg().layers[0], _cfg()).split("## 二、")[0]
    assert "- [ ] 售罄表述消失" in first and "- [ ] 出现降价指引" in first


# --------------------------------------------------------------------------- #
# 第二节：议题证据链
# --------------------------------------------------------------------------- #
def test_claim_verdict_carries_its_full_basis():
    md = report.render_layer(_verdict(), _cfg().layers[0], _cfg(),
                             assessments=[_common_assessment()])
    for expect in ("8/9", "65", "3 类", "支持 39", "有交叉印证"):
        assert expect in md, f"证据链里缺 {expect}"


def test_silence_is_named_never_folded_into_neutral():
    """`expect_from` 存在的全部理由就是让沉默显示成缺口而不是中性。

    报告若只渲染「说了什么」而不渲染「谁该说却没说」，那条纪律在最终产物上就失效了。
    """
    a = _common_assessment(silent_witnesses=["NVDA", "AMD"])
    md = report.render_layer(_verdict(), _cfg().layers[0], _cfg(), assessments=[a])
    assert "NVDA" in md and "AMD" in md
    assert "未发声" in md and "这是缺口，不是中性" in md


def test_each_judgement_shows_speaker_dimension_and_reason():
    a = _common_assessment(judgements=[
        ClusterJudgement(cluster_key="k1", polarity="support", speaker="TSM",
                         concept="packaging_throughput", reason="封装产能处于短缺模式")])
    md = report.render_layer(_verdict(), _cfg().layers[0], _cfg(), assessments=[a])
    assert "TSM" in md and "packaging_throughput" in md and "封装产能处于短缺模式" in md


def test_no_claims_and_no_verdicts_are_worded_differently():
    # 配置缺口 vs 证据缺口 —— 混为一谈，配置缺口会被当成「行业没消息」而永远不被发现。
    bare = _cfg(claims=())
    md_no_claims = report.render_layer(_verdict(has_claims=False), bare.layers[0], bare)
    assert "配置缺口" in md_no_claims and "该建的命题还没建" in md_no_claims

    md_no_evidence = report.render_layer(_verdict(has_claims=True), _cfg().layers[0], _cfg(),
                                         assessments=[])
    assert "证据缺口" in md_no_evidence and "配置缺口" not in md_no_evidence


# --------------------------------------------------------------------------- #
# 第三节：截面明细含 relative 逐家读数
# --------------------------------------------------------------------------- #
def test_structure_scores_are_traceable_to_their_readings():
    a = ClaimAssessment(claim_id="share_split", as_of=NOW, verdict="resolved",
                        entity_readings=[
                            EntityReading(entity="MU", standing="neutral",
                                          basis="self_reported", speakers=["MU"],
                                          reason="定价设了上限"),
                            EntityReading(entity="SKHY", standing="strong",
                                          basis="corroborated", speakers=["SKHY", "NVDA"],
                                          reason="HBM4 已量产")])
    md = report.render_layer(_verdict(), _cfg().layers[0], _cfg(), basket=_basket(),
                             assessments=[a])
    assert "逐家读数" in md
    assert "定价设了上限" in md and "HBM4 已量产" in md
    assert "仅自述" in md and "有交叉印证" in md
    assert "neutral" not in md, "位置取值必须译成中文，不能漏出英文字面量"


def test_all_self_reported_readings_carry_the_caveat():
    a = ClaimAssessment(claim_id="share_split", as_of=NOW, verdict="resolved",
                        entity_readings=[
                            EntityReading(entity="MU", standing="strong",
                                          basis="self_reported", speakers=["MU"])])
    md = report.render_layer(_verdict(), _cfg().layers[0], _cfg(), basket=_basket(),
                             assessments=[a])
    assert "无客户或第三方交叉印证" in md


def test_without_relative_claims_the_ranking_is_not_a_competitive_read():
    md = report.render_layer(_verdict(), _cfg().layers[0], _cfg(), basket=_basket(),
                             assessments=[_common_assessment()])
    assert "仅由量化因子决定" in md
    assert "不是竞争位置" in md


def test_inapplicable_cross_section_says_the_rank_is_not_a_finding():
    b = _basket(cross_section_applicable=False,
                rows=[BasketRow(symbol="MU", rank=1, weight=0.04)])
    md = report.render_layer(_verdict(cross_section_applicable=False),
                             _cfg().layers[0], _cfg(), basket=b)
    assert "截面不适用" in md and "不要据名次做层内取舍" in md


# --------------------------------------------------------------------------- #
# 第五节：候选议题
# --------------------------------------------------------------------------- #
def test_candidates_render_with_witness_and_falsifier_and_are_marked_inert():
    v = _verdict(candidate_claims=[CandidateClaim(
        statement="需求正外溢到 NAND", witnesses=["SNDK"], falsifier="出现售价下行指引")])
    md = report.render_layer(v, _cfg().layers[0], _cfg())
    assert "需求正外溢到 NAND" in md and "SNDK" in md and "出现售价下行指引" in md
    assert "不参与本期任何计算" in md


def test_candidates_do_not_touch_weights():
    plain = _verdict()
    with_cand = _verdict(candidate_claims=[CandidateClaim(
        statement="s", witnesses=["MU"], falsifier="f")])
    b1 = _basket()
    md1 = report.render_layer(plain, _cfg().layers[0], _cfg(), basket=b1)
    md2 = report.render_layer(with_cand, _cfg().layers[0], _cfg(), basket=b1)
    # 第一节（结论与权重）逐字相同 —— 候选只是多出来的一节
    assert md1.split("## 二、")[0] == md2.split("## 二、")[0]


# --------------------------------------------------------------------------- #
# 一层一份文件
# --------------------------------------------------------------------------- #
def test_one_file_per_layer_named_after_the_current_layer(tmp_path):
    cfg = _cfg().model_copy(update={"output_dir": str(tmp_path)})
    path = report.write_layer(_verdict(), cfg.layers[0], cfg, basket=_basket())
    assert path is not None and path.exists()
    assert path.name == "层分析-测试行业-L6存储-2026-08-20.md"
    assert list(tmp_path.glob("*.md")) == [path]          # 同一层不出第二份


def test_rerunning_the_same_day_overwrites(tmp_path):
    cfg = _cfg().model_copy(update={"output_dir": str(tmp_path)})
    report.write_layer(_verdict(allocation="低配"), cfg.layers[0], cfg)
    report.write_layer(_verdict(allocation="超配"), cfg.layers[0], cfg)
    assert len(list(tmp_path.glob("*.md"))) == 1
    assert "超配" in list(tmp_path.glob("*.md"))[0].read_text(encoding="utf-8")


def test_aggregate_report_is_an_index_not_a_copy():
    from ats.schemas.sector import SectorReview

    cfg = _cfg()
    v = _verdict(claim_attributions=["供给紧张 → 支持超配"],
                 name_calls=[LayerNameCall(symbol="MU", stance="增持",
                                           rationale="独有的详细理由文本")])
    r = SectorReview(sector="demo", as_of=NOW, regime="紧", rotation_advice="加 L6",
                     layer_verdicts=[v], baskets=[_basket()])
    md = report.render(r, cfg)
    assert "八层结论索引" in md and "超配" in md
    # 细节留在层报告里：两份文档抄同一份内容，其中一份迟早悄悄过期。
    assert "独有的详细理由文本" not in md
