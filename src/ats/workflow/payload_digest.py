"""A stable content hash for a JSON-able payload.

It lived in `shadow_replay` (a Phase F cutover tool) although the Dispatcher uses it to record what a reused projection
read; the Dispatcher must not import a tool that is about to be retired, so it is here. `shadow_replay` re-exports it for
the cutover modules that still import it from there.
"""

from __future__ import annotations

import hashlib
import json


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    allow_nan=False).encode()).hexdigest()
