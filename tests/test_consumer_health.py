"""Consumer-level data-product health checks (Phase F simplification, 2026-10-15).

The products and the projection store are explicit fakes: these tests prove what the
check treats as a failure and a warning, and that it never goes through a consumer's
read gate (the gate is exactly what the switch changes).
"""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ats.workflow import consumer_health as health

NOW = datetime(2026, 10, 15, 3, 0, tzinfo=UTC)
UNIVERSE = ["MRVL", "MSFT"]


def doc(entity, **overrides):
    values = dict(document_id=f"d-{entity}", version_id="v1", content_hash="h", source="ibkr_news")
    return SimpleNamespace(**{**values, **overrides})


class Structured:
    def __init__(self, lineage=True):
        self.closed, self._lineage = False, lineage

    def lineage(self, observation_id):
        return {"artifact": "a", "source": "s", "metric": "m"} if self._lineage else None

    def close(self):
        self.closed = True


class Products:
    def __init__(self, financial=None, lineage=True):
        self.unstructured = object()
        self.structured = Structured(lineage)
        self._financial = financial if financial is not None else {
            e: [{"observation_id": f"{e}-1"}] for e in UNIVERSE}

    def company_financial_snapshot(self, *, entity, as_of):
        value = self._financial[entity]
        if isinstance(value, Exception):
            raise value
        return {"rows": value}


@pytest.fixture
def world(monkeypatch):
    state = SimpleNamespace(products=Products(), documents={e: [doc(e)] for e in UNIVERSE}, briefs={})
    monkeypatch.setattr(health, "_universe", lambda: list(UNIVERSE))
    monkeypatch.setattr("ats.data.products.get_platform_data_products", lambda readonly=False: state.products)

    def admitted(*, entities, repository, **kwargs):
        value = state.documents[entities[0]]
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr("ats.data.products.unstructured.admitted_documents", admitted)
    monkeypatch.setattr(health, "_latest_brief", lambda entity: (state.briefs.get(entity) or [None])[0])
    monkeypatch.setattr("ats.memory.get_store", lambda: pytest.fail("health check must not open the memory store"))
    # The consumer read gate is what the switch changes: the check must never touch it.
    for name in ("ats.workflow.runtime_reads.gate_read", "ats.workflow.cutover_routing.read_route",
                 "ats.data.consumer_api.read_input"):
        monkeypatch.setattr(name, lambda *a, **k: pytest.fail("health check went through a consumer read gate"))
    return state


def fresh_brief(days=1, lineage=True):
    return [{"as_of": (NOW - timedelta(days=days)).isoformat(), "input_refs": ["ref"] if lineage else []}]


def test_information_is_healthy_when_documents_are_readable_and_well_formed(world):
    result = health.check("information", now=NOW)
    assert result["status"] == "ok" and result["problems"] == []
    assert result["detail"] == {"MRVL": {"documents": 1}, "MSFT": {"documents": 1}}
    assert world.products.structured.closed


def test_information_one_quiet_ticker_is_a_warning_not_a_failure(world):
    world.documents["MSFT"] = []
    result = health.check("information", now=NOW)
    assert result["status"] == "ok" and any("MSFT" in w for w in result["warnings"])


def test_information_fails_when_the_whole_universe_is_empty(world):
    world.documents = {e: [] for e in UNIVERSE}
    result = health.check("information", now=NOW)
    assert result["status"] == "failed" and any("whole universe" in p for p in result["problems"])


@pytest.mark.parametrize("field", ["version_id", "content_hash", "source"])
def test_information_fails_on_documents_without_version_hash_or_source(world, field):
    world.documents["MRVL"] = [doc("MRVL", **{field: ""})]
    result = health.check("information", now=NOW)
    assert result["status"] == "failed" and any("without version/hash/source" in p for p in result["problems"])


def test_information_fails_when_the_product_is_unreadable(world):
    world.documents["MRVL"] = RuntimeError("database is locked")
    result = health.check("information", now=NOW)
    assert result["status"] == "failed" and any("unreadable" in p and "database is locked" in p for p in result["problems"])
    assert world.products.structured.closed


def test_fundamental_is_healthy_with_financials_lineage_and_a_fresh_upstream_brief(world):
    world.briefs = {"MRVL": fresh_brief()}
    result = health.check("fundamental", now=NOW)
    assert result["status"] == "ok"
    assert result["detail"]["MRVL"]["fresh_information_brief_with_lineage"] is True
    assert any("MSFT" in w and "Information brief" in w for w in result["warnings"])


def test_fundamental_fails_without_any_fresh_information_brief(world):
    world.briefs = {}
    result = health.check("fundamental", now=NOW)
    assert result["status"] == "failed" and any("switch Information first" in p for p in result["problems"])


@pytest.mark.parametrize("brief", [fresh_brief(days=10), fresh_brief(lineage=False)])
def test_fundamental_does_not_count_a_stale_or_lineage_free_brief(world, brief):
    world.briefs = {"MRVL": brief, "MSFT": brief}
    assert health.check("fundamental", now=NOW)["status"] == "failed"


def test_fundamental_fails_on_financial_rows_without_lineage(world):
    world.products = Products(lineage=False)
    world.briefs = {"MRVL": fresh_brief()}
    result = health.check("fundamental", now=NOW)
    assert result["status"] == "failed" and any("without lineage" in p for p in result["problems"])


def test_fundamental_fails_when_there_are_no_financials_at_all(world):
    world.products = Products(financial={e: [] for e in UNIVERSE})
    world.briefs = {"MRVL": fresh_brief()}
    result = health.check("fundamental", now=NOW)
    assert result["status"] == "failed" and any("no financial rows for the whole universe" in p for p in result["problems"])


def test_fundamental_fails_when_a_financial_snapshot_is_unreadable(world):
    world.products = Products(financial={"MRVL": ValueError("bad artifact"), "MSFT": [{"observation_id": "x"}]})
    world.briefs = {"MRVL": fresh_brief()}
    result = health.check("fundamental", now=NOW)
    assert result["status"] == "failed" and any("MRVL" in p and "bad artifact" in p for p in result["problems"])


def test_a_consumer_without_a_defined_check_cannot_pass(world):
    result = health.check("chief", now=NOW)      # a decision role: it never moves with the research-read switch
    assert result["status"] == "failed" and "cannot switch yet" in result["problems"][0]


def test_the_check_closes_its_readonly_repository_even_when_it_fails(world):
    world.documents = {e: RuntimeError("down") for e in UNIVERSE}
    assert health.check("information", now=NOW)["status"] == "failed"
    assert world.products.structured.closed


def test_latest_brief_reads_through_a_readonly_connection_and_never_creates_schema(tmp_path, monkeypatch):
    import json
    import sqlite3

    path = tmp_path / "memory.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE task_projection_envelopes (agent_role TEXT, scope_kind TEXT, scope_id TEXT, "
                     "status TEXT, created_at TEXT, as_of TEXT, input_refs TEXT)")
        conn.execute("INSERT INTO task_projection_envelopes VALUES ('information_brief','entity','MRVL','published',"
                     "'2026-10-15','2026-10-14T00:00:00+00:00',?)", (json.dumps(["doc@v1"]),))
        conn.execute("INSERT INTO task_projection_envelopes VALUES ('information_brief','entity','MRVL','superseded',"
                     "'2026-10-16','2026-10-16T00:00:00+00:00','[]')")
    before = (path.stat().st_size, sorted(r[0] for r in sqlite3.connect(path).execute("SELECT name FROM sqlite_master")))
    monkeypatch.setenv("ATS_DB_PATH", str(path))
    brief = health._latest_brief("MRVL")
    assert brief == {"as_of": "2026-10-14T00:00:00+00:00", "input_refs": ["doc@v1"]}
    assert health._latest_brief("NVDA") is None
    after = (path.stat().st_size, sorted(r[0] for r in sqlite3.connect(path).execute("SELECT name FROM sqlite_master")))
    assert after == before
    monkeypatch.setenv("ATS_DB_PATH", str(tmp_path / "absent.sqlite"))
    assert health._latest_brief("MRVL") is None and not (tmp_path / "absent.sqlite").exists()


def test_a_weekend_old_information_brief_still_counts_as_fresh_upstream(world):
    world.briefs = {"MRVL": fresh_brief(days=4)}
    assert health.check("fundamental", now=NOW)["status"] == "ok"
    world.briefs = {"MRVL": fresh_brief(days=8)}
    assert health.check("fundamental", now=NOW)["status"] == "failed"


# --- Technical: runtime price gateway (2026-10-16) -------------------------------------------------------
def bar(symbol, *, days_old=1, status="succeeded"):
    from datetime import timedelta as td

    return SimpleNamespace(symbol=symbol, status=status, source="yfinance",
                           bar_as_of=(NOW - td(days=days_old)).date())


@pytest.fixture
def prices(world, monkeypatch):
    state = SimpleNamespace(answers=[], calls=0)
    monkeypatch.setattr(health, "PRICE_RETRY_SECONDS", 0)

    def fake(symbols):
        state.calls += 1
        answer = state.answers.pop(0) if state.answers else {}
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(health, "_price_history", fake)
    return state


def test_technical_is_healthy_when_the_universe_has_recent_bars(prices):
    prices.answers = [{"MRVL": bar("MRVL"), "MSFT": bar("MSFT", days_old=3)}]
    result = health.check("technical", now=NOW)
    assert result["status"] == "ok" and result["warnings"] == [] and prices.calls == 1


def test_technical_one_missing_or_stale_symbol_is_only_a_warning(prices):
    prices.answers = [{"MRVL": bar("MRVL"), "MSFT": bar("MSFT", days_old=9)}]
    result = health.check("technical", now=NOW)
    assert result["status"] == "ok" and any("MSFT" in w for w in result["warnings"])


def test_technical_retries_once_before_failing(prices):
    prices.answers = [RuntimeError("yfinance down"), {"MRVL": bar("MRVL")}]
    assert health.check("technical", now=NOW)["status"] == "ok" and prices.calls == 2


def test_technical_fails_when_the_whole_universe_has_no_recent_bar_twice(prices):
    prices.answers = [RuntimeError("down"), {"MRVL": bar("MRVL", status="failed"), "MSFT": bar("MSFT", days_old=30)}]
    result = health.check("technical", now=NOW)
    assert result["status"] == "failed" and prices.calls == 2
    assert any("no symbol in the universe" in p for p in result["problems"])
