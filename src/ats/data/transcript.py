"""Earnings-call transcript loader — pluggable, manual-file first.

Resolution order:
  1. explicit `source` arg that is a local file path        -> read it
  2. explicit `source` arg that is an http(s) URL           -> fetch + strip tags
  3. dropped file at var/transcripts/<SYM>_<fiscal>.txt     -> read it
  4. (best-effort) a fool.com / investing.com URL passed in -> fetch

Returns (text, source_label). Empty text + "none" if nothing is available — the
actuals agent then works from reported financials alone and notes the gap.

This matches the user's habit of grabbing transcripts from fool.com /
investing.com manually: drop the text into var/transcripts/ and the loop ingests
it. Automated scraping of those sites is brittle and intentionally not relied on.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..config import REPO_ROOT


def _slug(fiscal_label: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", fiscal_label) or "latest"


def manual_path(symbol: str, fiscal_label: str) -> Path:
    return REPO_ROOT / "var" / "transcripts" / f"{symbol.upper()}_{_slug(fiscal_label)}.txt"


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _fetch_url(url: str) -> str:
    import httpx

    r = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20, follow_redirects=True)
    r.raise_for_status()
    return _strip_html(r.text)


def fetch(symbol: str, fiscal_label: str = "", source: str | None = None) -> tuple[str, str]:
    # 1/2) explicit override
    if source:
        if source.startswith("http://") or source.startswith("https://"):
            try:
                return _fetch_url(source), f"url:{source}"
            except Exception:  # noqa: BLE001 - fall through to manual
                pass
        else:
            p = Path(source)
            if p.exists():
                return p.read_text(encoding="utf-8"), f"file:{p}"

    # 3) dropped manual file
    mp = manual_path(symbol, fiscal_label)
    if mp.exists():
        return mp.read_text(encoding="utf-8"), f"file:{mp}"

    return "", "none"
