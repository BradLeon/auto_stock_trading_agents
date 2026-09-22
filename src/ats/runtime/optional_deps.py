"""Optional dependency groups and explicit reporting for missing ones.

The project declares its optional dependencies as extras in ``pyproject.toml``
(``data``/``broker``/``memory``/``schedule``/``memory_persist``/``channel``/``dev``)
and installs them through the single ``uv sync --all-extras`` entry (see
``scripts/run_tests.sh``). When an extra is not installed, the symptom used to be a
runtime ``ModuleNotFoundError`` surfacing as an unrelated business assertion failure
deep inside a workflow. This module makes that class of failure explicit: it maps a
module name back to the extra group that owns it and produces a message naming both.

It has three consumers:

  * production modules that lazily import an optional dependency (``require_module``)
  * the test suite, which enriches collection failures with the owning group
  * operators reading a baseline report, to tell "dependency missing" from "regression"
"""

from __future__ import annotations

import importlib
import re
from typing import Iterable

# --------------------------------------------------------------------------- #
# Registry: module -> extras group. Mirrors pyproject.toml [project.optional-dependencies].
# --------------------------------------------------------------------------- #

OPTIONAL_DEPENDENCY_GROUPS: dict[str, str] = {
    # schedule
    "apscheduler": "schedule",
    "pandas_market_calendars": "schedule",
    # broker
    "ib_async": "broker",
    # memory_persist
    "langgraph.checkpoint.sqlite": "memory_persist",
    "langgraph_checkpoint_sqlite": "memory_persist",
    # channel
    "fastapi": "channel",
    "uvicorn": "channel",
    "discord": "channel",
    # memory
    "chromadb": "memory",
    # data
    "yfinance": "data",
    "curl_cffi": "data",
    "edgar_tools": "data",
    "edgartools": "data",
    "fredapi": "data",
    "praw": "data",
    "pandas": "data",
    "matplotlib": "data",
    "seaborn": "data",
    "feedparser": "data",
    "pypdf": "data",
    "openpyxl": "data",
    "PIL": "data",
    "pysocks": "data",
    "duckdb": "data",
    # dev
    "pytest": "dev",
    "pytest_asyncio": "dev",
    "ruff": "dev",
}

# Install command referenced by every report — the single entry point.
INSTALL_COMMAND = "uv sync --all-extras"

_NO_MODULE_RE = re.compile(r"No module named '([^']+)'")
_CANNOT_IMPORT_RE = re.compile(r"cannot import name '[^']+' from '([^']+)'")


class MissingOptionalDependencyError(ImportError):
    """A module that belongs to a declared extras group is not installed."""

    def __init__(self, module: str, group: str, purpose: str = "") -> None:
        self.module = module
        self.group = group
        self.purpose = purpose
        suffix = f" (needed for {purpose})" if purpose else ""
        super().__init__(
            f"missing optional dependency '{module}'{suffix} from extras group "
            f"'{group}'; install it with `{INSTALL_COMMAND}`"
        )


def group_for_module(module: str) -> str | None:
    """Return the extras group owning ``module`` (``None`` when not optional)."""
    group = OPTIONAL_DEPENDENCY_GROUPS.get(module)
    if group:
        return group
    # Sub-modules: `langgraph.checkpoint.sqlite` -> try progressively shorter prefixes.
    parts = module.split(".")
    for i in range(len(parts) - 1, 0, -1):
        group = OPTIONAL_DEPENDENCY_GROUPS.get(".".join(parts[:i]))
        if group:
            return group
    return None


def missing_module_from_message(text: str) -> str | None:
    """Extract the missing module name from an ImportError/ModuleNotFoundError text."""
    if not text:
        return None
    m = _NO_MODULE_RE.search(text)
    if m:
        return m.group(1)
    m = _CANNOT_IMPORT_RE.search(text)
    if m:
        return m.group(1)
    return None


def describe_missing_optional_dependency(text: str) -> str | None:
    """Return an explicit report for ``text`` when it is a missing optional dependency.

    Returns ``None`` when the failure is not attributable to a declared extras group,
    so callers can fall back to their normal error handling instead of mislabelling an
    unrelated ImportError as a dependency problem.
    """
    module = missing_module_from_message(text)
    if module is None:
        return None
    group = group_for_module(module)
    if group is None:
        return None
    return (
        f"missing optional dependency: module '{module}' belongs to extras group "
        f"'{group}'; install it with `{INSTALL_COMMAND}`"
    )


def require_module(module: str, *, purpose: str = "", group: str | None = None):
    """Import ``module`` or raise :class:`MissingOptionalDependencyError`.

    Raises a plain ``ImportError`` unchanged when the module is not part of any
    declared extras group — that is a real bug, not a missing optional dependency.
    """
    try:
        return importlib.import_module(module)
    except ImportError as exc:  # ModuleNotFoundError is a subclass
        resolved = group or group_for_module(getattr(exc, "name", None) or module)
        if resolved is None:
            raise
        raise MissingOptionalDependencyError(
            getattr(exc, "name", None) or module, resolved, purpose
        ) from exc


def missing_optional_modules(modules: Iterable[str]) -> list[tuple[str, str]]:
    """Return ``[(module, group)]`` for every listed module that cannot be imported.

    Used by environment self-checks (`scripts/run_tests.sh --check` and the baseline
    record) so operators see *which* group is missing rather than a generic failure.
    """
    missing: list[tuple[str, str]] = []
    for module in modules:
        group = group_for_module(module)
        if group is None:
            continue
        try:
            importlib.import_module(module)
        except Exception:
            missing.append((module, group))
    return missing


# Modules each test-bearing area depends on; used for the environment self-check.
ENVIRONMENT_PROBE_MODULES: tuple[str, ...] = (
    "apscheduler",
    "pandas_market_calendars",
    "langgraph.checkpoint.sqlite",
    "ib_async",
    "fastapi",
    "chromadb",
)
