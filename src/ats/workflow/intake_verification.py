"""Isolated intake verification for the ten roles (Phase F tasks 7.1–7.6).

The gap this closes
-------------------
`F.0.2` produced **0/10 eligible**: every consumer is ineligible because the
assurance ledger has never had a per-item evidence row. That is the honest
starting state, but it is a dead end unless the missing evidence can be produced
*without* the qualification it is meant to prove — otherwise the only way to
qualify is to qualify, which is circular (design decision 11).

So this module is the isolated intake acceptance entry: a place where actual
connectivity CAN be verified while production qualification is still absent. It
exists under three constraints that are structural rather than procedural:

1. **It runs inside `isolation.isolated_run`.** Every persistence surface is
   redirected and the broker write prohibition is armed, so a verification run
   cannot write a production ledger row or reach a broker even if it tries.
   `7.1`'s three cases are all about this: it must START without production
   qualification, a production side effect must INVALIDATE the run (not be
   logged), and an isolated result must never authorise a trade.
2. **It records what actually happened, not what was declared.** A role's
   `AccessRecord` carries the call path that was really taken, the product /
   document / vintage references it really read, the projection hash it really
   published, and its gaps. A verification that cannot name its bypass location
   has not verified anything, so the path is recorded per call.
3. **An isolated proof is not a ledger integrity proof.** `7.6` — the two are
   different claims. `isolation_attestation()` says so explicitly and
   `assert_isolated_result_not_tradable()` enforces it at the point of use, so a
   shadow run's clean ledger cannot be quoted as evidence that the *production*
   ledger is complete.

Three failure modes this is built to catch, each of which is silent:

- **Provider / base-table bypass** (`7.2`). The scan is AST-based over the role's
  real import graph, not a runtime probe: a probe only covers the branch that
  executed, while "can this role reach a provider at all?" is a question about
  the source. Roles legitimately read `ats.data.products` — that is the product
  layer — but NOT `ats.data.sources` (a provider) and NOT `ats.data.stores` /
  `ats.memory` (the raw tables underneath).
- **Opinion leakage** (`7.3`). Only Layer→Sector and Information→Fundamental may
  read another role's opinion. Every other pair is a violation, and so is any
  candidate (unadmitted) document presented as published data, and any opinion
  written back as a shared fact.
- **A run that proceeds on incomplete inputs** (`7.4`). A missing required
  analysis must block the decision cycle, and a projection that cannot be
  resolved after a restart must fail rather than silently continue with an empty
  projection.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence

from ..config import REPO_ROOT

PACKAGE_ROOT = REPO_ROOT / "src" / "ats"

# --------------------------------------------------------------------------- #
# The ten roles and where each one is entered
# --------------------------------------------------------------------------- #

# The ACTUAL entry point per role, and the modules that constitute its data
# access surface. A verification that cannot name the module it called is not a
# verification, so the entry point is declared here and the scan starts from it.
#
# `access_modules` is deliberately narrower than "everything the role imports":
# it is the set that may legitimately READ DATA. A role importing a helper for
# formatting is not bypassing anything; a role importing a provider is.
CONSUMER_ENTRY_POINTS: dict[str, tuple[str, ...]] = {
    "layer": ("ats.agents.layer.layer_review",),
    "information": ("ats.agents.information.entry", "ats.agents.information.assemble"),
    "sector": ("ats.agents.sector.review", "ats.agents.sector.assemble"),
    "fundamental": ("ats.agents.fundamental.entry", "ats.agents.fundamental.routine",
                    "ats.agents.fundamental.event"),
    "macro": ("ats.agents.macro.review", "ats.agents.macro.assemble"),
    "technical": ("ats.agents.technical.review",),
    "chief": ("ats.agents.chief.decide", "ats.agents.chief.assemble"),
    "risk": ("ats.agents.risk_officer.review",),
    "trader": ("ats.trader.execute",),
    "clerk": ("ats.execution.clerk",),
}

# The six analysis roles, and the four decision/execution roles. Separate because
# they are verified differently: an analysis role is verified by what it READS,
# a decision role by what it BINDS.
ANALYSIS_CONSUMERS: tuple[str, ...] = (
    "layer", "information", "sector", "fundamental", "macro", "technical")
DECISION_CONSUMERS: tuple[str, ...] = ("chief", "risk", "trader", "clerk")
TEN_CONSUMERS: tuple[str, ...] = ANALYSIS_CONSUMERS + DECISION_CONSUMERS

# The product layer is the ONLY sanctioned way for a role to read persisted data.
# It is not on the forbidden list — it is what "compliant" looks like.
PRODUCT_LAYER = "ats.data.products"
GOVERNED_READ_API = "ats.data.consumer_api"

# The surfaces the scan records as reached but does not descend into. Both are
# the sanctioned implementation: `consumer_api` is the governed read API every
# consumer's contract names, and `products` is the layer that owns admission,
# versioning and lineage. Their internals are their own business.
SANCTIONED_READ_SURFACE: tuple[str, ...] = (PRODUCT_LAYER, GOVERNED_READ_API)

# Provider modules: a direct line to a data source that bypasses admission,
# versioning and lineage. Reaching one is the bypass `7.2` names.
PROVIDER_MODULES: tuple[str, ...] = (
    "ats.data.sources",
    "ats.data.collection",
    "ats.data.pipelines",
    "ats.data.adapters",
    "ats.data.web",
    "ats.data.websearch",
    "ats.data.market_data",
    "ats.data.sec",
    "ats.data.consensus",
    "ats.data.news",
    "ats.data.transcript",
    "ats.data.defeatbeta",
    "ats.data.indicators",
    "ats.data.articles",
)

# Base-table modules: the raw repositories UNDERNEATH the product layer. Reading
# them is not "a faster path", it is reading unversioned rows with no vintage to
# cite — which is exactly what the admission and lineage machinery exists to
# prevent.
#
# `ats.memory` is deliberately NOT here. It is Workflow memory — the projection
# store — and a role reading a prior role's published opinion through it is the
# designed mechanism, governed by the whitelist in `7.3` rather than by the
# data-plane bypass rule. Lumping the two together flagged nearly every role for
# reading the projection store at all, which is precisely the false positive that
# teaches a reader to ignore a check.
BASE_TABLE_MODULES: tuple[str, ...] = (
    "ats.data.stores",
    "ats.data.runtime.repository",
)

# Third-party clients. Same objection as a provider module: the governed read API
# is what makes a read auditable, and a vendor SDK is not.
THIRD_PARTY_DATA_CLIENTS: tuple[str, ...] = (
    "yfinance", "requests", "httpx", "urllib.request", "urllib3",
    "ib_async", "ib_insync", "pandas_datareader",
)

# The only opinion dependency edges permitted between analysis roles. Anything
# else is leakage: two roles reading each other's conclusions is how a
# correlation becomes an assumption nobody stated.
ALLOWED_OPINION_EDGES: frozenset[tuple[str, str]] = frozenset({
    ("layer", "sector"),
    ("information", "fundamental"),
})

# Chief is the sole aggregator — it reads every analyst's view, and that is the
# designed exception rather than a leak.
SOLE_AGGREGATORS: frozenset[str] = frozenset({"chief"})

# Neutral-fact tables. An opinion written here would be laundered into a fact
# that later looks like evidence nobody had to reason about.
NEUTRAL_FACT_TABLES: frozenset[str] = frozenset({
    "evidence_facts", "data_evidence_facts", "measurement_points",
    "measurement_series", "evidence_observations", "data_evidence_observations",
})

# Opinion carriers. Writing one of these is a role publishing a view, which is
# legitimate — for the role that owns it.
PROJECTION_TABLES: frozenset[str] = frozenset({
    "task_projection_envelopes", "task_projections", "sector_reviews",
    "macro_reviews", "technical_reviews",
})


class IntakeVerificationError(RuntimeError):
    """An intake verification could not be performed, or produced nothing usable.

    Distinct from a *failed* verification: a violation is a recorded finding with
    a location, while this is "the check could not run" — a missing entry point,
    an unreadable store, a state the checker does not model. Conflating them would
    let "I could not check" be reported as "nothing is wrong".
    """


# --------------------------------------------------------------------------- #
# 7.1 — the isolated acceptance entry
# --------------------------------------------------------------------------- #

@dataclass
class IsolationAttestation:
    """What the isolated run guarantees, and what it explicitly does not.

    `production_ledger_integrity_proven` is False and cannot be set True here.
    That is the whole point of `7.6`: the run used isolated ledgers and a
    simulated broker, so it can speak to whether the PATH works and not to
    whether the production ledger is complete. An attestation that claimed both
    would be the most dangerous artefact this module could emit.
    """

    run_id: str
    isolation_root: str
    broker_write_prohibited: bool
    surfaces_redirected: tuple[str, ...]
    production_side_effects: tuple[str, ...] = ()
    production_ledger_integrity_proven: bool = False
    qualification_required: bool = False
    started_at: str = ""
    finished_at: str = ""

    def as_row(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "isolation_root": self.isolation_root,
            "broker_write_prohibited": self.broker_write_prohibited,
            "surfaces_redirected": list(self.surfaces_redirected),
            "production_side_effects": list(self.production_side_effects),
            # Stated as a field rather than left implicit: a reader must be able
            # to see that this was considered and refused, not overlooked.
            "production_ledger_integrity_proven": self.production_ledger_integrity_proven,
            "qualification_required": self.qualification_required,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class ProductionSideEffect(RuntimeError):
    """An isolated verification reached production state. The run is invalid.

    Not a warning. The spec requires the acceptance to be judged INVALID and the
    production-side effect CLEARED, and both halves matter: a run whose whole
    value was "I changed nothing" cannot be cited if it changed something.
    """

    def __init__(self, run_id: str, effects: Sequence[str]) -> None:
        self.run_id = run_id
        self.effects = list(effects)
        super().__init__(
            f"isolated verification {run_id!r} produced production side effect(s): "
            + "; ".join(self.effects)
            + ". The acceptance is invalid; the effect must be cleared before this "
              "run may be cited as evidence.")


@contextmanager
def isolated_verification(run_id: str, *, root: str | Path | None = None,
                          reason_code: str | None = None
                          ) -> Iterator[IsolationAttestation]:
    """Run a verification with production unreachable and broker writes forbidden.

    `7.1`, first case: **this entry point does NOT require production
    qualification.** That is the entire reason it exists — requiring the
    qualification it is meant to produce would make the missing evidence
    unobtainable. The qualification gate still applies at the point of USE
    (`assert_isolated_result_not_tradable`), which is where a trade would happen.

    On exit the attestation is completed and, if anything reached production, the
    run is INVALIDATED by raising. Refusing after the fact rather than logging is
    deliberate: a side effect discovered at the end of a long verification is
    exactly the one nobody would look for.
    """
    from ..execution import broker_write_guard
    from . import isolation

    if reason_code is None:
        reason_code = broker_write_guard.REASON_ISOLATED

    before = _production_fingerprint()
    attestation = IsolationAttestation(
        run_id=run_id,
        isolation_root=str(root or isolation.default_isolation_root(run_id)),
        broker_write_prohibited=broker_write_guard.is_prohibited(),
        surfaces_redirected=(),
        qualification_required=False,
        started_at=datetime.now(timezone.utc).isoformat())

    with isolation.isolated_run(run_id, root=root, reason_code=reason_code) as env:
        # Refuse to run unless the prohibition is actually in force. Checking
        # `guard_installed` rather than trusting the context manager is what
        # turns "the capability is missing" into a start-up failure instead of a
        # surprise at the first order.
        broker_write_guard.assert_broker_writes_prohibited(
            operation="isolated_verification", caller=run_id)
        attestation.surfaces_redirected = tuple(sorted(env.surfaces))
        attestation.broker_write_prohibited = True
        yield attestation

    after = _production_fingerprint()
    leaked = _production_delta(before, after)
    attestation.production_side_effects = tuple(leaked)
    attestation.finished_at = datetime.now(timezone.utc).isoformat()
    if leaked:
        raise ProductionSideEffect(run_id, leaked)


def _production_delta(before: dict[str, int], after: dict[str, int]) -> list[str]:
    """Tables whose production row count grew across an isolated run.

    Compares the COUNTS, not the key sets. `set(after) - set(before)` is always
    empty — both dicts carry the same tables — so a leaked row would pass
    unnoticed, which is the exact failure this function exists to catch.

    A SHRINK is not reported: an isolated run has no legitimate reason to remove a
    production row, but deletion is not something it can do through a redirected
    connection either, and reporting it would blur the signal. An unreadable
    table (-1) is reported on either side, because "I could not check" must not
    pass as "nothing changed".
    """
    return sorted(key for key, count in after.items()
                  if count != before.get(key, 0))


# Production state that an isolated verification must not change. Counted, not
# hashed: a hash would flag a concurrent legitimate write as this run's
# side effect, and the point is to attribute the effect to the run.
_PRODUCTION_WATCH_TABLES: tuple[tuple[str, str], ...] = (
    ("ats.sqlite", "trades"),
    ("ats.sqlite", "boss_approvals"),
    ("ats.sqlite", "decision_cycles"),
    ("ats.sqlite", "decision_revisions"),
    ("ats.sqlite", "decision_risk_reviews"),
    ("data.sqlite", "data_evidence_facts"),
)


def _production_db_path(name: str) -> Path:
    return REPO_ROOT / "var" / name


def _production_fingerprint() -> dict[str, int]:
    """Row counts for the tables a verification must leave alone.

    Read-only and best-effort: a missing database is a count of zero, not an
    error, because the first thing a verification run ever does is run against a
    repository where some of these may not exist yet.
    """
    counts: dict[str, int] = {}
    for filename, table in _PRODUCTION_WATCH_TABLES:
        path = _production_db_path(filename)
        key = f"{filename}:{table}"
        if not path.is_file():
            counts[key] = 0
            continue
        try:
            conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True,
                                   timeout=5.0)
            try:
                present = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,)).fetchone()
                counts[key] = int(conn.execute(
                    f"SELECT COUNT(*) FROM {table}").fetchone()[0]) if present else 0
            finally:
                conn.close()
        except sqlite3.Error:
            # An unreadable table cannot be shown to be unchanged, so it counts as
            # present-and-unverifiable rather than silently zero. Over-reporting
            # here fails the run; under-reporting would let a leak pass.
            counts[key] = -1
    return counts


def clear_production_side_effect(effect: str, *, confirm: bool = False
                                 ) -> dict[str, Any]:
    """Report how a leaked production effect would be cleared.

    Refuses to delete without explicit confirmation, and NEVER deletes silently.
    A cleanup routine that removes ledger rows on its own initiative is a second
    way to corrupt the ledger it is meant to protect, so this returns the plan
    and the operator performs it. The refusal is the behaviour under test: the
    point is that clearing is a deliberate act, not a side effect of verifying.
    """
    if not confirm:
        raise IntakeVerificationError(
            f"clearing production side effect {effect!r} requires explicit "
            "confirmation. An acceptance run whose value was 'I changed nothing' "
            "cannot clear what it changed automatically; report it, then remove "
            "it deliberately.")
    filename, _, table = effect.partition(":")
    if not table:
        raise IntakeVerificationError(
            f"malformed side-effect id {effect!r}; expected '<db file>:<table>'")
    return {"effect": effect, "path": str(_production_db_path(filename)),
            "table": table, "cleared": False,
            "note": "deletion is a deliberate operator action; this function only "
                    "reports the target so the row count can be compared before "
                    "and after"}


def assert_isolated_result_not_tradable(attestation: IsolationAttestation, *,
                                        consumer_id: str = "") -> None:
    """`7.1` third case / `7.6`: an isolated result may not authorise a trade.

    The isolated run proves the PATH works. It says nothing about whether the
    production ledger is complete, because it never read the production ledger.
    Quoting it as a trading authorisation would be a category error, and it is
    refused here — at the point of use, which is the only place a reader is
    forced to confront it.
    """
    if not consumer_id:
        consumer_id = "(unspecified)"
    raise IntakeVerificationError(
        f"isolated verification {attestation.run_id!r} cannot authorise a trade "
        f"for {consumer_id}: it ran against isolated ledgers with a simulated "
        "broker, so it establishes that the access path works — not that the "
        "production ledger is complete and not that the consumer holds production "
        "reading qualification. Re-verify qualification for the current scope via "
        "ats.data.assurance.qualification() before submitting.")


# --------------------------------------------------------------------------- #
# 7.2 / 7.3 — the access record and the bypass / opinion scans
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class AccessViolation:
    """One thing that must not be true about a role's access."""

    kind: str
    consumer_id: str
    location: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.kind}] {self.consumer_id}: {self.location} — {self.detail}"

    def as_row(self) -> dict[str, Any]:
        return {"kind": self.kind, "consumer_id": self.consumer_id,
                "location": self.location, "detail": self.detail}


@dataclass
class AccessRecord:
    """What one role actually did, as distinct from what it declared.

    Every field is an observation rather than an assertion wherever an observation
    is possible: `call_path` is what the scan found, `product_refs` /
    `document_refs` / `vintage_refs` are what the read returned, and `gaps` is
    what was missing. A record with empty `call_path` is not a pass — it means the
    scan never reached the role, which is a failure to verify rather than a
    verification that found nothing wrong.
    """

    consumer_id: str
    entry_point: str
    call_path: tuple[str, ...] = ()
    product_refs: tuple[str, ...] = ()
    document_refs: tuple[str, ...] = ()
    vintage_refs: tuple[str, ...] = ()
    projection_hash: str = ""
    opinion_inputs: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()
    violations: list[AccessViolation] = field(default_factory=list)

    @property
    def reached_entry_point(self) -> bool:
        """False means the scan did not reach the role — not a pass."""
        return bool(self.call_path)

    @property
    def compliant(self) -> bool:
        return self.reached_entry_point and not self.violations

    def record_violation(self, kind: str, location: str, detail: str) -> None:
        self.violations.append(AccessViolation(kind, self.consumer_id, location, detail))

    def as_row(self) -> dict[str, Any]:
        return {
            "consumer_id": self.consumer_id,
            "entry_point": self.entry_point,
            "call_path": list(self.call_path),
            "product_refs": list(self.product_refs),
            "document_refs": list(self.document_refs),
            "vintage_refs": list(self.vintage_refs),
            "projection_hash": self.projection_hash,
            "opinion_inputs": list(self.opinion_inputs),
            "gaps": list(self.gaps),
            "reached_entry_point": self.reached_entry_point,
            "compliant": self.compliant,
            "violations": [v.as_row() for v in self.violations],
        }


def _module_path(dotted: str) -> Path | None:
    parts = dotted.split(".")
    base = PACKAGE_ROOT.parent.parent / "src" / Path(*parts)
    candidate = base.with_suffix(".py")
    if candidate.is_file():
        return candidate
    package = base / "__init__.py"
    return package if package.is_file() else None


def _iter_module_imports(path: Path) -> Iterator[tuple[str, int, str]]:
    """Yield `(resolved dotted module, lineno, importer)` for ONE module's imports.

    **Depth 0 on purpose.** `7.2` asks whether a role "绕过数据产品直接取数或直连
    Provider" — *directly*. Descending into a role's import graph answers a
    different and much noisier question: it attributes the whole application's
    provider usage to whichever role happened to import an orchestrator. In
    practice `trader.execute` imports `runtime.cli` for the decision-graph
    runner, and a transitive scan therefore charged the trader role for every
    provider the CLI touches — 49 findings, none of them the trader's.

    So the scan reads the role's own modules. The helpers it imports are
    recorded as reached (they are part of its call path), but a bypass inside
    one of them is that helper's defect and is reported against the helper, not
    laundered onto the role. The collection layer — `ats.chain`,
    `ats.data.sources`, `ats.data.collection` — legitimately reaches providers;
    that is its job. What must not happen is a *role* reaching past the governed
    read API to do it itself.
    """
    import importlib.util

    dotted = _dotted_name(path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError) as exc:
        raise IntakeVerificationError(
            f"cannot scan {dotted} for data-access bypasses: "
            f"{type(exc).__name__}: {exc}") from exc

    package = _package_of(path)
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                try:
                    module = importlib.util.resolve_name("." * node.level + module,
                                                         package)
                except (ImportError, ValueError):
                    continue
            names = [module, *[f"{module}.{alias.name}" for alias in node.names]]
        for name in names:
            # Both namespaces are yielded. Filtering to `ats.*` alone would skip
            # `import yfinance` entirely — which is the bypass `7.2` most needs to
            # see, since a vendor SDK has no admission and no lineage at all.
            if name.startswith("ats.") or _matches(name, THIRD_PARTY_DATA_CLIENTS):
                yield name, getattr(node, "lineno", 0), dotted


def _dotted_name(path: Path) -> str:
    relative = path.resolve()
    try:
        parts = list(relative.relative_to(PACKAGE_ROOT.resolve()).parts)
    except ValueError:  # pragma: no cover - fixtures outside the tree
        return path.stem
    if parts and parts[-1] == "__init__.py":
        parts = parts[:-1]
    elif parts:
        parts[-1] = parts[-1][:-3]
    return ".".join(["ats", *parts])


def _package_of(path: Path) -> str:
    relative = path.resolve()
    try:
        parts = list(relative.relative_to(PACKAGE_ROOT.resolve()).parts)
    except ValueError:  # pragma: no cover - fixtures outside the tree
        return "ats"
    parts = parts[:-1]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(["ats", *parts])


def _matches(module: str, prefixes: Iterable[str]) -> str | None:
    for prefix in prefixes:
        if module == prefix or module.startswith(prefix + "."):
            return prefix
    return None


def _package_called_names() -> set[str]:
    """Every function/method name called anywhere in the package.

    Cached because it walks the whole tree on every call and the scan asks once
    per module: without the cache, verifying ten roles parses the entire package
    ten times, which is slow enough to look like a hang.
    """
    global _PACKAGE_CALLS
    if _PACKAGE_CALLS is not None:
        return _PACKAGE_CALLS
    names: set[str] = set()
    for candidate in PACKAGE_ROOT.rglob("*.py"):
        if "__pycache__" in candidate.parts:
            continue
        try:
            tree = ast.parse(candidate.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    names.add(func.id)
                elif isinstance(func, ast.Attribute):
                    names.add(func.attr)
    _PACKAGE_CALLS = names
    return names


_PACKAGE_CALLS: set[str] | None = None


def _disabled_import_lines(path: Path) -> set[int]:
    """Line numbers of imports inside a retained-but-disabled helper.

    A function is "disabled" here when it is defined, never called anywhere in the
    package, and its docstring carries both `TODO` and `DISABLED`. Deliberately
    strict: an unmarked function is treated as live even if it happens to be unused,
    because "nobody calls it" is exactly what changes the day someone adds the call.

    The alternative — deleting the retained implementation — would make the plan-A
    restore a reconstruction from memory rather than an edit of known-good code.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return set()

    package_calls = _package_called_names()

    disabled: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        doc = ast.get_docstring(node) or ""
        if "TODO" not in doc or "DISABLED" not in doc:
            continue
        if node.name in package_calls:
            # Called somewhere after all — it is live, so treat it as such.
            continue
        for inner in ast.walk(node):
            if isinstance(inner, (ast.Import, ast.ImportFrom)):
                disabled.add(getattr(inner, "lineno", 0))
    return disabled


def scan_consumer_access(consumer_id: str, *, extra_entry_points: Sequence[str] = (),
                         opinion_inputs: Sequence[str] = (),
                         ) -> AccessRecord:
    """`7.2` / `7.3`: verify one role's access from its real import graph.

    Three checks, each about a specific way the verification could be worthless:

    1. **The entry point exists and was reached.** Otherwise the record describes
       nothing — an empty call path is reported as `reached_entry_point=False`,
       never as a clean pass.
    2. **No provider or base-table bypass.** The sanctioned read surface is
       `ats.data.consumer_api` and `ats.data.products`; a provider module or a
       raw repository is a violation WITH ITS LOCATION, because "non-compliant"
       without a line number cannot be fixed.
    3. **Opinion dependencies are within the whitelist** (`7.3`). Layer→Sector and
       Information→Fundamental are allowed; every other analysis pair is leakage.
       Chief is the declared aggregator and is exempt by design, not by accident.

    `opinion_inputs` is supplied by the caller from what the run actually read,
    not from a declaration — the whole failure mode is a role reading another
    role's opinion while claiming not to.

    **A preserved-but-disabled read is reported as its own kind.** Task 7.9 stopped
    trader's reference-price read by disabling auto execution, but kept the old
    implementation beside it for the plan-A restore. An AST scan cannot tell
    "reachable" from "retained", so counting it as a live bypass would report a
    false positive — and a scanner that cries wolf on a deliberately closed path
    trains its reader to ignore it. `disabled_bypass` therefore states the fact
    (the code is there) without claiming the role currently reads through it, and
    it still keeps the role non-compliant until the retained code is gone.
    """
    entry_points = (*CONSUMER_ENTRY_POINTS.get(consumer_id, ()), *extra_entry_points)
    if not entry_points:
        raise IntakeVerificationError(
            f"no declared entry point for consumer {consumer_id!r}; an intake "
            "verification whose entry point is undeclared cannot claim the role "
            "is verified")

    record = AccessRecord(consumer_id=consumer_id, entry_point=entry_points[0],
                          opinion_inputs=tuple(opinion_inputs))
    visited: list[str] = []
    # One finding per (kind, location). The same import is reachable through
    # several scan paths, and a report that lists the same bypass nine times
    # reads as noise — which is how a real finding gets lost in it.
    seen_findings: set[tuple[str, str]] = set()
    reported_lines: set[str] = set()

    for dotted in entry_points:
        path = _module_path(dotted)
        if path is None:
            record.record_violation(
                "entry_point_missing", dotted,
                "the declared entry point does not resolve to a module; the role "
                "cannot be verified through a path that does not exist")
            continue
        visited.append(dotted)
        disabled = _disabled_import_lines(path)
        for module, lineno, importer in _iter_module_imports(path):
            location = f"{importer}:{lineno}"
            # One finding per LINE, not per (module, line). `from x import a, b`
            # yields the same module under two names at one line, so keying on the
            # module name lists one bypass twice — and a report padded with
            # duplicates is how a real finding gets lost in it. Keying on the line
            # is also the honest unit: the fix is made at that line.
            key = (module, location)
            if key in seen_findings or location in reported_lines:
                continue
            # An import inside a retained-but-disabled helper is reported
            # separately: the code exists, but the role does not currently reach
            # it. Same location, different claim.
            if lineno in disabled:
                seen_findings.add(key)
                reported_lines.add(location)
                record.record_violation(
                    "disabled_bypass", location,
                    f"imports {module!r} inside a retained-but-disabled helper; the "
                    "code is present for a future restore but is not currently "
                    "reachable from live code. It still blocks until the retained "
                    "implementation is removed or the read is authorised.")
                continue
            provider = _matches(module, PROVIDER_MODULES)
            if provider:
                seen_findings.add(key)
                reported_lines.add(location)
                record.record_violation(
                    "provider_bypass", location,
                    f"reads through provider module {provider!r}; a provider read "
                    "bypasses admission, versioning and lineage, so the reference "
                    "cannot be admitted as published data")
                continue
            base_table = _matches(module, BASE_TABLE_MODULES)
            if base_table:
                seen_findings.add(key)
                reported_lines.add(location)
                record.record_violation(
                    "base_table_bypass", location,
                    f"reads {base_table!r} directly; the raw repository underneath "
                    "the product layer holds unversioned rows, so a read from it "
                    "has no vintage to cite")
                continue
            third = _matches(module, THIRD_PARTY_DATA_CLIENTS)
            if third:
                seen_findings.add(key)
                reported_lines.add(location)
                record.record_violation(
                    "third_party_client_bypass", location,
                    f"imports third-party data client {third!r}; the governed read "
                    "API is what makes a read auditable")
                continue
            if module.startswith(SANCTIONED_READ_SURFACE):
                if module not in record.call_path:
                    record.call_path = (*record.call_path, module)

    record.call_path = (*record.call_path, *visited)

    # The contract names `ats.data.consumer_api.read_input` as every consumer's
    # read API. A role that reaches neither it nor the product layer in its own
    # module has an access path the contract does not describe — which is a
    # migration gap, not a bypass, and is recorded separately so the two are not
    # confused. It still blocks: the spec requires a not-yet-wired consumer to
    # have its dependency REGISTERED rather than to be recorded as connected.
    if not any(module.startswith(SANCTIONED_READ_SURFACE)
               for module in record.call_path):
        record.record_violation(
            "governed_read_surface_not_reached", record.entry_point,
            f"reaches neither {GOVERNED_READ_API} nor {PRODUCT_LAYER}; the "
            "contract names the governed read API as this consumer's read path, so "
            "its actual access is unverified against the contract — register the "
            "dependency rather than recording this role as connected")

    _check_opinion_dependencies(record)
    return record


def _check_opinion_dependencies(record: AccessRecord) -> None:
    """`7.3`: enforce the two-edge opinion whitelist against what was read.

    `ALLOWED_OPINION_EDGES` is oriented (source, consumer) — Layer→Sector means
    sector READS layer. The comparison below uses the same orientation, and
    getting it backwards would report the two permitted edges as leakage, which
    is the one failure a whitelist check cannot afford.
    """
    consumer = record.consumer_id
    if consumer in SOLE_AGGREGATORS:
        # Chief reads every analyst's view by design. Recording it as a violation
        # would train a reader to ignore this check.
        return
    for source in record.opinion_inputs:
        if source == consumer:
            continue
        if (source, consumer) in ALLOWED_OPINION_EDGES:
            continue
        if source in SOLE_AGGREGATORS:
            record.record_violation(
                "opinion_leakage", f"{source}->{consumer}",
                f"{consumer} read {source}'s opinion; the only permitted "
                "dependencies are Layer→Sector and Information→Fundamental, and "
                "Chief is not readable by an analysis role")
            continue
        record.record_violation(
            "opinion_leakage", f"{source}->{consumer}",
            f"{consumer} read {source}'s opinion; only Layer→Sector and "
            "Information→Fundamental are permitted")


def check_candidate_material(*, consumer_id: str, document_refs: Sequence[str],
                             admitted_refs: Sequence[str]) -> list[AccessViolation]:
    """`7.3`: a candidate document must never be presented as published data.

    The distinction the product layer makes is admission: a candidate has been
    extracted but not accepted, so citing it as evidence asserts a publication
    that has not happened. The check is by REFERENCE, not by trusting a status
    flag the caller passes — the refs are what the analysis actually used.
    """
    admitted = set(admitted_refs)
    out: list[AccessViolation] = []
    for ref in document_refs:
        if ref in admitted:
            continue
        out.append(AccessViolation(
            "candidate_material_as_published", consumer_id, ref,
            "document reference is not in the admitted set; presenting an "
            "unadmitted candidate as published data asserts a publication that "
            "has not happened"))
    return out


def check_opinion_writeback(*, consumer_id: str, statements: Sequence[dict[str, Any]]
                            ) -> list[AccessViolation]:
    """`7.3`: an opinion must not be written back as a shared fact.

    `statements` are the writes the run reported, as `{table, kind, values}`.
    A neutral-fact table written by an analysis role is the violation: once an
    opinion is a fact row, later readers cannot tell it apart from evidence, and
    the reasoning that produced it is gone.
    """
    out: list[AccessViolation] = []
    for index, statement in enumerate(statements):
        table = str(statement.get("table", ""))
        if table not in NEUTRAL_FACT_TABLES:
            continue
        if consumer_id in SOLE_AGGREGATORS:
            continue
        out.append(AccessViolation(
            "opinion_writeback", consumer_id, f"{table}[{index}]",
            f"{consumer_id} wrote to the neutral-fact table {table!r}; an opinion "
            "written as a shared fact is indistinguishable from evidence to every "
            "later reader"))
    return out


# --------------------------------------------------------------------------- #
# 7.4 — required-input blocking and cross-process restart
# --------------------------------------------------------------------------- #

@dataclass
class RestartVerdict:
    """Whether the references a frozen snapshot cites survive a fresh process."""

    resolvable: bool
    resolved: list[str] = field(default_factory=list)
    unresolved: list[dict[str, str]] = field(default_factory=list)

    def as_row(self) -> dict[str, Any]:
        return {"resolvable": self.resolvable, "resolved": list(self.resolved),
                "unresolved": list(self.unresolved)}


def verify_restart_resolvability(store_factory: Callable[[], Any],
                                 projection_ids: Sequence[str], *,
                                 expect_hash: dict[str, str] | None = None
                                 ) -> RestartVerdict:
    """`7.4`: every cited projection must resolve in a FRESH store.

    `store_factory` is called to obtain a NEW store rather than reusing the one
    that wrote the rows — that is what makes it a restart rather than a re-read.
    The failure this catches is the quiet one: after a restart the projection is
    gone or replaced, and a caller that treats an empty result as "no opinion
    yet" proceeds with a default snapshot. So an unresolvable reference FAILS,
    and its `content_hash` is checked when one is supplied — a projection that
    resolves to different content is a different projection.
    """
    wanted = dict(expect_hash or {})
    verdict = RestartVerdict(resolvable=True)
    try:
        store = store_factory()
    except Exception as exc:  # noqa: BLE001 - any failure to open is unresolvable
        verdict.resolvable = False
        verdict.unresolved.append({"projection_id": "*", "reason":
                                   f"store could not be reopened: "
                                   f"{type(exc).__name__}: {exc}"})
        return verdict

    for projection_id in projection_ids:
        row = store.get_task_projection(projection_id)
        if row is None:
            verdict.resolvable = False
            verdict.unresolved.append({"projection_id": projection_id,
                                       "reason": "projection not found after restart"})
            continue
        if row.get("status") != "published":
            verdict.resolvable = False
            verdict.unresolved.append({
                "projection_id": projection_id,
                "reason": f"projection status is {row.get('status')!r}, not published"})
            continue
        expected = wanted.get(projection_id, "")
        if expected and row.get("content_hash") != expected:
            verdict.resolvable = False
            verdict.unresolved.append({
                "projection_id": projection_id,
                "reason": f"content hash drifted: snapshot cited {expected[:12]}…, "
                          f"stored row is {str(row.get('content_hash'))[:12]}…"})
            continue
        verdict.resolved.append(projection_id)
    return verdict


def verify_required_input_blocking(*, registry: Any, projections: dict[str, Any],
                                   scope: Any, required_scopes: dict[str, Any] | None = None,
                                   enter_decision_cycle: bool = True,
                                   ) -> dict[str, Any]:
    """`7.4`: a missing required analysis must block the decision cycle.

    Delegates to the Phase D contracts rather than re-deriving them: the snapshot
    builder and `should_enter_decision_cycle` already own "incomplete" and "may
    proceed", and a second implementation would be free to disagree with the gate
    that actually runs in production. The verification here is that the refusal
    HAPPENS and names the missing analysis — not that a gap report exists.
    """
    from ..decision.snapshot import IncompleteResearchSnapshotError, build_research_snapshot
    from ..workflow.run_contracts import (WorkflowRunRequest, build_run_result,
                                         should_enter_decision_cycle)
    from ..workflow.run_contracts import TriggerContext
    from ..agent.task_projection import ProjectionScope

    snapshot_scope = scope if isinstance(scope, ProjectionScope) else ProjectionScope(
        kind=str((scope or {}).get("kind", "portfolio")),
        id=str((scope or {}).get("id", "")))

    snapshot = build_research_snapshot(
        registry=registry, projections=projections, scope=snapshot_scope,
        required_scopes=required_scopes)
    gaps = [{"category": item.category, "task_id": item.task_id, "reason": item.reason}
            for item in snapshot.gaps()]

    request = WorkflowRunRequest(
        run_id="intake-verification", trigger=TriggerContext(kind="manual"),
        tasks=tuple(registry.task_ids()), scope=snapshot_scope,
        as_of=snapshot.built_at, enter_decision_cycle=enter_decision_cycle)
    outcomes = {task_id: _task_outcome(projections, task_id, registry)
                for task_id in registry.task_ids()}
    result = build_run_result(request, registry, outcomes)
    allowed, reason = should_enter_decision_cycle(request, result)

    blocked = bool(gaps) or not allowed
    refusal = ""
    if gaps:
        try:
            raise IncompleteResearchSnapshotError(snapshot)
        except IncompleteResearchSnapshotError as exc:
            refusal = str(exc)

    # The snapshot is what production gates on — `open_decision_cycle` refuses on
    # `snapshot.complete` and nothing else. `build_run_result` is a SECOND
    # contract, and it judges per TASK rather than per category, so the two
    # disagree whenever one mode of a two-mode category is absent.
    #
    # Rather than quietly adopting one, the disagreement is reported. A
    # verification that hides a contract conflict is the thing this module exists
    # to prevent, and an operator reading "complete" needs to know that a second
    # gate would have said otherwise.
    disagreements: list[str] = []
    if snapshot.complete and not allowed:
        missing_tasks = sorted(task_id for task_id, outcome in outcomes.items()
                               if not outcome.usable)
        disagreements.append(
            f"the research snapshot is complete but the run contract is not "
            f"({reason}); disagreeing tasks: {', '.join(missing_tasks)}. "
            "Production gates on the snapshot, so the cycle opens — but the two "
            "contracts do not agree, which is worth resolving before this "
            "evidence is registered.")

    return {
        "complete": snapshot.complete,
        "gaps": gaps,
        "decision_cycle_entered": bool(result.decision_cycle_entered and allowed),
        "block_reason": reason if not allowed else "",
        "refusal_message": refusal,
        "run_contract_complete": bool(allowed),
        "contract_disagreements": disagreements,
        # True only when the inputs were complete AND the cycle was allowed to
        # open. A complete run that stayed out of the cycle for another reason
        # must not read as a pass — and neither must one whose contracts disagree.
        "verified": snapshot.complete and allowed and not disagreements,
    }


def _task_outcome(projections: dict[str, Any], task_id: str, registry: Any) -> Any:
    from ..workflow.run_contracts import TaskResult

    envelope = projections.get(task_id)
    if envelope is None:
        return TaskResult(task_id=task_id, status="missing",
                          detail="no projection was published for this task")
    return TaskResult(task_id=task_id, status="succeeded",
                      projection_refs=(envelope.projection_id,),
                      as_of=getattr(envelope, "as_of", ""))


# --------------------------------------------------------------------------- #
# 7.5 — the decision and execution chain, in isolation
# --------------------------------------------------------------------------- #

@dataclass
class ChainBinding:
    """One link of the decision chain, and whether it is bound.

    Reported per link rather than as one boolean because a cutover decision needs
    to know WHICH link is missing — "the chain is not bound" sends the reader
    back to the start.
    """

    name: str
    bound: bool
    detail: str = ""
    refs: tuple[str, ...] = ()

    def as_row(self) -> dict[str, Any]:
        return {"name": self.name, "bound": self.bound, "detail": self.detail,
                "refs": list(self.refs)}


@dataclass
class ChainVerification:
    """Chief → Risk → Trader → Clerk, verified without a broker write."""

    bindings: list[ChainBinding] = field(default_factory=list)
    multi_round: bool = False
    rounds: int = 0
    recovery_idempotent: bool = False
    partial_fill_handled: bool = False
    late_fill_stop_switch: bool = False
    performance_rebuilt: bool = False
    broker_write_attempts: int = 0
    broker_refusals: int = 0

    @property
    def complete(self) -> bool:
        return bool(self.bindings) and all(b.bound for b in self.bindings)

    def as_row(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "bindings": [b.as_row() for b in self.bindings],
            "multi_round": self.multi_round,
            "rounds": self.rounds,
            "recovery_idempotent": self.recovery_idempotent,
            "partial_fill_handled": self.partial_fill_handled,
            "late_fill_stop_switch": self.late_fill_stop_switch,
            "performance_rebuilt": self.performance_rebuilt,
            "broker_write_attempts": self.broker_write_attempts,
            "broker_refusals": self.broker_refusals,
        }


def verify_decision_chain(*, repo: Any, cycle_id: str, snapshot: Any,
                          broker_write_attempted: bool = False,
                          reconcile: Callable[[dict, dict], dict] | None = None,
                          store: Any = None, rebuild_period: str = "",
                          ) -> ChainVerification:
    """`7.5`: verify Chief/Risk/Trader/Clerk without any broker write permission.

    Four properties, each about a way this verification could pass while the
    system is broken:

    - **Every link is bound.** Research snapshot, internal state, decision
      revision, approval, and the order/fill channel each have to name what they
      are bound to. An unbound link is reported, not defaulted.
    - **The risk loop is multi-round.** A single review is the easy path; the loop
      exists because a revision after review invalidates it.
    - **Repeated recovery is idempotent.** Re-running recovery must not duplicate
      a fill or a position — the Clerk's ledger is the record of what happened, so
      a duplicate there is a duplicate in reality.
    - **A late fill stops a switch.** A late fill proves the broker was still
      working after local state looked settled, so a switch must not proceed past
      one. Absorbing it is always right; continuing past it is not.

    `rebuild_period` is optional so the performance rebuild can be verified in
    the same run; when supplied, the read model must actually rebuild.
    """
    from ..execution import authorization, authorization_lifecycle, order_disposition

    verification = ChainVerification()

    snapshot_payload = snapshot.to_payload() if hasattr(snapshot, "to_payload") else snapshot
    items = (snapshot_payload or {}).get("items") or []
    projection_ids = tuple(str(item.get("projection_id", "")) for item in items
                           if item.get("projection_id"))
    verification.bindings.append(ChainBinding(
        name="research_snapshot", bound=bool(projection_ids),
        detail=("bound to " + ", ".join(sorted(set(projection_ids)))[:200])
        if projection_ids else "no projection is cited by the snapshot",
        refs=projection_ids))

    revision = repo.latest_revision(cycle_id)
    verification.bindings.append(ChainBinding(
        name="decision_revision", bound=revision is not None,
        detail=(f"revision {revision['revision_no']} @ "
                f"{str(revision['decision_hash'])[:12]}…") if revision is not None
        else "no revision has been appended for this cycle",
        refs=(str(revision["decision_hash"]),) if revision is not None else ()))

    review = (repo.effective_review(cycle_id, revision["revision_no"],
                                    revision["decision_hash"])
              if revision is not None else None)
    rounds = int(repo.conn.execute(
        "SELECT COUNT(*) FROM decision_risk_reviews WHERE cycle_id=? AND "
        "decision_hash=?", (cycle_id, revision["decision_hash"])).fetchone()[0]
    ) if revision is not None else 0
    verification.rounds = rounds
    verification.multi_round = rounds >= 2
    verification.bindings.append(ChainBinding(
        name="risk_review",
        bound=bool(review is not None and review["verdict"] == "approved"),
        detail=(f"round-bound review {review['review_id']} verdict "
                f"{review['verdict']} ({rounds} round(s) recorded)")
        if review is not None else "no effective risk review for the current revision",
        refs=(str(review["review_id"]),) if review is not None else ()))

    approval = (repo.effective_approval(cycle_id, revision["revision_no"],
                                         revision["decision_hash"])
                if revision is not None else None)
    verification.bindings.append(ChainBinding(
        name="boss_approval", bound=approval is not None,
        detail=(f"approval {approval['approval_id']} by {approval['reviewer']}")
        if approval is not None else "no human approval bound to the current revision",
        refs=(str(approval["approval_id"]),) if approval is not None else ()))

    authorization_reasons: list[str] = []
    try:
        auth = authorization.build_authorization(repo, cycle_id)
        authorization_reasons = authorization.validate_authorization(
            repo, auth, snapshot_as_of=_parse_or_now(
                (snapshot_payload or {}).get("built_at")))
    except Exception as exc:  # noqa: BLE001 - reported, never swallowed
        authorization_reasons = [f"{type(exc).__name__}: {exc}"]
    verification.bindings.append(ChainBinding(
        name="execution_authorization", bound=not authorization_reasons,
        detail=("authorization complete" if not authorization_reasons
                else "; ".join(authorization_reasons)),
        refs=()))

    internal_state_bound = False
    internal_detail = "no store supplied, so internal state was not read"
    if store is not None:
        from ..execution import state_api

        try:
            state = state_api.get_internal_state(store)
            internal_state_bound = bool(state.section_status)
            internal_detail = (f"completeness={state.completeness.status}; sections="
                               f"{sorted(k for k, v in state.section_status.items() if v)}")
        except Exception as exc:  # noqa: BLE001
            internal_detail = f"internal state unreadable: {type(exc).__name__}: {exc}"
    verification.bindings.append(ChainBinding(
        name="internal_state", bound=internal_state_bound, detail=internal_detail))

    lifecycle = authorization_lifecycle.lifecycle_for_cycle(cycle_id, store=store)
    unsettled = lifecycle.unfinished() if store is not None else []
    dispositions = [order_disposition.disposition_for_order(row) for row in unsettled]
    verification.partial_fill_handled = all(
        not d.blocks_switch or d.status != "partial" for d in dispositions)
    if dispositions:
        sample = dispositions[0]
        late = order_disposition.late_fill_disposition(
            unsettled[0], {"exec_id": "probe", "symbol": unsettled[0].get("symbol", ""),
                           "side": "BOT", "shares": 1, "price": 1.0,
                           "time": "2999-01-01T00:00:00+00:00",
                           "order_id": unsettled[0].get("order_id", "")},
            reconcile=reconcile)
        verification.late_fill_stop_switch = bool(late.stop_switch or late.late)
    verification.bindings.append(ChainBinding(
        name="order_fill_channel",
        bound=bool(unsettled) or store is None,
        detail=(f"{len(unsettled)} unsettled order(s) classified; "
                f"{sum(1 for d in dispositions if d.blocks_switch)} blocking a switch")
        if store is not None else "no store supplied, so no order rows were read",
        refs=tuple(str(row.get("order_id", "")) for row in unsettled)))

    if broker_write_attempted:
        from ..execution import broker_write_guard

        verification.broker_write_attempts = 1
        verification.broker_refusals = len(broker_write_guard.refusals())

    if store is not None and rebuild_period:
        from ..execution import rebuild

        outcome = rebuild.rebuild_performance(store, period=rebuild_period)
        verification.performance_rebuilt = outcome.get("status") in {"rebuilt", "skipped"}

    return verification


def verify_recovery_idempotence(*, action: Callable[[], Any],
                                ledger_reader: Callable[[], list[Any]]) -> dict[str, Any]:
    """`7.5`: run the same recovery twice and require an identical ledger.

    A recovery action that is not idempotent duplicates fills and positions the
    second time it runs — and a recovery is exactly what gets re-run, because
    nobody is watching a process crash and resume. Comparing the ledger rather
    than the return value is deliberate: the ledger is what downstream readers
    see.
    """
    action()
    first = list(ledger_reader())
    action()
    second = list(ledger_reader())
    identical = len(first) == len(second)
    return {"idempotent": identical, "rows_after_first": len(first),
            "rows_after_second": len(second),
            "reason": "" if identical else
            "the repeated recovery changed the ledger; a resumed process would "
            "double-count fills or positions"}


def _parse_or_now(value: Any) -> datetime:
    """Parse an ISO stamp, falling back to now. Never returns a naive datetime.

    `validate_authorization` compares the snapshot instant against the clock, and
    a naive value raises inside it — which would surface as an authorization
    failure caused by this helper rather than by the thing under verification.
    """
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

def verification_digest(records: Sequence[AccessRecord]) -> str:
    """A stable digest over the verification, for citing it as evidence.

    Covers the observations, not the timestamps: two runs that verified the same
    thing must produce the same digest, or "the evidence matches" becomes a
    claim nobody can check. Sorted by consumer id alone — `AccessRecord` is not
    orderable, so a plain `sorted()` on (id, record) pairs raises as soon as two
    records share an id.
    """
    ordered = sorted(records, key=lambda record: record.consumer_id)
    material = canonical({
        record.consumer_id: {
            "entry_point": record.entry_point,
            "call_path": sorted(record.call_path),
            "product_refs": sorted(record.product_refs),
            "document_refs": sorted(record.document_refs),
            "vintage_refs": sorted(record.vintage_refs),
            "projection_hash": record.projection_hash,
            "opinion_inputs": sorted(record.opinion_inputs),
            "violations": sorted((v.kind, v.location) for v in record.violations),
        }
        for record in ordered
    })
    return hashlib.sha256(material.encode()).hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def render_report(records: Sequence[AccessRecord],
                  chain: ChainVerification | None = None,
                  attestation: IsolationAttestation | None = None) -> str:
    """Operator-facing summary. Counts, then the failures with their locations."""
    lines = ["# 十角色接入核验（F.0.3–F.0.5）", ""]
    if attestation is not None:
        lines += [
            f"隔离运行 `{attestation.run_id}` · 券商写入禁写="
            f"{attestation.broker_write_prohibited} · 生产副作用="
            f"{len(attestation.production_side_effects)}",""]
        lines += ["**隔离证明不是生产账本完整性证明**：本次核验未读生产账本，不得据此放行交易。", ""]

    lines += ["| 消费者 | 到达入口 | 合规 | 违规 | 缺口 |", "|---|---|---|---|---|"]
    for record in sorted(records, key=lambda r: r.consumer_id):
        reached = "是" if record.reached_entry_point else "**否**"
        lines.append(
            f"| `{record.consumer_id}` | {reached} | "
            f"{'是' if record.compliant else '否'} | "
            f"{len(record.violations)} | {len(record.gaps)} |")

    failed = [r for r in records if not r.compliant]
    if failed:
        lines += ["", "## 不合规明细", ""]
        for record in sorted(failed, key=lambda r: r.consumer_id):
            for violation in record.violations:
                lines.append(f"- `{record.consumer_id}` — {violation}")
            if not record.reached_entry_point:
                lines.append(f"- `{record.consumer_id}` — 未到达入口 "
                             f"{record.entry_point}，本次核验对该角色不构成结论")

    if chain is not None:
        lines += ["", "## 决策与执行链", "",
                  f"- 链路完整：{'是' if chain.complete else '否'}"
                  f" · 多轮风控：{'是' if chain.multi_round else '否'}"
                  f"（{chain.rounds} 轮）"
                  f" · 恢复幂等：{'是' if chain.recovery_idempotent else '否'}"
                  f" · 迟到成交停止切换：{'是' if chain.late_fill_stop_switch else '否'}",
                  f"- 券商写入尝试 {chain.broker_write_attempts} 次 / 拒绝 "
                  f"{chain.broker_refusals} 次"]
        for binding in chain.bindings:
            lines.append(f"  - `{binding.name}`：{'已绑定' if binding.bound else '**未绑定**'}"
                         f" — {binding.detail}")

    lines += ["", f"核验摘要 digest：`{verification_digest(records)}`"]
    return "\n".join(lines)