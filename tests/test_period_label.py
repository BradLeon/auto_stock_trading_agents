"""fiscal_label resolution — derive from the calendar without orphaning history.

`fiscal_label` is part of `pead_dossier`'s primary key, so changing how it is spelled
would silently strand every stored dossier. The resolution order therefore prefers a
label that already HAS a dossier over a freshly derived one; these tests pin that
down, plus the actual rot it is meant to fix (`TODO`, `Q FY2026`, empty).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from ats.data.earnings_calendar import EarningsPrint
from ats.data.period import label_from, resolve_and_cache, resolve_fiscal_label
from ats.memory import get_store
from ats.schemas.pead import PeadDossier


def _print(q=2, y=2026, d=date(2026, 7, 22), eps_actual=9.11) -> EarningsPrint:
    return EarningsPrint(symbol="GOOG", date=d, session="amc", session_source="yf-clock",
                         quarter=q, year=y, eps_actual=eps_actual)


def _seed_dossier(symbol: str, label: str, phase: str = "prep") -> None:
    get_store().save_dossier(PeadDossier(symbol=symbol, fiscal_label=label, phase=phase,
                                         updated_at=datetime.now(timezone.utc)))


def test_label_from():
    assert label_from(2, 2026) == "Q2 FY2026"
    assert label_from(None, 2026) == ""
    assert label_from(2, None) == ""


def test_existing_dossier_spelling_wins_over_derived():
    """GOOG's history is under 'Q2 2026'; deriving would give 'Q2 FY2026'.

    Without this rule, turning derivation on would orphan every stored dossier.
    """
    _seed_dossier("GOOG", "Q2 2026", phase="score")
    label, source = resolve_fiscal_label("GOOG", _print(), config_label="Q2 2026")
    assert (label, source) == ("Q2 2026", "dossier")
    assert label != label_from(2, 2026)          # the rule genuinely diverges


def test_derives_when_config_says_TODO():
    """CRDO/AXT/VRT ship 'TODO', which parses to no quarter and disables the guard."""
    label, source = resolve_fiscal_label("VRT", _print(q=2, y=2026), config_label="TODO")
    assert (label, source) == ("Q2 FY2026", "derived")


def test_derives_when_config_has_no_quarter_number():
    """COHR/LITE/SKHY ship 'Q FY2026' — a year but no quarter."""
    label, source = resolve_fiscal_label("SKHY", _print(q=3, y=2026), config_label="Q FY2026")
    assert (label, source) == ("Q3 FY2026", "derived")


def test_derives_when_config_is_empty():
    label, source = resolve_fiscal_label("AVGO", _print(q=3, y=2026), config_label="")
    assert (label, source) == ("Q3 FY2026", "derived")


def test_config_label_kept_when_it_matches_the_print():
    """A correct hand-written label with no dossier yet is honoured as-is."""
    label, source = resolve_fiscal_label("KLAC", _print(q=4, y=2026), config_label="Q4 FY2026")
    assert (label, source) == ("Q4 FY2026", "config")


def test_stale_config_label_loses_to_the_calendar():
    """Config still says last quarter -> the calendar wins, so the guard stays valid."""
    label, source = resolve_fiscal_label("GOOG", _print(q=3, y=2026), config_label="Q2 2026")
    assert (label, source) == ("Q3 FY2026", "derived")


def test_no_print_falls_back_to_config_verbatim():
    """Off-season: nothing to derive from, so don't invent a quarter."""
    label, source = resolve_fiscal_label("CRDO", None, config_label="TODO")
    assert (label, source) == ("TODO", "config-fallback")


def test_cache_pins_the_label_against_upstream_revision():
    """The calendars revise quarter/year; a label that moved after the score was
    written would orphan the dossier, so the first decision is cached and reused."""
    store = get_store()
    p = _print(q=2, y=2026)
    first = resolve_and_cache("GOOG", p, config_label="", store=store)
    assert first == "Q2 FY2026"

    # Upstream now claims a different quarter for the SAME print date.
    revised = EarningsPrint(symbol="GOOG", date=p.date, quarter=3, year=2026)
    again, source = resolve_fiscal_label("GOOG", revised, config_label="", store=store)
    assert again == "Q2 FY2026"
    assert source.startswith("cache:")


def test_cache_is_per_print_date():
    store = get_store()
    resolve_and_cache("GOOG", _print(q=2, y=2026, d=date(2026, 7, 22)), store=store)
    label, source = resolve_fiscal_label(
        "GOOG", _print(q=3, y=2026, d=date(2026, 10, 27)), store=store)
    assert label == "Q3 FY2026"
    assert not source.startswith("cache:")
