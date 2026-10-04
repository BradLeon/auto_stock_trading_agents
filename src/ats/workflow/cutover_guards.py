"""Cutover invariants: the routes that must not be short-circuited (Phase F 1.6).

Three rules, each about a way a cutover could be declared done while the old
path is still reachable:

1. **Route resolution goes through the control plane.** A module that decides
   which route to take by reading the mode itself, rather than asking the
   cutover control plane, can serve a route the operator has already switched
   away from — and nothing reports the divergence.
2. **Broker writes go through the arbitration.** Task 1.1 put the prohibition
   in the broker's submit layer. A new order path that builds its own broker
   client and submits directly would bypass it, which is the exact failure the
   prohibition was built to make impossible.
3. **A boundary is not a single global switch.** Reading one project-wide
   "mode" to decide every consumer's route is the failure mode Phase F exists
   to remove: it cannot express "this consumer qualified, that one did not", so
   it either strands eligible consumers or promotes ineligible ones.

Scope note: this scans the whole package, not just `agents/`. The routes live
in `data/`, `execution/`, `workflow/` and `runtime/`, and an agent-only scan
would miss every one of them.

AST scanning, same as the existing architecture guards: a runtime probe only
covers the branch that happened to execute, while "can this module decide a
route on its own?" is a question about the source.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from ..config import REPO_ROOT

PACKAGE_ROOT = REPO_ROOT / "src" / "ats"

# Modules that ARE the control plane or the primitives it delegates to. They
# necessarily reference the mode functions and the broker client.
ROUTE_AUTHORITY_MODULES: frozenset[str] = frozenset({
    "ats.workflow.cutover",
    "ats.workflow.cutover_guards",
    "ats.workflow.assurance_surface",
    "ats.workflow.ownership",
    "ats.workflow.isolation",
    "ats.data.rollout_modes",
    "ats.data.release",
    "ats.execution.broker_write_guard",
    "ats.execution.authorization",
    "ats.broker.ibkr",
    "ats.runtime.cli",           # the operator-facing entry point
    "ats.runtime.scheduler",     # dispatches, and is the thing being migrated
})

# Calling a mode accessor is NOT a violation. Twenty-three modules legitimately
# ask "what mode is this consumer in?" — that is the existing, working rollout
# mechanism, and flagging them would mean 23 false exemptions and a guard nobody
# reads. What Phase F forbids is a module that SETS a route itself, because that
# bypasses the qualification gate: it can promote a consumer no evidence covers.
#
# So the rule is about the setter, not the reader. Matched as
# (receiver, method) pairs rather than bare method names: `rollback` alone would
# also match `conn.rollback()`, which is a SQLite transaction and appears in
# four unrelated modules.
ROUTE_MUTATORS: tuple[tuple[frozenset[str], str], ...] = (
    # ReleaseManager.apply / .rollback — the overlay that decides which path
    # serves a consumer. `ats.data.release` is the implementation; `ats.runtime.cli`
    # is the operator entry point that is allowed to drive it.
    (frozenset({"manager", "release_manager", "rm"}), "apply"),
    (frozenset({"manager", "release_manager", "rm"}), "rollback"),
    (frozenset({"manager", "release_manager", "rm"}), "publish"),
    # Owner configuration writers (workflow owner mode).
    (frozenset({"registry", "owners", "owner_registry"}), "set_mode"),
    (frozenset({"registry", "owners", "owner_registry"}), "set_owner_mode"),
)

# Environment variables that select a route. Setting one of these by hand is
# choosing a route without asking the control plane — an operator override and
# a code path are different things, and only the latter is governed.
ROLLOUT_ENV_VARS: frozenset[str] = frozenset({
    "ATS_STRUCTURED_DEFAULT_MODE",
    "ATS_STRUCTURED_RELEASE_FILE",
    "ATS_SHADOW_DB_PATH",
})

# Writing a rollout variable directly is only a violation when the module also
# decides the mode, rather than passing configuration to something that does.
# `setenv("ATS_STRUCTURED_<CONSUMER>_MODE", ...)` with a computed consumer name
# is caught by the mutation rule below; a literal DEFAULT_MODE is always wrong.
GLOBAL_MODE_SWITCHES: frozenset[str] = frozenset({
    "ATS_STRUCTURED_DEFAULT_MODE",
})

# Broker submit entry points. The arbitration lives inside the broker
# implementation, so submitting through an injected client is fine. Building a
# raw broker client IN THE SAME MODULE and submitting is the bypass.
BROKER_SUBMIT_CALLS: frozenset[str] = frozenset({
    "placeOrder", "place_order", "place_orders", "submitOrder",
})

# Client constructors. Matched as CALLED names, not variable names: the variable
# holding a client is named whatever the author liked (`ib`, `client`, `conn`),
# so `ib.placeOrder(...)` cannot be recognised by the receiver.
BROKER_CLIENT_FACTORIES: frozenset[str] = frozenset({
    "IB", "InteractiveBrokers",
})

# Importing a client library is the same signal: a module that imports a broker
# SDK and calls a submit method is building its own path.
BROKER_SDK_IMPORTS: frozenset[str] = frozenset({"ib_async", "ib_insync"})


class CutoverViolationError(RuntimeError):
    """A module can reach a route or a broker write the cutover does not govern."""


@dataclass(frozen=True)
class CutoverViolation:
    kind: str
    module: str
    target: str
    lineno: int
    detail: str = ""

    def __str__(self) -> str:
        return (f"[{self.kind}] {self.module}:{self.lineno} — {self.target}"
                + (f" ({self.detail})" if self.detail else ""))


@dataclass(frozen=True)
class CutoverException:
    """A declared bypass with a reason and the phase that removes it.

    Same discipline as the architecture guards: a bypass without a removal
    phase is a permanent hole, so `validate_cutover_exceptions` rejects one.
    """

    module: str
    target: str
    reason: str
    phase: str = ""


# Phase F's own migration is the only phase that may declare a bypass, and only
# against code it is actively rewriting. Empty by construction: a new bypass
# must be declared here with a concrete later phase.
CUTOVER_EXCEPTIONS: tuple[CutoverException, ...] = ()

# The vocabulary is shared with the architecture guards so a typo cannot widen
# the calendar, and Phase F is the active phase while this change is applied.
# `Phase G` exists because a bypass declared during Phase F may only name a
# LATER phase — without one, every declaration would be either expired or
# outside the vocabulary, and the discipline would collapse into "never
# declare anything".
PHASES: tuple[str, ...] = ("Phase A", "Phase B", "Phase C", "Phase D", "Phase E",
                           "Phase F", "Phase G")
ACTIVE_PHASE = "Phase F"


class CutoverExceptionPhaseError(RuntimeError):
    """A declared cutover bypass lacks a removal phase, or it has expired."""


def validate_cutover_exceptions(exceptions: Iterable[CutoverException] | None = None,
                                *, active_phase: str = ACTIVE_PHASE) -> None:
    """Fail loudly on an undated or overdue bypass.

    A removal phase must be strictly later than the active phase: declaring
    "Phase F" while Phase F is being applied is how a bypass declares itself
    temporary and then outlives the change. `exceptions` defaults to the
    module-level tuple read at call time so tests validate what is declared.
    """
    if exceptions is None:
        exceptions = CUTOVER_EXCEPTIONS
    if active_phase not in PHASES:
        raise CutoverExceptionPhaseError(f"unknown active phase {active_phase!r}")
    active_index = PHASES.index(active_phase)
    errors: list[str] = []
    for entry in exceptions:
        where = f"{entry.module} -> {entry.target}"
        if not entry.phase:
            errors.append(f"{where}: bypass declares no removal phase")
        elif entry.phase not in PHASES:
            errors.append(f"{where}: unknown removal phase {entry.phase!r}")
        elif PHASES.index(entry.phase) <= active_index:
            errors.append(f"{where}: removal phase {entry.phase} already expired "
                          f"(active: {active_phase}) — remove the bypass or the entry")
    if errors:
        raise CutoverExceptionPhaseError("; ".join(errors))


def _declared(module: str, target: str,
              exceptions: Iterable[CutoverException] = ()) -> bool:
    for entry in exceptions or CUTOVER_EXCEPTIONS:
        if entry.module == module and entry.target == target:
            return True
    return False


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _receiver(node: ast.Call) -> str:
    """The name a method is called on, unwrapping `self` and plain attributes.

    `manager.apply(...)` and `self.manager.apply(...)` are the same call site for
    this rule; `conn.rollback(...)` is not, which is why the rule matches on the
    receiver rather than the method name alone.
    """
    func = node.func
    if not isinstance(func, ast.Attribute):
        return ""
    base: ast.expr = func.value
    while isinstance(base, ast.Attribute):
        base = base.value
    if isinstance(base, ast.Name):
        return base.id
    return ""


def _string_constants(tree: ast.AST) -> set[str]:
    return {node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)}


def _imports_broker_sdk(tree: ast.AST) -> bool:
    """True when the module imports a broker client library."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.split(".")[0] in BROKER_SDK_IMPORTS
                   for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root in BROKER_SDK_IMPORTS:
                return True
    return False


def scan_cutover_module(path: Path, *, root: Path | None = None,
                        exceptions: Sequence[CutoverException] = ()) -> list[CutoverViolation]:
    """Cutover violations in one module, after declared exceptions."""
    resolved = path.resolve()
    try:
        relative = resolved.relative_to((root or PACKAGE_ROOT.parent).resolve()).as_posix()
    except ValueError:  # pragma: no cover - fixtures outside the tree
        relative = resolved.as_posix()
    # Normalise to the dotted module path the exception table uses.
    dotted = relative[:-3].replace("/", ".") if relative.endswith(".py") else relative
    if dotted in ROUTE_AUTHORITY_MODULES:
        return []

    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    literals = _string_constants(tree)
    # A module that imports a broker SDK and calls a submit method is building
    # its own path, whatever it named the client variable.
    imports_sdk = _imports_broker_sdk(tree)
    found: list[CutoverViolation] = []
    seen: set[tuple[str, str, int]] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        receiver = _receiver(node)

        for receivers, method in ROUTE_MUTATORS:
            if name != method or receiver not in receivers:
                continue
            # The overlay implementation and the operator entry point are the two
            # places allowed to change a route on purpose.
            if dotted in {"ats.data.release", "ats.runtime.cli"}:
                continue
            key = ("route_mutate", f"{receiver}.{method}", node.lineno)
            if key not in seen and not _declared(dotted, f"{receiver}.{method}", exceptions):
                seen.add(key)
                found.append(CutoverViolation(
                    "route_mutation", dotted, f"{receiver}.{method}()", node.lineno,
                    "module changes which route serves traffic outside the "
                    "cutover control plane"))
            break
        if name in BROKER_SUBMIT_CALLS and not _declared(dotted, name, exceptions):
            # A submit through an INJECTED broker is governed by the arbitration
            # inside the broker implementation. Constructing or importing a client
            # in the same module is the bypass.
            constructs_client = imports_sdk or any(
                isinstance(inner, ast.Call) and _call_name(inner) in BROKER_CLIENT_FACTORIES
                for inner in ast.walk(node))
            if constructs_client:
                key = ("broker_write", name, node.lineno)
                if key not in seen:
                    seen.add(key)
                    found.append(CutoverViolation(
                        "broker_write_bypass", dotted, name, node.lineno,
                        "module builds its own broker client and submits, "
                        "bypassing the write arbitration"))

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and node.value in GLOBAL_MODE_SWITCHES \
                and not _declared(dotted, "global_mode_switch", exceptions):
            found.append(CutoverViolation(
                "global_mode_switch", dotted, node.value, getattr(node, "lineno", 0),
                "a project-wide default mode decides every consumer's route; "
                "Phase F requires per-consumer qualification"))
            break

    return found


def scan_cutover(base: Path | None = None, *, root: Path | None = None,
                 exceptions: Sequence[CutoverException] = ()) -> list[CutoverViolation]:
    """Scan every package module. Declared bypasses are validated first."""
    validate_cutover_exceptions()
    start = base or PACKAGE_ROOT
    out: list[CutoverViolation] = []
    for path in sorted(start.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        out.extend(scan_cutover_module(path, root=root, exceptions=exceptions))
    return out


def assert_cutover_invariants(base: Path | None = None, *,
                              exceptions: Sequence[CutoverException] = ()) -> None:
    """Raise when any cutover invariant is violated."""
    found = scan_cutover(base, exceptions=exceptions)
    if found:
        raise CutoverViolationError(
            "cutover invariants violated:\n"
            + "\n".join(str(item) for item in found))
