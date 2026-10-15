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


class Store:
    def __init__(self, briefs):
        self.briefs = briefs

    def task_projection_envelopes(self, *, agent_role, scope_kind, scope_id, limit):
        assert agent_role == "information_brief" and scope_kind == "entity"
        return self.briefs.get(scope_id, [])


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
    monkeypatch.setattr("ats.memory.get_store", lambda: Store(state.briefs))
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
    result = health.check("technical", now=NOW)
    assert result["status"] == "failed" and "cannot switch yet" in result["problems"][0]


def test_the_check_closes_its_readonly_repository_even_when_it_fails(world):
    world.documents = {e: RuntimeError("down") for e in UNIVERSE}
    assert health.check("information", now=NOW)["status"] == "failed"
    assert world.products.structured.closed
