"""Official document sources for fundamental analysis.

  (a) Earnings release  -> auto from the latest SEC 8-K Exhibit 99.1 (canonical,
      free, no IR-site scraping).
  (b) Investor presentation / other -> read from a local folder (<docs_root>/<SYM>/),
      where you drop the PDFs you download from the IR site. PDFs parsed with pypdf.

Returns a list of (label, text). Each source degrades independently.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date
from pathlib import Path

from .base import safe_fetch

log = logging.getLogger("ats.data.documents")
name = "documents"

_MIN_DOC_CHARS = 1000


_RELEASE_KW = ("earning", "release", "press", "8-k", "8k")
_DECK_KW = ("presentation", "investor", "deck", "slide")


def gather(symbol: str, docs_root: str | None = None, *, period: str = "",
           report_date: str = "",
           store=None) -> list[tuple[str, str]]:
    """Earnings release + investor presentation + any extra curated docs.

    Priority per doc type: a curated file in <docs_root>/<SYM>/ (most precise) wins;
    otherwise auto-fetch — release from SEC 8-K, deck via Tavily. No duplicates.
    """
    folder = _from_folder(symbol, docs_root)
    used: set[str] = set()
    docs: list[tuple[str, str]] = []
    new_docs: list[tuple[str, str]] = []
    metadata: dict[str, dict] = {}

    f_release = _first_semantic(folder, "company_release")
    if f_release:
        docs.append(f_release); new_docs.append(f_release); used.add(f_release[0])
    else:
        cached = _cached(symbol, period, "release")
        if cached:
            docs.append(cached)
        else:
            rel = safe_fetch(lambda: sec_8k_release(symbol, near=report_date, period=period),
                             source=f"sec-8k:{symbol}", attempts=2)
            if rel:
                item = (rel["label"], rel["text"])
                docs.append(item); new_docs.append(item)
                metadata[rel["label"]] = rel

    f_deck = _first_semantic(folder, "investor_presentation")
    if f_deck:
        docs.append(f_deck); new_docs.append(f_deck); used.add(f_deck[0])
    else:
        cached = _cached(symbol, period, "deck")
        if cached:
            docs.append(cached)
        else:
            deck = safe_fetch(lambda: _tavily_deck(symbol), source=f"tavily-deck:{symbol}", attempts=1)
            if deck:
                docs.append(deck); new_docs.append(deck)

    extras = [d for d in folder if d[0] not in used]
    docs += extras                                  # extra curated docs (e.g. a saved 10-K)
    new_docs += extras
    accepted = _persist(symbol, period, new_docs, metadata=metadata, store=store)
    new_labels = {label for label, _ in new_docs}
    return [item for item in docs if item[0] not in new_labels or item[0] in accepted]


def _cached(symbol: str, period: str, doc_type: str) -> tuple[str, str] | None:
    if not period:
        return None
    from . import source_cache
    from .document_types import compatible_type_values

    try:
        kinds = compatible_type_values(doc_type)
    except (KeyError, ValueError):
        kinds = (doc_type, doc_type)
    for kind in kinds:
        doc = source_cache.load(symbol, period, kind)
        if doc:
            return (doc.title or f"cached {doc_type} ({period})", doc.text)
    return None


def classify_semantic(label: str) -> str:
    """Classify business meaning; presentation overrides the word 'earnings'."""
    low = label.lower()
    if any(k in low for k in _DECK_KW):
        return "investor_presentation"
    if any(k in low for k in ("10-k", "10-q", "6-k", "20-f", "filing", "annual report")):
        return "regulatory_filing"
    if any(k in low for k in ("release", "press", "8-k", "8k", "financial results")):
        return "company_release"
    return "announcement"


def _official_domains(symbol: str) -> tuple[str, ...]:
    from ..config import entity_meta

    return tuple(str(domain).lower() for domain in (
        entity_meta(symbol).get("ir_domains", []) or []
    ))


def official_document_issues(candidate) -> list:
    """Validate source authority and semantic shape after Tavily URL discovery."""
    from urllib.parse import urlparse

    from .admission import ValidationIssue
    from .document_types import semantic_type

    issues = []
    host = (urlparse(candidate.source_url).hostname or "").lower()
    source = candidate.source.lower()
    allowed = tuple(str(item).lower() for item in (
        candidate.metadata.get("official_domains", ()) or ()))
    if source == "sec":
        if not host.endswith("sec.gov"):
            issues.append(ValidationIssue("source", "sec_domain_mismatch", host))
    elif source in {"tavily", "ir"}:
        if not allowed:
            issues.append(ValidationIssue("source", "official_domain_unconfigured", host))
        elif not any(host == domain or host.endswith(f".{domain}") for domain in allowed):
            issues.append(ValidationIssue("source", "official_domain_mismatch", host))

    try:
        semantic = semantic_type(candidate.expected_semantic).value
    except ValueError:
        return issues
    head = f"{candidate.title}\n{candidate.text[:12000]}".lower()
    if semantic == "investor_presentation":
        if not any(marker in head for marker in ("investor presentation", "earnings presentation",
                                                  "results presentation", "slides")):
            issues.append(ValidationIssue("type", "presentation_semantics_missing"))
    elif semantic == "company_release":
        hits = sum(marker in head for marker in (
            "financial results", "quarter ended", "revenue", "net income",
            "earnings per share", "guidance",
        ))
        if hits < 2:
            issues.append(ValidationIssue("type", "release_semantics_missing"))
    return issues


def _persist(symbol: str, period: str, docs: list[tuple[str, str]], *,
             metadata: dict[str, dict] | None = None, store=None) -> set[str]:
    """Register accepted official documents while preserving the tuple API."""
    from ..config import entity_meta
    from . import admission, document_assets, fiscal
    from .document_types import infer_carrier_format

    metadata = metadata or {}
    accepted: set[str] = set()
    for label, text in docs:
        meta = metadata.get(label, {})
        doc_type = classify_semantic(label)
        url_match = re.search(r"(?:tavily:)?(https?://[^)\s]+)", label)
        source_url = meta.get("source_url") or (url_match.group(1) if url_match else "")
        source = ("sec" if label.startswith("SEC ") else
                  "tavily" if "tavily:" in label else
                  "manual" if label.startswith("doc:") else "documents")
        # Release/deck are one-per-period roles. Extra filings and announcements use
        # their own stable label so several documents for the same quarter coexist.
        key = period if period and doc_type in {"company_release", "investor_presentation"} else \
            document_assets.stable_key(f"{period}|{label}", prefix=doc_type)
        published = ""
        filed = re.search(r"\((\d{4}-\d{2}-\d{2})\)", label)
        if filed:
            published = filed.group(1)
        if source == "manual":
            document = document_assets.ingest(
                entity=symbol, key=key, doc_type=doc_type, text=text, source=source,
                source_url=source_url,
                external_id=meta.get("accession") or source_url or f"{symbol}:{label}",
                title=label, published_at=published, related_entities=(symbol,), store=store,
            )
            if document is not None:
                accepted.add(label)
            continue

        company = entity_meta(symbol).get("name", "")
        claimed_entity = symbol if admission.mentions_entity(text, symbol, company) else ""
        detected = fiscal.detect_period(text, f"{label} {source_url}")
        claimed_period = f"Q{detected[1]} FY{detected[0]}" if detected else ""
        candidate = admission.CandidateDocument(
            expected_entity=symbol, claimed_entity=claimed_entity,
            target_period=period, claimed_period=claimed_period,
            expected_semantic=doc_type, claimed_semantic=doc_type,
            text=text, source=source, source_url=source_url,
            external_id=meta.get("accession") or source_url or f"{symbol}:{label}",
            title=label, published_at=published,
            carrier_format=infer_carrier_format(source_url or label),
            completeness="full", min_chars=_MIN_DOC_CHARS,
            related_entities=(symbol,),
            metadata={"official_domains": _official_domains(symbol)},
        )
        outcome = admission.admit(
            candidate, extensions=(official_document_issues,), store=store)
        if outcome.validation.accepted:
            accepted.add(label)
    return accepted


def _classify(folder: list[tuple[str, str]], keywords: tuple[str, ...]) -> tuple[str, str] | None:
    for label, text in folder:
        if any(k in label.lower() for k in keywords):
            return (label, text)
    return None


def _first_semantic(folder: list[tuple[str, str]], semantic: str) -> tuple[str, str] | None:
    return next((item for item in folder if classify_semantic(item[0]) == semantic), None)


# --------------------------------------------------------------------------- #
# (a) SEC 8-K Exhibit 99.1 earnings release
# --------------------------------------------------------------------------- #
def _headers() -> dict:
    from ..config import get_config

    return {"User-Agent": get_config().secrets.sec_edgar_user_agent}


def _sec_8k_release(symbol: str) -> tuple[str, str] | None:
    """(label, text) for `gather()`. See sec_8k_release for the dated form."""
    rel = sec_8k_release(symbol)
    return (rel["label"], rel["text"]) if rel else None


def sec_8k_release(symbol: str, *, near: str = "", period: str = "") -> dict | None:
    """Latest 8-K Exhibit 99.1 as {label, text, filed: date|None}.

    The filing DATE is what makes this usable as a "has the print happened" probe:
    an 8-K lands within minutes of the release, well before the data vendors
    populate an actual EPS. Callers must still check the date against the expected
    print — the newest 8-K may be an unrelated filing, or last quarter's release.
    """
    from . import sec

    return sec.earnings_release_record(symbol, near=near, period=period)


_XBRL_MARKERS = ("IDEA: XBRL DOCUMENT", "Namespace Prefix: dei_", "X - Definition")


def strip_xbrl_boilerplate(text: str) -> str:
    """Drop the SEC XBRL viewer's element dictionary from a rendered filing.

    An 8-K cover page rendered through EDGAR's viewer is ~7k characters of
    "X - Definition / Namespace Prefix: dei_ / Data Type: xbrli:..." with no financial
    content. Two costs, one of them silent: the observer pays to read it, and — worse —
    it fills the head of the document, which is the only window `fiscal.detect_period`
    examines. That is how a stale NVIDIA deck concatenated after one of these cover
    pages came back as "period undetectable" instead of "eleven months old".
    """
    body = text or ""
    if "X - Definition" in body:
        body = body[:body.index("X - Definition")]
    return body.strip()


def _text(html: str) -> str:
    t = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    t = re.sub(r"(?s)<[^>]+>", " ", t)
    return strip_xbrl_boilerplate(re.sub(r"\s+", " ", t).strip())


# --------------------------------------------------------------------------- #
# Investor presentation via Tavily web search (generalizes across companies)
# --------------------------------------------------------------------------- #
def _tavily_deck(symbol: str) -> tuple[str, str] | None:
    import httpx

    from ..config import get_config

    key = get_config().secrets.tavily_api_key
    if not key:
        return None
    r = httpx.post("https://api.tavily.com/search", timeout=40, json={
        "api_key": key, "query": f"{symbol} latest quarterly investor presentation slides pdf",
        "include_raw_content": True, "max_results": 6})
    r.raise_for_status()
    results = r.json().get("results", []) or []

    def is_deck(x: dict) -> bool:
        return "presentation" in (x.get("url", "") + x.get("title", "")).lower()

    # Prefer an actual .pdf deck over an IR landing page; then longer content.
    ranked = sorted(results, key=lambda x: (is_deck(x), x.get("url", "").lower().endswith(".pdf"),
                                            len(x.get("raw_content") or "")), reverse=True)
    for res in ranked:
        if not is_deck(res):
            continue
        url = res.get("url", "")
        content = res.get("raw_content") or ""
        if len(content) < _MIN_DOC_CHARS and url.lower().endswith(".pdf"):
            content = _download_pdf_text(url)        # Tavily didn't extract -> fetch the PDF
        if len(content) >= _MIN_DOC_CHARS:
            return (f"investor presentation (tavily:{url})", content)
    return None


def _download_pdf_text(url: str) -> str:
    import io

    import httpx

    try:
        from pypdf import PdfReader

        r = httpx.get(url, headers={"User-Agent": _BROWSER_UA}, timeout=40, follow_redirects=True)
        r.raise_for_status()
        reader = PdfReader(io.BytesIO(r.content))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception:  # noqa: BLE001
        return ""


_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


# --------------------------------------------------------------------------- #
# (b) Local folder (<docs_root>/<SYM>/) — investor decks etc.
# --------------------------------------------------------------------------- #
def _docs_root(override: str | None) -> str:
    if override:
        return override
    if os.environ.get("ATS_DOCS_ROOT"):
        return os.environ["ATS_DOCS_ROOT"]
    from ..config import load_pead_global

    return load_pead_global().get("docs_root", "") or ""


def _from_folder(symbol: str, docs_root: str | None) -> list[tuple[str, str]]:
    root = _docs_root(docs_root)
    if not root:
        return []
    folder = Path(root) / symbol.upper()
    if not folder.is_dir():
        return []
    out: list[tuple[str, str]] = []
    for p in sorted(folder.iterdir()):
        if _is_auto_cached(p):
            # source_cache writes into this same folder by design (one directory, one
            # reader, so a hand-dropped correction wins). But gather() must skip what we
            # fetched ourselves: this function's output is what gets cached, so reading
            # our own cache back would re-ingest it every run and grow the file without
            # bound. Hand-dropped files have no frontmatter and are still read.
            continue
        text = safe_fetch(lambda p=p: _read_doc(p), source=f"doc:{p.name}", attempts=1)
        if text and len(text) >= _MIN_DOC_CHARS:
            out.append((f"doc:{p.name}", text))
    return out


def _is_auto_cached(path: Path) -> bool:
    """True for files written by data.source_cache (frontmatter with a `source:` line)."""
    if path.suffix.lower() not in (".md", ".txt"):
        return False
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as fh:
            if fh.readline().strip() != "---":
                return False
            for _ in range(12):
                line = fh.readline()
                if not line or line.strip() == "---":
                    return False
                if line.startswith("source:") and line.split(":", 1)[1].strip():
                    return True
    except OSError:
        return False
    return False


def _read_doc(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        return "\n".join((page.extract_text() or "") for page in PdfReader(str(path)).pages)
    if suffix in (".txt", ".md", ".htm", ".html"):
        raw = path.read_text(encoding="utf-8", errors="ignore")
        return _text(raw) if suffix in (".htm", ".html") else raw
    return ""
