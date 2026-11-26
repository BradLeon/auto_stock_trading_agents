"""Third-party evidence sources — series to observations (hermetic, no network).

The gates must treat a customs bureau exactly like a company: same table, same dedup,
same adjudication. What differs is only that a source declares its own stance, because
no claim's witness table will ever name it.
"""

from datetime import datetime, timedelta, timezone

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
    exports say nothing" are different claims about the world.

    A true failure is `-1`, never `0` — see the next test for why the distinction is
    load-bearing, not cosmetic.
    """
    monkeypatch.setattr(sources, "load_sources", lambda: [_source(id="kr", adapter="kr_customs")])
    monkeypatch.setattr(sources, "fetch", lambda *a, **k: [])
    store = get_store()
    assert sources.collect(store, now=NOW) == {"kr": -1}
    assert store.observations(entity="TW_IC_EXPORT") == []
    assert [d for d in store.documents(entity="TW_IC_EXPORT", ok_only=False) if not d["ok"]]


def test_current_data_is_not_the_same_zero_as_unreachable(monkeypatch):
    """A monthly source fetched twice inside one month returns the same print both
    times — every point is already in the ledger, so zero rows are NEW. That is a
    healthy, live source, not a dead one, and must not collapse to the same `0` a
    genuine outage reports.

    This is not hypothetical: TrendForce's contract-price page returned "2H Jun" on two
    consecutive `collect()` runs, and a CLI surface that treated any `0` as failure
    printed "取不到数据" over a source that had just been fetched successfully.
    """
    monkeypatch.setattr(sources, "load_sources", lambda: [_source()])
    monkeypatch.setattr(sources, "fetch", lambda *a, **k: _points())
    store = get_store()
    sources.collect(store, now=NOW)                        # first run: all new
    second = sources.collect(store, now=NOW)                # second run: identical print
    assert second["tw_ic_exports"] == 0                     # reached, nothing NEW —
    assert second["tw_ic_exports"] != -1                    # — genuinely not a gap


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


# --- the version a fact cites: one document per print, one line per basis --------------------------------------------

JUNE = "tw_ic_exports:2026-06"


def _collect(monkeypatch, day: int, points=None) -> int:
    monkeypatch.setattr(sources, "load_sources", lambda: [_source()])
    monkeypatch.setattr(sources, "fetch", lambda *a, **k: points or _points())
    return sources.collect(get_store(), now=NOW + timedelta(days=day))["tw_ic_exports"]


def _revised_june() -> list[SeriesPoint]:
    may, june = _points()
    return [may, june.model_copy(update={"value": 25500.0, "yoy": 0.334, "mom": -0.050})]


def _body(version_id: str) -> str:
    return get_store().data_store().conn.execute(
        "SELECT text FROM data_document_chunks WHERE version_id=? AND ordinal=0", (version_id,)).fetchone()[0]


def _miscited() -> list[str]:
    """Live facts whose cited version does not hold their own evidence line."""
    return sorted(f"{fact['period']} {fact['metric']}" for fact in get_store().facts(limit=100)
                  if fact["evidence_span"] not in _body(fact["document_version_id"]).split("\n"))


def _sizes() -> dict[str, int]:
    conn = get_store().data_store().conn
    return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in
            ("data_document_versions", "data_document_publications", "data_evidence_facts",
             "data_evidence_projections", "data_evidence_observations")}


def test_both_bases_of_a_print_share_one_body_with_a_line_each(monkeypatch):
    _collect(monkeypatch, 1)
    (version,) = get_store().document_versions(JUNE)
    assert _body(version["version_id"]) == (
        "2026-06 台湾 IC 出口 25,385.00 百万美元，同比 +32.8%（tw_mof 2026-06）\n"
        "2026-06 台湾 IC 出口 25,385.00 百万美元，环比 -5.4%（tw_mof 2026-06）\n"
        "\n"
        "来源：台湾 IC 出口（tw_mof）；口径期：2026-06；数值：25385 百万美元")
    assert {f["document_version_id"] for f in get_store().facts(document_id=JUNE)} == {version["version_id"]}


def test_a_single_reading_body_keeps_its_format():
    """One-line documents (DRAM, governed observers) must hash as before, or every print gains a spurious version."""
    source = _source(direction_from=["yoy"])
    (obs,) = sources.to_observations(source, [SeriesPoint(period="2026-06", value=100.0, yoy=0.3)], now=NOW)
    assert sources._reading_text(source, [obs]) == (
        f"{obs.evidence_span}\n\n来源：台湾 IC 出口（tw_mof）；口径期：2026-06；数值：100")


def test_repeat_collection_adds_nothing_and_every_fact_still_cites_its_own_line(monkeypatch):
    _collect(monkeypatch, 1)
    assert _miscited() == []
    first = _sizes()
    assert first["data_document_versions"] == first["data_document_publications"] == 2    # one per print

    for day in (2, 3):
        assert _collect(monkeypatch, day) == 0
        assert _miscited() == []
        assert _sizes() == first


def test_a_revised_print_adds_one_version_that_both_bases_cite(monkeypatch):
    _collect(monkeypatch, 1)
    before = _sizes()

    _collect(monkeypatch, 2, _revised_june())
    _collect(monkeypatch, 3, _revised_june())

    assert _sizes()["data_document_versions"] == before["data_document_versions"] + 1
    assert _sizes()["data_evidence_facts"] == before["data_evidence_facts"] + 2
    assert _miscited() == []
    latest = get_store().latest_document_version(JUNE)["version_id"]
    assert {f["document_version_id"] for f in get_store().facts(document_id=JUNE)} == {latest}
    retired = [f for f in get_store().facts(document_id=JUNE, include_superseded=True) if f["superseded_at"]]
    assert len(retired) == 2 and all(f["evidence_span"] in _body(f["document_version_id"]) for f in retired)


def test_a_print_that_returns_to_an_earlier_value_cites_its_own_lines(monkeypatch):
    _collect(monkeypatch, 1)
    _collect(monkeypatch, 2, _revised_june())
    replaced = get_store().latest_document_version(JUNE)["version_id"]

    _collect(monkeypatch, 3)
    assert _miscited() == []
    assert f"取代版本 {replaced}" in _body(get_store().latest_document_version(JUNE)["version_id"])
    restored = _sizes()

    _collect(monkeypatch, 4)
    assert _sizes() == restored and _miscited() == []


def test_facts_left_citing_the_other_basis_are_replaced_without_rewriting_history(monkeypatch):
    """The state production was left in: one body per basis under one id, so a second run re-cited yoy to the mom body."""
    from ats.agents.evidence.adjudicator import build_context
    from ats.chain.corroborate import build_clusters
    from ats.schemas.chain import ClaimDef, Concept, Observation

    source, store = _source(), get_store()
    for day in (1, 2):
        for obs in sources.to_observations(source, _points(), now=NOW + timedelta(days=day)):
            sources._publish_reading(store, source, obs, now=NOW + timedelta(days=day),
                                     text=sources._reading_text(source, [obs]))
            for concept in source.concepts:
                key = Observation.deterministic_id(obs.document_id, obs.entity, f"{obs.metric}:{concept}", obs.period)
                store.save_observation(obs.model_copy(update={"concept": concept, "id": key}),
                                       projection_profile="structured_evidence", projection_version="v1")
    assert _miscited() == ["2026-05 tw_ic_exports_yoy", "2026-06 tw_ic_exports_yoy"]
    claim = ClaimDef(id="c", kind="common", layer="L5_fab",
                     concepts=[Concept(key="supply_tightness", desc="紧张")])

    def judged_text() -> str:
        return build_context(claim, build_clusters(claim, store.observations(entity="TW_IC_EXPORT", limit=50)))

    prompt = judged_text()
    observations = _sizes()["data_evidence_observations"]
    old = {f["fact_id"]: f["document_version_id"] for f in store.facts(limit=100, include_superseded=True)}

    assert _collect(monkeypatch, 3) == 0                             # same observation ids: nothing counted twice

    assert _miscited() == []
    assert _sizes()["data_evidence_observations"] == observations
    assert judged_text() == prompt                                   # the adjudicator is asked exactly what it was
    now_held = {f["fact_id"]: f["document_version_id"] for f in store.facts(limit=100, include_superseded=True)}
    assert old.items() <= now_held.items()                           # earlier vintages kept, citations untouched
    healed = _sizes()
    _collect(monkeypatch, 4)
    assert _sizes() == healed
