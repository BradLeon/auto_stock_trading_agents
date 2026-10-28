"""Logical evaluation time for isolated replay; source timestamps remain untouched."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime

_POINT = ContextVar("evaluation_time", default=None)
_TRANSPORT = ContextVar("evaluation_clock_transport", default=None)


def now(tz=None):
    transport = _TRANSPORT.get()
    if transport is not None:
        return transport(tz, lambda: _now(tz))
    return _now(tz)


def _now(tz):
    point = _POINT.get()
    if point is None:
        return datetime.now(tz)
    if tz is None:
        return point.astimezone().replace(tzinfo=None)
    return point.astimezone(tz)


@contextmanager
def at(point):
    from .isolation import verified_isolation_root
    if point.tzinfo is None or verified_isolation_root() is None:
        raise PermissionError("logical clock requires timezone and complete isolation")
    token = _POINT.set(point)
    try:
        yield
    finally:
        _POINT.reset(token)


@contextmanager
def recorded(transport):
    """Record evaluation receipts in isolation without changing source times."""
    from .isolation import verified_isolation_root
    if verified_isolation_root() is None:
        raise PermissionError("recorded clock requires complete isolation")
    token = _TRANSPORT.set(transport)
    try:
        yield
    finally:
        _TRANSPORT.reset(token)
