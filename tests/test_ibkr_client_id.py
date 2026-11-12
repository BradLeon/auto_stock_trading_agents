"""IBKR client ids: distinct per process, and distinct between connections open at once inside one."""
import sys
import threading
import types

import pytest

from ats.broker import ibkr
from ats.broker.ibkr import IBKRBroker


class _FakeIB:
    connected: list[int] = []

    def connect(self, host, port, clientId, timeout):
        type(self).connected.append(clientId)

    def disconnect(self):
        pass


@pytest.fixture
def fake_ib(monkeypatch):
    _FakeIB.connected = []
    monkeypatch.setitem(sys.modules, "ib_async", types.SimpleNamespace(IB=_FakeIB))
    monkeypatch.setattr(ibkr, "_LEASED_IDS", set())
    return _FakeIB


def test_connections_open_at_once_get_distinct_client_ids(fake_ib):
    first, second, third = IBKRBroker(), IBKRBroker(), IBKRBroker()
    with first.session(), second.session(), third.session():
        pass
    assert len(set(fake_ib.connected)) == 3
    assert fake_ib.connected[0] == first.client_id


def test_a_released_id_is_reused_so_fills_stay_visible_to_the_placing_client(fake_ib):
    broker = IBKRBroker()
    with broker.session():
        pass
    with IBKRBroker().session():
        pass
    assert fake_ib.connected == [broker.client_id, broker.client_id]


def test_concurrent_threads_never_share_a_client_id(fake_ib):
    inside = threading.Barrier(4)
    errors = []

    def work():
        try:
            with IBKRBroker().session():
                inside.wait(timeout=5)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(set(fake_ib.connected)) == 4


def test_a_pinned_client_id_is_used_as_given(fake_ib):
    with IBKRBroker(client_id=99).session():
        pass
    assert fake_ib.connected == [99]


def test_a_failed_connect_releases_the_id(fake_ib, monkeypatch):
    def refuse(self, host, port, clientId, timeout):
        raise OSError("refused")

    monkeypatch.setattr(_FakeIB, "connect", refuse)
    broker = IBKRBroker()
    with pytest.raises(ibkr.IBKRUnavailable):
        with broker.session():
            pass
    assert ibkr._LEASED_IDS == set()
