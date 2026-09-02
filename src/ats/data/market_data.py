"""Compatibility module alias for runtime-only market data."""

# Preserve the old module object's private monkeypatch/import contract while the
# implementation ownership moves under ``ats.data.runtime``.
import sys

from .runtime import market_data as _implementation

sys.modules[__name__] = _implementation
