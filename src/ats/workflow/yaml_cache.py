"""Parse a YAML file once per change of the file, not once per read.

The decision gates re-read the same few manifests hundreds of times in one validation (a 52 KB
coverage manifest was parsed 524 times, ~27 s of pure-Python YAML work between an approval and the
execution gate, which has a 60 s clock). The cache key is (path, mtime_ns, size), so an edit to the
file is seen on the next read; every caller gets its own deep copy, so a caller that mutates what it
read cannot change what the next one sees.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

_CACHE: dict[tuple[str, int, int], Any] = {}


def load_yaml(path: str | Path) -> Any:
    p = Path(path)
    stat = p.stat()
    key = (str(p.resolve()), stat.st_mtime_ns, stat.st_size)
    if key not in _CACHE:
        # drop older versions of the same file, then parse
        for old in [k for k in _CACHE if k[0] == key[0]]:
            del _CACHE[old]
        _CACHE[key] = yaml.safe_load(p.read_text(encoding="utf-8"))
    return copy.deepcopy(_CACHE[key])
