"""Single source of truth for "which fiscal quarter are we on".

`fiscal_label` is the PK component of `pead_dossier` and the key every PEAD document
is named after, but it used to be hand-maintained per ticker in
`config/pead/<SYM>.yaml`. That rots: as of 2026-07 CRDO/AXT/VRT read `"TODO"` and
COHR/LITE/SKHY read `"Q FY2026"` (no quarter number), and both forms make
`fiscal.parse_label` return None — which silently disables the transcript
period guard and made 7 of 13 targets unusable for quarter-targeted retrieval.

The earnings calendar already reports `quarter` / `year` per print, so the label can
be derived. The resolution order below is deliberately conservative: it prefers a
label that ALREADY has a dossier stored under it, so turning this on cannot orphan
existing history. Only when nothing usable exists does it mint a derived label.
"""

from __future__ import annotations

import logging

from . import fiscal

log = logging.getLogger("ats.data")


def label_from(quarter: int | None, year: int | None) -> str:
    """(2, 2026) -> 'Q2 FY2026'. Empty when the quarter is unknown."""
    if not (quarter and year):
        return ""
    return f"Q{quarter} FY{year}"


def resolve_fiscal_label(symbol: str, print_=None, *, config_label: str = "",
                         store=None) -> tuple[str, str]:
    """Return (fiscal_label, source) for `symbol`'s current quarter.

    Order:
      1. the label already cached for THIS print (stable across reads)
      2. an existing dossier label whose (year, quarter) matches the print
         — migration safety: never orphan history stored under a hand-written label
      3. the config label, if it parses AND matches the print
      4. a label derived from the print's quarter/year
      5. the config label verbatim (legacy fallback, may be "TODO")
    """
    sym = symbol.upper()
    if store is None:
        from ..memory import get_store

        store = get_store()

    target = (print_.year, print_.quarter) if print_ else (None, None)

    # 1) cached decision for this exact print
    if print_ is not None:
        cached = store.get_period(sym, print_.date)
        if cached and cached.get("fiscal_label"):
            return cached["fiscal_label"], f"cache:{cached.get('label_source', '?')}"

    # 2) an existing dossier for the same quarter — keep its exact spelling
    if all(target):
        try:
            for meta in store.recent_dossiers(sym, limit=8):
                lbl = meta.get("fiscal_label") or ""
                if fiscal.parse_label(lbl) == target:
                    return lbl, "dossier"
        except Exception as exc:  # noqa: BLE001 - resolution must not break a run
            log.warning("dossier label lookup failed for %s: %s", sym, exc)

    # 3) config label, only if it actually names this quarter
    if config_label and all(target) and fiscal.parse_label(config_label) == target:
        return config_label, "config"

    # 4) derive from the calendar
    derived = label_from(print_.quarter if print_ else None, print_.year if print_ else None)
    if derived:
        return derived, "derived"

    # 5) nothing better than what config says (may be "TODO" / "Q FY2026")
    return config_label, "config-fallback"


def resolve_and_cache(symbol: str, print_, *, config_label: str = "", store=None) -> str:
    """resolve_fiscal_label + persist the choice, so it cannot drift later."""
    if store is None:
        from ..memory import get_store

        store = get_store()
    label, source = resolve_fiscal_label(symbol, print_, config_label=config_label, store=store)
    if print_ is not None and label:
        try:
            store.upsert_period(print_, label, source)
        except Exception as exc:  # noqa: BLE001 - caching is best-effort
            log.warning("period cache write failed for %s: %s", symbol, exc)
    return label
