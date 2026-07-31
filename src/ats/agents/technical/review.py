"""Technical analyst orchestration: resolve universe -> fetch prices -> compute.

No LLM anywhere in this module by design. The 7-point score and the exposure it
implies were validated deterministically over 7.5 years; wrapping them in a model
would add unvalidated variance to a number that is already exact, and the Chief
is itself an LLM perfectly capable of reading them.

Never raises into the scheduler: every boundary degrades to a note.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from ...schemas.technical import TechnicalReading, TechnicalReview
from . import strategy as st

log = logging.getLogger("ats.agents.technical")

# Symbols that are cash by intent, not positions to time. Extended by config.
_CASHLIKE = {"SGOV", "SHV", "BIL", "USD", "CASH"}


def resolve_universe(cfg, *, live_broker: bool = True) -> tuple[list[str], list[str]]:
    """→ (symbols, notes). Live holdings ∪ PEAD targets, minus what cannot be timed.

    Option positions are filtered out: this book holds short calls whose IBKR
    symbol matches the underlying, but a contract's exposure is not the
    underlying's exposure and a moving-average rule says nothing useful about it.
    """
    from ...config import load_pead_global

    u = cfg.universe
    notes: list[str] = []
    syms: set[str] = set()

    if u.get("include_pead_targets", True):
        try:
            syms |= {s.upper() for s in load_pead_global().get("targets", [])}
        except Exception as exc:  # noqa: BLE001
            notes.append(f"PEAD targets 读取失败: {exc}")

    if u.get("include_holdings", True):
        if not live_broker:
            notes.append("offline：跳过实时持仓，仅用 PEAD targets")
        else:
            try:
                from ...trader import portfolio as tp

                pf = tp.snapshot()
                if pf is None:
                    notes.append("IBKR 不可用：退化为 PEAD targets-only")
                else:
                    for p in pf.positions:
                        if getattr(p, "sec_type", "STK") not in ("STK", "", None):
                            # The option contract itself is not timeable; the
                            # underlying may still be evaluated if it is a target.
                            notes.append(f"{p.symbol}: 期权持仓本身不评估（标的若在 "
                                         f"targets 中仍会出读数）")
                            continue
                        syms.add(p.symbol.upper())
            except Exception as exc:  # noqa: BLE001
                notes.append(f"持仓读取失败，退化为 targets-only: {exc}")

    excluded = {s.upper() for s in u.get("exclude", [])} | _CASHLIKE
    dropped = sorted(syms & excluded)
    if dropped:
        notes.append(f"现金等价物不评估: {', '.join(dropped)}")
    kept = sorted(syms - excluded)

    # Collapse tickers that are the same instrument once normalised. HY9H (the
    # Frankfurt ADR) and SKHY both resolve to SKHY; without this, fetch_prices'
    # reverse map keeps only one and the other silently reports "no price data".
    from ...data.base import yf_symbol

    canonical: dict[str, str] = {}
    aliases: list[str] = []
    for sym in kept:
        key = yf_symbol(sym)
        prev = canonical.get(key)
        if prev is None:
            canonical[key] = sym
            continue
        # Keep whichever ticker already IS the normalised form (SKHY over HY9H):
        # it is the one with price data, and it reads correctly in the report.
        keep, drop = (sym, prev) if sym == key else (prev, sym)
        canonical[key] = keep
        aliases.append(f"{drop}≡{keep}")
    if aliases:
        notes.append(f"同一标的的别名已合并: {', '.join(aliases)}")
    return sorted(canonical.values()), notes


def _params(cfg) -> dict:
    """Config values over strategy.py defaults, key by key (single source rule)."""
    p = cfg.params or {}
    return {
        "vix_anchor": p.get("vix_anchor", st.VIX_ANCHOR),
        "panic_ratio": p.get("panic_ratio", st.PANIC_TERM_RATIO),
        "bear_cap": p.get("bear_cap", st.BEAR_PRICE_CAP),
        "sigma_cap": p.get("sigma_cap", st.SIGMA_CAP),
    }


def compute_readings(closes_by_symbol: dict[str, list[float]], *, vix: float | None,
                     vix3m: float | None, cfg, prior: dict[str, float] | None = None
                     ) -> list[TechnicalReading]:
    """Pure: prices in, readings out. No I/O — this is what the tests drive."""
    par = _params(cfg)
    mode = cfg.strategy or st.DEFAULT_MODE
    min_bars = int(cfg.universe.get("min_bars", 200))
    prior = prior or {}
    out: list[TechnicalReading] = []

    for sym in sorted(closes_by_symbol):
        closes = closes_by_symbol[sym] or []
        if len(closes) < min_bars:
            out.append(TechnicalReading(symbol=sym, bars=len(closes), stale=True,
                                        note=f"历史不足（{len(closes)} < {min_bars} 根）"))
            continue
        i = len(closes) - 1
        detail = st.score_detail(closes, i)
        score = st.momentum_score_7(closes, i)
        base = st.exposure_from(score, vix, mode, vix_anchor=par["vix_anchor"],
                                sigma_cap=par["sigma_cap"])
        exposure, panic, bear = st.apply_tiers(
            base, close=detail["close"], sma200=detail["sma200"], vix=vix, vix3m=vix3m,
            panic_ratio=par["panic_ratio"], bear_cap=par["bear_cap"])
        sigma = (min(par["sigma_cap"], par["vix_anchor"] / vix)
                 if vix and vix > 0 else None)
        out.append(TechnicalReading(
            symbol=sym, score=score, score_detail=detail, raw_ladder=st.LADDER[score],
            vol_scalar=round(sigma, 4) if sigma else None,
            target_exposure=round(exposure, 4), prev_exposure=prior.get(sym),
            panic_fired=panic, bear_fired=bear, bars=len(closes),
            close=detail["close"], sma20=detail["sma20"], sma50=detail["sma50"],
            sma200=detail["sma200"]))
    return out


def _fetch(symbols: list[str], days: int) -> tuple[dict[str, list[float]], float | None,
                                                   float | None, list[str]]:
    """One batched download for the basket, plus VIX/VIX3M. Never raises."""
    from ...data import sector_snapshot

    notes: list[str] = []
    period = f"{max(2, days // 365 + 1)}y"
    closes = sector_snapshot.fetch_prices(symbols, period=period) or {}
    missing = sorted(set(symbols) - set(closes))
    if missing:
        notes.append(f"无价格数据: {', '.join(missing)}")

    vol = sector_snapshot.fetch_prices(["^VIX", "^VIX3M"], period="1y") or {}
    vix = vol.get("^VIX", [None])[-1] if vol.get("^VIX") else None
    v3 = vol.get("^VIX3M", [None])[-1] if vol.get("^VIX3M") else None
    if vix is None:
        notes.append("VIX 不可用：本次不做波动率调节")
    if v3 is None:
        # Deliberate: a stale VIX3M against a spiking VIX manufactures an
        # inverted term structure exactly when the signal matters most.
        notes.append("VIX3M 不可用：本次不评估 Tier1 恐慌（不做前向填充）")
    return closes, vix, v3, notes


def run(name: str = "technical", *, live_data: bool = True, persist: bool = True,
        write_report: bool = True) -> TechnicalReview:
    from ...config import load_technical_config
    from ...memory import get_store

    cfg = load_technical_config(name)
    now = datetime.now(timezone.utc)
    store = get_store()

    symbols, notes = resolve_universe(cfg, live_broker=live_data)
    review = TechnicalReview(
        name=cfg.name, as_of=now, strategy=cfg.strategy,
        fingerprint=st.params_fingerprint(cfg.strategy, st.LADDER,
                                          _params(cfg)["vix_anchor"],
                                          _params(cfg)["panic_ratio"],
                                          _params(cfg)["bear_cap"]),
        notes=notes)
    if not symbols:
        review.notes.append("universe 为空，未产生读数")
        return review

    if not live_data:
        review.notes.append("offline：跳过取价，未产生读数")
        return review

    closes, vix, v3, fetch_notes = _fetch(symbols, int(cfg.review.get("history_days", 420)))
    review.notes += fetch_notes
    review.vix, review.vix3m = vix, v3
    review.market_panic = bool(vix and v3 and v3 > 0
                               and vix / v3 >= _params(cfg)["panic_ratio"])

    prev = store.previous_technical_review(cfg.name, before=now.date().isoformat())
    prior = {r.symbol: r.target_exposure for r in prev.readings} if prev else {}

    review.readings = compute_readings(closes, vix=vix, vix3m=v3, cfg=cfg, prior=prior)
    review.skipped = sorted(set(symbols) - set(closes))

    if persist:
        store.save_technical_review(review)
    if write_report:
        from . import report as tech_report

        path = tech_report.write(review, cfg)
        if path:
            log.info("technical report: %s", path)
    return review
