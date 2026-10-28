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

import json
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


def label_for_reads(symbol: str, *, store=None, config_label: str = "") -> str:
    """The fiscal label to READ a symbol's current dossier under (no network, no derivation).

    Hand-written labels in `config/pead/<SYM>.yaml` go stale every quarter (ASML/TSM still said Q2 after the Q3
    prints), and readers that took the configured label — trade sizing, the daily digest, the journal — then looked
    up the wrong dossier. The dossier a symbol was last prepared or scored under IS its current label, because the
    PEAD paths write under `resolve_fiscal_label`, which rolls with the earnings calendar. The configured label is
    only the fallback for a symbol that has no dossier yet.
    """
    if store is None:
        from ..memory import get_store

        store = get_store()
    try:
        recent = store.recent_dossiers(symbol.upper(), limit=1)
    except Exception as exc:  # noqa: BLE001 - a lookup failure must degrade to the old behaviour, not break a read
        log.warning("label_for_reads(%s): dossier lookup failed (%s)", symbol, exc)
        recent = []
    return (recent[0].get("fiscal_label") if recent else "") or config_label



def dossier_label(symbol: str, label: str, *, store=None) -> str:
    """The one spelling a quarter's dossier is stored under, whatever spelling the caller holds.

    The earnings calendar names a quarter `FY2027Q1`; the PEAD prep path stores its dossier as `Q1 FY2027`. Two
    spellings made two dossiers for one quarter: the event review started from an empty one and the prep baseline sat
    unused in the other (found 2026-10-26, G0 E02). The canonical spelling is `label_from` (`Q1 FY2027`), unless a
    dossier already exists under another spelling of the same quarter and none under the canonical one.
    """
    year, quarter = fiscal.parse_label(label)
    if not (year and quarter):
        return label
    canonical = label_from(quarter, year)
    if store is None:
        from ..memory import get_store

        store = get_store()
    try:
        spellings = [meta.get("fiscal_label") or "" for meta in store.recent_dossiers(symbol.upper(), limit=50)
                     if fiscal.parse_label(meta.get("fiscal_label") or "") == (year, quarter)]
    except Exception as exc:  # noqa: BLE001 - a lookup failure must not stop a run
        log.warning("dossier_label(%s): dossier lookup failed (%s)", symbol, exc)
        return canonical
    return canonical if canonical in spellings or not spellings else spellings[0]


def merge_dossier_aliases(store, symbol: str) -> list[tuple[str, str]]:
    """Fold a quarter's alternative-spelling rows into its canonical one; returns [(stray, canonical)].

    Dossiers: the canonical row wins when it exists (the stray one was started from nothing), else the stray is
    renamed. Score-run ledger rows move over under fresh version numbers so history is kept, not overwritten.
    """
    sym = symbol.upper()
    moved: list[tuple[str, str]] = []
    labels = [row["fiscal_label"] for row in store.recent_dossiers(sym, limit=200)]
    for stray in labels:
        year, quarter = fiscal.parse_label(stray)
        canonical = label_from(quarter, year)
        if not canonical or stray == canonical:
            continue
        with store.conn:
            has = store.conn.execute("SELECT 1 FROM pead_dossier WHERE symbol=? AND fiscal_label=?",
                                     (sym, canonical)).fetchone()
            if has:
                store.conn.execute("DELETE FROM pead_dossier WHERE symbol=? AND fiscal_label=?", (sym, stray))
            else:
                row = store.conn.execute("SELECT payload FROM pead_dossier WHERE symbol=? AND fiscal_label=?",
                                         (sym, stray)).fetchone()
                payload = json.loads(row[0])
                payload["fiscal_label"] = canonical
                store.conn.execute("UPDATE pead_dossier SET fiscal_label=?, payload=? "
                                   "WHERE symbol=? AND fiscal_label=?",
                                   (canonical, json.dumps(payload, ensure_ascii=False), sym, stray))
            top = store.conn.execute("SELECT COALESCE(MAX(version),0) FROM pead_score_runs "
                                     "WHERE symbol=? AND fiscal_label=?", (sym, canonical)).fetchone()[0]
            rows = store.conn.execute("SELECT version FROM pead_score_runs WHERE symbol=? AND fiscal_label=? "
                                      "ORDER BY version", (sym, stray)).fetchall()
            for offset, row in enumerate(rows, start=1):
                store.conn.execute("UPDATE pead_score_runs SET fiscal_label=?, version=? "
                                   "WHERE symbol=? AND fiscal_label=? AND version=?",
                                   (canonical, top + offset, sym, stray, row[0]))
        moved.append((stray, canonical))
    return moved
