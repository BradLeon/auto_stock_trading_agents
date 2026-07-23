"""Cross-sectional layer basket — WHO within a chain layer, and HOW MUCH.

The PEAD scorecard is a *time-series / event* signal (one name's earnings
surprise: answers WHEN to act on a name). It never ranks peers against each
other, so it gives no basis to pick among a layer's names (COHR/LITE/CRDO/AXT/
AAOI/VRT…) or size them. This module adds the missing *cross-sectional* leg:
standardize a handful of factors WITHIN the cohort (Barra-lite z-scores),
composite them into a rank (selection), then turn the rank into weights under a
risk budget (inverse-beta + a small-cap liquidity haircut + a single-name cap).

Factors are sourced from yfinance/finnhub (fundamentals.fetch_light), price
momentum (sector_snapshot) and analyst rating revisions (consensus.rating_trend).
Purity / competitive-position (need LLM/manual tags) come later. Deterministic +
auditable: weights and the composite are pure code; nothing here calls an LLM.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from statistics import fmean, pstdev

log = logging.getLogger("ats.agents.sector.cross_section")

# Factor weights (v1 priors — tune later). Value/quality/revisions tilted, momentum
# light: the AI-optical cohort just sold off, so we don't want momentum to punish
# the very pullback that may be the entry. Revisions = the cohort's steadiest alpha.
FACTOR_WEIGHTS = {"growth": 0.25, "quality": 0.20, "value": 0.25,
                  "momentum": 0.10, "revisions": 0.20}

LIQ_FLOOR_USD = 5e9      # market cap below which a liquidity haircut kicks in
SOFTMAX_TEMP = 0.75      # lower = more concentrated in the top-ranked names
PEG_GROWTH_CAP = 60.0    # cap growth% in the PEG denominator (hyper-growth flatters PEG)
NEG_INF = float("-inf")


@dataclass
class FactorRow:
    symbol: str
    subgroup: str = ""
    market_cap: float | None = None
    beta: float | None = None
    rev_growth: float | None = None      # fraction (0.25 = +25% YoY)
    gross_margin: float | None = None
    op_margin: float | None = None
    fwd_pe: float | None = None
    mom_60d: float | None = None         # % 60-day price change
    rating_delta: float | None = None    # net analyst-rating change (0m minus older), -4..+4
    sizable: bool = True                 # False for cohort_extra peers (rank only, no budget)
    # filled by rank_cohort:
    data_ok: bool = True
    z: dict = field(default_factory=dict)
    composite: float = 0.0
    rank: int = 0
    weight: float = 0.0                  # suggested weight as fraction of NAV

    def peg(self) -> float | None:
        g = (self.rev_growth or 0) * 100
        if self.fwd_pe is None or g <= 0:
            return None
        return self.fwd_pe / min(g, PEG_GROWTH_CAP)

    def quality(self) -> float | None:
        ms = [m for m in (self.gross_margin, self.op_margin) if m is not None]
        return fmean(ms) if ms else None


# --------------------------------------------------------------------------- #
# Analyst rating-revision factor (consensus.rating_trend)
# --------------------------------------------------------------------------- #
def _net_rating(entry: dict) -> float | None:
    vals = [entry.get(k) for k in ("strong_buy", "buy", "hold", "sell", "strong_sell")]
    if all(v is None for v in vals):
        return None
    sb, b, h, s, ss = (v or 0 for v in vals)
    tot = sb + b + h + s + ss
    return (2 * sb + b - s - 2 * ss) / tot if tot else None


def _rating_delta(rating_trend: list[dict]) -> float | None:
    """Net-rating now (0m) minus the oldest available month (-3m/-2m/-1m)."""
    by = {e.get("period"): e for e in (rating_trend or [])}
    now = _net_rating(by.get("0m", {}))
    if now is None:
        return None
    for p in ("-3m", "-2m", "-1m"):
        old = _net_rating(by.get(p, {}))
        if old is not None:
            return now - old
    return None


def fetch_factors(symbols: list[str], subgroups: dict[str, str] | None = None) -> list[FactorRow]:
    from ...data import consensus as consensus_src, fundamentals, sector_snapshot

    subgroups = subgroups or {}
    prices = sector_snapshot.fetch_prices(symbols, period="1y")
    rows: list[FactorRow] = []
    for s in symbols:
        lt = fundamentals.fetch_light(s)
        closes = prices.get(s) or []
        cons = consensus_src.fetch(s)
        rows.append(FactorRow(
            symbol=s, subgroup=subgroups.get(s, ""),
            market_cap=lt.get("market_cap"), beta=lt.get("beta"),
            rev_growth=lt.get("rev_growth"), gross_margin=lt.get("gross_margin"),
            op_margin=lt.get("op_margin"), fwd_pe=lt.get("fwd_pe"),
            mom_60d=sector_snapshot.momentum(closes, 60) if closes else None,
            rating_delta=_rating_delta(cons.get("rating_trend", []))))
    return rows


def _zscores(vals: list[float | None]) -> list[float]:
    """Cross-sectional z-score; None -> 0 (cohort-neutral, never dominates)."""
    present = [v for v in vals if v is not None]
    if len(present) < 2:
        return [0.0 for _ in vals]
    mu, sd = fmean(present), pstdev(present)
    if sd == 0:
        return [0.0 for _ in vals]
    return [((v - mu) / sd) if v is not None else 0.0 for v in vals]


def rank_cohort(rows: list[FactorRow], *, layer_cap: float = 0.10,
                single_name_cap_frac: float = 0.40) -> list[FactorRow]:
    """Composite rank + risk-budgeted weights summing to `layer_cap` (fraction of
    NAV). Names lacking core data are flagged (data_ok=False), ranked last, and
    excluded from sizing. single_name_cap_frac caps any name at that fraction OF
    the layer cap."""
    if not rows:
        return rows
    for r in rows:
        r.data_ok = r.market_cap is not None and (
            r.rev_growth is not None or r.gross_margin is not None)

    ok = [r for r in rows if r.data_ok]
    if ok:
        zg = _zscores([r.rev_growth for r in ok])
        zq = _zscores([r.quality() for r in ok])
        zv = _zscores([r.peg() for r in ok])          # lower PEG = cheaper
        zm = _zscores([r.mom_60d for r in ok])
        zr = _zscores([r.rating_delta for r in ok])
        for i, r in enumerate(ok):
            r.z = {"growth": zg[i], "quality": zq[i], "value": -zv[i],
                   "momentum": zm[i], "revisions": zr[i]}
            r.composite = sum(FACTOR_WEIGHTS[k] * r.z[k] for k in FACTOR_WEIGHTS)
    for r in rows:
        if not r.data_ok:
            r.composite = NEG_INF

    for rank, r in enumerate(sorted(rows, key=lambda x: x.composite, reverse=True), 1):
        r.rank = rank

    # --- sizing over data_ok AND sizable names (cohort_extra peers rank but get no
    # budget — their weight lives in their own layer): softmax × 1/beta × liq haircut ---
    def liq_haircut(mcap: float | None) -> float:
        return min(1.0, math.sqrt(mcap / LIQ_FLOOR_USD)) if mcap else 0.5

    sized = [r for r in ok if r.sizable]
    raw = {r.symbol: math.exp(r.composite / SOFTMAX_TEMP) / max(r.beta or 1.0, 0.5)
                     * liq_haircut(r.market_cap) for r in sized}
    total = sum(raw.values()) or 1.0
    for r in rows:
        r.weight = layer_cap * raw.get(r.symbol, 0.0) / total

    # single-name cap (one redistribution pass among the sized names)
    cap = layer_cap * single_name_cap_frac
    over = {r.symbol: r.weight - cap for r in sized if r.weight > cap}
    if over:
        spill = sum(over.values())
        room = {r.symbol: cap - r.weight for r in sized if r.weight < cap}
        room_tot = sum(room.values()) or 1.0
        for r in sized:
            if r.symbol in over:
                r.weight = cap
            elif r.symbol in room:
                r.weight += spill * room[r.symbol] / room_tot
    return rows


def to_basket(rows: list[FactorRow], layer_key: str, layer_cap: float):
    from ...schemas.sector import BasketRow, LayerBasket

    return LayerBasket(
        layer_key=layer_key, as_of=datetime.now(timezone.utc), layer_cap=layer_cap,
        rows=[BasketRow(
            symbol=r.symbol, subgroup=r.subgroup, composite=(0.0 if r.composite == NEG_INF else r.composite),
            rank=r.rank, weight=r.weight, data_ok=r.data_ok, factors=r.z,
            metrics={"market_cap": r.market_cap, "beta": r.beta, "rev_growth": r.rev_growth,
                     "gross_margin": r.gross_margin, "op_margin": r.op_margin,
                     "fwd_pe": r.fwd_pe, "peg": r.peg(), "mom_60d": r.mom_60d,
                     "rating_delta": r.rating_delta})
            for r in sorted(rows, key=lambda x: x.rank)])


def run_layer(sector_name: str, layer_key: str, *, persist: bool = True):
    """Fetch factors for a layer's cohort (its tickers + cohort_extra peers), rank,
    size, build a LayerBasket, and (best-effort) persist it onto the latest sector
    review. Returns (rows, basket)."""
    from ...config import load_sector_config

    cfg = load_sector_config(sector_name)
    layer = next((ly for ly in cfg.layers if ly.key == layer_key), None)
    if layer is None:
        raise ValueError(f"layer {layer_key!r} not in sector {sector_name!r}")

    subgroups = {t.symbol: t.subgroup for t in layer.tickers}
    cohort = [t.symbol for t in layer.tickers]
    for x in layer.cohort_extra:
        if x not in cohort:
            cohort.append(x)
            subgroups[x] = "(peer)"
    layer_cap = layer.weight_cap if layer.weight_cap is not None else 0.10

    rows = fetch_factors(cohort, subgroups)
    extra = set(layer.cohort_extra)
    for r in rows:
        r.sizable = r.symbol not in extra
    rank_cohort(rows, layer_cap=layer_cap)
    basket = to_basket(rows, layer_key, layer_cap)

    if persist:
        try:
            from ...memory import get_store

            store = get_store()
            review = store.latest_sector_review(sector_name)
            if review is not None:
                review.baskets = [b for b in review.baskets if b.layer_key != layer_key] + [basket]
                store.save_sector_review(review)   # same as_of -> replace in place
        except Exception as exc:  # noqa: BLE001 - persistence is best-effort
            log.warning("basket persist skipped for %s/%s: %s", sector_name, layer_key, exc)
    return rows, basket


FACTOR_LABELS = {"growth": "增长", "quality": "质量(毛利/营利)", "value": "估值(PEG)",
                 "momentum": "动量(60d)", "revisions": "评级修正"}


def _drivers(r: FactorRow) -> tuple[str, str]:
    """Rank a name's factors by weighted contribution → (drivers, drags) prose."""
    contrib = sorted(((k, FACTOR_WEIGHTS[k] * v, v) for k, v in r.z.items()),
                     key=lambda x: x[1], reverse=True)
    pos = [f"{FACTOR_LABELS[k]} {z:+.1f}σ" for k, c, z in contrib if c > 0.03][:3]
    neg = [f"{FACTOR_LABELS[k]} {z:+.1f}σ" for k, c, z in contrib[::-1] if c < -0.03][:3]
    return "、".join(pos) or "—", "、".join(neg) or "—"


def _flags(r: FactorRow) -> list[str]:
    out = []
    if not r.sizable:
        out.append("同业(风控归属在别层，仅排名不占该层额度)")
    if r.beta and r.beta >= 2.5:
        out.append(f"高 beta {r.beta:.1f}（加仓抬升组合 beta）")
    if r.op_margin is not None and r.op_margin < 0:
        out.append("尚未盈利（营业利润率为负）")
    if r.rev_growth is not None and r.rev_growth > 1.0:
        out.append("超高增速，可持续性存疑（PEG 已封顶抑制）")
    if r.mom_60d is not None and r.mom_60d < -20:
        out.append(f"近期大幅回调（60d {r.mom_60d:+.0f}%）——逢调 vs 趋势逆转需判断")
    return out


def render_report(rows: list[FactorRow], layer_key: str, layer_label: str,
                  layer_cap: float, sector_label: str, sector_name: str) -> str:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    lines = [
        f"# 🤖 截面选股 — {layer_label}（{sector_label}）（{now:%Y-%m-%d}）",
        "",
        f"> 由 `ats sector crosssection {sector_name} --layer {layer_key}` 生成"
        f"（**确定性因子模型，无 LLM**）。回答「层内选谁、各配多少」。",
        "",
        "## 方法",
        f"- 因子（组内 z-score 标准化）与权重：" + "、".join(
            f"{FACTOR_LABELS[k]} {w:.0%}" for k, w in FACTOR_WEIGHTS.items()) + "。",
        "- 配权 = softmax(composite)×(1/beta)×小盘流动性 haircut，单票 ≤ 层cap×40%；数据不足者排除、不占额度。",
        "- `cohort_extra` 同业（如 MRVL）参与排名但不占本层额度（其风控归属在别层）。",
        "",
        f"## 排序与建议权重（layer_cap = {layer_cap:.0%} NAV，权重和 = "
        f"{sum(r.weight for r in rows):.0%}）",
        "",
        "```",
        format_table(rows, layer_cap),
        "```",
        "",
        "## 逐名推理",
    ]
    for r in sorted(rows, key=lambda x: x.rank):
        if not r.data_ok:
            lines += [f"\n### {r.rank}. {r.symbol}（{r.subgroup or '-'}）— ⚠ 数据缺失，未纳入配权",
                      "- 免费源(yfinance/finnhub)无基本面；需付费源或手工录入后再评。"]
            continue
        drv, drg = _drivers(r)
        def p(v, s=1.0, suf=""):
            return f"{v * s:.1f}{suf}" if v is not None else "—"
        lines += [
            f"\n### {r.rank}. {r.symbol}（{r.subgroup or '-'}）— 建议 {r.weight * 100:.1f}%",
            f"- 综合分 **{r.composite:+.2f}**（第 {r.rank}）。驱动：{drv}；拖累：{drg}。",
            f"- 原始：营收 {p(r.rev_growth, 100, '%')} · 毛利 {p(r.gross_margin, 100, '%')} · "
            f"营利 {p(r.op_margin, 100, '%')} · fwdPE {p(r.fwd_pe)} · PEG {p(r.peg())} · "
            f"60d动量 {p(r.mom_60d, 1, '%')} · 评级Δ {p(r.rating_delta)} · beta {p(r.beta)} · "
            f"市值 ${(r.market_cap or 0) / 1e9:.0f}B。",
        ]
        for fl in _flags(r):
            lines.append(f"- ⚠ {fl}")

    lines += [
        "", "## 方法 caveat",
        "- v1 仅量化因子，缺「主题纯度/结构位」定性因子 → backlog 强但增速温和的名字(如 VRT)会被系统性低估，"
        "需结合行业分析师的定性 call。",
        "- PEG 分母增速封顶 60%，抑制超高增速把 PEG 压得虚低。",
        "- 数据缺失者不臆造排名；小盘/微盘覆盖差需付费源。",
        "", "---",
        f"*确定性因子模型；数据截至 {now:%Y-%m-%d %H:%M} UTC。加仓仍受组合级风控(beta/簇/压测)约束。*",
        "",
    ]
    return "\n".join(lines)


def write_report(rows: list[FactorRow], layer_key: str, cfg) -> "object | None":
    from datetime import datetime, timezone
    from pathlib import Path

    if not cfg.output_dir:
        return None
    folder = Path(cfg.output_dir)
    if not folder.is_dir():
        log.warning("cross-section report: output_dir missing — skipped: %s", folder)
        return None
    layer = next((ly for ly in cfg.layers if ly.key == layer_key), None)
    layer_label = layer.label if layer else layer_key
    layer_cap = (layer.weight_cap if layer and layer.weight_cap is not None else 0.10)
    text = render_report(rows, layer_key, layer_label, layer_cap, cfg.label, cfg.name)
    path = folder / f"截面选股-{cfg.label}-{layer_key}-{datetime.now(timezone.utc):%Y-%m-%d}.md"
    path.write_text(text, encoding="utf-8")
    return path


def format_table(rows: list[FactorRow], layer_cap: float) -> str:
    hdr = (f"{'sym':<6}{'sub':<8}{'mcap$B':>7}{'beta':>6}{'revG%':>7}{'GM%':>6}{'OM%':>6}"
           f"{'fwdPE':>7}{'PEG':>6}{'mom60':>7}{'revΔ':>6} | {'comp':>6}{'rank':>5}{'wt%':>7}")
    lines = [hdr, "-" * len(hdr)]
    for r in sorted(rows, key=lambda x: x.rank):
        def p(v, s=1.0):
            return f"{v * s:.1f}" if v is not None else "—"
        flag = "" if r.data_ok else "  ⚠无数据"
        comp = "  —" if r.composite == NEG_INF else f"{r.composite:>6.2f}"
        lines.append(
            f"{r.symbol:<6}{(r.subgroup or ''):<8}{(r.market_cap or 0)/1e9:>7.1f}{(r.beta or 0):>6.2f}"
            f"{p(r.rev_growth,100):>7}{p(r.gross_margin,100):>6}{p(r.op_margin,100):>6}"
            f"{p(r.fwd_pe):>7}{p(r.peg()):>6}{p(r.mom_60d):>7}{p(r.rating_delta):>6} | "
            f"{comp}{r.rank:>5}{r.weight*100:>6.1f}%{flag}")
    lines.append(f"\nlayer_cap={layer_cap:.0%} of NAV · weights sum={sum(r.weight for r in rows)*100:.1f}%")
    lines.append(f"factor weights: {FACTOR_WEIGHTS}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    if len(sys.argv) >= 3 and not sys.argv[1].isupper():
        rows, basket = run_layer(sys.argv[1], sys.argv[2], persist=False)
        print(format_table(rows, basket.layer_cap))
    else:
        syms = sys.argv[1:] or ["COHR", "LITE", "AAOI", "CRDO", "AXT", "VRT", "MRVL"]
        rows = fetch_factors(syms)
        rank_cohort(rows, layer_cap=0.10)
        print(format_table(rows, 0.10))
