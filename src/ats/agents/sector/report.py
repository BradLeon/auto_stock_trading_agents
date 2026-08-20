"""Obsidian markdown report for a SectorReview.

Always creates its own file (行业分析-<label>-<date>.md) — never appends to the
user's own notes. Same-day reruns overwrite (idempotent). Unset/missing output
dir degrades to None.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ...schemas.sector import SectorConfig, SectorReview

log = logging.getLogger("ats.agents.sector.report")


def render(review: SectorReview, cfg: SectorConfig) -> str:
    pead = _pead_symbols(cfg)
    lines = [
        f"# 🤖 行业分析 — {cfg.label}（{review.as_of:%Y-%m-%d}）",
        "",
        f"> 由 `ats sector review {cfg.name}` 自动生成（sector_analyst，每周评审）。",
        "",
        "## 行业状态",
        f"**Regime**: {review.regime}",
        "",
        review.summary,
        "",
    ]

    if review.layer_verdicts:
        lines += _layer_verdict_section(review, cfg, pead)
    elif review.layers:
        lines += _legacy_layer_section(review, cfg)

    if review.company_calls and not review.layer_verdicts:
        lines += ["", "## 个股观点", "", "| 层 | 代码 | 观点 | 信心 | 理由 |", "|---|---|---|---|---|"]
        layer_order = {layer.key: i for i, layer in enumerate(cfg.layers)}
        for c in sorted(review.company_calls, key=lambda c: layer_order.get(c.layer, 99)):
            sym = f"**{c.symbol}**" if c.symbol in pead else c.symbol
            label = next((la.label for la in cfg.layers if la.key == c.layer), c.layer)
            lines.append(f"| {label} | {sym} | {c.stance} | {c.conviction:.2f} | {c.rationale} |")

    lines += ["", "## 跨层轮动", "", review.rotation_advice or "（本轮未产出）",
              "", "## 主要风险", ""]
    lines += [f"- {r}" for r in review.top_risks]
    lines += ["", "---",
              f"*universe {len(cfg.all_symbols())} 家（**加粗** = PEAD 活体档案标的）；"
              f"数据截至 {review.as_of:%Y-%m-%d %H:%M} UTC*", ""]
    return "\n".join(lines)


def _layer_verdict_section(review: SectorReview, cfg: SectorConfig, pead: set) -> list[str]:
    """The cross-layer report's INDEX — one row per layer, pointing at the layer report.

    Deliberately not a copy of each layer's detail: the evidence chain, the factor table
    and the per-name reasoning live in `层分析-<层>-<日期>.md`, one file per layer. Two
    documents carrying the same content is how one of them silently goes stale.
    """
    budgets = _budgets(cfg)
    by_key = {v.layer_key: v for v in review.layer_verdicts}
    basket_by_key = {b.layer_key: b for b in review.baskets}
    order = {ly.key: i for i, ly in enumerate(cfg.layers)}

    lines = ["## 八层结论索引（需求沿 L1→L8 传导）", "",
             "> 每层的议题证据链、截面明细与逐票理由在各自的**层分析**报告里，此处只给结论。",
             "",
             "| 层 | 配置 | 信心 | 预算 | 周期 | 一句依据 | 标记 |",
             "|---|---|---|---|---|---|---|"]
    for key in sorted(by_key, key=lambda k: order.get(k, 99)):
        v = by_key[key]
        label = next((ly.label for ly in cfg.layers if ly.key == key), key)
        flags = []
        if not v.has_claims:
            flags.append("**无命题**（配置缺口）")
        if not v.cross_section_applicable:
            flags.append("截面不适用")
        budget = basket_by_key[key].layer_cap if key in basket_by_key else budgets.get(key)
        gist = (v.claim_attributions[0] if v.claim_attributions
                else (v.rationale.splitlines()[0] if v.rationale else "—"))
        lines.append(f"| {label} | **{v.allocation}** | {v.confidence:.2f} | "
                     f"{budget:.1%} | {v.cycle_position.split('：')[0][:12] or '—'} | "
                     f"{gist[:70]} | {'；'.join(flags) or '—'} |")
    lines.append("")
    return lines


# 判读词的中文映射有唯一真源，别在这里重抄一份 —— 2026-08-20 抄错过一次
# （`mid` 而实际取值是 `neutral`，于是报告里直接漏出英文）。
POLARITY_MARK = {"support": "＋", "refute": "－", "neutral": "・"}
UNRESOLVED_CN = {"dissent": "证据互相冲突", "single_stance": "只有单一立场且独立信源不足"}


def _cn():
    """(verdict, basis, standing) 三张映射表，取自各自的定义处。"""
    from ...chain.factor_evidence import BASIS_CN, STANDING_CN
    from ...chain.report import VERDICT_MARK

    return VERDICT_MARK, BASIS_CN, STANDING_CN


def render_layer(verdict, layer, cfg: SectorConfig, *, basket=None, assessments=None,
                 rows=None) -> str:
    """One layer's report. **Conclusions first** — evidence and reasoning come after.

    Section 1 must be enough to act on: how much this layer gets, and how it splits
    across names. Sections 2-4 are for whoever wants to check it — without them the
    first section is only an assertion, but putting them first means reading three
    thousand words every week before reaching the answer. The fix is ORDER, not choice.
    """
    from ...config import is_pead_covered

    pead = {s for s in cfg.all_symbols() if is_pead_covered(s)}
    assessments = list(assessments or [])
    by_claim = {c.id: c for c in layer.claims}
    common = [a for a in assessments if by_claim.get(a.claim_id)
              and by_claim[a.claim_id].kind != "relative"]
    relative = [a for a in assessments if by_claim.get(a.claim_id)
                and by_claim[a.claim_id].kind == "relative"]

    lines = [f"# 🤖 {layer.label}（{cfg.label}）— 层级分析（{verdict.as_of:%Y-%m-%d}）", ""]
    lines += _section_conclusion(verdict, layer, cfg, basket, pead)
    lines += _section_claim_evidence(common, by_claim, verdict)
    lines += _section_cross_section(layer, basket, relative, by_claim, rows)
    lines += _section_name_rationale(verdict, pead)
    lines += _section_candidates(verdict)
    lines += ["", "---",
              f"*由 `ats sector review {cfg.name}` 的层级分析师产出；"
              f"数据截至 {verdict.as_of:%Y-%m-%d %H:%M} UTC。*", ""]
    return "\n".join(lines)


def _section_conclusion(verdict, layer, cfg, basket, pead) -> list[str]:
    budget = basket.layer_cap if basket is not None else None
    cap = layer.weight_cap or 0.0
    head = [f"## 一、本层结论", "",
            f"### {verdict.allocation}　（信心 {verdict.confidence:.2f}）", ""]
    b = f"{budget:.1%} NAV" if budget is not None else "—"
    head += [f"- **本层预算**：{b}　（层上限 {cap:.0%}"
             + ("；已被跨层组上限压低" if budget is not None and budget < cap * 0.999
                and verdict.allocation == "超配" else "") + "）",
             f"- **周期位置**：{verdict.cycle_position or '—'}"]
    flags = []
    if not verdict.has_claims:
        flags.append("**本层无命题**（配置缺口，不是本季没人发声）")
    if not verdict.cross_section_applicable:
        flags.append("**截面不适用**（可比样本 <2，名次不是发现）")
    if flags:
        head.append("- ⚠️ " + "；".join(flags))
    head.append("")

    if verdict.name_calls or (basket is not None and basket.rows):
        head += ["### 层内配置", "",
                 "| 代码 | 子层 | 观点 | 建议权重 | 排名 |", "|---|---|---|---|---|"]
        weights = {r.symbol: r for r in (basket.rows if basket is not None else [])}
        called = {c.symbol: c for c in verdict.name_calls}
        for sym in list(called) + [s for s in weights if s not in called]:
            c, r = called.get(sym), weights.get(sym)
            name = f"**{sym}**" if sym in pead else sym
            mark = " ⚠️仅自述" if (c and c.self_reported_only) else ""
            stance = f"{c.stance}{mark}" if c else "—"
            sub = (c.subgroup if c and c.subgroup else (r.subgroup if r else "")) or "—"
            wt = f"{r.weight:.1%}" if r else "—"
            rank = (str(r.rank) if r and verdict.cross_section_applicable else "—")
            head.append(f"| {name} | {sub} | {stance} | {wt} | {rank} |")
        head.append("")

    if verdict.reversal_triggers:
        head += ["### 反转触发条件（下一轮逐条核对）", ""]
        head += [f"- [ ] {t}" for t in verdict.reversal_triggers] + [""]
    if verdict.rationale:
        head += ["> " + verdict.rationale.replace("\n", "\n> "), ""]
    return head


def _section_claim_evidence(common, by_claim, verdict) -> list[str]:
    lines = ["## 二、议题证据链", ""]
    if not verdict.has_claims:
        return lines + ["⚠️ **本层没有任何命题** —— 这是**配置缺口**（该建的命题还没建），"
                        "不是本季没人发声。上面的结论只来自快照与判据笔记，没有议题证据支撑。",
                        ""]
    if not common:
        return lines + ["本层有命题，但本期**没有一条产出结论** —— 这是**证据缺口**"
                        "（本季没人发声），与「本层无命题」不同。", ""]
    lines.append("> 结论由确定性引擎给出（筛选、去重、立场统计、记分）；下面是它依据的东西，"
                 "**逐条可核对**。⛔ 共同需求的结论**不得**读成「谁在赢」。")
    lines.append("")
    for a in common:
        claim = by_claim[a.claim_id]
        lines += [f"### 「{claim.statement}」", ""]
        verdict_cn, basis_cn, _ = _cn()
        lines.append(f"**结论 {verdict_cn.get(a.verdict, a.verdict)}**"
                     f"（{basis_cn.get(a.basis, a.basis)}）"
                     f"　·　证人覆盖 {a.coverage}　·　独立证据簇 {a.evidence_clusters}"
                     f"　·　立场类别 {a.stance_classes} 类"
                     f"　·　支持 {a.support_score:.0f} / 反驳 {a.refute_score:.0f}")
        if a.unresolved_reason:
            lines.append(f"- 未能定论的原因：{UNRESOLVED_CN.get(a.unresolved_reason, a.unresolved_reason)}"
                         + (f"（异议方：{'、'.join(a.dissenters)}）" if a.dissenters else ""))
        if a.silent_witnesses:
            # 沉默必须可见：expect_from 存在的全部理由就是让沉默显示成缺口而不是中性。
            lines.append(f"- ⚠️ **已声明但本期未发声**：{'、'.join(a.silent_witnesses)}"
                         "　—— 这是缺口，不是中性")
        lines.append("")
        judged = [j for j in a.judgements if j.polarity != "neutral" or j.reason]
        if judged:
            lines += ["| | 说话人 | 维度 | 判读理由 |", "|---|---|---|---|"]
            for j in judged:
                lines.append(f"| {POLARITY_MARK.get(j.polarity, '・')} | {j.speaker or '—'} "
                             f"| {j.concept or '—'} | {j.reason or '—'} |")
            lines.append("")
    return lines


def _section_cross_section(layer, basket, relative, by_claim, rows) -> list[str]:
    lines = ["## 三、截面明细", ""]
    if basket is None or not basket.rows:
        return lines + ["（本轮未产出截面：取数失败或本层无可排序标的。）", ""]
    if not basket.cross_section_applicable:
        lines.append("⚠️ **本层截面不适用**：可比样本少于两个，z 分全为 0，名次只是配置顺序的"
                     "副产品。预算仍照落（受单票限额约束），但**不要据名次做层内取舍**。")
        lines.append("")
    else:
        lines.append(f"> layer_cap = {basket.layer_cap:.1%} NAV"
                     + ("　·　含 KB 结构因子（与量化 60/40 复合）" if basket.structural
                        else "　·　仅量化因子，未跑结构层"))
        lines.append("")
    if rows:
        from .cross_section import format_table
        lines += ["```", format_table(rows, basket.layer_cap), "```", ""]
    else:
        lines += ["| 代码 | 子层 | 排名 | 复合分 | 权重 | 技术久期 | 护城河/定价权 |",
                  "|---|---|---|---|---|---|---|"]
        for r in sorted(basket.rows, key=lambda x: x.rank):
            def n(v):
                return f"{v:+.1f}" if v is not None else "—"
            lines.append(f"| {r.symbol}{'' if r.data_ok else ' ⚠️数据缺失'} | "
                         f"{r.subgroup or '—'} | {r.rank} | {r.composite:+.2f} | "
                         f"{r.weight:.1%} | {n(r.tech_tenor)} | {n(r.moat_pricing)} |")
        lines.append("")

    if not relative:
        # 没有 relative 命题时必须说清楚：排序只是财务因子，不是竞争位置的判断。
        lines += ["> ⚠️ **本层没有相对命题的当期读数**，上面的排序**仅由量化因子决定**。"
                  "它衡量的是财务特征的相对位置，**不是竞争位置**——不要把名次当作"
                  "「谁在赢」的判断。", ""]
        return lines
    lines += ["### 相对命题的逐家读数（结构因子的依据）", "",
              "> 由判读器横向比较同期财报原文得出。**与静态判据笔记冲突时以此为准**：",
              "> 笔记是稳定的结构背景，这里是本期实际发生的事。", ""]
    for a in relative:
        claim = by_claim[a.claim_id]
        lines += [f"**「{claim.statement}」**", ""]
        if not a.entity_readings:
            lines += ["（本期无逐家读数）", ""]
            continue
        _, basis_cn, standing_cn = _cn()
        lines += ["| 公司 | 位置 | 依据强度 | 说话人 | 判读理由 |", "|---|---|---|---|---|"]
        for r in a.entity_readings:
            lines.append(f"| **{r.entity}** | {standing_cn.get(r.standing, r.standing)} "
                         f"| {basis_cn.get(r.basis, r.basis)} | {'、'.join(r.speakers) or '—'} "
                         f"| {r.reason or '—'} |")
        graded = [r for r in a.entity_readings if r.standing != "unknown"]
        if graded and all(r.basis == "self_reported" for r in graded):
            lines.append("")
            lines.append("> ⚠️ 全部读数均为各家自述，无客户或第三方交叉印证——"
                         "可比性来自横向对照，而非单条的可信度。")
        lines.append("")
    return lines


def _section_name_rationale(verdict, pead) -> list[str]:
    lines = ["## 四、逐票取舍理由", ""]
    if not verdict.name_calls:
        return lines + ["（本轮未产出逐票取舍。）", ""]
    lines.append("> 证据优先级：① 相对命题的逐家读数 → ② 截面排名与结构因子 → ③ 判据知识库。")
    lines.append("")
    for c in verdict.name_calls:
        name = f"**{c.symbol}**" if c.symbol in pead else c.symbol
        mark = "　⚠️ 依据仅为该公司自述，未经交叉验证" if c.self_reported_only else ""
        lines += [f"### {name}　—　{c.stance}"
                  + (f"（{c.subgroup}）" if c.subgroup else "") + mark, "",
                  c.rationale or "（无理由）", ""]
    return lines


def _section_candidates(verdict) -> list[str]:
    lines = ["## 五、候选追踪议题", ""]
    if not verdict.candidate_claims:
        return lines + ["（本轮未提出。）", ""]
    lines.append("> 分析师读完本层素材后自发提出，**尚未预设**。仅供人工评估——"
                 "**不参与本期任何计算**（不影响配置结论、截面排序或权重），也未写入配置。")
    lines.append("")
    for c in verdict.candidate_claims:
        lines += [f"### 「{c.statement}」", "",
                  f"- **谁能作证**：{'、'.join(c.witnesses)}",
                  f"- **什么读数会证伪**：{c.falsifier}"]
        if c.why_now:
            lines.append(f"- 本轮因何提出：{c.why_now}")
        lines.append("")
    return lines


def write_layer(verdict, layer, cfg: SectorConfig, *, basket=None, assessments=None,
                rows=None) -> Path | None:
    """Write one layer's report. Same-day reruns overwrite (idempotent)."""
    if not cfg.output_dir:
        log.info("layer report: output_dir unset — skipped")
        return None
    folder = Path(cfg.output_dir)
    if not folder.is_dir():
        log.warning("layer report: output_dir missing — skipped: %s", folder)
        return None
    safe = layer.label.replace("/", "／").replace(" ", "")
    path = folder / f"层分析-{cfg.label}-{safe}-{verdict.as_of:%Y-%m-%d}.md"
    path.write_text(render_layer(verdict, layer, cfg, basket=basket,
                                 assessments=assessments, rows=rows), encoding="utf-8")
    return path


def _legacy_layer_section(review: SectorReview, cfg: SectorConfig) -> list[str]:
    """Pre-2026-08-20 shape, kept so stored reviews still render."""
    lines = ["## 分层评审", "",
             "| 层 | 景气度 | 供需 | 定价权 | 资金流(proxy) | 周期 | 信号 |",
             "|---|---|---|---|---|---|---|"]
    for layer in cfg.layers:
        a = review.layer_assessment(layer.key)
        if a is None:
            continue
        lines.append(f"| {a.label or layer.label} | **{a.boom_score:.0f}** | {a.supply_demand} "
                     f"| {a.pricing_power} | {a.capital_flow} | {a.cycle_position} | {a.signal} |")
    return lines


def _budgets(cfg: SectorConfig) -> dict:
    return {ly.key: (ly.weight_cap or 0.0) for ly in cfg.layers}


def write(review: SectorReview, cfg: SectorConfig) -> Path | None:
    if not cfg.output_dir:
        log.info("sector report: output_dir unset — skipped")
        return None
    folder = Path(cfg.output_dir)
    if not folder.is_dir():
        log.warning("sector report: output_dir missing — skipped: %s", folder)
        return None
    path = folder / f"行业分析-{cfg.label}-{review.as_of:%Y-%m-%d}.md"
    path.write_text(render(review, cfg), encoding="utf-8")
    return path


def _pead_symbols(cfg: SectorConfig) -> set[str]:
    from ...config import is_pead_covered

    return {s for s in cfg.all_symbols() if is_pead_covered(s)}
