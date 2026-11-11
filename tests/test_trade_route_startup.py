"""Startup admission reads the trade-route authority (switch-trade r5 9.3, cutover-control 「冲突的交易路由配置必须在启动时 fail closed」)."""

from __future__ import annotations

import sqlite3

import pytest

from ats.execution import route_registry as rr


def test_no_authority_file_means_trading_is_closed_and_research_starts(tmp_path):
    assert rr.assert_runtime_startup(caller="test", path=tmp_path / "absent.sqlite") is None


def test_an_authority_without_a_registered_route_lets_research_start(tmp_path):
    path = tmp_path / "routes.sqlite"
    with rr._connect(path):
        pass
    assert rr.assert_runtime_startup(caller="test", path=path) is None


def test_the_single_registered_route_is_returned(tmp_path):
    path = tmp_path / "routes.sqlite"
    rr.install_route("paper-a", path=path)
    state = rr.assert_runtime_startup(caller="test", path=path)
    assert state.route_id == "paper-a" and state.generation == 1


def test_two_active_routes_fail_startup_and_name_both(tmp_path):
    path = tmp_path / "routes.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE trade_route_state (singleton INTEGER, route_id TEXT, generation INTEGER, "
                     "environment TEXT, account TEXT, updated_at TEXT, updated_by TEXT, reason TEXT)")
        conn.executemany("INSERT INTO trade_route_state VALUES (1,?,1,'','','','','')", [("live-a",), ("live-b",)])
    with pytest.raises(rr.RouteRegistryError, match="conflicting active trade routes: live-a, live-b"):
        rr.assert_runtime_startup(caller="test", path=path)


def test_an_unreadable_authority_fails_closed_with_the_reason(tmp_path):
    path = tmp_path / "routes.sqlite"
    path.write_bytes(b"not a sqlite database at all" * 100)
    with pytest.raises(rr.RouteRegistryError, match="unreadable"):
        rr.assert_runtime_startup(caller="test", path=path)


def test_an_authority_without_its_table_is_not_parseable(tmp_path):
    path = tmp_path / "routes.sqlite"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE other (x)")
    with pytest.raises(rr.RouteRegistryError, match="no route table"):
        rr.assert_runtime_startup(caller="test", path=path)
