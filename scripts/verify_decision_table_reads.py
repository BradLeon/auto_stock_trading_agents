#!/usr/bin/env python3
"""Print every approval or ledger code path that still reads the legacy `cycles`/`decisions` tables.

Read-only: it parses source files and touches no database. Empty output and exit code 0 mean none do.
Run it before and after the paired approval/ledger switch (tasks 4.3) and record both results.

    uv run python scripts/verify_decision_table_reads.py [--allowed]
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ats.workflow import decision_read_audit as audit  # noqa: E402


def main(argv: list[str]) -> int:
    if "--allowed" in argv:
        for module, reason in audit.ALLOWED_READERS.items():
            print(f"allowed: {module} — {reason}")
        return 0
    readers = audit.scan_tree(ROOT / "src" / "ats")
    for reader in readers:
        print(reader.render())
    return 1 if readers else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
