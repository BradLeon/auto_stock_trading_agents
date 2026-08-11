"""Third-party evidence sources — series to observations (hermetic, no network).

The gates must treat a customs bureau exactly like a company: same table, same dedup,
same adjudication. What differs is only that a source declares its own stance, because
no claim's witness table will ever name it.
"""

from datetime import datetime, timezone

from ats.chain import sources
from ats.memory import get_store
from ats.schemas.chain import SeriesPoint, SourceDef

NOW = datetime(2026, 8, 7, tzinfo=timezone.utc)


def _source(**kw):
    base = dict(id="tw_ic_exports", label="台湾 IC 出口", adapter="tw_mof",
                entity="TW_IC_EXPORT", stance="regulator",
                observation_type="regulatory", cadence="monthly",
                concepts=["supply_tightness", "hbm_demand"],
                direction_from=["yoy", "mom"])
    return SourceDef(**{**base, **kw})


def _points():
    return [SeriesPoint(period="2026-05", value=26830.6, unit="百万美元",
                        yoy=0.559, mom=0.179),
            SeriesPoint(period="2026-06", value=25385.0, unit="百万美元",
                        yoy=0.328, mom=-0.054)]


def test_trend_and_turning_point_are_separate_observations():
    """A series can rise year-on-year while turning down month-on-month, and that
    divergence is the leading signal. Blending them into one number erases it —
    measured on the real Taiwan print: 2026-06 was +32.8% yoy and -5.4% mom."""
    obs = sources.to_observations(_source(), _points(), now=NOW)
    june = {o.metric: o for o in obs if o.period == "2026-06"}
    assert june["tw_ic_exports_yoy"].direction == "up"
    assert june["tw_ic_exports_mom"].direction == "down"


def test_small_moves_are_flat_not_a_reading():
    """±1% on volatile monthly trade data is a rounding artefact. Emitting `up` for it
    would hand the adjudicator a fact that is really noise."""
    pts = [SeriesPoint(period="2026-06", value=100.0, yoy=0.005, mom=-0.01)]
    obs = sources.to_observations(_source(), pts, now=NOW)
    assert {o.direction for o in obs} == {"flat"}


def test_every_observation_is_recheckable():
    """The evidence_span rule does not relax because the number came from a
    spreadsheet: it must name the period, the value, the basis and the agency."""
    obs = sources.to_observations(_source(), _points(), now=NOW)
    span = next(o.evidence_span for o in obs if o.metric.endswith("_yoy"))
    assert "2026-05" in span and "同比" in span and "tw_mof" in span
    assert "26,830" in span


def test_stance_and_type_come_from_the_declaration():
    """Declared, never inferred — the same invariant as a company's witness table,
    which is what lets a source contribute the scarce `regulator` stance class."""
    obs = sources.to_observations(_source(), _points(), now=NOW)
    assert {o.stance for o in obs} == {"regulator"}
    assert {o.observation_type for o in obs} == {"regulatory"}
    assert {o.entity for o in obs} == {"TW_IC_EXPORT"}


def test_one_print_files_under_every_declared_concept(monkeypatch):
    """Filed once under a blank concept it would sit in the unmapped pool where no
    claim can reach it. The same customs print is evidence on both dimensions."""
    monkeypatch.setattr(sources, "load_sources", lambda: [_source()])
    monkeypatch.setattr(sources, "fetch", lambda *a, **k: _points())
    store = get_store()
    saved = sources.collect(store, now=NOW)
    assert saved["tw_ic_exports"] == 8            # 2 periods x 2 bases x 2 concepts
    got = {r["concept"] for r in store.observations(entity="TW_IC_EXPORT", limit=50)}
    assert got == {"supply_tightness", "hbm_demand"}


def test_an_unreachable_source_is_a_gap_not_silence(monkeypatch):
    """Korea's series needs a key we do not have. "We have no Korean data" and "Korean
    exports say nothing" are different claims about the world."""
    monkeypatch.setattr(sources, "load_sources", lambda: [_source(id="kr", adapter="kr_customs")])
    monkeypatch.setattr(sources, "fetch", lambda *a, **k: [])
    store = get_store()
    assert sources.collect(store, now=NOW) == {"kr": 0}
    assert store.observations(entity="TW_IC_EXPORT") == []
    assert [d for d in store.documents(entity="TW_IC_EXPORT", ok_only=False) if not d["ok"]]


def test_a_series_does_not_inflate_the_cluster_count():
    """Six months pointing the same way is one body of evidence, not six votes —
    otherwise a monthly source would outweigh every company simply by publishing often."""
    from ats.chain.corroborate import build_clusters
    from ats.schemas.chain import ClaimDef, Concept

    claim = ClaimDef(id="c", kind="common", layer="L5_fab",
                     concepts=[Concept(key="supply_tightness", desc="紧张")])
    pts = [SeriesPoint(period=f"2026-0{m}", value=100.0 + m, yoy=0.30) for m in range(1, 7)]
    rows = [o.model_dump() | {"concept": "supply_tightness"}
            for o in sources.to_observations(_source(), pts, now=NOW)]
    clusters = build_clusters(claim, rows)
    assert len(clusters) == 1
    assert len(clusters[0].rows) == 6            # members kept, one voice


def test_collect_is_wired_into_the_weekly_job(monkeypatch):
    """A configured source that nobody ever fetches is worse than none — it looks
    live in the config and is frozen in the ledger. `collect()` had no production
    caller at all: the only invocation in the repo was this test file, so the three
    declared series only ever refreshed when someone ran it from a REPL.

    The weekly job must refresh BEFORE rendering, or the report shows last month's
    customs print beside this week's filings.
    """
    import inspect

    from ats.runtime import scheduler

    src = inspect.getsource(scheduler._cross_section_weekly)
    assert "sources.collect" in src or "chain_sources.collect" in src
    # ...and it must come before the report is written, not after.
    assert src.index("collect(") < src.index("chain_report.write")


def test_a_source_outage_does_not_break_the_weekly_job():
    """An agency being down is a recorded gap, never an exception that costs the whole
    sector job its report — the same rule the adapters follow one level down."""
    import inspect

    from ats.runtime import scheduler

    src = inspect.getsource(scheduler._cross_section_weekly)
    after_call = src[src.index("chain_sources.collect("):]
    assert "except Exception" in after_call.split("chain_report")[0]
