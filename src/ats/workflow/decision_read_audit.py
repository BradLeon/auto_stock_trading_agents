"""Read-only audit: which approval or ledger code still reads the legacy decision tables?

The decision audit (`decision_cycles`, `decision_revisions`, `decision_risk_reviews`, `boss_approvals`,
`cycle_events`) is the authority. The older `cycles` and `decisions` tables are only mirrored into, for
the transition. Switching approval and the ledger together (design D4, plan B) is safe only if no
approval reader and no ledger publisher still reads those two tables, so this walks every module under
`src/ats` and reports each SQL statement that selects from or joins them.

The allowlist names the readers that exist on purpose; each carries its reason. An empty report means
nothing outside the allowlist reads them. Nothing here writes or opens a database.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

LEGACY_TABLES = ("cycles", "decisions")
_READ = re.compile(r"\b(?:FROM|JOIN)\s+(cycles|decisions)\b", re.IGNORECASE)

#: `module` (relative to src/ats) or `module::function` -> why reading the legacy tables there is intended.
ALLOWED_READERS: dict[str, str] = {
    "decision/legacy.py": "one-time migration classifier: reads legacy rows to build audit revisions, "
                          "and is the only place that does",
    "decision/repository.py::mirror_revision_to_legacy":
        "the mirror's own duplicate check before it writes a legacy row; its result feeds no decision, "
        "approval or ledger figure",
}


@dataclass(frozen=True)
class Reader:
    path: str
    line: int
    function: str
    table: str
    sql: str

    def render(self) -> str:
        return f"{self.path}:{self.line} {self.function} reads `{self.table}`: {self.sql}"


def _functions(tree: ast.AST) -> dict[int, str]:
    """Line -> innermost enclosing function name, so a report names the code path, not just a file."""
    owner: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for line in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                owner[line] = node.name
    return owner


def _strings(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value
        elif isinstance(node, ast.JoinedStr):
            text = "".join(p.value for p in node.values if isinstance(p, ast.Constant)
                           and isinstance(p.value, str))
            if text:
                yield node.lineno, text


def scan_source(source: str, path: str) -> list[Reader]:
    tree = ast.parse(source)
    owner = _functions(tree)
    found: dict[tuple[int, str], Reader] = {}
    for line, text in _strings(tree):
        for match in _READ.finditer(text):
            # An f-string is seen whole and again as its literal parts: one statement, one report.
            found.setdefault((line, match.group(1).lower()), Reader(
                path, line, owner.get(line, "<module>"), match.group(1).lower(),
                " ".join(text.split())[:100]))
    return sorted(found.values(), key=lambda r: (r.line, r.table))


def scan_tree(root: Path, *, allowed: dict[str, str] | None = None) -> list[Reader]:
    """Every un-allowlisted reader under `root` (the `src/ats` directory)."""
    allowed = ALLOWED_READERS if allowed is None else allowed
    out: list[Reader] = []
    for file in sorted(root.rglob("*.py")):
        rel = file.relative_to(root).as_posix()
        if rel in allowed:
            continue
        out.extend(r for r in scan_source(file.read_text(encoding="utf-8"), rel)
                   if f"{rel}::{r.function}" not in allowed)
    return out
