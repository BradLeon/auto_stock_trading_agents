"""The baseline an event review is measured against is the one published before the print.

Found 2026-10-26 (G0 E02): LRCX / TSM / ASML reviews all read "no basis for comparison" although each had a baseline
published on 10-19. The freeze ran at review time, read the dossier's expectation set, which the scoring phase had
already emptied, and wrote that empty record over any earlier one.
"""

from types import SimpleNamespace

from ats.agents.fundamental import event

PRINT = "2026-10-21T20:00:00+00:00"


class FakeStore:
    def __init__(self, projections=(), dossier=None):
        self.meta: dict[str, str] = {}
        self.projections = list(projections)
        self.dossier = dossier

    def get_meta(self, key, default=""):
        return self.meta.get(key, default)

    def set_meta(self, key, value):
        self.meta[key] = value

    def get_dossier(self, symbol, label):
        return self.dossier

    def task_projection_envelopes(self, **_):
        return self.projections


def _baseline(version, created_at, *, neutral="48.0%", period="Q1 FY2027"):
    return {"projection_id": f"p{version}", "created_at": created_at, "payload": {
        "period": period, "driver": f"[基线 v{version}] {period} 财报前预期基线",
        "baseline": {"synthesis": f"叙事 v{version}", "built_at": created_at, "expectation_table": [
            {"dim_key": "gross_margin", "metric": "GAAP 毛利率 %", "neutral": neutral,
             "conservative": "47%", "optimistic": "49%", "source": "x"}],
            "consensus": {"eps": 2.18, "revenue": 8.13e9, "target_price": None}}}}


def test_period_spellings_of_the_calendar_and_the_baseline_match():
    assert event._period_key("FY2027Q1") == event._period_key("Q1 FY2027") == ("2027", "1")
    assert event._period_key("") is None


def test_freeze_reads_the_baseline_published_before_the_print():
    store = FakeStore([_baseline(2, "2026-10-19T13:31:21+00:00", neutral="48.5%"),
                       _baseline(1, "2026-10-19T01:58:12+00:00")])
    frozen = event.freeze_baseline(store, symbol="LRCX", fiscal_label="FY2027Q1", cutoff=PRINT)
    assert frozen["expectations"][0]["neutral"] == "48.5%"        # newest of those published before the print
    assert frozen["consensus"]["consensus_eps"] == 2.18
    assert frozen["refs"]["baseline_projection_id"] == "p2"
    assert event.load_frozen_baseline(store, symbol="LRCX", fiscal_label="FY2027Q1") == frozen


def test_a_baseline_published_after_the_print_is_not_a_frozen_one():
    store = FakeStore([_baseline(2, "2026-10-22T01:00:00+00:00"), _baseline(1, "2026-10-19T01:58:12+00:00")])
    frozen = event.freeze_baseline(store, symbol="LRCX", fiscal_label="FY2027Q1", cutoff=PRINT)
    assert frozen["refs"]["baseline_projection_id"] == "p1"


def test_an_existing_frozen_baseline_is_never_overwritten():
    store = FakeStore([_baseline(1, "2026-10-19T01:58:12+00:00")])
    first = event.freeze_baseline(store, symbol="LRCX", fiscal_label="FY2027Q1", cutoff=PRINT)
    store.projections = [_baseline(3, "2026-10-19T20:00:00+00:00", neutral="50%")]
    assert event.freeze_baseline(store, symbol="LRCX", fiscal_label="FY2027Q1", cutoff=PRINT) == first


def test_no_baseline_and_an_empty_dossier_writes_nothing_and_keeps_the_gap_visible():
    store = FakeStore(dossier=SimpleNamespace(expectation_set=None))
    frozen = event.freeze_baseline(store, symbol="LRCX", fiscal_label="FY2027Q1", cutoff=PRINT)
    assert not frozen["expectations"] and not frozen["narrative"]
    assert event.load_frozen_baseline(store, symbol="LRCX", fiscal_label="FY2027Q1") is None   # nothing to clobber


def test_an_empty_record_left_by_the_old_code_is_replaced():
    store = FakeStore([_baseline(1, "2026-10-19T01:58:12+00:00")])
    store.set_meta("frozen_baseline:LRCX:FY2027Q1", '{"expectations": [], "narrative": ""}')
    frozen = event.freeze_baseline(store, symbol="LRCX", fiscal_label="FY2027Q1", cutoff=PRINT)
    assert frozen["expectations"]


def test_the_frozen_consensus_stands_in_when_the_dossier_has_none():
    frozen = {"expectations": [], "consensus": {"consensus_eps": 2.0, "consensus_revenue": 8.0e9}}
    actuals = SimpleNamespace(metrics=[], reported_eps=2.4, reported_revenue=8.4e9, reporting_currency="USD")
    tri = event.compute_tri_diffs(frozen_baseline=frozen, expectation_set=None, actuals=actuals,
                                  market_setup=None, symbol="LRCX")
    assert tri["vs_consensus"]["eps"]["delta_pct"] == 0.2
    assert tri["vs_consensus"]["revenue"]["consensus"] == 8.0e9


def test_a_dossier_expectation_set_built_after_the_print_is_not_a_baseline():
    """TSM / ASML printed on 10-14/15, before the baseline mechanism existed; their only expectation set was built
    on 10-19. Freezing it would present a post-print guess as a pre-print one."""
    from datetime import datetime, timezone

    from ats.schemas.pead import Expectation, ExpectationSet

    built = datetime(2026, 10, 19, 13, 0, tzinfo=timezone.utc)
    es = ExpectationSet(symbol="TSM", as_of=built, narrative="事后", expectations=[
        Expectation(dim_key="revenue", metric="r", neutral="1", conservative="0", optimistic="2")])
    store = FakeStore(dossier=SimpleNamespace(expectation_set=es))
    late = event.freeze_baseline(store, symbol="TSM", fiscal_label="Q3 FY2026", cutoff="2026-10-15T06:00:00+00:00")
    assert not late["expectations"] and event.load_frozen_baseline(store, symbol="TSM", fiscal_label="Q3 FY2026") is None
    ok = event.freeze_baseline(store, symbol="TSM", fiscal_label="Q3 FY2026", cutoff="2026-10-20T00:00:00+00:00")
    assert ok["expectations"]
