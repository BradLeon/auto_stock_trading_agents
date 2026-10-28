"""Report file naming shared by the readable-report writers.

A report is a derived, human-readable copy of a published result. A later run on the same day must not
replace an earlier file (a reader may be looking at it, or may have added notes to it): the first file of
a day keeps the plain name and later ones get `-v2`, `-v3`, ...
"""
from __future__ import annotations

from pathlib import Path


def unique_path(path: Path) -> Path:
    """`path` itself if free, else the first free `<stem>-vN<suffix>` next to it (N starts at 2)."""
    if not path.exists():
        return path
    version = 2
    while True:
        candidate = path.with_name(f"{path.stem}-v{version}{path.suffix}")
        if not candidate.exists():
            return candidate
        version += 1
