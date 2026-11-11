"""Consumers read by declared product and business scope (switch-trade r5 9.2).

The four scenarios of `data/target-dataflow-assurance` "消费者必须按声明的数据产品与业务 scope 读取": an undeclared
product is refused, an unfit product fails explicitly, reads need no route table or qualification ledger, and a
read outside the bound scope is refused rather than served empty.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ats.agent.task_projection import ProjectionScope
from ats.data import consumer_api
from ats.workflow import runtime_reads as reads
from ats.workflow.read_scope import ReadUnavailable, read_projection

pytestmark = pytest.mark.governed_decision_reads

NOW = datetime(2026, 11, 11, 2, 0, tzinfo=UTC)


def _risk_scope(entities=("NVDA",)):
    return reads.business_identity("risk", kind="portfolio", scope_id="portfolio", entities=list(entities), as_of=NOW)


# --------------------------------------------------------------------------- #
# 读取未声明的产品
# --------------------------------------------------------------------------- #
def test_an_undeclared_product_is_refused():
    with pytest.raises(PermissionError, match="undeclared_data_input:trader:HIER_DATA"):
        consumer_api.read_input("trader", "HIER_DATA", scope={"kind": "evidence", "entity": "NVDA"})


def test_another_consumers_declaration_does_not_stand_in():
    assert consumer_api.input_contract("risk", "HISTORY_DATA")["consumer"] == "risk"
    with pytest.raises(PermissionError, match="undeclared_data_input:trader:HISTORY_DATA"):
        consumer_api.input_contract("trader", "HISTORY_DATA")


def test_an_undeclared_projection_input_is_refused():
    class Store:
        def get_task_projection(self, projection_id):
            raise AssertionError("an undeclared projection must be refused before any read")

    with pytest.raises(PermissionError, match="undeclared_projection_input"):
        read_projection(Store(), consumer="trader", role="technical_review",
                        scope=ProjectionScope(kind="entity", id="NVDA"), projection_id="p1")


# --------------------------------------------------------------------------- #
# 数据产品不合格
# --------------------------------------------------------------------------- #
def test_an_unfit_projection_fails_explicitly_instead_of_reading_empty():
    class Store:
        def get_task_projection(self, projection_id):
            return {"projection_id": projection_id, "agent_role": "technical_review", "scope_kind": "entity",
                    "scope_id": "NVDA", "content_hash": "stored-hash"}

    with pytest.raises(ReadUnavailable, match="scope/hash mismatch"):
        read_projection(Store(), consumer="chief", role="technical_review",
                        scope=ProjectionScope(kind="entity", id="NVDA"), projection_id="p1",
                        content_hash="frozen-hash")


# --------------------------------------------------------------------------- #
# 不依赖路由表与资格账本
# --------------------------------------------------------------------------- #
def test_reads_need_no_route_table_or_qualification_ledger(tmp_path, monkeypatch):
    authority = tmp_path / "no-cutover.sqlite"
    monkeypatch.setenv("ATS_CUTOVER_DB", str(authority))

    with reads.bind_read(_risk_scope(), as_of=NOW):
        packet = consumer_api.read_input("risk", "RISK_RULES", scope={})

    assert packet.status == "complete"
    assert not authority.exists()


# --------------------------------------------------------------------------- #
# scope 与当次入口不一致
# --------------------------------------------------------------------------- #
def test_an_entity_outside_the_bound_scope_is_refused():
    with reads.bind_read(_risk_scope(["NVDA"]), as_of=NOW):
        reads.guard_input("risk", {"entity": "NVDA"})
        with pytest.raises(ReadUnavailable, match="entity escapes"):
            reads.guard_input("risk", {"entity": "AAPL"})


def test_another_consumer_cannot_read_under_the_bound_scope():
    with reads.bind_read(_risk_scope(), as_of=NOW):
        with pytest.raises(ReadUnavailable, match="consumer escapes"):
            reads.guard_input("trader", {"entity": "NVDA"})


def test_a_time_point_outside_the_bound_scope_is_refused():
    with reads.bind_read(_risk_scope(), as_of=NOW):
        with pytest.raises(ReadUnavailable, match="time escapes"):
            reads.guard_input("risk", {"entity": "NVDA"}, as_of=NOW + timedelta(hours=1))
    with pytest.raises(ReadUnavailable, match="outside the bound scope"):
        with reads.bind_read(_risk_scope(), as_of=NOW + timedelta(hours=1)):
            pass


def test_an_explicit_scope_cannot_widen_the_request():
    wider = {"kind": "portfolio", "id": "portfolio", "entities": ["AAPL", "NVDA"],
             "time_range": {"start": NOW.isoformat(), "end": NOW.isoformat()}}
    with pytest.raises(ReadUnavailable, match="does not match"):
        reads.business_identity("risk", kind="portfolio", scope_id="portfolio", entities=["NVDA"],
                                as_of=NOW, explicit=wider)
