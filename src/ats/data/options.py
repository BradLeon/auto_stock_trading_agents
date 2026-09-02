"""Compatibility module alias for runtime-only option inputs."""

import sys

from .runtime import options as _implementation

sys.modules[__name__] = _implementation
