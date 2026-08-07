"""EDGAR exhibit fetch by accession number — the earnings release itself.

Why this exists alongside the transcript: the call is where management NARRATES, the
release is where the numbers are TABULATED. On 2026-08-07 every one of the ledger's 379
`reported_actual` observations came from a transcript, so the most checkable facts in
the system were being extracted from its least precise source — spoken, rounded figures
like "ASP rose by mid 40%". The release carries the same quarter as a table.

For a US filer the earnings release IS Exhibit 99.1 of the 8-K; foreign private issuers
(ASML, TSM, SK hynix) attach it to a 6-K. They are not two artefacts to choose between.

Deterministic by construction: the caller supplies CIK and accession number (from
data.defeatbeta.filings), so there is no search step and no possibility of being handed
another company's document. EDGAR requires a declared User-Agent — `SEC_EDGAR_USER_AGENT`
in `.env`; requests without one are refused, and rightly so.
"""

from __future__ import annotations

import logging

log = logging.getLogger("ats.data.sec")

ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
MIN_CHARS = 800


def _headers() -> dict:
    from ..config import get_config

    return {"User-Agent": get_config().secrets.sec_edgar_user_agent,
            "Accept-Encoding": "gzip, deflate"}


def exhibit_text(cik: str, accession: str) -> tuple[str, str]:
    """(text, url) of a filing's earnings-release exhibit. ("", "") when absent.

    An `ex99` exhibit is REQUIRED, never merely preferred. Falling back to the largest
    document in the filing returned SK hynix's bare 6-K cover page — 4.8k of "UNITED
    STATES SECURITIES AND EXCHANGE COMMISSION, Washington D.C." with no financial
    content — because that 6-K carried no exhibit at all. A cover page is not an
    earnings release, and a filing without an exhibit is simply not the filing we want:
    the caller moves on to the next one rather than accepting boilerplate.

    Among the exhibits, the largest wins. A filing carries several — the press release,
    sometimes slides, sometimes a short consent letter — and size separates them.
    """
    import httpx

    from .documents import _text, strip_xbrl_boilerplate

    import re

    accn = (accession or "").replace("-", "")
    if not accn or not cik:
        return "", ""
    base = f"{ARCHIVES}/{int(cik)}/{accn}"
    # The filing index PAGE, not index.json. The JSON's `type` field is an icon name
    # ("text.gif"); only the page carries the declared document type. Matching on the
    # filename instead is what this replaced, and it failed on the first filer tried:
    # AMD's Exhibit 99.1 is named `q22026991.htm`, which contains no "ex99" at all.
    try:
        page = httpx.get(f"{base}/{accession}-index.html", headers=_headers(), timeout=25)
        page.raise_for_status()
    except Exception as exc:  # noqa: BLE001 - EDGAR hiccup must not break a window
        log.info("sec: index unavailable for %s/%s (%s)", cik, accession, exc)
        return "", ""

    exhibits: list[tuple[int, str]] = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page.text, re.S):
        cells = [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) < 5 or not cells[3].upper().startswith("EX-99"):
            continue
        doc = next((h for h in re.findall(r'href="([^"]+)"', row)
                    if h.lower().endswith((".htm", ".html")) and "/ix?doc=" not in h), "")
        if doc:
            exhibits.append((int(cells[4]) if cells[4].isdigit() else 0, doc))
    if not exhibits:
        log.info("sec: %s/%s carries no EX-99 exhibit — not an earnings release",
                 cik, accession)
        return "", ""
    url = "https://www.sec.gov" + max(exhibits)[1]
    try:
        body = httpx.get(url, headers=_headers(), timeout=30)
        body.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        log.info("sec: exhibit fetch failed %s (%s)", url, exc)
        return "", ""
    text = strip_xbrl_boilerplate(_text(body.text))
    if len(text) < MIN_CHARS:
        log.info("sec: exhibit at %s is only %d chars — treating as absent", url, len(text))
        return "", ""
    return text, url


def earnings_release(symbol: str, *, near: str = "") -> tuple[str, str, str]:
    """(text, url, note) for the earnings release nearest `near` (the print date).

    `near` matters: a company files hundreds of 8-Ks, and only the one landing on the
    print date is the release. Without it "latest 8-K" returns director changes and
    shelf registrations — which is exactly the class of error the keyed sources were
    adopted to eliminate, so reintroducing it here would be self-defeating.
    """
    from . import defeatbeta

    hits = defeatbeta.filings(symbol, near=near)
    if not hits:
        return "", "", f"未找到 {near or '近期'} 附近的 8-K/6-K 备案"
    for filing in hits:
        text, url = exhibit_text(filing.cik, filing.accession)
        if text:
            return text, url, (f"{filing.form_type} 财报稿 · 备案日 {filing.filing_date}"
                               f" · accession {filing.accession}")
    return "", "", f"{hits[0].form_type}@{hits[0].filing_date} 无可用 ex99 附件"
