"""Isolated run environment (Phase F task 1.2).

A shadow or isolated run must be unable to affect production state, and the
guarantee has to be structural rather than procedural: "remember to point the
DBs at temp files" is a rule that survives exactly until the run that matters.
So isolation here is two independent mechanisms, and either one alone is
insufficient:

1. **Path redirection.** Every persistence surface the run can reach is rebound
   under one root: Workflow memory, the data-layer database and its artifact
   root, LangGraph checkpoints, the workflow/trigger store and the document
   root. A run started this way has no path to production data even if it
   decides to read.
2. **Write prohibition.** The broker write guard is installed at the same time.
   Redirecting the ledgers does not stop a real order from reaching a broker —
   the broker is not a database.

Both are installed together, and `isolation_active()` only reports true once
both hold. A caller that redirects paths without arming the prohibition has an
isolated ledger and a live broker, which is the worst of the two.

The module deliberately does not monkeypatch global state permanently: the
context manager restores whatever it found, so a test or a one-shot CLI can
enter and leave without leaking isolation into the rest of the process.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from ..config import REPO_ROOT
from ..execution import broker_write_guard

# Every env var that selects a persistence surface. A surface missing from this
# table is a surface an isolated run can still write to, so the set is asserted
# against the codebase by `test_isolation_covers_every_known_persistence_env`
# rather than trusted to be complete.
PERSISTENCE_ENV_VARS: tuple[str, ...] = (
    "ATS_DB_PATH",                    # Workflow memory (ats.memory) + workflow store
    "ATS_DATA_DB_PATH",               # data-layer platform database
    "ATS_DATA_ARTIFACT_ROOT",         # data-layer artifacts
    "ATS_STRUCTURED_DB_PATH",         # structured repository (governed observations)
    "ATS_STRUCTURED_ARTIFACT_ROOT",   # structured artifact store
    "ATS_PERSISTENT_QUEUE_PATH",      # persistent ingestion queue
    "ATS_CHECKPOINT_DB",              # LangGraph checkpoints
    "ATS_SHADOW_DB_PATH",             # Phase E shadow workflow/trigger store
    "ATS_SHADOW_REPORT_DB",           # Phase F shadow comparison reports
    "ATS_SHADOW_ORDER_DB",            # Phase F shadow order intents
    # Phase F dispatch claims. Shared owner state and per-trigger claims decide who
    # may execute, so an isolated run that reused the production file would be
    # governed by — and would write into — the production dispatch ledger.
    "ATS_DISPATCH_STATE_PATH",
    # Phase F cutover control plane. Boundary routes and activations live here, so
    # an isolated run that reused the production file would be governed by — and
    # would write into — the production routing state.
    "ATS_CUTOVER_DB",
    # Phase F batch manifest (F.0.6). The dry-run ledger is the record of which
    # batches were judged ready and which were blocked; a verification run that
    # appended into the production one would forge that history.
    "ATS_CUTOVER_BATCH_DB",
    "ATS_ROUTE_REGISTRY_PATH",        # Phase F active trade route + generation
    "ATS_DOCS_ROOT",                  # document assets
)

# Env vars that look like paths but are NOT isolation surfaces. Declared with a
# reason so the coverage test can require an explicit decision for each one
# instead of either silently ignoring them or over-isolating them.
NON_ISOLATED_ENV: dict[str, str] = {
    "ATS_CONFIG_DIR": "configuration must be IDENTICAL between the run and "
                      "production, otherwise the comparison is not a comparison",
    "ATS_TESSERACT_PATH": "path to an external binary, not a data surface",
    "ATS_RAMP_OFFICIAL_EXPORT_DIR": "read-only third-party export directory",
    "ATS_DOCS_ROOT_PROBE": "reserved",
}

# Relpath inside the isolation root for each surface. Workflow memory and the
# data layer stay separate files because a run that mixes neutral facts with
# opinions into one file would defeat the ownership split the cutover enforces.
_SURFACE_FILES: dict[str, str] = {
    "ATS_DB_PATH": "memory.sqlite",
    "ATS_DATA_DB_PATH": "data.sqlite",
    "ATS_DATA_ARTIFACT_ROOT": "data_artifacts",
    "ATS_STRUCTURED_DB_PATH": "structured.sqlite",
    "ATS_STRUCTURED_ARTIFACT_ROOT": "structured_artifacts",
    "ATS_PERSISTENT_QUEUE_PATH": "ingestion_queue.sqlite",
    "ATS_CHECKPOINT_DB": "checkpoints.sqlite",
    "ATS_SHADOW_DB_PATH": "workflow.sqlite",
    # Shadow surfaces are separate FILES, not relabelled uses of an existing one:
    # a shadow report must not be able to read a production report, and a shadow
    # order intent must not appear in a production ledger.
    "ATS_SHADOW_REPORT_DB": "shadow_reports.sqlite",
    "ATS_SHADOW_ORDER_DB": "shadow_orders.sqlite",
    "ATS_DISPATCH_STATE_PATH": "dispatch.sqlite",
    "ATS_CUTOVER_DB": "cutover.sqlite",
    "ATS_CUTOVER_BATCH_DB": "cutover_batches.sqlite",
    "ATS_ROUTE_REGISTRY_PATH": "phase_f_routes.sqlite",
    "ATS_DOCS_ROOT": "docs",
}


@dataclass(frozen=True)
class IsolatedEnvironment:
    """The paths one isolated run owns. Nothing outside `root` is writable."""

    root: Path
    surfaces: dict[str, str] = field(default_factory=dict)

    def path_for(self, env_var: str) -> Path:
        return Path(self.surfaces[env_var])

    def as_rows(self) -> list[dict[str, Any]]:
        return [{"env_var": var, "path": path}
                for var, path in sorted(self.surfaces.items())]


def default_isolation_root(run_id: str) -> Path:
    """Where an isolated run's files live.

    Under `var/isolated/` rather than a system temp dir: a shadow run's evidence
    is meant to be read back during acceptance, and a temp directory is
    reclaimed on reboot.
    """
    return REPO_ROOT / "var" / "isolated" / run_id


def build_environment(root: str | Path) -> IsolatedEnvironment:
    """Resolve every persistence surface under `root` without touching os.environ."""
    base = Path(root).expanduser()
    if not base.is_absolute():
        base = REPO_ROOT / base
    base = base.resolve()
    surfaces = {var: str(base / rel) for var, rel in _SURFACE_FILES.items()}
    return IsolatedEnvironment(root=base, surfaces=surfaces)


def _reset_caches() -> None:
    """Drop process-cached connections so the new paths take effect.

    Every store caches a connection per process; without this the redirection
    would install cleanly and then be ignored by an already-open handle — the
    most dangerous shape of a silent failure, because the run reports the
    redirected path while reading production.
    """
    from ..memory import reset_store_cache

    reset_store_cache()
    for module_name, attr in (
        ("ats.data.structured", "reset_repository_cache"),
        ("ats.data.stores.structured.repository", "reset_repository_cache"),
    ):
        try:
            module = __import__(module_name, fromlist=[attr])
            getattr(module, attr)()
        except (ImportError, AttributeError):  # pragma: no cover - optional layers
            continue


# The root of the isolation currently installed in this process, or None. Kept
# rather than re-derived from the default path: callers may pass an explicit
# root, and a probe that assumed `var/isolated` would report "not isolated" for
# a correctly installed run using a different root.
_ACTIVE_ROOT: Path | None = None


def active_isolation_root() -> Path | None:
    return _ACTIVE_ROOT


def verified_isolation_root() -> Path | None:
    """Verify the whole redirection in parents and inherited child environments."""
    if not broker_write_guard.is_prohibited() or not os.environ.get("ATS_DB_PATH"):
        return None
    root = (_ACTIVE_ROOT or Path(os.environ["ATS_DB_PATH"]).resolve().parent).resolve()
    expected = build_environment(root)
    if any(os.environ.get(name) != filename for name, filename in expected.surfaces.items()):
        return None
    return root


def isolation_active(env: dict[str, str] | None = None) -> bool:
    """True only when every surface is redirected AND writes are prohibited.

    Both halves are required. Reporting "isolated" for a half-installed
    environment is how a run ends up writing orders while believing it is a
    shadow.
    """
    if not broker_write_guard.is_prohibited():
        return False
    if _ACTIVE_ROOT is not None:
        return True
    # No active context: fall back to checking that every surface is redirected
    # somewhere other than its production location.
    environ = os.environ if env is None else env
    return all(environ.get(var, "") not in ("", str(REPO_ROOT / "var" / "ats.sqlite"))
               for var in PERSISTENCE_ENV_VARS)


def _assert_distinct_destinations(environment):
    """Reject a root that aliases production before creating any store."""
    if verified_isolation_root() is not None:
        return  # Nested verified environments already own their destinations.
    defaults = build_environment(REPO_ROOT / "var").surfaces
    defaults["ATS_DB_PATH"] = str(REPO_ROOT / "var/ats.sqlite")
    defaults["ATS_DOCS_ROOT"] = str(REPO_ROOT / "docs")
    directories = {"ATS_DOCS_ROOT", "ATS_DATA_ARTIFACT_ROOT", "ATS_STRUCTURED_ARTIFACT_ROOT"}
    for name, destination in environment.surfaces.items():
        target = Path(destination).resolve()
        for original in (os.environ.get(name), defaults.get(name)):
            if not original:
                continue
            source = Path(original).resolve()
            if (source == target or (source.exists() and target.exists() and source.samefile(target))
                    or (name in directories and (source.is_relative_to(target) or target.is_relative_to(source)))):
                raise PermissionError(f"isolation_destination_aliases_production:{name}")


@contextmanager
def inspect_isolated_records(root):
    """Bind an existing evidence side for readonly SQL assertions only.

    Unlike isolated_run, this does not bootstrap, create directories, open a
    TradingMemory, or initialize authority. The report verifier owns its readonly
    connections. Keep the broker prohibition even during inspection and restore
    the caller's environment/capability on every exit.
    """
    environment = build_environment(root)
    _assert_distinct_destinations(environment)
    if not environment.root.is_dir():
        raise ValueError("isolated evidence root missing")
    previous = {name: os.environ.get(name) for name in (*PERSISTENCE_ENV_VARS,
                "ATS_RUN_MODE", "ATS_BROKER_WRITE_PROHIBITION")}
    state = broker_write_guard._STATE
    grant, mode, reason = state.grant, state.mode, state.reason_code
    global _ACTIVE_ROOT
    active = _ACTIVE_ROOT
    try:
        for name,path in environment.surfaces.items():
            os.environ[name] = path
        os.environ["ATS_RUN_MODE"] = "isolated"
        os.environ["ATS_BROKER_WRITE_PROHIBITION"] = "1"
        broker_write_guard.prohibit_broker_writes(reason_code=broker_write_guard.REASON_ISOLATED)
        _ACTIVE_ROOT = environment.root
        yield environment
    finally:
        for name,value in previous.items():
            if value is None:
                os.environ.pop(name,None)
            else:
                os.environ[name] = value
        state.grant, state.mode, state.reason_code = grant, mode, reason
        _ACTIVE_ROOT = active


@contextmanager
def isolated_run(run_id: str, *, root: str | Path | None = None,
                 reason_code: str = broker_write_guard.REASON_ISOLATED,
                 ) -> Iterator[IsolatedEnvironment]:
    """Run a block with production data unreachable and broker writes forbidden.

    Restores the previous environment and guard state on exit, including on
    exception — an isolation leak that survives the run that caused it would be
    invisible until something much later wrote to the wrong database.
    """
    environment = build_environment(root or default_isolation_root(run_id))
    _assert_distinct_destinations(environment)
    environment.root.mkdir(parents=True, exist_ok=True)
    for path in environment.surfaces.values():
        Path(path).parent.mkdir(parents=True, exist_ok=True)

    previous_env = {var: os.environ.get(var) for var in PERSISTENCE_ENV_VARS}
    previous_run_mode = os.environ.get("ATS_RUN_MODE")
    previous_prohibition = os.environ.get("ATS_BROKER_WRITE_PROHIBITION")
    previous_grant = broker_write_guard._STATE.grant
    previous_mode = broker_write_guard._STATE.mode
    previous_reason = broker_write_guard._STATE.reason_code
    global _ACTIVE_ROOT
    previous_root = _ACTIVE_ROOT
    try:
        for var, path in environment.surfaces.items():
            os.environ[var] = path
        os.environ["ATS_RUN_MODE"] = "isolated"
        os.environ["ATS_BROKER_WRITE_PROHIBITION"] = "1"
        broker_write_guard.prohibit_broker_writes(reason_code=reason_code)
        _reset_caches()
        # Prove the capability rather than assume it: a run that started without
        # the prohibition must fail here, not at the first order.
        broker_write_guard.assert_broker_writes_prohibited(
            operation="isolated_run", caller=run_id)
        _ACTIVE_ROOT = environment.root
        # A fresh isolated authority may start at the safe legacy/disabled
        # defaults. This never initializes the production control database.
        from .cutover import bootstrap

        bootstrap(actor=run_id, path=environment.path_for("ATS_CUTOVER_DB"))
        from .schedule_runtime import initialize

        initialize()
        yield environment
    finally:
        for var, value in previous_env.items():
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value
        for var, value in (("ATS_RUN_MODE", previous_run_mode),
                           ("ATS_BROKER_WRITE_PROHIBITION", previous_prohibition)):
            if value is None:
                os.environ.pop(var, None)
            else:
                os.environ[var] = value
        broker_write_guard._STATE.grant = previous_grant
        broker_write_guard._STATE.mode = previous_mode
        broker_write_guard._STATE.reason_code = previous_reason
        _ACTIVE_ROOT = previous_root
        _reset_caches()
