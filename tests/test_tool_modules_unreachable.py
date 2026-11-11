"""Operator tool modules stay unreachable from runtime code (architecture guard).

Kept from the retired freeze surface (`config/workflow/freeze_surface.yaml`, switch-trade r5 9.3, user ruling D5): the
qualification tiers went, but "a tool run by an operator from the CLI is not imported by business code" still holds.
"""

from __future__ import annotations

import ast
import warnings
from pathlib import Path

import pytest

from ats.config import REPO_ROOT

# Modules reached only from the CLI, the data-refresh controller or test runs; never imported by business code.
TOOL_MODULES = {
    "src/ats/runtime/evidence_observe.py": "chain-evidence extraction run by the data refresh controller",
    "src/ats/workflow/pytest_optional_deps_plugin.py": "pytest plugin; test runs only",
    "src/ats/workflow/retirement_clearance.py": "legacy retirement clearance report; reached from the cli only",
    "src/ats/workflow/test_baseline.py": "test baseline bookkeeping; imported by no module",
    "src/ats/trader/statement_import.py": "one-off backfill of broker-statement fills; reached only from the cli",
}


def _module_imports(path: Path) -> set[str]:
    name = ".".join(path.relative_to(REPO_ROOT / "src").with_suffix("").parts)
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]
    found: set[str] = set()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)  # unrelated escape warnings in src
        tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parts = package.split(".")
                parts = parts[:len(parts) - (node.level - 1)]
                base = ".".join(parts + ([node.module] if node.module else []))
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names)
    return found


@pytest.mark.parametrize("tool", sorted(TOOL_MODULES))
def test_tool_module_exists(tool):
    assert (REPO_ROOT / tool).is_file(), tool


def test_tool_modules_are_unreachable_from_runtime_modules():
    """The CLI is exempt: its tool commands are run by an operator."""
    modules = {".".join(Path(p).relative_to("src").with_suffix("").parts): p for p in TOOL_MODULES}
    for source in (REPO_ROOT / "src/ats").rglob("*.py"):
        label = source.relative_to(REPO_ROOT).as_posix()
        if label in TOOL_MODULES or label == "src/ats/runtime/cli.py":
            continue
        reached = _module_imports(source) & set(modules)
        assert not reached, f"{label} imports tool module {sorted(reached)}"
