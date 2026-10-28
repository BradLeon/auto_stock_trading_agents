"""The evidence-fingerprint surface, and what it forbids Phase F to touch.

Every recorded qualification is bound to the content hash of the files that
define the contract: `required_fingerprint_paths` for all ten consumers, plus
`consumer_fingerprint_paths` for the four internal-state ones. Editing any of
them after evidence is recorded makes that evidence `manifest_drift` /
`dependency_drift`, and the consumer silently becomes ineligible again.

This matters more during cutover than during normal operation, because Phase F
*has* to change some of these files — the authorization gate gains a generation
binding, for instance, and `authorization.py` is a fingerprint path for `trader`
and `clerk`. The naive order (change code, register evidence, discover the drift,
re-register) makes every batch invalidate the previous one, which is how
"evidence" degrades into a treadmill.

So the surface is made explicit here, and the rule follows from it: **runtime
cutover changes routes, never the contract.** Route changes go through the
release overlay (`var/structured_data/releases.yaml`) and this module's own
state; the fingerprint paths change only before evidence is recorded, and doing
so obliges a re-verification of every affected consumer.

Two failure modes this exists to prevent, both of which are quiet:
- reading `config/data/structured.yaml` to "just flip one consumer to platform",
  which invalidates the other nine consumers that share the file;
- bumping the manifest to make a new evidence type legal, which invalidates
  every previously recorded row at once.

The surface has two tiers. The manifest lists bind qualification: production
runtime code/config plus the data evidence's own verifiers. The freeze-only tier
(`config/workflow/freeze_surface.yaml`) holds tests, evidence scripts, tooling
modules and verifiers of other evidence families. The freeze covers both tiers,
so nothing leaves the final implementation freeze; only qualification stops
retiring on edits to files production never executes. A freeze-only change is
disposed of explicitly when the next freeze is taken (`scoped_intake`), instead
of silently retiring every recorded row.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from ..config import REPO_ROOT

MANIFEST = REPO_ROOT / "config" / "data" / "target_dataflow_coverage.yaml"

# The manifest is the digest baseline for every recorded row, so a change here
# is not a change to one policy entry — it retires all evidence. Tracked
# separately from the fingerprinted code because it is config, not code, and
# because the remedy differs (re-verify everything, vs. re-verify the consumers
# that depend on the touched file).
MANIFEST_RELATIVE = "config/data/target_dataflow_coverage.yaml"

# Freeze-only tier. Deliberately a separate file: registering a new test in the
# manifest would change the manifest digest and retire every recorded row.
FREEZE_SURFACE_RELATIVE = "config/workflow/freeze_surface.yaml"
FREEZE_SURFACE = REPO_ROOT / FREEZE_SURFACE_RELATIVE

# Only these prefixes may be freeze-only without a stated reason; anything under
# src/ must be declared as a tool or a self-verifying verifier, with its reason.
_FREEZE_ONLY_PREFIXES = ("tests/", "scripts/")


@dataclass(frozen=True)
class FingerprintSurface:
    """Which files a given consumer's evidence is bound to, and what invalidates it."""

    required: tuple[str, ...]
    by_consumer: dict[str, tuple[str, ...]]
    # Every declared consumer. Needed separately because `by_consumer` only
    # carries the ones with EXTRA paths — six consumers have none, yet the
    # shared set binds them all. Iterating `by_consumer` alone would report
    # "trader, clerk" for `assurance.py` and silently omit the other eight.
    consumers: tuple[str, ...] = ()
    manifest: str = MANIFEST_RELATIVE
    # Frozen but not bound by qualification evidence. Empty for a manifest that
    # has no freeze-surface file beside it (isolated test manifests).
    freeze_only: tuple[str, ...] = ()
    freeze_surface: str | None = None

    def qualification_paths(self) -> tuple[str, ...]:
        """Every file some consumer's qualification evidence is bound to."""
        out: list[str] = [self.manifest, *self.required]
        for extra in self.by_consumer.values():
            out.extend(extra)
        return tuple(dict.fromkeys(out))

    def is_freeze_only(self, path: str) -> bool:
        return Path(path).as_posix() in self.freeze_only

    def paths_for(self, consumer_id: str) -> tuple[str, ...]:
        """Every file this consumer's evidence is bound to.

        Union, not replacement: the shared set is required for ALL consumers, so
        a per-consumer entry adds to it rather than narrowing it.
        """
        return tuple(dict.fromkeys(self.required + self.by_consumer.get(consumer_id, ())))

    def consumers_touched_by(self, path: str) -> tuple[str, ...]:
        """Every consumer whose evidence a change to `path` would invalidate.

        This is the question the naive approach gets wrong: a shared file has
        more than one consumer, and only re-verifying the one you cared about
        leaves the rest silently ineligible.
        """
        normalized = str(Path(path).as_posix())
        return tuple(sorted(
            consumer_id for consumer_id in self.consumers
            if normalized in self.paths_for(consumer_id)))

    def all_paths(self) -> tuple[str, ...]:
        """The whole frozen surface: qualification tier plus freeze-only tier."""
        out: list[str] = list(self.qualification_paths())
        if self.freeze_surface:
            out.append(self.freeze_surface)
        out.extend(self.freeze_only)
        return tuple(dict.fromkeys(out))

    def as_rows(self) -> list[dict[str, Any]]:
        """Flat, sorted inventory for evidence export and review."""
        rows: list[dict[str, Any]] = [
            {"path": self.manifest, "scope": "all_evidence",
             "invalidates": "every recorded evidence row"}]
        for path in self.required:
            rows.append({"path": path, "scope": "required",
                         "invalidates": ",".join(self.consumers_touched_by(path))})
        for consumer_id in self.consumers:
            for path in self.by_consumer.get(consumer_id, ()):
                rows.append({"path": path, "scope": consumer_id,
                             "invalidates": consumer_id})
        for path in ((self.freeze_surface,) if self.freeze_surface else ()) + self.freeze_only:
            rows.append({"path": path, "scope": "freeze_only",
                         "invalidates": "freeze only; disposition required at next freeze"})
        return sorted(rows, key=lambda row: (row["path"], row["scope"]))


def load_freeze_only(path: str | Path = FREEZE_SURFACE) -> tuple[str, ...]:
    """Freeze-only paths, validated structurally.

    A src/ path is accepted only with a stated reason, and a self-verifying
    verifier must also name the test proving its own evidence family binds it.
    Whether a tool module is really unreachable from runtime code is asserted by
    the surface tests, not here: that check needs the import graph.
    """
    document = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    out: list[str] = []
    for key in ("tests", "scripts"):
        for item in document.get(key) or ():
            if not str(item).startswith(_FREEZE_ONLY_PREFIXES):
                raise ValueError(f"freeze-only {key} entry outside tests/ or scripts/: {item}")
            out.append(str(item))
    for key, extra in (("src_tools", ()), ("self_verifying", ("family", "self_check"))):
        for row in document.get(key) or ():
            if not isinstance(row, dict) or not str(row.get("path", "")).startswith("src/"):
                raise ValueError(f"freeze-only {key} entry must be a src/ path row: {row}")
            missing = [field for field in ("reason", *extra) if not str(row.get(field) or "").strip()]
            if missing:
                raise ValueError(f"freeze-only {key} entry {row['path']} lacks: {', '.join(missing)}")
            out.append(row["path"])
    if len(out) != len(set(out)):
        raise ValueError("freeze-only surface lists a path twice")
    return tuple(out)


def load_surface(manifest_path: str | Path | None = None,
                 freeze_surface_path: str | Path | None = None) -> FingerprintSurface:
    path = Path(manifest_path or MANIFEST)
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    policy = document["qualification_policy"]
    surface = FingerprintSurface(
        required=tuple(policy.get("required_fingerprint_paths", ())),
        by_consumer={consumer: tuple(paths)
                     for consumer, paths
                     in (policy.get("consumer_fingerprint_paths") or {}).items()},
        consumers=tuple(sorted(row["id"] for row in document.get("consumers", ()))),
    )
    # The repository manifest always pairs with the repository freeze surface;
    # an isolated manifest pairs with one only when the caller names it.
    if freeze_surface_path is None and path.resolve() == MANIFEST.resolve():
        freeze_surface_path = FREEZE_SURFACE
    if freeze_surface_path is None:
        return surface
    freeze_only = load_freeze_only(freeze_surface_path)
    overlap = set(freeze_only) & set(surface.qualification_paths())
    if overlap:
        # One file cannot both retire evidence and merely need disposition.
        raise ValueError("paths are both qualification-bound and freeze-only: "
                         + ", ".join(sorted(overlap)))
    resolved = Path(freeze_surface_path).resolve()
    try:
        label = resolved.relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        label = str(resolved)
    return FingerprintSurface(
        required=surface.required, by_consumer=surface.by_consumer,
        consumers=surface.consumers, freeze_only=freeze_only, freeze_surface=label)


def fingerprint(paths: Iterable[str]) -> dict[str, str]:
    """Content hashes for the given repo-relative paths.

    Same computation `assurance` performs, so a drift check here and the
    qualification verdict cannot disagree about what "unchanged" means.
    """
    result: dict[str, str] = {}
    for raw in paths:
        candidate = Path(raw)
        resolved = candidate if candidate.is_absolute() else REPO_ROOT / candidate
        if not resolved.is_file():
            raise FileNotFoundError(f"fingerprint path is not a file: {raw}")
        label = resolved.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
        result[label] = hashlib.sha256(resolved.read_bytes()).hexdigest()
    return result


def recorded_hashes(event: dict[str, Any]) -> dict[str, str]:
    """The dependency hashes a recorded evidence row carries."""
    import json

    return json.loads(event.get("dependencies_json") or "{}")


def drift(surface: FingerprintSurface, recorded: dict[str, str]) -> dict[str, str]:
    """Recorded paths whose content no longer matches, plus newly added ones.

    Both directions matter. A removed path is drift too: the contract no longer
    requires a file the evidence was proved against, which means the proof was
    made under rules that no longer exist.
    """
    current = fingerprint(surface.all_paths())
    out = {path: digest for path, digest in recorded.items()
           if current.get(path) != digest}
    return dict(sorted(out.items()))


def affected_consumers(surface: FingerprintSurface, paths: Iterable[str]) -> dict[str, tuple[str, ...]]:
    """Map each changed path to the consumers whose evidence it invalidates.

    `("*",)` is the manifest case: a manifest change does not retire a subset,
    it retires every row ever recorded, and the report has to say exactly that
    rather than listing ten names that read like a partial impact.
    """
    out: dict[str, tuple[str, ...]] = {}
    for path in paths:
        normalized = Path(path).as_posix()
        if normalized == surface.manifest:
            out[normalized] = ("*",)
            continue
        consumers = surface.consumers_touched_by(normalized)
        if consumers:
            out[normalized] = consumers
    return dict(sorted(out.items()))


def assert_outside_fingerprint_surface(changed_paths: Iterable[str],
                                       surface: FingerprintSurface | None = None) -> None:
    """Refuse a cutover action that edits the evidence-fingerprint surface.

    Deliberately an assertion at the ACTION boundary rather than a lint: the
    legitimate exception (the code has to change before evidence is taken) is
    handled by ordering, not by a weaker check here. A cutover that modifies
    these files invalidates every qualification it was about to rely on, so it
    must fail loudly at the point of the action.

    Paths are resolved against the repository before comparison, so a caller that
    reports a changed file by a slightly different (or wrong) route still gets
    caught rather than silently passing an unsurfaced path through the check.

    Raises `FingerprintSurfaceError` naming the offending paths and the
    consumers each one would retire.
    """
    surface = surface or load_surface()
    known = set(surface.all_paths())
    repository = REPO_ROOT.resolve()
    changed: list[str] = []
    unknown: list[str] = []
    for raw in changed_paths:
        candidate = Path(raw)
        resolved = candidate if candidate.is_absolute() else REPO_ROOT / candidate
        try:
            label = resolved.resolve().relative_to(repository).as_posix()
        except ValueError:
            # Outside the checkout entirely — cannot be a repository input, and
            # silently ignoring it would hide a genuine misconfiguration.
            unknown.append(str(raw))
            continue
        if label in known:
            changed.append(label)
        elif not resolved.exists():
            # A path that does not exist and is not on the surface is a typo in
            # the report. A path that DOES exist is simply new work (Phase F
            # adds modules every day) and is allowed through.
            unknown.append(str(raw))
        else:
            changed.append(label)

    if unknown:
        raise FingerprintSurfaceError(
            "changed paths reported for a cutover action are not on the "
            "evidence-fingerprint surface, so their effect on recorded evidence "
            "is unknown: " + ", ".join(sorted(unknown))
            + ". Check the paths — a typo here would make the surface check pass "
              "while the real change retires evidence.")

    affected = affected_consumers(surface, changed)
    if not affected:
        return
    total = len(surface.consumers)
    lines = []
    for path, consumers in affected.items():
        if consumers == ("*",):
            retired = "every consumer (all recorded evidence)"
        elif len(consumers) >= total:
            # A shared path is the common case, and "every consumer" is both
            # shorter and more accurate than a ten-name list.
            retired = f"every consumer (all {total})"
        else:
            retired = ", ".join(consumers)
        lines.append(f"  {path} would invalidate evidence for: {retired}")
    raise FingerprintSurfaceError(
        "cutover must not modify the evidence-fingerprint surface; route changes "
        "belong in the release overlay.\n"
        "Offending paths and the consumers they would retire:\n" + "\n".join(lines)
        + "\nIf the change is unavoidable, make it BEFORE recording evidence and "
          "re-verify every consumer listed above.")


class FingerprintSurfaceError(RuntimeError):
    """A cutover action would have invalidated recorded evidence."""
