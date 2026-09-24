"""Machine-checkable versions of three architecture rules.

The rules themselves are old news: analysts must not read each other's opinions, agents
must not reach past the data products into providers, and an opinionated conclusion must
not be laundered into a neutral shared fact. What has been missing is an executable
judgement — until now they were enforced by review, which is exactly the kind of
constraint that decays the moment a deadline lands.

Implementation is AST scanning, not runtime probing (see design D7): a runtime probe
only covers the branches that happen to execute, while the question "does role A read
role B's output anywhere?" is a question about the source.

Exceptions are allowed but must be *specific* — a module and a target, with a reason.
A wildcard exclusion would make the guard a decoration.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from ..config import REPO_ROOT

AGENT_ROOT = REPO_ROOT / "src" / "ats" / "agents"

# --- roles ----------------------------------------------------------------- #
# Directory → role. Deliberately explicit: inferring a role from a file name is how a
# renamed module silently changes what it is allowed to read.
ROLE_BY_PATH_PREFIX: tuple[tuple[str, str], ...] = (
    ("src/ats/agents/sector/layer_analyst", "layer_analyst"),
    ("src/ats/agents/sector/layer_review", "layer_analyst"),
    ("src/ats/agents/layer", "layer_analyst"),
    ("src/ats/agents/sector", "sector_analyst"),
    ("src/ats/agents/information", "information_analyst"),
    ("src/ats/agents/evidence", "evidence_observer"),
    ("src/ats/agents/fundamental", "fundamental_analyst"),
    ("src/ats/agents/pead", "fundamental_analyst"),
    ("src/ats/agents/macro", "macro_analyst"),
    ("src/ats/agents/technical", "technical_analyst"),
    ("src/ats/agents/chief", "chief"),
    ("src/ats/agents/risk", "risk_officer"),
)

# The two dependencies the design explicitly permits. Everything else is a violation.
# Key = the reader, value = the roles it may read. Both spellings are listed because
# the read call carries the PROJECTION role (e.g. `information_brief`), while the
# dependency is usually named after the analyst role — the guard compares literals,
# so missing a spelling would silently reintroduce a forbidden channel.
ALLOWED_CROSS_ROLE_READS: dict[str, tuple[str, ...]] = {
    "sector_analyst": ("layer_analyst", "layer_analysis"),      # 行业分析师读层次分析师
    "fundamental_analyst": ("information_analyst", "information_brief"),  # 基本面读信息简报
}

# Projection roles each agent role OWNS — reading your own projections is not a
# cross-role read (the projection role name differs from the agent role name,
# e.g. `information_analyst` publishes/reads `information_brief`).
OWNED_PROJECTION_ROLES: dict[str, tuple[str, ...]] = {
    "information_analyst": ("information_brief",),
    "layer_analyst": ("layer_analysis",),
    "sector_analyst": ("sector_allocation",),
    "fundamental_analyst": ("fundamental_expectation_update", "fundamental_event_review"),
    "macro_analyst": ("macro_review",),
    "technical_analyst": ("technical_review",),
}

# Projection readers: calls through which one role can consume another's output.
PROJECTION_READ_CALLS = frozenset({
    "task_projection_envelopes", "reusable_task_projection", "reuse_decision",
    "task_projections",
})

# Phase D 5.11: a fundamental module may read fundamental-family projections ONLY
# scoped to the ticker under review. An unscoped read (no scope_id kwarg at all)
# can silently surface OTHER tickers' event reviews — exactly the cross-ticker
# opinion channel the spec closes. Statically checkable, so it is.
FUNDAMENTAL_ROLE = "fundamental_analyst"


class CrossScopeReadError(RuntimeError):
    """A fundamental module read a fundamental-family projection scoped to a
    different ticker than the one under review."""


def assert_fundamental_scope(read_role: str, scope_id: str, *, own_symbol: str) -> None:
    """Runtime backstop for 5.11: cross-ticker fundamental reads are refused."""
    if read_role not in OWNED_PROJECTION_ROLES.get(FUNDAMENTAL_ROLE, ()):
        return
    if str(scope_id).upper() != str(own_symbol).upper():
        raise CrossScopeReadError(
            f"fundamental module read {read_role} projection scoped to {scope_id!r} "
            f"while reviewing {own_symbol!r}: cross-ticker fundamental conclusions "
            f"are not a sanctioned input")

# --- providers -------------------------------------------------------------- #
PROVIDER_PREFIXES: tuple[str, ...] = ("ats.data.adapters",)

PROVIDER_MODULES: frozenset[str] = frozenset({
    "ats.data.defeatbeta", "ats.data.factset", "ats.data.news", "ats.data.websearch",
    "ats.data.sec", "ats.data.transcript", "ats.data.source_cache",
    "ats.data.documents", "ats.data.document_assets", "ats.data.research",
    "ats.data.consensus", "ats.data.fundamentals", "ats.data.industry",
    "ats.data.regional", "ats.data.base", "ats.data.market_data", "ats.data.options",
})

# --- shared-fact write targets ---------------------------------------------- #
SHARED_FACT_WRITE_CALLS: frozenset[str] = frozenset({
    "save_evidence_observation", "save_evidence_failure", "save_measurement_points",
    "save_document", "save_document_candidate", "save_document_alias",
    "save_document_chunks", "ingest",
})

# Shared-fact WRITE owners: the data-layer repository and Workflow memory's delegating
# methods. A call to one of these from an agent module is the laundering we forbid —
# except where a module is registered as an exception with a stated reason.
WRITE_OWNER_HINTS: frozenset[str] = frozenset({
    "document_assets", "data_store", "platform", "repository",
})


@dataclass(frozen=True)
class Violation:
    kind: str
    module: str
    target: str
    lineno: int
    detail: str = ""

    def __str__(self) -> str:
        place = f"{self.module}:{self.lineno}"
        return f"[{self.kind}] {place} — {self.target}" + (
            f" ({self.detail})" if self.detail else "")


@dataclass(frozen=True)
class ExceptionEntry:
    """A declared, reasoned exception. Module-level, never a pattern.

    `phase` names the phase that will REMOVE the exception — an exception without
    one, or whose removal phase has already passed, fails the guard (Phase D 8.7).
    """

    module: str
    target: str
    reason: str
    phase: str = ""


# --- first batch ------------------------------------------------------------ #
# These are current-state violations, each declared with the reason it still exists and
# the phase that will remove it. They are NOT a tolerance list: adding a new violation
# fails the guard until it is declared here in the same change.
#
# Phase D 8.6: the first batch — every entry flagged "Phase D" — has been retired by
# moving acquisition into `ats.data.collection` and analyst reads behind the
# `ats.data.products` entries. The list is now empty BY CONSTRUCTION and stays
# that way: a new violation must be declared here with a concrete removal phase
# (a future one), never silently tolerated.
FIRST_BATCH_EXCEPTIONS: tuple[ExceptionEntry, ...] = ()

# --- exception lifecycle (Phase D 8.7) --------------------------------------- #
# An exception is a debt with a due date. The phase vocabulary is fixed so a typo
# cannot silently widen the calendar, and `ACTIVE_PHASE` advances one step per phase
# change: when it passes a declared removal phase, that exception expires and the
# guard starts failing until the violation is actually gone.
PHASES: tuple[str, ...] = ("Phase A", "Phase B", "Phase C", "Phase D", "Phase E", "Phase F")
ACTIVE_PHASE = "Phase D"


class ExceptionPhaseError(RuntimeError):
    """A declared exception lacks a removal phase, or its removal phase expired."""


def validate_exceptions(exceptions: Iterable[ExceptionEntry] | None = None,
                        *, active_phase: str = ACTIVE_PHASE) -> None:
    """Fail loudly when an exception is undated or overdue.

    A removal phase must be strictly FUTURE relative to the active phase: declaring
    "Phase D" while Phase D is the phase being executed is how an exception pretends
    it will remove itself. Unknown phase strings are rejected outright. `exceptions`
    defaults to the module-level list read at call time, so tests and callers always
    validate what is actually declared.
    """
    if exceptions is None:
        exceptions = FIRST_BATCH_EXCEPTIONS
    if active_phase not in PHASES:
        raise ExceptionPhaseError(f"unknown active phase {active_phase!r}")
    active_index = PHASES.index(active_phase)
    errors: list[str] = []
    for entry in exceptions:
        where = f"{entry.module} -> {entry.target}"
        if not entry.phase:
            errors.append(f"{where}: exception declares no removal phase")
        elif entry.phase not in PHASES:
            errors.append(f"{where}: unknown removal phase {entry.phase!r}")
        elif PHASES.index(entry.phase) <= active_index:
            errors.append(f"{where}: removal phase {entry.phase} already expired "
                          f"(active: {active_phase}) — remove the violation or the entry")
    if errors:
        raise ExceptionPhaseError("; ".join(errors))


def declared_exception(module: str, target: str,
                       exceptions: Iterable[ExceptionEntry] = ()) -> ExceptionEntry | None:
    for entry in exceptions or FIRST_BATCH_EXCEPTIONS:
        if entry.module == module and entry.target == target:
            return entry
    return None


def role_for(relative_path: str) -> str | None:
    for prefix, role in ROLE_BY_PATH_PREFIX:
        if relative_path.startswith(prefix):
            return role
    return None


def _relative(path: Path, root: Path | None = None) -> str:
    return path.relative_to(root or REPO_ROOT).as_posix()


def _imported_modules(node: ast.ImportFrom) -> list[str]:
    """Absolute modules an `ImportFrom` actually binds, relative levels resolved.

    `from ...data import defeatbeta` binds `ats.data.defeatbeta`, not `ats.data` —
    resolving only the module part would let every provider import through the package's
    front door.
    """
    if not node.module:
        return []
    base = (node.module if node.level == 0
            else ("ats." + node.module) if node.level >= 3 else node.module)
    out = [base]
    out.extend(f"{base}.{alias.name}" for alias in node.names)
    return out


def _is_provider(module: str) -> bool:
    return (module in PROVIDER_MODULES
            or any(module.startswith(prefix) for prefix in PROVIDER_PREFIXES))


def _role_literal(node: ast.Call) -> str | None:
    for keyword in node.keywords:
        if keyword.arg in ("agent_role", "role") and isinstance(keyword.value, ast.Constant):
            return str(keyword.value.value)
    return None


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _write_owner(node: ast.Call) -> str:
    """Base name a write call hangs off — `data_store().save_x()` is `data_store`.

    Unwrapping through the call is required: the delegating accessor is itself a call,
    and stopping at the Attribute would miss every write made through it.
    """
    if not isinstance(node.func, ast.Attribute):
        return ""
    base: ast.expr = node.func.value
    while isinstance(base, ast.Attribute):
        base = base.value
    if isinstance(base, ast.Name):
        return base.id
    if isinstance(base, ast.Call):
        return _call_name(base)
    return ""


def scan_module(path: Path, *, root: Path | None = None,
                exceptions: Sequence[ExceptionEntry] = ()) -> list[Violation]:
    """All architecture violations in one module, after declared exceptions."""
    relative = _relative(path, root or REPO_ROOT)
    role = role_for(relative)
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    found: list[Violation] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # Report one violation per import statement, naming the shortest matching
            # target: `from ats.data.adapters.x import y` matches on both the module and
            # the module+name form, and duplicating it would inflate the count.
            hits = [m for m in _imported_modules(node) if _is_provider(m)
                    and not declared_exception(relative, m, exceptions)]
            if hits:
                found.append(Violation(
                    "provider_import", relative, min(hits, key=len), node.lineno,
                    "agent module imports a source adapter or provider"))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if not alias.name.startswith("ats."):
                    continue
                if not _is_provider(alias.name):
                    continue
                if declared_exception(relative, alias.name, exceptions):
                    continue
                found.append(Violation("provider_import", relative, alias.name,
                                       node.lineno, "direct provider import"))
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            if name in PROJECTION_READ_CALLS and role is not None:
                read_role = _role_literal(node)
                owned = OWNED_PROJECTION_ROLES.get(role, ())
                if (read_role and read_role != role and read_role not in owned):
                    allowed = ALLOWED_CROSS_ROLE_READS.get(role, ())
                    if read_role not in allowed:
                        found.append(Violation(
                            "cross_role_read", relative, read_role, node.lineno,
                            f"{role} reads {read_role}'s projection"))
                elif (read_role and read_role in owned and role == FUNDAMENTAL_ROLE
                        and "scope_id" not in {kw.arg for kw in node.keywords}):
                    found.append(Violation(
                        "unscoped_projection_read", relative, read_role, node.lineno,
                        "fundamental module reads fundamental-family projections without "
                        "a scope_id; an unscoped read can surface other tickers' "
                        "conclusions (5.11)"))
            if name in SHARED_FACT_WRITE_CALLS and role is not None:
                owner = _write_owner(node)
                if owner in WRITE_OWNER_HINTS:
                    if declared_exception(relative, name, exceptions):
                        continue
                    found.append(Violation(
                        "opinion_writeback", relative, name, node.lineno,
                        f"{role} writes a shared-fact record via {owner}"))
    return found


def scan_agents(base: Path | None = None, *, root: Path | None = None) -> list[Violation]:
    """Scan every agent module. `root` is only overridden by tests using a temp tree.

    Declared exceptions are validated first (Phase D 8.7): an undated or overdue
    exception is itself a failure — it never gets the chance to suppress anything.
    """
    validate_exceptions()
    start = base or AGENT_ROOT
    out: list[Violation] = []
    for path in sorted(start.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        out.extend(scan_module(path, root=root))
    return out
