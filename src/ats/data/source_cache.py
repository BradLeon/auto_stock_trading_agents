"""Local cache of primary source documents (filings, transcripts, releases).

Everything the evidence ledger rests on is a primary document, and until now each run
re-fetched them. That cost money three times over on 2026-08-06 alone, but the real
damage was that **re-fetching is not idempotent**: the same query returned Sherwin-
Williams for SKHY on one run and the correct call on the next, so results measured
before and after a prompt change were not comparable. A cache makes the corpus fixed,
which is what lets the downstream judgement work be iterated on at all.

Layout — under `docs_root` (already `信息源/` in the Obsidian vault, and already read by
`documents.gather` as the manual tier):

    信息源/<SYMBOL>/<SYMBOL>-<FY26Q2>-transcript.md

with YAML frontmatter carrying provenance. Two consequences that are deliberate:

  * Manual and fetched documents share one directory and one reader. Drop a correct
    file in by hand and it wins from then on — the existing "手工纪要优先" tier is not a
    separate mechanism any more, just an entry that was written by a person.
  * You can open, search and link them in Obsidian. Text stays in files; SQLite gets
    metadata only (see memory.store.save_document), because duplicating 5MB of prose
    into a database buys nothing that a path does not.

The identity and period guards run HERE, before writing. A document that fails is never
cached — the wrong fetch is paid for once, recorded as a failure with its URL, and not
repeated.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("ats.data.source_cache")

MIN_CHARS = 800          # below this it is a stub/paywall page, not a document


@dataclass
class CachedDoc:
    symbol: str              # canonical entity id
    period: str              # fiscal label, e.g. FY26Q2 ("" when unresolved)
    doc_type: str            # transcript | release | filing | deck | manual
    text: str
    path: Path
    source: str = ""         # fetcher that produced it (fmp / tavily / sec / manual)
    source_url: str = ""
    fetched_at: str = ""
    sha256: str = ""
    from_cache: bool = False
    # Exact immutable bytes for lineage. `path` remains the human-friendly latest
    # location so all existing readers keep working.
    version_path: Path | None = None
    external_id: str = ""
    title: str = ""
    published_at: str = ""

    @property
    def document_id(self) -> str:
        return f"{self.symbol}:{self.period or 'unknown'}:{self.doc_type}"


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
def root() -> Path | None:
    """`docs_root` from config/pead.yaml (ATS_DOCS_ROOT overrides). None when unset."""
    import os

    from ..config import load_pead_global

    raw = os.environ.get("ATS_DOCS_ROOT") or load_pead_global().get("docs_root", "")
    return Path(raw) if raw else None


def _slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", (value or "").strip()).strip("-")


def path_for(symbol: str, period: str, doc_type: str) -> Path | None:
    base = root()
    if base is None:
        return None
    sym = _slug(symbol.upper())
    return base / sym / f"{sym}-{_slug(period) or 'unknown'}-{_slug(doc_type)}.md"


def _version_path(path: Path, digest: str) -> Path:
    """Hidden immutable sibling; keeps the visible Obsidian folder uncluttered."""
    return path.parent / ".versions" / f"{path.stem}.{digest[:16]}{path.suffix}"


# --------------------------------------------------------------------------- #
# Read
# --------------------------------------------------------------------------- #
def load(symbol: str, period: str, doc_type: str, *, min_chars: int = MIN_CHARS) -> CachedDoc | None:
    """Return the cached document, or None. Never touches the network."""
    from ..config import canonical_entity

    sym = canonical_entity(symbol).upper()
    p = path_for(sym, period, doc_type)
    if p is None or not p.is_file():
        return None
    try:
        raw = p.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        log.warning("source_cache: unreadable %s (%s)", p, exc)
        return None
    meta, body = _split_frontmatter(raw)
    if len(body.strip()) < min_chars:
        return None
    digest = meta.get("sha256", "") or hashlib.sha256(body.encode("utf-8")).hexdigest()
    version_path = _version_path(p, digest)
    return CachedDoc(symbol=sym, period=meta.get("period", period),
                     doc_type=meta.get("doc_type", doc_type), text=body,
                     path=p, source=meta.get("source", "manual"),
                     source_url=meta.get("source_url", ""),
                     fetched_at=meta.get("fetched_at", ""),
                     sha256=digest, from_cache=True,
                     version_path=version_path if version_path.is_file() else p)


def _split_frontmatter(raw: str) -> tuple[dict, str]:
    """Parse `---\\n...\\n---\\n` frontmatter. Files without it are hand-dropped text."""
    if not raw.startswith("---"):
        return {}, raw
    parts = raw.split("\n---", 2)
    if len(parts) < 2:
        return {}, raw
    import yaml

    try:
        meta = yaml.safe_load(parts[0].lstrip("-").strip()) or {}
    except Exception:  # noqa: BLE001 - a corrupt header must not lose the body
        meta = {}
    if not isinstance(meta, dict):
        return {}, parts[1].lstrip("\n")
    # yaml turns an ISO timestamp into a datetime and a bare FY26 into an int; every
    # consumer here treats these as strings (sorting, formatting), and a mixed-type
    # column raises only for the user whose vault happens to hold both kinds of file.
    return ({k: ("" if v is None else str(v)) for k, v in meta.items()},
            parts[1].lstrip("\n"))


# --------------------------------------------------------------------------- #
# Write
# --------------------------------------------------------------------------- #
def store(symbol: str, period: str, doc_type: str, text: str, *, source: str = "",
          source_url: str = "", now: datetime | None = None,
          external_id: str = "", title: str = "",
          published_at: str = "", min_chars: int = MIN_CHARS) -> CachedDoc | None:
    """Write a document to the cache. Callers must have run the guards first."""
    from ..config import canonical_entity

    sym = canonical_entity(symbol).upper()
    body = (text or "").strip()
    if len(body) < min_chars:
        log.info("source_cache: refusing to cache %s %s — only %d chars",
                 sym, doc_type, len(body))
        return None
    p = path_for(sym, period, doc_type)
    if p is None:
        return None
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    header = "\n".join([
        "---",
        f"symbol: {sym}",
        f"period: {period or ''}",
        f"doc_type: {doc_type}",
        f"source: {source}",
        f"source_url: {source_url}",
        f"fetched_at: {stamp}",
        f"sha256: {digest}",
        f"chars: {len(body)}",
        "---",
        "",
    ])
    rendered = header + body
    p.parent.mkdir(parents=True, exist_ok=True)
    version_path = _version_path(p, digest)
    version_path.parent.mkdir(parents=True, exist_ok=True)
    if not version_path.exists():
        version_path.write_text(rendered, encoding="utf-8")
    # The visible path is the latest accepted version for humans and legacy readers.
    p.write_text(rendered, encoding="utf-8")
    log.info("source_cache: stored %s (%d chars) -> %s", sym, len(body), p)
    return CachedDoc(symbol=sym, period=period, doc_type=doc_type, text=body, path=p,
                     source=source, source_url=source_url, fetched_at=stamp,
                     sha256=digest, from_cache=False, version_path=version_path,
                     external_id=external_id, title=title, published_at=published_at)


# --------------------------------------------------------------------------- #
# Inventory (what do we hold, for whom)
# --------------------------------------------------------------------------- #
def inventory(symbol: str) -> list[CachedDoc]:
    """Every cached document for one entity, including hand-dropped files."""
    from ..config import canonical_entity

    base = root()
    if base is None:
        return []
    folder = base / canonical_entity(symbol).upper()
    if not folder.is_dir():
        return []
    out: list[CachedDoc] = []
    for p in sorted(folder.iterdir()):
        if p.suffix.lower() not in (".md", ".txt", ".pdf", ".htm", ".html"):
            continue
        try:
            if p.suffix.lower() in (".md", ".txt"):
                meta, body = _split_frontmatter(p.read_text(encoding="utf-8", errors="ignore"))
            else:
                # Hand-dropped PDFs/HTML are the ONLY source for names the dataset does
                # not carry (Samsung, Nanya). Skipping them here would report a witness
                # as "no document" while `documents.gather` was happily reading it —
                # a coverage view that disagrees with the fetch path is worse than none.
                from . import documents

                meta, body = {"doc_type": "manual"}, documents._read_doc(p)
        except Exception:  # noqa: BLE001 - one unreadable file must not hide the rest
            continue
        if len(body.strip()) < MIN_CHARS:
            continue
        digest = meta.get("sha256", "") or hashlib.sha256(body.encode("utf-8")).hexdigest()
        version_path = _version_path(p, digest)
        out.append(CachedDoc(symbol=canonical_entity(symbol).upper(),
                             period=meta.get("period", ""),
                             doc_type=meta.get("doc_type", "manual"), text=body, path=p,
                             source=meta.get("source", "manual"),
                             source_url=meta.get("source_url", ""),
                             fetched_at=meta.get("fetched_at", ""),
                             sha256=digest, from_cache=True,
                             version_path=version_path if version_path.is_file() else p))
    return out
