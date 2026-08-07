"""Earnings-call transcripts from the defeatbeta Yahoo Finance mirror (HuggingFace).

Primary transcript source, ahead of every search-based path. The point is not accuracy
in the usual sense — it is that **two whole classes of failure become impossible**:

  * Wrong company. Rows are keyed by ticker. Nothing here can hand back Sherwin-Williams
    for SKHY, Teradyne or Newmont for Samsung, ASM International for ASML, or Apple for
    AMD — all five of which a web search did on 2026-08-06.
  * Wrong period. `fiscal_year` / `fiscal_quarter` / `report_date` are structured
    columns, so the period check is an equality test rather than a regex sniffing prose.
    A September-2025 deck cannot be served as Q2 FY2027 the way one was here.

It also carries speaker names per paragraph, which the scraped pages do not: the
observer currently receives an undifferentiated wall of text and has to infer who is
talking before it can attribute a quote.

Coverage, measured 2026-08-06: 236k transcripts, dataset refreshed daily (spec.json
said 2026-08-05). **US-listed only** — zero symbols carry an exchange suffix, so
005930.KS (Samsung) and 2408.TW (Nanya) are absent and must be hand-dropped into
`信息源/<SYM>/`. SK hynix IS present under its US ADR ticker SKHY, which is also our
canonical id, so the most important non-US witness is covered.

Licence: ODC-BY. A 2.2GB parquet read over HTTPS with a symbol predicate takes ~40s —
irrelevant at eight witnesses reporting four times a year.

Risk worth stating: this is one person's mirror. If it stalls we fall back to the
search path, with every guard still in place, and `信息源/` keeps what was already
pulled. That is a degradation, not a data loss.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger("ats.data.defeatbeta")

REPO = "defeatbeta/yahoo-finance-data"
TRANSCRIPTS = (f"https://huggingface.co/datasets/{REPO}/resolve/main/data/"
               "stock_earning_call_transcripts.parquet")


@dataclass
class Transcript:
    symbol: str
    fiscal_year: int
    fiscal_quarter: int
    report_date: str
    text: str

    @property
    def label(self) -> str:
        """Fiscal label in this repo's usual shape, e.g. `Q2 FY2026`."""
        return f"Q{self.fiscal_quarter} FY{self.fiscal_year}"


def _connect():
    import duckdb

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    return con


def _render(rows) -> str:
    """`SPEAKER: content` per paragraph — keep the attribution the dataset gives us."""
    out = []
    for speaker, content in rows:
        body = (content or "").strip()
        if not body:
            continue
        who = (speaker or "").strip()
        out.append(f"{who}: {body}" if who else body)
    return "\n\n".join(out)


def fetch(symbol: str, *, fiscal_year: int | None = None,
          fiscal_quarter: int | None = None) -> Transcript | None:
    """Latest transcript for `symbol`, or the exact quarter when one is named.

    Returns None when the symbol is absent (every non-US listing) or the dataset is
    unreachable — callers fall through to the next source. Never raises: an outage in a
    third-party mirror must not take down a scheduled window.
    """
    from ..config import canonical_entity

    sym = canonical_entity(symbol).upper()
    where = ["symbol = ?"]
    args: list = [sym]
    if fiscal_year:
        where.append("fiscal_year = ?")
        args.append(int(fiscal_year))
    if fiscal_quarter:
        where.append("fiscal_quarter = ?")
        args.append(int(fiscal_quarter))
    sql = (f"SELECT symbol, fiscal_year, fiscal_quarter, report_date, transcripts "
           f"FROM read_parquet('{TRANSCRIPTS}') WHERE {' AND '.join(where)} "
           f"ORDER BY report_date DESC LIMIT 1")
    try:
        con = _connect()
        row = con.execute(sql, args).fetchone()
    except Exception as exc:  # noqa: BLE001 - third-party mirror; degrade, never break
        log.info("defeatbeta: query failed for %s (%s)", sym, exc)
        return None
    if not row:
        log.info("defeatbeta: no transcript for %s%s", sym,
                 f" {fiscal_year}Q{fiscal_quarter}" if fiscal_year else "")
        return None

    paragraphs = [(p.get("speaker"), p.get("content")) for p in (row[4] or [])]
    text = _render(paragraphs)
    if not text.strip():
        return None
    return Transcript(symbol=row[0], fiscal_year=int(row[1]), fiscal_quarter=int(row[2]),
                      report_date=str(row[3]), text=text)


def available(symbols: list[str]) -> dict[str, str]:
    """{symbol: latest report_date} for the ones the dataset carries. For coverage
    reporting — tells you which witnesses will never arrive on their own."""
    from ..config import canonical_entity

    syms = [canonical_entity(s).upper() for s in symbols]
    if not syms:
        return {}
    placeholders = ",".join("?" * len(syms))
    try:
        con = _connect()
        rows = con.execute(
            f"SELECT symbol, max(report_date) FROM read_parquet('{TRANSCRIPTS}') "
            f"WHERE symbol IN ({placeholders}) GROUP BY symbol", syms).fetchall()
    except Exception as exc:  # noqa: BLE001
        log.info("defeatbeta: availability query failed (%s)", exc)
        return {}
    return {r[0]: str(r[1]) for r in rows}


FILINGS = (f"https://huggingface.co/datasets/{REPO}/resolve/main/data/"
           "stock_sec_filing.parquet")
# US domestic issuers report on 8-K; foreign private issuers (ASML, TSM, SK hynix) use
# 6-K. Both carry the earnings release as an exhibit, so both are in scope — restricting
# to 8-K would silently exclude exactly the non-US witnesses we work hardest to cover.
EARNINGS_FORMS = ("8-K", "6-K")


@dataclass
class Filing:
    symbol: str
    cik: str
    accession: str
    form_type: str
    filing_date: str
    url: str


def filings(symbol: str, *, forms: tuple[str, ...] = EARNINGS_FORMS,
            near: str = "", window_days: int = 4) -> list[Filing]:
    """Filing METADATA for a symbol — the table carries no document text.

    That is the useful shape rather than a limitation: it hands us the CIK and the
    accession number, which is all EDGAR needs to serve the exhibit deterministically.
    No search, no guessing which company or which quarter.

    `near` (YYYY-MM-DD) keeps only filings within `window_days` of that date, which is
    how the earnings release gets told apart from the other 400 8-Ks a company files:
    the release lands on the print date. Without it, "latest 8-K" picks up director
    changes and shelf registrations.
    """
    from datetime import date, timedelta

    from ..config import canonical_entity

    sym = canonical_entity(symbol).upper()
    where = ["symbol = ?", f"form_type IN ({','.join('?' * len(forms))})"]
    args: list = [sym, *forms]
    if near:
        try:
            pivot = date.fromisoformat(near)
        except ValueError:
            pivot = None
        if pivot:
            where.append("filing_date BETWEEN ? AND ?")
            args += [str(pivot - timedelta(days=window_days)),
                     str(pivot + timedelta(days=window_days))]
    sql = (f"SELECT symbol, cik, accession_number, form_type, filing_date, filing_url "
           f"FROM read_parquet('{FILINGS}') WHERE {' AND '.join(where)} "
           f"ORDER BY filing_date DESC LIMIT 20")
    try:
        rows = _connect().execute(sql, args).fetchall()
    except Exception as exc:  # noqa: BLE001 - a mirror outage must not break the window
        log.info("defeatbeta: filing query failed for %s (%s)", sym, exc)
        return []
    return [Filing(symbol=r[0], cik=str(r[1]), accession=str(r[2]), form_type=str(r[3]),
                   filing_date=str(r[4]), url=str(r[5] or "")) for r in rows]
