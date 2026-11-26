"""Obsidian audit report for a Chief run — decision + the exact context it saw."""

from __future__ import annotations

import logging
from pathlib import Path

from .decide import ChiefResult

log = logging.getLogger("ats.agents.chief.report")


def _freshness_block(context_text: str) -> str:
    """The stale-input block the Chief saw (runtime/input_freshness), for the top of the report."""
    marker = "## 数据新鲜度（采集异常）\n"
    if marker not in (context_text or ""):
        return ""
    rest = context_text.split(marker, 1)[1]
    return rest.split("\n\n## ", 1)[0].strip()


def render(result: ChiefResult) -> str:
    lines = [
        f"# 🤖 首席决策 — {result.as_of:%Y-%m-%d %H:%M} UTC（{result.cycle_id}）",
        "",
        "## 决策",
        "",
        result.summary,
        "",
    ]
    stale = _freshness_block(result.context_text)
    if stale:
        lines += ["> ⚠️ **输入数据滞后**", ">"] + [f"> {line}" for line in stale.splitlines()] + [""]
    if result.decisions:
        if result.outcome_note:
            lines += [f"> ⚠️ **以下建议没有形成订单，不在待审批里**：{result.outcome_note}", ""]
        if result.skipped_note:
            lines += [f"> 另有建议本轮没有下单：{result.skipped_note}", ""]
        lines += ["| 动作 | 标的 | 规模 | 限价 | 信心 | 理由 | 引用 |", "|---|---|---|---|---|---|---|"]
        for d in result.decisions:
            lines.append(f"| {d.action} | {d.symbol} | {_size(d)} | {_price(d)} | {d.conviction:.2f} "
                         f"| {d.rationale} | {_refs(d.references, result)} |")
    else:
        lines.append("**（无行动 — 零决策）**")
    if result.option_actions:
        lines += ["", "## 期权建议（人工执行，系统不下单）", "",
                  "| 动作 | 合约 | 张数 | 展期到 | 理由 |", "|---|---|---|---|---|"]
        for a in result.option_actions:
            roll = f"{a.get('roll_to_expiry') or ''} {a.get('roll_to_strike') or ''}".strip() or "—"
            lines.append(f"| {a['action']} | {a['symbol']} {a['expiry']} {a['strike']:g}{a['right']} "
                         f"| {a.get('contracts') or '—'} | {roll} | {a.get('rationale', '')} |")
    lines += _reasoning_lines(result.reasoning, result)
    lines += _basis_lines(result)
    if result.context_text:
        lines += ["", "---", "", "## 决策时所见的完整上下文（审计）", "",
                  "```", result.context_text, "```"]
    lines.append("")
    return "\n".join(lines)


def _size(d) -> str:
    """Shares and money both, whichever the order has; a proposal with neither says so instead of a bare dash."""
    parts = []
    if d.qty:
        parts.append(f"{d.qty:g} 股")
    if d.notional_usd:
        parts.append(f"≈ ${d.notional_usd:,.0f}" if d.qty else f"${d.notional_usd:,.0f}")
    if parts:
        return " ".join(parts)
    return f"目标权重 {d.target_weight:.0%}" if d.target_weight is not None else "未给规模"


def _price(d) -> str:
    if d.limit_price:
        return f"{d.limit_price:,.2f}"
    return "市价" if d.order_type == "market" else "系统按盘口定"


def _refs(references, result: ChiefResult) -> str:
    """References as `来源 · 截至 · 距决策`, resolved by code against this round's snapshot.

    Without a citation index (stub runs, old callers) the short ids are kept as before.
    """
    references = list(references or [])
    if not references:
        return "—"
    if not result.citations:
        return ", ".join(r[:8] for r in references)
    from ..provenance import cite_projection

    return "；".join(cite_projection(result.citations[r], result.as_of) if r in result.citations
                    else f"❓ 未知引用 {r[:8]}" for r in references)


def _cited_in_order(result: ChiefResult) -> list[str]:
    r = result.reasoning or {}
    seen: list[str] = []
    for refs in ([d.references for d in result.decisions]
                 + [h.get("references") or [] for h in r.get("holdings_review") or []]
                 + [e.get("references") or [] for e in r.get("evidence_weighing") or []]
                 + [a.get("references") or [] for a in result.option_actions]):
        for ref in refs:
            if ref not in seen:
                seen.append(ref)
    return seen


def _basis_lines(result: ChiefResult) -> list[str]:
    """依据清单: every cited projection, its as_of and age, and the data it was built on."""
    if not result.citations:
        return []
    from ..provenance import cite_projection

    cited = _cited_in_order(result)
    if not cited:
        return []
    unknown = [r for r in cited if r not in result.citations]
    out = ["", "## 依据清单", "",
           f"> 时间均为 UTC；“距决策”以本轮决策时刻 {result.as_of:%Y-%m-%d %H:%M} UTC 为准；"
           "⚠️ 表示超出该类研究的新鲜度窗口。"]
    if unknown:
        out.append(f"> ❓ 有 {len(unknown)} 个引用不在本轮研究快照中（未拦截，仅标出）："
                   + "、".join(u[:8] for u in unknown))
    out.append("")
    for ref in cited:
        if ref not in result.citations:
            continue
        item = result.citations[ref]
        out.append(f"- {cite_projection(item, result.as_of)}　`{ref}`")
        out += [f"    - {v}" for v in item.get("vintages") or []]
    return out


def _reasoning_lines(r: dict, result: ChiefResult | None = None) -> list[str]:
    if not r:
        return []
    out = ["", "## 推理", "", f"**组合判断：** {r.get('portfolio_stance', '')}", ""]
    if r.get("holdings_review"):
        out += ["### 逐个持仓复查", "", "| 标的 | 判断 | 依据 | 冲突信号与取舍 | 引用 |", "|---|---|---|---|---|"]
        out += [f"| {h['symbol']} | {h['verdict']} | {h['basis']} | {h.get('conflicts') or '—'} "
                f"| {_refs(h.get('references'), result) if result else '—'} |"
                for h in r["holdings_review"]]
        out.append("")
    if r.get("evidence_weighing"):
        out += ["### 证据权衡", "", "| 来源 | 结论 | 采用 | 理由 | 引用 |", "|---|---|---|---|---|"]
        out += [f"| {e['source']} | {e['said']} | {e['use']} | {e['why']} "
                f"| {_refs(e.get('references'), result) if result else '—'} |" for e in r["evidence_weighing"]]
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
