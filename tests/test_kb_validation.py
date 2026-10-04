"""Two ways to check the knowledge base is being USED, not just cited (hermetic).

`kb_audit` is deterministic and runs weekly; `kb_perturb`'s transforms are pure text
functions and are tested here without touching a model. The perturbation run itself
needs two live calls and is exercised by hand (`ats sector kbperturb`).
"""

from datetime import datetime, timezone

from ats.agents.sector import kb_perturb
from ats.chain import kb_audit
from ats.config import load_sector_config
from ats.memory import get_store
from ats.schemas.chain import Observation
from ats.schemas.sector import BasketRow, LayerBasket, SectorReview

NOW = datetime(2026, 8, 11, tzinfo=timezone.utc)

NOTE = """# 测试子层
## 一、价值链分工
衬底 → 激光器 → 模块
## 二、技术曲线
光每代蚕食一段。
## 三、护城河判据
1. **瓶颈环节的所有权** —— 拥有瓶颈 = 拥有定价权。**最硬**。
2. **垂直整合深度** —— 打通了几段。
3. **模块封装规模** —— 组装壁垒低。**最软**。
## 四、常见误判
高毛利 ≠ 定价权。
"""

# The same note after it outgrew the four-section template: criteria at §五, two extra
# sections around them. Every check below that uses NOTE must also hold here — the old
# `split("## 三")` implementation passed the whole suite while being a no-op on exactly
# this shape, because no fixture ever moved the criteria off §三.
NOTE_RENUMBERED = """# 测试子层
## 一、收入分解
收入 ≈ 产能 × 份额。
## 二、价值链分工
衬底 → 激光器 → 模块
## 三、技术曲线
光每代蚕食一段。
## 四、Fabless 的边界
轻资产不等于没有资本开支。
## 五、护城河判据：先看平台
1. **瓶颈环节的所有权** —— 拥有瓶颈 = 拥有定价权。**最硬**。
2. **垂直整合深度** —— 打通了几段。
3. **模块封装规模** —— 组装壁垒低。**最软**。
## 六、风险的时间尺度
物理替代慢，出口管制快。
## 七、常见误判
高毛利 ≠ 定价权。
"""


def _review(rows, layer_key="L4_interconnect", as_of=NOW):
    return SectorReview(sector="ai_hardware", as_of=as_of, baskets=[
        LayerBasket(layer_key=layer_key, as_of=as_of, structural=True, rows=rows)])


# --- audit ---------------------------------------------------------------- #
def test_attributing_a_company_claim_to_the_kb_is_fabrication():
    """The rewritten notes contain no ticker and no company name — by construction,
    they give criteria and not rankings. So "KB 指出 COHR 最强" cannot have come from
    the KB, and saying so needs no judgement about content."""
    store = get_store()
    store.save_sector_review(_review([
        BasketRow(symbol="COHR", moat_pricing=1.5,
                  rationale="KB 指出 COHR 自有衬底产能最强，故给高分")]))

    reps = kb_audit.audit(load_sector_config("ai_hardware"), store)
    kinds = [f.kind for r in reps for f in r.findings]
    assert "fabricated" in kinds


def test_saying_the_kb_does_not_cover_a_name_is_not_fabrication():
    """The one legitimate way a company name and the KB appear in one sentence — and
    it is exactly what the analyst is instructed to write when it has no criteria.
    Flagging it would make the check fire on compliance with its own rules."""
    store = get_store()
    store.save_sector_review(_review([
        BasketRow(symbol="MRVL", rationale="KB 未覆盖 MRVL 所属环节，按纪律中性")]))

    reps = kb_audit.audit(load_sector_config("ai_hardware"), store)
    assert not [f for r in reps for f in r.findings if f.kind == "fabricated"]


def test_grounding_is_classified_not_accused():
    """A score resting on the sector review is weaker than one resting on a filing, but
    it is a real, auditable source. An earlier version reported it as "no source" —
    including rows whose rationale named the review in the flagged sentence."""
    store = get_store()
    store.save_observation(Observation(
        document_id="d1", entity="COHR", source_entity="COHR", metric="asp",
        concept="pricing", period="FY26Q3", observation_type="reported_actual",
        stance="incumbent", direction="up", evidence_span="ASP up", observed_at=NOW))
    store.save_sector_review(_review([
        BasketRow(symbol="COHR", moat_pricing=1.5, rationale="ASP 上行"),
        BasketRow(symbol="LITE", moat_pricing=1.0, rationale="行业评审指出其受益于短缺"),
        BasketRow(symbol="AAOI", moat_pricing=0.5, rationale="毛利率不错")]))

    rep = kb_audit.audit(load_sector_config("ai_hardware"), store)[0]
    assert rep.grounding["台账读数"] == ["COHR"]
    assert rep.grounding["行业评审"] == ["LITE"]
    assert [f.symbol for f in rep.findings if f.kind == "ungrounded"] == ["AAOI"]


def test_criterion_citation_matches_how_people_actually_write():
    """Titles read 「软件生态的实际锁定深度」 while rationales write 「软件生态、系统级
    互联」. Requiring the whole title scored L4 at 0/5 while every rationale was visibly
    citing criteria — a coverage metric that wrong is worse than none, because it reads
    as "the KB is being ignored" when the opposite is true."""
    keys = kb_audit._criterion_keys("软件生态的实际锁定深度")
    assert "软件生态" in keys
    assert kb_audit._criterion_keys("系统级绑定（互联/整机）") == ["系统级绑定"]


def test_criteria_are_parsed_by_heading_not_by_section_number(tmp_path):
    """Same regression on the audit side: criteria at §五 must still be counted.

    When they were not, nothing said so — `criteria_total` fell to 0, which suppressed
    the coverage line, emptied `uncited`, and let the layer render 「无异常」.
    """
    p = tmp_path / "renumbered.md"
    p.write_text(NOTE_RENUMBERED, encoding="utf-8")
    assert kb_audit._criteria_of(p) == [
        "瓶颈环节的所有权", "垂直整合深度", "模块封装规模"]
    assert kb_audit._criteria_gap(p) == ""


def test_every_mounted_note_yields_criteria():
    """Pins the parser to the real notes, which is where the coupling actually broke.

    The suite passed for as long as it did because every fixture put the criteria at §三.
    This one fails the moment a note is restructured out of the parser's reach — which is
    the event that silently disabled the audit for L4 and L6.
    """
    from ats.config import REPO_ROOT

    cfg = load_sector_config("ai_hardware")
    mounted = {rel for layer in cfg.layers for rel in layer.structure_notes.values()}
    assert mounted, "no layer mounts a knowledge note — the audit would measure nothing"
    for rel in sorted(mounted):
        path = REPO_ROOT / rel
        assert kb_audit._criteria_gap(path) == "", f"{rel}: {kb_audit._criteria_gap(path)}"
        assert kb_audit._criteria_of(path), f"{rel} contributed no criteria"


def test_a_note_with_no_criteria_section_is_reported_not_swallowed(tmp_path):
    """Zero criteria must read as "measured nothing", never as a clean run."""
    p = tmp_path / "headless.md"
    p.write_text("# 笔记\n## 一、价值链分工\n只有分工，没有判据。\n", encoding="utf-8")
    assert kb_audit._criteria_of(p) == []
    assert "判据" in kb_audit._criteria_gap(p)

    rep = kb_audit.AuditReport(layer="L9_test")
    rep.findings.append(kb_audit.AuditFinding(
        kind="no_criteria", layer="L9_test", symbol="", detail="x"))
    assert not rep.ok, "a note defect must not leave the layer looking healthy"


# --- perturbation transforms ---------------------------------------------- #
def test_ablate_removes_only_the_moat_criteria():
    out = kb_perturb.ablate(NOTE)
    assert "瓶颈环节的所有权" not in out and "垂直整合深度" not in out
    assert "衬底 → 激光器 → 模块" in out          # §一 kept
    assert "光每代蚕食一段" in out                 # §二 kept
    assert "高毛利 ≠ 定价权" in out                # §四 kept


def test_poison_reverses_the_order_and_the_hard_soft_labels():
    """Reversing rather than rewriting keeps the vocabulary identical, so the two arms
    differ in ORDER and nothing else — otherwise a score change could be attributed to
    new wording rather than to the ranking."""
    out = kb_perturb.poison(NOTE)
    body = out.split("## 三")[1].split("## 四")[0]
    order = [ln for ln in body.splitlines() if ln.strip().startswith(("1.", "2.", "3."))]
    assert "模块封装规模" in order[0] and "瓶颈环节的所有权" in order[-1]
    assert "**最硬**" in order[0] and "**最软**" in order[-1]
    for token in ("瓶颈环节的所有权", "垂直整合深度", "模块封装规模"):
        assert token in out                        # nothing invented, nothing lost


def test_control_changes_nothing_so_a_delta_can_be_read():
    """Without a noise floor a delta is uninterpretable: two arms are two model calls.
    Measured floors differ a lot by layer (0.05 on L6, 0.33 on L3), so a fixed
    threshold alone would call the same number a signal in one layer and noise in
    another."""
    assert kb_perturb.control(NOTE) == NOTE


def test_a_note_without_the_criteria_section_is_returned_unchanged():
    plain = "# 笔记\n只有一段话。\n"
    assert kb_perturb.ablate(plain) == plain
    assert kb_perturb.poison(plain) == plain


def test_perturbation_finds_the_criteria_by_heading_not_by_section_number():
    """The regression. A note whose criteria sit at §五 must still be ablated at §五 —
    and the sections that merely HAPPEN to be §三/§四 must survive untouched.

    Before this, `ablate` deleted 技术曲线 while writing a heading announcing that the
    criteria had been removed, and `poison` returned the note unchanged — a control arm
    labelled 投毒, whose null result reads as 「判据不是 load-bearing」. That is the one
    conclusion the perturbation test exists to rule out, so the bug did not just lose
    signal, it manufactured the wrong answer.
    """
    out = kb_perturb.ablate(NOTE_RENUMBERED)
    assert "瓶颈环节的所有权" not in out and "模块封装规模" not in out
    assert "光每代蚕食一段" in out                  # §三 kept — it is NOT the criteria
    assert "轻资产不等于没有资本开支" in out          # §四 kept
    assert "物理替代慢，出口管制快" in out            # §六 kept
    assert "高毛利 ≠ 定价权" in out                 # §七 kept

    poisoned = kb_perturb.poison(NOTE_RENUMBERED)
    assert poisoned != NOTE_RENUMBERED
    body = poisoned.split("## 五")[1].split("## 六")[0]
    order = [ln for ln in body.splitlines() if ln.strip().startswith(("1.", "2.", "3."))]
    assert "模块封装规模" in order[0] and "瓶颈环节的所有权" in order[-1]


def test_perturbed_copies_never_touch_the_real_notes(tmp_path):
    """Poison writes a lie about the industry. It may exist only in a temp dir."""
    from ats.config import REPO_ROOT

    src = REPO_ROOT / "config" / "knowledge" / "光互联.md"
    before = src.read_text(encoding="utf-8")
    mapping = kb_perturb._perturbed_notes({"光互联": "config/knowledge/光互联.md"},
                                          "poison", tmp_path)
    assert src.read_text(encoding="utf-8") == before
    assert str(tmp_path) in mapping["光互联"]


def test_one_arm_abstaining_is_reported_as_invalid_not_as_an_effect():
    """A whole-cohort abstention renders as a clean sweep to zero — i.e. as the most
    dramatic possible "the KB is load-bearing" result. Observed for real on L3, on both
    sides, with identical notes in both arms."""
    base = kb_perturb.ArmScores("原始", {"COHR": (1.2, 1.2, ""), "LITE": (1.2, 1.0, "")})
    dead = kb_perturb.ArmScores("投毒", {"COHR": (0.0, 0.0, ""), "LITE": (0.0, 0.0, "")})

    out = kb_perturb.render(base, dead, mode="poison")
    assert "本次比较无效" in out
    assert "load-bearing" not in out.split("本次比较无效")[1]


def test_abstentions_do_not_dilute_the_measured_effect():
    """Averaging over the whole cohort let five names the overlay never scored dilute a
    clean 1.2 collapse on the two that mattered down to 0.36 — turning the strongest
    available evidence into "inconclusive"."""
    base = kb_perturb.ArmScores("原始", {
        "COHR": (1.2, 1.2, ""), "LITE": (1.2, 1.2, ""),
        **{s: (0.5, 0.0, "") for s in ("AAOI", "AXT", "CRDO", "MRVL", "VRT")}})
    other = kb_perturb.ArmScores("消融", {
        "COHR": (1.2, 0.0, ""), "LITE": (1.2, 0.0, ""),
        **{s: (0.5, 0.0, "") for s in ("AAOI", "AXT", "CRDO", "MRVL", "VRT")}})

    out = kb_perturb.render(base, other, mode="ablate")
    assert "有信息的标的 2/7" in out
    assert "load-bearing" in out
