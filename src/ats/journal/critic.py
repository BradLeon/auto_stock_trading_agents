"""Stage E — the critic agent: `系统反思-<YYYY>Q<n>.md`. Quarterly, human-in-the-loop,
and the only stage in this whole journal that generates narrative rather than
classification or arithmetic.

## The division of labor this whole module protects

The critic only *interprets* — it never computes a number and never decides whether
a category has enough evidence to interpret. Both of those are done by deterministic
code (`_category_blocks`, `EvidenceBlock.sufficient`) BEFORE the LLM is ever called.
Concretely: `FindingCategory` has 7 values but only 6 ever reach the LLM
(`open_position` is a live fact, not a statistical claim — see below), and within
those 6, any category whose evidence blocks are all still under `n_min` never gets a
prompt at all. That is the literal implementation of the plan's "样本不足时连让它
猜的机会都不给" — not an approximation of it.

`FindingCategory` -> evidence source:
    calibration  Stage D blocks 1-4 (band / threshold sweep / expected_move / consensus_pt)
    holding      Stage D block 5 (per-setup expectancy + MFE:|MAE|)
    risk_gate    Stage D block 6
    human_gate   Stage D block 7
    execution    NEW — `_execution_quality_block` (slippage / retries vs outcome)
    evidence     NEW — `_evidence_quality_block` (transcript vs no-transcript vs outcome)
    open_position  NOT a statistical block — see `_open_position_findings`

The two new blocks only feed the critic; they don't appear in Stage D's own
`系统校准-*.md` (that report has its own, separately-verified scope — see
calibration.py). They reuse `calibration.win_rate_stats` / `_group_outlier_
counterexamples`, the exact same shape as Stage D's `_risk_gate_audit`.

## `open_position` is deterministic, not LLM-backed

It restates a live fact B3 already computed (`invalidation_triggered` /
`horizon_overdue_days`) — one finding per flagged position, `n=1`,
`n_sufficient=True` (there is nothing to sample here; it is simply happening),
`hypothesis`/`falsifier` always empty. It never calls the LLM.

## `cases` are NOT blinded — this is the deliberate opposite of B3

`invalidation.py` calls `.blind()` because judging "has the thesis failed" is a
forward-looking question where seeing the P&L invites hindsight bias. The critic's
job here is explicitly retrospective — reviewing what already happened requires
knowing what happened. `EpisodeCard`s handed to the LLM keep every outcome field.
If you're reading this after reading invalidation.py and wondering why one module
blinds and the other doesn't: that's why.

## Hard constraint (nothing here enforces it in code — it's an absence)

No module in this codebase imports `critic.py` to build Chief's context. A single-
episode attribution narrative fed back into Chief's context can't be un-seen — an
LLM will construct a self-consistent causal story from pure noise given a P&L
figure, and once that story is in Chief's context it shapes every future decision.
Adopting a finding is a human action (edit a YAML, edit a skill, write one line into
a standing playbook) — never automatic.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path

from ..agents.base import run_structured
from ..schemas.journal import (
    CriticBrief,
    CriticFinding,
    EpisodeCard,
    EvidenceBlock,
    ProposedChange,
)
from . import calibration
from .card import build_card
from .outputs import FindingItemView

log = logging.getLogger("ats.journal")

# 决策质量：关注"当时怎么判断的/怎么把关的"。结果质量：关注"后来发生了什么"。
# open_position 两边都不进——它既不是回顾决策也不是回顾结果，是当下事实。
DECISION_CATEGORIES = ("evidence", "human_gate", "risk_gate", "execution")
OUTCOME_CATEGORIES = ("calibration", "holding")


# --------------------------------------------------------------------------- #
# two new evidence blocks — critic-only, not part of Stage D's own report
# --------------------------------------------------------------------------- #
_SLIP_BPS_THRESHOLD = 20.0


def _execution_quality_block(store) -> EvidenceBlock:
    episodes_by_entry = {e.primary_entry_id: e for e in store.list_episodes(limit=100_000)
                         if e.decision_gradeable and e.status == "closed"}
    entries = store.journal_entries(limit=5000)
    matched = [(e, ep) for e in entries
              if (ep := episodes_by_entry.get(e.entry_id)) is not None and ep.realized_pnl is not None]

    high_slip = [(ep.episode_id, ep.realized_pnl) for e, ep in matched
                if e.slippage_bps is not None and abs(e.slippage_bps) >= _SLIP_BPS_THRESHOLD]
    low_slip = [(ep.episode_id, ep.realized_pnl) for e, ep in matched
               if e.slippage_bps is not None and abs(e.slippage_bps) < _SLIP_BPS_THRESHOLD]
    retried = [(ep.episode_id, ep.realized_pnl) for e, ep in matched if (e.submit_attempts or 0) > 1]
    not_retried = [(ep.episode_id, ep.realized_pnl) for e, ep in matched if (e.submit_attempts or 0) <= 1]

    table = [calibration.win_rate_stats(f"滑点≥{_SLIP_BPS_THRESHOLD:.0f}bp", high_slip),
            calibration.win_rate_stats("滑点较低", low_slip),
            calibration.win_rate_stats("提交重试过", retried),
            calibration.win_rate_stats("一次提交成功", not_retried)]
    cx = calibration._group_outlier_counterexamples(
        {"high_slip": high_slip, "low_slip": low_slip, "retried": retried, "not_retried": not_retried})
    return EvidenceBlock(
        question="执行质量（滑点/重试）与后续回合表现有没有关系？",
        table=table, n_closed=len(matched), n_open=0, n_min=10, counterexamples=cx)


def _evidence_quality_block(store) -> EvidenceBlock:
    episodes_by_entry = {e.primary_entry_id: e for e in store.list_episodes(limit=100_000)
                         if e.decision_gradeable and e.status == "closed"}
    entries = store.journal_entries(limit=5000)
    with_tx: list[tuple[str, float]] = []
    without_tx: list[tuple[str, float]] = []
    for entry in entries:
        ep = episodes_by_entry.get(entry.entry_id)
        if ep is None or ep.realized_pnl is None or entry.ev_has_transcript is None:
            continue
        (with_tx if entry.ev_has_transcript else without_tx).append((ep.episode_id, ep.realized_pnl))

    table = [calibration.win_rate_stats("有纪要", with_tx),
            calibration.win_rate_stats("缺纪要（凭发布稿打分）", without_tx)]
    cx = calibration._group_outlier_counterexamples({"with_tx": with_tx, "without_tx": without_tx})
    return EvidenceBlock(
        question="有电话会纪要 vs 只凭发布稿打分 —— 后续回合表现有差异吗？",
        table=table, n_closed=len(with_tx) + len(without_tx), n_open=0, n_min=10,
        counterexamples=cx)


def _category_blocks(store) -> dict[str, list[EvidenceBlock]]:
    """Every FindingCategory that gets an LLM-backed finding, mapped to the
    EvidenceBlock(s) that back it. `open_position` is deliberately absent — it is
    handled entirely by `_open_position_findings`, never through this path."""
    return {
        "calibration": [calibration._band_calibration(store), calibration._threshold_sweep(store),
                       calibration._expected_move_calibration(store),
                       calibration._consensus_pt_calibration(store)],
        "holding": [calibration._setup_expectancy(store)],
        "risk_gate": [calibration._risk_gate_audit(store)],
        "human_gate": [calibration._human_gate_audit(store)],
        "execution": [_execution_quality_block(store)],
        "evidence": [_evidence_quality_block(store)],
    }


# --------------------------------------------------------------------------- #
# open_position — deterministic, never touches the LLM
# --------------------------------------------------------------------------- #
def _open_position_findings(store) -> list[CriticFinding]:
    findings = []
    for ep in store.list_episodes(status="open", limit=100_000):
        flags = []
        if ep.invalidation_triggered:
            flags.append("论点已判定失效")
        if ep.horizon_overdue_days:
            flags.append(f"超过计划持有期 {ep.horizon_overdue_days} 天")
        if not flags:
            continue
        findings.append(CriticFinding(
            finding_id=f"open_position:{ep.episode_id}", category="open_position",
            observation=f"{ep.symbol}（{ep.episode_id}）：{'；'.join(flags)}，仍未平仓",
            n=1, n_sufficient=True, evidence_ref=[ep.episode_id]))
    return findings


# --------------------------------------------------------------------------- #
# cases: resolved straight from blocks' own counterexample IDs — no separate
# selection logic (see Stage D's bare-ID revision)
# --------------------------------------------------------------------------- #
def _select_cases(store, blocks: list[EvidenceBlock], cap: int = 4) -> list[EpisodeCard]:
    seen: set[str] = set()
    cases: list[EpisodeCard] = []
    for b in blocks:
        for cx in b.counterexamples:
            if cx in seen:
                continue
            ep = store.get_episode(cx)
            if ep is None:      # not an episode_id (a prediction_id / entry_id instead) — skip
                continue
            seen.add(cx)
            cases.append(build_card(store, ep, with_predictions=True))
            if len(cases) >= cap:
                return cases
    return cases


# --------------------------------------------------------------------------- #
# LLM-backed finding, one per category
# --------------------------------------------------------------------------- #
def _summarize_table(rows: list[dict]) -> str:
    if not rows:
        return "(无数据)"
    return " | ".join("[" + ", ".join(f"{k}={v}" for k, v in r.items()) + "]" for r in rows)


def _context_for_category(category: str, blocks: list[EvidenceBlock],
                          cases: list[EpisodeCard]) -> str:
    parts = [f"类别：{category}", ""]
    for b in blocks:
        cx = f"（反例：{', '.join(b.counterexamples)}）" if b.counterexamples else ""
        parts.append(f"- {b.question}\n  n_closed={b.n_closed} n_open={b.n_open}\n  "
                     f"{_summarize_table(b.table)}{cx}")
    if cases:
        parts += ["", "代表性回合个案（完整盈亏，供解释用——这是回顾性分析，不是前瞻判断）："]
        for c in cases:
            ep = c.episode
            plan_note = f"计划：{c.plan.rationale}" if c.plan else "无预登记计划（手工单/存量持仓）"
            parts.append(f"- {ep.symbol}（{ep.episode_id}）setup={ep.setup} "
                        f"exit_reason={ep.exit_reason} realized_pnl={ep.realized_pnl} "
                        f"r_multiple={ep.r_multiple}；{plan_note}")
    parts += ["", "请针对以上这一类问题给出 hypothesis / falsifier（可选 proposed_change）。"]
    return "\n".join(parts)


def _llm_finding_for_category(category: str, blocks: list[EvidenceBlock],
                              cases: list[EpisodeCard], period_label: str, *,
                              use_llm: bool) -> CriticFinding:
    n_total = sum(b.n_closed + b.n_open for b in blocks)
    evidence_ref = [cx for b in blocks for cx in b.counterexamples]
    observation = "；".join(f"{b.question}：{_summarize_table(b.table)}" for b in blocks)
    finding_id = f"{category}:{period_label}"

    if not use_llm:
        return CriticFinding(
            finding_id=finding_id, category=category,
            observation=observation + "（--no-llm：未调用模型，仅展示确定性证据）",
            n=n_total, n_sufficient=False, evidence_ref=evidence_ref)

    if not any(b.sufficient for b in blocks):
        return CriticFinding(finding_id=finding_id, category=category, observation=observation,
                             n=n_total, n_sufficient=False, evidence_ref=evidence_ref)

    ctx = _context_for_category(category, [b for b in blocks if b.sufficient], cases)
    try:
        view: FindingItemView = run_structured("critic", FindingItemView, ctx,
                                               skill_slug="trade-journal-critic")
    except Exception as exc:  # noqa: BLE001
        log.warning("critic LLM call failed for category=%s: %s", category, exc)
        return CriticFinding(finding_id=finding_id, category=category, observation=observation,
                             n=n_total, n_sufficient=True, evidence_ref=evidence_ref)

    proposed = ProposedChange(**view.proposed_change.model_dump()) if view.proposed_change else None
    return CriticFinding(
        finding_id=finding_id, category=category, observation=observation,
        n=n_total, n_sufficient=True, evidence_ref=evidence_ref,
        hypothesis=view.hypothesis, falsifier=view.falsifier,
        proposed_change=proposed, confidence=view.confidence)


# --------------------------------------------------------------------------- #
# assemble
# --------------------------------------------------------------------------- #
def _quarter_label(as_of: date) -> str:
    q = (as_of.month - 1) // 3 + 1
    return f"{as_of.year}Q{q}"


def build_brief(store, period_label: str) -> CriticBrief:
    """The full, honest input snapshot — every block considered this quarter,
    across every category, plus every case resolved from their counterexamples.
    Kept as one object mainly for audit: the actual LLM calls happen per-category
    (see run_critic), each seeing only its own slice of this."""
    categorized = _category_blocks(store)
    all_blocks = [b for blocks in categorized.values() for b in blocks]
    return CriticBrief(period=period_label, blocks=all_blocks,
                       cases=_select_cases(store, all_blocks, cap=8))


def run_critic(*, store=None, period_label: str | None = None,
              use_llm: bool = True) -> list[CriticFinding]:
    from ..memory import get_store

    store = store or get_store()
    period_label = period_label or _quarter_label(datetime.now(timezone.utc).date())

    findings: list[CriticFinding] = []
    for category, blocks in _category_blocks(store).items():
        cases = _select_cases(store, blocks, cap=4)
        findings.append(_llm_finding_for_category(category, blocks, cases, period_label,
                                                   use_llm=use_llm))
    findings += _open_position_findings(store)
    return findings


# --------------------------------------------------------------------------- #
# render + write
# --------------------------------------------------------------------------- #
def _render_finding(f: CriticFinding) -> list[str]:
    out = [f"### {f.category}（n={f.n}）", "", f"**事实**：{f.observation}", ""]
    if not f.n_sufficient:
        out += ["> 样本不足，不作结论（不渲染假设）", ""]
    else:
        out += [f"**假设**：{f.hypothesis or '（本季无新增假设）'}", "",
                f"**证伪条件**：{f.falsifier or '—'}", "",
                f"**可信度**：{f.confidence}", ""]
        if f.proposed_change:
            pc = f.proposed_change
            out += [f"**建议改动**：`{pc.locator}`：{pc.current} → {pc.proposed}"
                   f"（{pc.expected_effect}）", ""]
    if f.evidence_ref:
        out += ["证据：" + "、".join(f"`{r}`" for r in f.evidence_ref), ""]
    return out


def render_reflection(findings: list[CriticFinding], period_label: str) -> str:
    lines = [f"# 系统反思 — {period_label}", "",
            f"> 生成于 {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC ｜ "
            "假设部分（hypothesis/falsifier）由 LLM 生成，仅供参考；采纳是人的动作，"
            "系统绝不自动应用任何改动", ""]

    open_findings = [f for f in findings if f.category == "open_position"]
    lines += ["## 当前需要处理（不算复盘，是提醒）", ""]
    lines += ([f"- {f.observation}" for f in open_findings] if open_findings
             else ["（无失效/超期未平仓位）"])
    lines.append("")

    lines += ["## 决策质量", "", "> 关注当时怎么判断的、怎么把关的；不按盈亏排序（本栏本就无盈亏字段）", ""]
    decision = [f for f in findings if f.category in DECISION_CATEGORIES]
    for f in decision:
        lines += _render_finding(f)

    lines += ["## 结果质量", "", "> 关注后来发生了什么；固定按类别顺序渲染，不按盈亏排序", ""]
    outcome = [f for f in findings if f.category in OUTCOME_CATEGORIES]
    for f in outcome:
        lines += _render_finding(f)

    return "\n".join(lines)


def write_reflection(*, store=None, as_of: date | None = None,
                     use_llm: bool = True) -> Path | None:
    from ..memory import get_store
    from ..runtime.digest import _write_md

    store = store or get_store()
    today = as_of or datetime.now(timezone.utc).date()
    label = _quarter_label(today)
    findings = run_critic(store=store, period_label=label, use_llm=use_llm)
    return _write_md(f"系统反思-{label}.md", render_reflection(findings, label))


def run(*, use_llm: bool = True) -> int:
    path = write_reflection(use_llm=use_llm)
    print(f"系统反思 → {path}" if path else "（未配置 Obsidian 输出目录）")
    return 0
