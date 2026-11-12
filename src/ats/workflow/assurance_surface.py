"""Content fingerprints of the business implementation (narrowed in switch-trade r5 9.3).

The qualification tiers that used to live here — `required_fingerprint_paths`, per-consumer fingerprint lists and
the freeze-only surface — bound read qualification evidence to code and went with the qualification ledger (user
ruling D5, 2026-11-11). What remains is long-term: `route_switch` pins its isolated recovery proof to the hashes of
everything the business runs, so a proof taken against other code is refused.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Iterable

from ..config import REPO_ROOT


def fingerprint(paths: Iterable[str]) -> dict[str, str]:
    """Content hashes for the given repo-relative (or absolute in-repo) paths."""
    result: dict[str, str] = {}
    for raw in paths:
        candidate = Path(raw)
        resolved = candidate if candidate.is_absolute() else REPO_ROOT / candidate
        if not resolved.is_file():
            raise FileNotFoundError(f"fingerprint path is not a file: {raw}")
        label = resolved.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
        result[label] = hashlib.sha256(resolved.read_bytes()).hexdigest()
    return result


def implementation_paths() -> list[str]:
    """Everything the business runs: the ats source tree with its prompts, the configuration, and the locked
    dependency set."""
    paths = {str(p.relative_to(REPO_ROOT)) for p in (REPO_ROOT / "src/ats").rglob("*")
             if p.is_file() and p.suffix in {".py", ".md"}}
    paths.update(str(p.relative_to(REPO_ROOT)) for p in (REPO_ROOT / "config").rglob("*.yaml") if p.is_file())
    paths.update(name for name in ("pyproject.toml", "uv.lock") if (REPO_ROOT / name).is_file())
    return sorted(paths)


def implementation_hashes():
    """Conservatively pin business code, role prompts, model adapters and configuration."""
    return fingerprint(implementation_paths())
