"""Macro-review context assembly — pure code, no LLM.

Quantitative block (enhanced macro.fetch: rates/inflation/growth/employment/
financial-conditions/commodities) + per-theme blocks (relevant quant fields +
Tavily news for qualitative themes). One prompt body for the strategist synthesis.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ...schemas.macro_strategy import MacroConfig

log = logging.getLogger("ats.agents.macro.assemble")


@dataclass
class MacroContext:
    cfg: MacroConfig
    quant_block: str = ""
    theme_blocks: list[str] = field(default_factory=list)

    def as_context(self) -> str:
        parts = [
            "Weekly macro strategist review (equity-strategist lens: synthesize into "
            "regime + rate path + SECTOR TILTS, not per-topic summaries).",
            "## 定量仪表盘（FRED + 市场）\n" + self.quant_block,
            "## 分主题（定量关联 + 近期新闻）\n" + "\n\n".join(self.theme_blocks),
        ]
        return "\n\n".join(parts)

    def stats(self) -> dict:
        return {
            "themes": len(self.theme_blocks),
            "quant_chars": len(self.quant_block),
            "theme_chars": sum(len(b) for b in self.theme_blocks),
            "total_chars": len(self.as_context()),
        }


def build(cfg: MacroConfig, *, live_data: bool = True) -> MacroContext:
    from ...data import macro as macro_src

    mc = MacroContext(cfg=cfg)
    data = macro_src.fetch() if live_data else None
    mc.quant_block = data.to_context() if data else "(offline — 定量数据跳过)"

    field_vals = _field_map(data)
    search_cfg = cfg.search
    for theme in cfg.themes:
        lines = [f"### {theme.label} [{theme.kind}]"]
        # Relevant quant fields for this theme.
        if theme.quant:
            picked = [f"{k}={field_vals.get(k, 'n/a')}" for k in theme.quant]
            lines.append("定量: " + ", ".join(picked))
        # Tavily news for themes with queries (qualitative + a couple quant ones).
        if live_data and theme.queries:
            lines.append(_search_block(theme, search_cfg))
        mc.theme_blocks.append("\n".join(x for x in lines if x))

    # Bound total size: truncate each theme block's news tail (quant stays intact).
    cap = int(cfg.review.get("max_context_chars", 48000))
    per_block = max(600, cap // max(1, len(mc.theme_blocks)))
    if len(mc.as_context()) > cap:
        mc.theme_blocks = [b[:per_block] for b in mc.theme_blocks]
    return mc


def _field_map(data) -> dict:
    if data is None:
        return {}
    out = {}
    for f_name, val in data.__dict__.items():
        if isinstance(val, (int, float)):
            out[f_name] = round(val, 2)
    return out


def _search_block(theme, search_cfg: dict) -> str:
    from ...data import websearch

    hits = []
    for q in theme.queries:
        hits += websearch.search_news(
            q, max_results=int(search_cfg.get("max_results_per_query", 4)),
            days=int(search_cfg.get("recency_days", 21)),
            max_chars=int(search_cfg.get("max_chars_per_result", 1800)))
    if not hits:
        return "新闻: (无结果/无 Tavily key)"
    seen: set[str] = set()
    lines = ["近期新闻:"]
    for h in hits:
        if h["url"] in seen or not h["content"]:
            continue
        seen.add(h["url"])
        pub = (h.get("published") or "")[:16]
        lines.append(f"- [{pub}] {h['title']}: {h['content']}")
    return "\n".join(lines)
