"""Obsidian audit report for a Chief run — decision + the exact context it saw."""

from __future__ import annotations

import logging
from pathlib import Path

from .decide import ChiefResult

log = logging.getLogger("ats.agents.chief.report")


def render(result: ChiefResult) -> str:
    lines = [
        f"# 🤖 首席决策 — {result.as_of:%Y-%m-%d %H:%M} UTC（{result.cycle_id}）",
        "",
        "## 决策",
        "",
        result.summary,
        "",
    ]
    if result.decisions:
        lines += ["| 动作 | 标的 | 规模 | 信心 | 理由 | 引用 |", "|---|---|---|---|---|---|"]
        for d in result.decisions:
            size = (f"${d.notional_usd:,.0f}" if d.notional_usd else f"{d.qty:g} 股" if d.qty
                    else f"目标权重 {d.target_weight:.0%}" if d.target_weight is not None else "—")
            lines.append(f"| {d.action} | {d.symbol} | {size} | {d.conviction:.2f} "
                         f"| {d.rationale} | {', '.join(r[:8] for r in d.references) or '—'} |")
    else:
        lines.append("**（无行动 — 零决策）**")
    if result.option_actions:
        lines += ["", "## 期权建议（人工执行，系统不下单）", "",
                  "| 动作 | 合约 | 张数 | 展期到 | 理由 |", "|---|---|---|---|---|"]
        for a in result.option_actions:
            roll = f"{a.get('roll_to_expiry') or ''} {a.get('roll_to_strike') or ''}".strip() or "—"
            lines.append(f"| {a['action']} | {a['symbol']} {a['expiry']} {a['strike']:g}{a['right']} "
                         f"| {a.get('contracts') or '—'} | {roll} | {a.get('rationale', '')} |")
    lines += _reasoning_lines(result.reasoning)
    if result.context_text:
        lines += ["", "---", "", "## 决策时所见的完整上下文（审计）", "",
                  "```", result.context_text, "```"]
    lines.append("")
    return "\n".join(lines)


def _reasoning_lines(r: dict) -> list[str]:
    if not r:
        return []
    out = ["", "## 推理", "", f"**组合判断：** {r.get('portfolio_stance', '')}", ""]
    if r.get("holdings_review"):
        out += ["### 逐个持仓复查", "", "| 标的 | 判断 | 依据 | 冲突信号与取舍 |", "|---|---|---|---|"]
        out += [f"| {h['symbol']} | {h['verdict']} | {h['basis']} | {h.get('conflicts') or '—'} |"
                for h in r["holdings_review"]]
        out.append("")
    if r.get("evidence_weighing"):
        out += ["### 证据权衡", "", "| 来源 | 结论 | 采用 | 理由 |", "|---|---|---|---|"]
        out += [f"| {e['source']} | {e['said']} | {e['use']} | {e['why']} |" for e in r["evidence_weighing"]]
        out.append("")
    if r.get("considered_not_taken"):
        out += ["### 考虑过但没做", ""] + [f"- {a['idea']}：{a['why_not']}" for a in r["considered_not_taken"]]
        out.append("")
    out += ["### 风险与证伪", "", r.get("risks_and_invalidation", "")]
    return out


def write(result: ChiefResult, out_dir: str) -> Path | None:
    if not out_dir:
        log.info("chief report: output_dir unset — skipped")
        return None
    from ...workflow.isolation import verified_isolation_root
    root = verified_isolation_root()
    folder = root / "docs" / "chief" if root is not None else Path(out_dir)
    if root is not None:
        folder.mkdir(parents=True, exist_ok=True)
    if not folder.is_dir():
        log.warning("chief report: output_dir missing — skipped: %s", folder)
        return None
    path = folder / f"首席决策-{result.as_of:%Y-%m-%d-%H%M}.md"
    path.write_text(render(result), encoding="utf-8")
    return path
