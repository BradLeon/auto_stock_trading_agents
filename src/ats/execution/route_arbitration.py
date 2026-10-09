"""Cross-process mutex for route changes and the broker's irreversible write.

The file is a stable companion to the registry, never removed on release. flock
is released by the OS on process death. A receipt is committed BEFORE the broker
call so a crash cannot turn an uncertain write into a retryable absent intent.
"""

from __future__ import annotations

import fcntl
import threading
import time
from contextlib import contextmanager
from functools import wraps
from inspect import signature
from pathlib import Path

_LOCAL = threading.local()


@contextmanager
def authority_lock(path, *, timeout: float = 5.0):
    from .route_registry import RouteRegistryError

    target = Path(path).expanduser().resolve()
    key = str(target)
    held = getattr(_LOCAL, "held", {})
    if key in held:
        yield
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with Path(key + ".lock").open("a+b") as handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RouteRegistryError("route arbitration lock timed out")
                time.sleep(0.01)
        _LOCAL.held = {**held, key: True}
        try:
            yield
        finally:
            _LOCAL.held = held
            fcntl.flock(handle, fcntl.LOCK_UN)


def arbitrated(func):
    """Use the same mutex for every registry operation changing authority."""
    params = signature(func)

    @wraps(func)
    def wrapped(*args, **kwargs):
        from .route_registry import default_registry_path

        path = params.bind_partial(*args, **kwargs).arguments.get("path")
        with authority_lock(path or default_registry_path()):
            return func(*args, **kwargs)

    return wrapped
