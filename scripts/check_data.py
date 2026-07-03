"""Probe each PEAD data source, print a status line, and DUMP the full result.

    PYTHONPATH=src .venv/bin/python scripts/check_data.py            # all sources (no LLM)
    PYTHONPATH=src .venv/bin/python scripts/check_data.py news COHR  # one source
    PYTHONPATH=src .venv/bin/python scripts/check_data.py triage COHR    # channel-1 news scoring (LLM)
    PYTHONPATH=src .venv/bin/python scripts/check_data.py insights COHR  # channel-2 insights (LLM)

LLM-inclusive checks (triage/insights) are run by name only — excluded from the
no-arg all-pass so a bare run never spends on the LLM.

Each source's full response is written to var/data_dumps/<source>_<SYM>.json (or
.txt for the transcript) so you can inspect everything — `open var/data_dumps/`.
Sources are fetched live (network); raw fetches are not otherwise cached.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel

from ats.data import (
    consensus,
    documents,
    earnings_calendar,
    fundamentals,
    industry,
    macro,
    market_data,
    news,
    options,
    research,
    runup,
    transcript,
)
from ats.schemas.market import Ticker

SYM = sys.argv[2] if len(sys.argv) > 2 else "COHR"
NOW = datetime.now(timezone.utc)
DUMP_DIR = Path(__file__).resolve().parents[1] / "var" / "data_dumps"


def market():
    s = market_data.fetch_snapshot(Ticker(symbol=SYM))
    return bool(s.last_price), f"last={s.last_price} bars={len(s.history)} ind={len(s.indicators)}", s


def fund():
    f = fundamentals.fetch(SYM)
    return bool(f.market_cap or f.trailing_pe), \
        f"P/E={f.trailing_pe} margin={f.profit_margin} filings={len(f.recent_filings)}", f


def macro_():
    m = macro.fetch()
    return any([m.ust_10y, m.vix, m.spx]), \
        f"UST10Y={m.ust_10y} CPI={m.cpi_yoy} VIX={m.vix} SPX={m.spx} F&G={m.fear_greed}", m


def opts():
    ed = earnings_calendar.next_earnings_date(SYM)
    o = options.fetch(SYM, ed)
    return bool(o.get("expected_move_pct")), \
        f"src={o.get('source')} EM={o.get('expected_move_pct')}% IV={o.get('atm_iv')}% " \
        f"skew={o.get('iv_skew')} exp={o.get('expiration')}", o


def earn():
    ev = earnings_calendar.next_earnings(SYM)
    return bool(ev and ev.get("date")), \
        (f"date={ev['date']} hour={ev['hour']} epsEst={ev['eps_estimate']}" if ev else "none"), ev


def cons():
    c = consensus.fetch(SYM)
    ratings = (f"{c.get('rating_strong_buy')}/{c.get('rating_buy')}/{c.get('rating_hold')}/"
               f"{c.get('rating_sell')}/{c.get('rating_strong_sell')}")
    ok = bool((c.get("eps") or c.get("revenue")) and c.get("target_mean") is not None)
    return ok, \
        (f"EPS={c.get('eps')} Rev={c.get('revenue')} "
         f"PT={c.get('target_low')}~{c.get('target_mean')}~{c.get('target_high')} "
         f"SB/B/H/S/SS={ratings} U/D(120d)={len(c.get('upgrades_downgrades') or [])}"), c


def run():
    r = runup.compute(SYM)
    return r.get("pre_earnings_close") is not None, \
        f"close={r.get('pre_earnings_close')} vsSMH={r.get('run_up_vs_sector_pct')}% " \
        f"distATH={r.get('dist_to_ath_pct')}%", r


def news_():
    items = news.fetch_news(SYM, NOW - timedelta(days=14))
    return len(items) > 0, f"{len(items)} items; latest: " + (items[0].headline[:60] if items else "-"), items


def research_():
    from ats.config import get_config

    arts = research.fetch_articles(NOW - timedelta(days=3))
    creds = "yes" if get_config().secrets.gmail_address else "NO — set GMAIL_ADDRESS/GMAIL_APP_PASSWORD"
    latest = f"{arts[0].source}: {arts[0].title[:50]}" if arts else "-"
    return len(arts) > 0, f"{len(arts)} articles (imap creds={creds}); latest: {latest}", \
        [{"id": a.id, "source": a.source, "title": a.title, "url": a.url,
          "published_at": a.published_at, "chars": len(a.body), "body": a.body[:2000]}
         for a in arts]


def triage_():
    """Channel 1: fetch news, flash-score materiality, show kept vs dropped."""
    from ats.agents.pead import triage
    from ats.config import load_pead_config, load_pead_global
    from ats.memory import get_store

    cfg = load_pead_config(SYM)
    dossier = get_store().get_dossier(SYM, cfg.fiscal_label)
    thesis = (dossier.expectation_set.narrative if dossier and dossier.expectation_set
              else cfg.narrative_seed)
    items = news.fetch_news(SYM, NOW - timedelta(days=14))
    scores = triage.score_items(SYM, thesis, items)
    tcfg = load_pead_global()["monitor"]["triage"]
    rows = []
    for it in items:
        sc, cat = scores.get(it.id, (None, ""))
        rows.append({"score": sc, "category": cat,
                     "kept": sc is None or sc >= tcfg["min_score"],
                     "source": it.source, "headline": it.headline, "url": it.url})
    rows.sort(key=lambda r: r["score"] if r["score"] is not None else -1, reverse=True)
    kept = sum(1 for r in rows if r["kept"])
    return bool(scores), \
        f"{len(items)} items scored → {kept} kept (≥{tcfg['min_score']}), {len(items)-kept} dropped", rows


def insights_():
    """Channel 2: fetch newsletters + extract per-ticker insights (read-only, no persist)."""
    from ats.agents.pead import research as ra
    from ats.config import load_pead_global

    arts = research.fetch_articles(NOW - timedelta(days=7))
    if not arts:
        return False, "0 newsletter articles in inbox (need a matching sender; see senders config)", []
    card, mapping = ra._build_universe(load_pead_global().get("targets", []))
    out = []
    for art in arts[:5]:
        ins = ra._extract(art, card, set(mapping), 40000)
        out.append({"article": {"source": art.source, "title": art.title, "url": art.url,
                                "chars": len(art.body)},
                    "insights": [i.model_dump(mode="json") for i in ins]})
    n = sum(len(o["insights"]) for o in out)
    return n > 0, f"{len(out)} articles → {n} insights extracted", out


def industry_():
    notes = industry.fetch_notes()
    total = sum(len(t) for _, t in notes)
    listing = "; ".join(f"{n}({len(t)//1000}k)" for n, t in notes) or "-"
    return len(notes) > 0, f"{len(notes)} notes, {total//1000}k chars: {listing}", \
        [{"name": n, "chars": len(t), "text": t} for n, t in notes]


def trans():
    text, src = transcript.fetch(SYM, "Q FY2026")
    return bool(text), f"src={src} chars={len(text)}", (text, src)


def docs():
    d = documents.gather(SYM)
    summary = f"{len(d)} docs: " + ", ".join(f"{lbl}({len(t)//1000}k)" for lbl, t in d)
    return len(d) > 0, summary, [{"label": lbl, "chars": len(t), "text": t} for lbl, t in d]


CHECKS = {
    "market": ("yfinance (no key)", market),
    "fundamentals": ("yfinance + SEC", fund),
    "macro": ("FRED + yfinance", macro_),
    "options": ("ThetaData / yfinance", opts),
    "earnings": ("Finnhub / yfinance", earn),
    "consensus": ("yfinance", cons),
    "runup": ("yfinance", run),
    "news": ("Finnhub + RSS", news_),
    "research": ("Gmail IMAP + Substack RSS", research_),
    "transcript": ("Tavily/FMP/manual", trans),
    "documents": ("SEC 8-K + folder PDFs", docs),
    "industry": ("Obsidian 行业合集", industry_),
}

# LLM-inclusive verifications (cost $): run by name only, excluded from the all-pass.
#   triage   = channel-1 output (news materiality scores, kept vs dropped)
#   insights = channel-2 output (per-ticker insights extracted from newsletters)
LLM_CHECKS = {
    "triage": ("news_triage (Gemini Flash)", triage_),
    "insights": ("research_extract (Opus)", insights_),
}


def _dump(name: str, obj) -> Path:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    # Transcript: (text, source) -> .txt
    if isinstance(obj, tuple) and len(obj) == 2 and isinstance(obj[0], str):
        text, src = obj
        p = DUMP_DIR / f"{name}_{SYM}.txt"
        p.write_text(f"# source: {src}\n# chars: {len(text)}\n\n{text}", encoding="utf-8")
        return p
    if isinstance(obj, BaseModel):
        data = obj.model_dump(mode="json")
    elif isinstance(obj, list):
        data = [o.model_dump(mode="json") if isinstance(o, BaseModel) else o for o in obj]
    else:
        data = obj
    p = DUMP_DIR / f"{name}_{SYM}.json"
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return p


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    registry = {**CHECKS, **LLM_CHECKS}
    if only in registry:
        items = {only: registry[only]}       # named check (incl. LLM-only ones)
    else:
        items = CHECKS                        # all-pass excludes LLM checks (cost $)
    print(f"Testing data sources for {SYM} (full results -> var/data_dumps/):\n")
    for name, (needs, fn) in items.items():
        try:
            ok, summary, obj = fn()
            mark = "✓" if ok else "✗"
            path = _dump(name, obj)
            loc = path.relative_to(Path(__file__).resolve().parents[1])
        except Exception as exc:  # noqa: BLE001
            mark, summary, loc = "✗", f"ERROR: {exc}", "(no dump)"
        print(f"  {mark} {name:13} [{needs}]\n      {summary}\n      → {loc}")


if __name__ == "__main__":
    main()
