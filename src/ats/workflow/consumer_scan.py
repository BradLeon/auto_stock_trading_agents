"""Consumer access scan: what each of the ten roles can actually reach (r5 task 8.1).

Moved out of `intake_verification` so that `ats retirement zero/decide` — which
uses this scan to judge whether a legacy path still has a consumer — no longer
depends on the Phase F intake tooling it will later retire. The scan itself is
unchanged: an AST walk over each role's declared entry points, recording the
provider, base-table, third-party and runtime-source imports it reaches and the
opinion edges it reads, each violation with a location a reader can act on.

`intake_verification` re-exports these names until it is removed (task 8.7).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

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
    # `inputs` is where the governed target path reads company data and `graph.pead` is the PEAD graph that calls it
    # (and still holds the route-gated legacy fallbacks). Scanning only the three entry modules never reached either,
    # so the Fundamental consumer looked as if it read nothing governed at all (found 2026-10-17, task 12.1).
    "fundamental": ("ats.agents.fundamental.entry", "ats.agents.fundamental.routine",
                    "ats.agents.fundamental.event", "ats.agents.fundamental.inputs", "ats.graph.pead"),
    "macro": ("ats.agents.macro.review", "ats.agents.macro.assemble"),
    "technical": ("ats.agents.technical.review",),
    "chief": ("ats.agents.chief.decide", "ats.agents.chief.assemble"),
    # Risk's reads are made where the review is computed, not only in the memo module: the order review
    # (`risk.checks`), the six-layer engine (`risk.assess`), the daily report and its renderers, and the
    # role's own read module (`risk.inputs`). Scanning only the memo module let a review that read the
    # broker and the providers directly pass as unscanned (switch-trade r5 task 6.4, 2026-11-07).
    "risk": ("ats.agents.risk_officer.review", "ats.agents.risk_officer.daily", "ats.risk.assess",
             "ats.risk.checks", "ats.risk.exposure", "ats.risk.report", "ats.risk.inputs"),
    "trader": ("ats.trader.execute",),
    # The broker reads Clerk makes are in its read adapter, so the adapter is scanned with the run.
    "clerk": ("ats.execution.clerk", "ats.data.runtime.clerk_reads"),
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

# Runtime and internal sources a DECISION role must read through the governed API, not directly: the
# broker gateway, the runtime market/option readers, the beta cache, the account snapshot helper and the
# ledger's internal-state module. Each sits underneath a governed product (BROKER_STATE, PORTFOLIO_DATA,
# MARKET_DATA, HISTORY_DATA); importing it directly skips the contract, the scope check, replay freezing
# and the audited read. Analysis roles are not checked against this list — their direct-read rule is the
# provider list above.
DECISION_RUNTIME_MODULES: tuple[str, ...] = (
    "ats.data.fundamentals",
    "ats.data.sector_snapshot",
    "ats.data.options",
    "ats.data.runtime.broker",
    "ats.data.runtime.market_data",
    "ats.data.runtime.options",
    "ats.trader.portfolio",
    "ats.execution.state_api",
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


class ConsumerScanError(RuntimeError):
    """A consumer scan (or a check built on it) could not be performed, or produced nothing usable.

    Distinct from a *failed* verification: a violation is a recorded finding with
    a location, while this is "the check could not run" — a missing entry point,
    an unreadable store, a state the checker does not model. Conflating them would
    let "I could not check" be reported as "nothing is wrong".
    """



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
        raise ConsumerScanError(
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
        raise ConsumerScanError(
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
            runtime = (_matches(module, DECISION_RUNTIME_MODULES)
                       if consumer_id in DECISION_CONSUMERS else None)
            if runtime:
                seen_findings.add(key)
                reported_lines.add(location)
                record.record_violation(
                    "runtime_bypass", location,
                    f"reads {runtime!r} directly; a decision role reads runtime and internal "
                    "state through the governed read API, which checks the contract and the "
                    "business scope, freezes the input for replay and audits the read")
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
