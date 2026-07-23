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

# Quant factor weights (v1). Value/quality/revisions tilted, momentum light: the
# AI-optical cohort just sold off, so momentum shouldn't punish the very pullback
# that may be the entry. Revisions = the cohort's steadiest alpha.
QUANT_WEIGHTS = {"growth": 0.25, "quality": 0.20, "value": 0.25,
                 "momentum": 0.10, "revisions": 0.20}
# Structure (KB-qualitative) factors — added by the structure analyst overlay.
STRUCT_WEIGHTS = {"tech_tenor": 0.20, "moat_pricing": 0.20}
# Blended = quant scaled to 60% + structure 40% (sums to 1.0). Used when --structure.
BLENDED_WEIGHTS = {**{k: round(v * 0.6, 4) for k, v in QUANT_WEIGHTS.items()}, **STRUCT_WEIGHTS}
FACTOR_WEIGHTS = QUANT_WEIGHTS   # back-compat default

# key -> (row value getter, sign). value: lower PEG is better, so sign -1.
FACTOR_VALUE = {
    "growth": lambda r: r.rev_growth,
    "quality": lambda r: r.quality(),
    "value": lambda r: r.peg(),
    "momentum": lambda r: r.mom_60d,
    "revisions": lambda r: r.rating_delta,
    "tech_tenor": lambda r: r.tech_tenor,
    "moat_pricing": lambda r: r.moat_pricing,
}
FACTOR_SIGN = {"value": -1.0}

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
    # KB structural overlay (set by structure analyst; None until run):
    tech_tenor: float | None = None      # -2..+2 技术时间朝向（光进铜退等 secular 位置）
    moat_pricing: float | None = None    # -2..+2 护城河/份额/定价权/客户集中
    rationale: str = ""
    # filled by rank_cohort:
    data_ok: bool = True
    z: dict = field(default_factory=dict)
    composite: float = 0.0
    rank: int = 0
    quant_rank: int = 0                  # pure-quant rank captured before the blend
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
                single_name_cap_frac: float = 0.40, weights: dict | None = None) -> list[FactorRow]:
    """Composite rank + risk-budgeted weights summing to `layer_cap` (fraction of
    NAV). `weights` selects the factor set (QUANT_WEIGHTS default, BLENDED_WEIGHTS
    when the structure overlay ran). Names lacking core data are flagged
    (data_ok=False), ranked last, and excluded from sizing. single_name_cap_frac
    caps any name at that fraction OF the layer cap."""
    if not rows:
        return rows
    weights = weights or QUANT_WEIGHTS
    for r in rows:
        r.data_ok = r.market_cap is not None and (
            r.rev_growth is not None or r.gross_margin is not None)

    ok = [r for r in rows if r.data_ok]
    if ok:
        for r in ok:
            r.z = {}
        for key in weights:
            sign = FACTOR_SIGN.get(key, 1.0)
            zs = _zscores([FACTOR_VALUE[key](r) for r in ok])
            for i, r in enumerate(ok):
                r.z[key] = sign * zs[i]
        for r in ok:
            r.composite = sum(weights[k] * r.z[k] for k in weights)
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


def to_basket(rows: list[FactorRow], layer_key: str, layer_cap: float, *,
              structural: bool = False, subgroup_notes: dict | None = None):
    from ...schemas.sector import BasketRow, LayerBasket

    return LayerBasket(
        layer_key=layer_key, as_of=datetime.now(timezone.utc), layer_cap=layer_cap,
        structural=structural, subgroup_notes=subgroup_notes or {},
        rows=[BasketRow(
            symbol=r.symbol, subgroup=r.subgroup, composite=(0.0 if r.composite == NEG_INF else r.composite),
            rank=r.rank, quant_rank=r.quant_rank, weight=r.weight, data_ok=r.data_ok,
            tech_tenor=r.tech_tenor, moat_pricing=r.moat_pricing, rationale=r.rationale, factors=r.z,
            metrics={"market_cap": r.market_cap, "beta": r.beta, "rev_growth": r.rev_growth,
                     "gross_margin": r.gross_margin, "op_margin": r.op_margin,
                     "fwd_pe": r.fwd_pe, "peg": r.peg(), "mom_60d": r.mom_60d,
                     "rating_delta": r.rating_delta})
            for r in sorted(rows, key=lambda x: x.rank)])


def run_layer(sector_name: str, layer_key: str, *, persist: bool = True, structure: bool = False):
    """Fetch factors for a layer's cohort (its tickers + cohort_extra peers), rank,
    size, and (if structure=True and KB notes exist) blend in the structure analyst's
    tech_tenor/moat_pricing overlay → re-rank. Persists the basket. Returns (rows, basket)."""
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
    rank_cohort(rows, layer_cap=layer_cap)          # pure-quant pass
    for r in rows:
        r.quant_rank = r.rank

    structural, subgroup_notes = False, {}
    if structure and layer.structure_notes:
        from . import structure as struct_mod

        scores, subgroup_notes = struct_mod.assess(rows, layer.structure_notes)
        if scores:
            for r in rows:
                s = scores.get(r.symbol)
                if s:
                    r.tech_tenor, r.moat_pricing, r.rationale = s
            rank_cohort(rows, layer_cap=layer_cap, weights=BLENDED_WEIGHTS)   # blended re-rank
            structural = True

    basket = to_basket(rows, layer_key, layer_cap, structural=structural, subgroup_notes=subgroup_notes)

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
                 "momentum": "动量(60d)", "revisions": "评级修正",
                 "tech_tenor": "技术久期", "moat_pricing": "护城河/定价权"}
_ALL_WEIGHTS = {**QUANT_WEIGHTS, **STRUCT_WEIGHTS}


def _drivers(r: FactorRow) -> tuple[str, str]:
    """Rank a name's factors by weighted contribution → (drivers, drags) prose."""
    contrib = sorted(((k, _ALL_WEIGHTS.get(k, 0.1) * v, v) for k, v in r.z.items()),
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
                  layer_cap: float, sector_label: str, sector_name: str, *,
                  structural: bool = False, subgroup_notes: dict | None = None) -> str:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    weights = BLENDED_WEIGHTS if structural else QUANT_WEIGHTS
    gen = ("**量化因子 + KB 结构层(LLM)复合**" if structural else "**确定性因子模型，无 LLM**")
    flag = " --structure" if structural else ""
    lines = [
        f"# 🤖 截面选股 — {layer_label}（{sector_label}）（{now:%Y-%m-%d}）",
        "",
        f"> 由 `ats sector crosssection {sector_name} --layer {layer_key}{flag}` 生成"
        f"（{gen}）。回答「层内选谁、各配多少」。",
        "",
        "## 方法",
        "- 因子（组内 z-score 标准化）与权重：" + "、".join(
            f"{FACTOR_LABELS[k]} {w:.0%}" for k, w in weights.items()) + "。",
        "- 配权 = softmax(composite)×(1/beta)×小盘流动性 haircut，单票 ≤ 层cap×40%；数据不足者排除、不占额度。",
        "- `cohort_extra` 同业（如 MRVL）参与排名但不占本层额度（其风控归属在别层）。",
    ]
    if structural:
        lines.append("- **结构层**：`structure_analyst` 读子层知识库(KB) → 打 技术久期(tech_tenor) / "
                     "护城河·定价权(moat_pricing) 分（-2..+2），与量化 60/40 复合。q→b = 量化rank→复合rank。")
    lines += [
        "",
        f"## 排序与建议权重（layer_cap = {layer_cap:.0%} NAV，权重和 = {sum(r.weight for r in rows):.0%}）",
        "", "```", format_table(rows, layer_cap), "```", "",
    ]
    if structural and subgroup_notes:
        lines += ["## 子层技术曲线（KB 判断）", ""]
        lines += [f"- **{k}**：{v}" for k, v in subgroup_notes.items()]
        lines.append("")
    lines.append("## 逐名推理")

    for r in sorted(rows, key=lambda x: x.rank):
        if not r.data_ok:
            lines += [f"\n### {r.rank}. {r.symbol}（{r.subgroup or '-'}）— ⚠ 数据缺失，未纳入配权",
                      "- 免费源(yfinance/finnhub)无基本面；需付费源或手工录入后再评。"]
            if r.rationale:
                lines.append(f"- 结构判断：{r.rationale}")
            continue
        drv, drg = _drivers(r)
        def p(v, s=1.0, suf=""):
            return f"{v * s:.1f}{suf}" if v is not None else "—"
        rank_txt = (f"复合第 {r.rank}（量化原第 {r.quant_rank}）" if structural and r.quant_rank != r.rank
                    else f"第 {r.rank}")
        lines += [
            f"\n### {r.rank}. {r.symbol}（{r.subgroup or '-'}）— 建议 {r.weight * 100:.1f}%",
            f"- 综合分 **{r.composite:+.2f}**（{rank_txt}）。驱动：{drv}；拖累：{drg}。",
            f"- 原始：营收 {p(r.rev_growth, 100, '%')} · 毛利 {p(r.gross_margin, 100, '%')} · "
            f"营利 {p(r.op_margin, 100, '%')} · fwdPE {p(r.fwd_pe)} · PEG {p(r.peg())} · "
            f"60d动量 {p(r.mom_60d, 1, '%')} · 评级Δ {p(r.rating_delta)} · beta {p(r.beta)} · "
            f"市值 ${(r.market_cap or 0) / 1e9:.0f}B。",
        ]
        if structural and (r.tech_tenor is not None or r.moat_pricing is not None):
            lines.append(f"- **结构**：技术久期 {p(r.tech_tenor)} · 护城河/定价权 {p(r.moat_pricing)}"
                         + (f" — {r.rationale}" if r.rationale else ""))
        for fl in _flags(r):
            lines.append(f"- ⚠ {fl}")

    lines += ["", "## 方法 caveat"]
    if structural:
        lines.append("- 结构分是 LLM 基于 KB 的判断；KB 硬数字(市占%等)需人核（标 TODO 者尤其）。")
    else:
        lines.append("- 仅量化因子，缺「结构/技术久期」定性维度 → 加 `--structure` 引入 KB 结构层修正。")
    lines += [
        "- PEG 分母增速封顶 60%，抑制超高增速把 PEG 压得虚低。",
        "- 数据缺失者不臆造排名；小盘/微盘覆盖差需付费源。",
        "", "---",
        f"*数据截至 {now:%Y-%m-%d %H:%M} UTC。加仓仍受组合级风控(beta/簇/压测)约束。*", "",
    ]
    return "\n".join(lines)


def write_report(rows: list[FactorRow], basket, cfg) -> "object | None":
    from datetime import datetime, timezone
    from pathlib import Path

    if not cfg.output_dir:
        return None
    folder = Path(cfg.output_dir)
    if not folder.is_dir():
        log.warning("cross-section report: output_dir missing — skipped: %s", folder)
        return None
    layer = next((ly for ly in cfg.layers if ly.key == basket.layer_key), None)
    layer_label = layer.label if layer else basket.layer_key
    text = render_report(rows, basket.layer_key, layer_label, basket.layer_cap, cfg.label,
                         cfg.name, structural=basket.structural, subgroup_notes=basket.subgroup_notes)
    path = folder / f"截面选股-{cfg.label}-{basket.layer_key}-{datetime.now(timezone.utc):%Y-%m-%d}.md"
    path.write_text(text, encoding="utf-8")
    return path


def format_table(rows: list[FactorRow], layer_cap: float) -> str:
    structural = any(r.tech_tenor is not None or r.moat_pricing is not None for r in rows)
    scol = f"{'ten':>5}{'moat':>6}" if structural else ""
    rcol = f"{'q→b':>7}" if structural else f"{'rank':>5}"
    hdr = (f"{'sym':<6}{'sub':<8}{'mcap$B':>7}{'beta':>6}{'revG%':>7}{'GM%':>6}{'OM%':>6}"
           f"{'fwdPE':>7}{'PEG':>6}{'mom60':>7}{'revΔ':>6}{scol} | {'comp':>6}{rcol}{'wt%':>7}")
    lines = [hdr, "-" * len(hdr)]
    for r in sorted(rows, key=lambda x: x.rank):
        def p(v, s=1.0):
            return f"{v * s:.1f}" if v is not None else "—"
        flag = "" if r.data_ok else "  ⚠无数据"
        comp = "  —" if r.composite == NEG_INF else f"{r.composite:>6.2f}"
        s = f"{p(r.tech_tenor):>5}{p(r.moat_pricing):>6}" if structural else ""
        rk = f"{r.quant_rank}→{r.rank:<3}".rjust(7) if structural else f"{r.rank:>5}"
        lines.append(
            f"{r.symbol:<6}{(r.subgroup or ''):<8}{(r.market_cap or 0)/1e9:>7.1f}{(r.beta or 0):>6.2f}"
            f"{p(r.rev_growth,100):>7}{p(r.gross_margin,100):>6}{p(r.op_margin,100):>6}"
            f"{p(r.fwd_pe):>7}{p(r.peg()):>6}{p(r.mom_60d):>7}{p(r.rating_delta):>6}{s} | "
            f"{comp}{rk}{r.weight*100:>6.1f}%{flag}")
    lines.append(f"\nlayer_cap={layer_cap:.0%} of NAV · weights sum={sum(r.weight for r in rows)*100:.1f}%")
    lines.append(f"factor weights: {BLENDED_WEIGHTS if structural else QUANT_WEIGHTS}"
                 + ("  (q→b: 量化rank→复合rank; ten=技术久期 moat=护城河/定价权, -2..+2)" if structural else ""))
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
