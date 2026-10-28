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
    earnings_block: str = ""          # FactSet Earnings Insight (S&P500 盈利/估值 backdrop)
    earnings_source: str = ""
    earnings_backdrop: object | None = None
    earnings_packet: object | None = None
    regional_block: str = ""

    def as_context(self) -> str:
        parts = [
            "Weekly macro strategist review (equity-strategist lens: synthesize into "
            "regime + rate path + SECTOR TILTS, not per-topic summaries).",
            "## 定量仪表盘（FRED + 市场）\n" + self.quant_block,
        ]
        if self.earnings_block:
            parts.append(
                f"## S&P500 FactSet 完整分析材料（{self.earnings_source}）\n"
                + self.earnings_block)
        if self.regional_block:
            parts.append("## 区域半导体需求（持久化月度序列）\n" + self.regional_block)
        parts.append("## 分主题（定量关联 + 近期新闻）\n" + "\n\n".join(self.theme_blocks))
        return "\n\n".join(parts)

    def stats(self) -> dict:
        return {
            "themes": len(self.theme_blocks),
            "quant_chars": len(self.quant_block),
            "theme_chars": sum(len(b) for b in self.theme_blocks),
            "earnings_chars": len(self.earnings_block),
            "earnings_source": self.earnings_source or "-",
            "regional_chars": len(self.regional_block),
            "total_chars": len(self.as_context()),
        }


def build(cfg: MacroConfig, *, live_data: bool = True) -> MacroContext:
    from ...data.products import macro_inputs

    mc = MacroContext(cfg=cfg)
    # Indicators are read from the series the data platform collected (2026-10-16); the review
    # no longer calls FRED/yfinance/CNN itself.
    data = macro_inputs.macro_snapshot() if live_data else None
    mc.quant_block = (data.to_context() if data else
                      "(offline 离线运行：不访问互联网；本地未持久化的实时宏观行情不可用。"
                      "FactSet、本地区域数据和历史正式评审仍会读取。)")
    # Regional data is a governed local product, so offline only disables its
    # acquisition, not this read.  A saved monthly snapshot remains valid input.
    try:
        mc.regional_block = macro_inputs.regional_monthly("macro_agent").render()
    except Exception as exc:  # regional evidence is additive, never blocks regime review
        log.warning("macro regional snapshot unavailable: %s", exc)
        mc.regional_block = "(区域月度数据不可用)"

    # FactSet is a released local DataProducts snapshot, not a FRED/yfinance/
    # Tavily runtime fetch.  `--offline` therefore still includes it.  The
    # weekly ingest pipeline is the only acquisition route; Macro never reads
    # a PDF, a user folder, or the source URL at review time.
    (mc.earnings_block, mc.earnings_source, mc.earnings_backdrop,
     mc.earnings_packet) = macro_inputs.factset_macro_material()

    field_vals = _field_map(data)
    search_cfg = cfg.search
    for theme in cfg.themes:
        lines = [f"### {theme.label} [{theme.kind}]"]
        # Relevant quant fields for this theme.
        if theme.quant:
            picked = [f"{k}={field_vals.get(k, 'n/a')}" for k in theme.quant]
            lines.append("定量: " + ", ".join(picked))
        # Admitted news for themes with queries (qualitative + a couple quant ones). A database
        # read, so it is also available offline.
        if theme.queries:
            lines.append(_news_block(theme, search_cfg))
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


def _news_block(theme, search_cfg: dict) -> str:
    """Admitted news for the theme. Never searches: the data platform collects (2026-10-16)."""
    from ...data.products import macro_inputs

    days = int(search_cfg.get("recency_days", 21))
    cap = int(search_cfg.get("max_chars_per_result", 1800))
    docs = macro_inputs.admitted_news(theme.key, days=days)
    if not docs:
        return f"新闻: (无已准入宏观材料：近 {days} 天该主题没有通过准入的新闻文档)"
    lines = ["近期新闻（已准入文档，正文为搜索服务摘录）:"]
    for doc in docs:
        pub = (doc.published_at or "")[:16]
        body = doc.text.split("\n\n", 1)[-1][:cap]
        lines.append(f"- [{pub}] {doc.title}: {body} ｜文档 {doc.document_id}@{doc.version_id[:12]}")
    return "\n".join(lines)
