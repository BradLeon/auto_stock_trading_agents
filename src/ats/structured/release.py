"""Quality-gated, reversible runtime release overlays."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile

import yaml

from .catalog import StructuredCatalog
from .reporting import build_quality_report
from .runtime_registry import validate_source_registration


SAFE_INGESTION_STATES = {"succeeded", "no_change"}
READ_MODES = {"legacy", "shadow", "platform", "fallback"}


def default_release_path() -> Path:
    from ..config import REPO_ROOT

    return Path(os.environ.get(
        "ATS_STRUCTURED_RELEASE_FILE",
        REPO_ROOT / "var" / "structured_data" / "releases.yaml"))


def load_release_overlay(path: str | Path | None = None) -> dict:
    target = Path(path) if path else default_release_path()
    if not target.exists():
        return {"version": 1, "sources": {}, "consumers": {}, "history": []}
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if int(raw.get("version", 1)) != 1:
        raise ValueError(f"unsupported structured release overlay: {target}")
    return {"version": 1, "sources": dict(raw.get("sources") or {}),
            "consumers": dict(raw.get("consumers") or {}),
            "history": list(raw.get("history") or [])}


def overlay_mode(kind: str, target_id: str, *, path: str | Path | None = None) -> str:
    plural = "sources" if kind == "source" else "consumers"
    value = load_release_overlay(path).get(plural, {}).get(target_id, "")
    return str(value).lower() if value else ""


def _write_overlay(raw: dict, path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
    with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=target.parent, delete=False) as handle:
        handle.write(body)
        temporary = Path(handle.name)
    os.replace(temporary, target)


class ReleaseManager:
    def __init__(self, repository, *, catalog: StructuredCatalog | None = None,
                 path: str | Path | None = None):
        self.repository = repository
        self.catalog = catalog or StructuredCatalog.load()
        self.path = Path(path) if path else default_release_path()

    def check_source(self, source_id: str, *, mode: str = "platform") -> dict:
        checks: list[dict] = []

        def check(name: str, passed: bool, detail="") -> None:
            checks.append({"check": name, "passed": bool(passed), "detail": detail})

        if mode not in READ_MODES:
            raise ValueError(f"invalid release mode: {mode}")
        registration = validate_source_registration(source_id, catalog=self.catalog)
        check("registration", registration["valid"], registration["reason_codes"])
        source = self.repository.source(source_id)
        check("repository_registration", source is not None, "source_not_bootstrapped")
        datasets = registration.get("datasets", [])
        health = next((row for row in self.repository.source_health()
                       if row["source_id"] == source_id), {})
        last_status = health.get("last_status") or "no_run"
        if mode in {"platform", "fallback"}:
            check("latest_ingestion", last_status in SAFE_INGESTION_STATES, last_status)
            try:
                report = build_quality_report(self.repository)
            except Exception as exc:
                report = {"datasets": []}
                check("quality_report", False, f"{type(exc).__name__}:{exc}")
            by_dataset = {row["dataset_id"]: row for row in report["datasets"]}
            for dataset_id in datasets:
                quality = by_dataset.get(dataset_id)
                check(f"quality:{dataset_id}",
                      bool(quality and quality["overall_status"] == "passed"),
                      (quality or {}).get("overall_status", "missing_quality_report"))
        else:
            check("latest_ingestion", True, "not_required_for_non_platform_mode")
        return {"kind": "source", "target_id": source_id, "requested_mode": mode,
                "current_overlay_mode": overlay_mode("source", source_id, path=self.path),
                "last_ingestion_status": last_status, "ready": all(
                    row["passed"] for row in checks), "checks": checks}

    def check_consumer(self, consumer: str, *, mode: str = "platform") -> dict:
        if mode not in READ_MODES:
            raise ValueError(f"invalid release mode: {mode}")
        consumers = self.catalog.raw.get("feature_flags", {}).get("consumers", {}) or {}
        configured = consumer in consumers
        reconciliation_approved = mode != "platform" or consumers.get(consumer) == "platform"
        return {"kind": "consumer", "target_id": consumer,
                "requested_mode": mode,
                "current_overlay_mode": overlay_mode("consumer", consumer, path=self.path),
                "ready": configured and reconciliation_approved,
                "checks": [
                    {"check": "consumer_configured", "passed": configured,
                     "detail": "" if configured else "unknown_consumer"},
                    {"check": "reconciliation_approved", "passed": reconciliation_approved,
                     "detail": "" if reconciliation_approved
                     else "consumer_not_approved_for_platform_in_checked_config"},
                ]}

    def apply(self, check: dict, *, actor: str = "cli") -> dict:
        if not check.get("ready"):
            raise ValueError("release check failed; overlay was not changed")
        raw = load_release_overlay(self.path)
        plural = "sources" if check["kind"] == "source" else "consumers"
        previous = raw[plural].get(check["target_id"], "")
        raw[plural][check["target_id"]] = check["requested_mode"]
        raw["history"].append({
            "at": datetime.now(timezone.utc).isoformat(), "actor": actor,
            "kind": check["kind"], "target_id": check["target_id"],
            "previous_mode": previous, "mode": check["requested_mode"],
            "action": "publish" if check["requested_mode"] == "platform" else "set_mode",
        })
        _write_overlay(raw, self.path)
        return {**check, "applied": True, "previous_mode": previous,
                "release_file": str(self.path)}

    def rollback(self, *, kind: str, target_id: str, mode: str = "legacy",
                 actor: str = "cli") -> dict:
        if kind not in {"source", "consumer"} or mode not in READ_MODES:
            raise ValueError("invalid rollback target or mode")
        raw = load_release_overlay(self.path)
        plural = "sources" if kind == "source" else "consumers"
        previous = raw[plural].get(target_id, "")
        raw[plural][target_id] = mode
        raw["history"].append({
            "at": datetime.now(timezone.utc).isoformat(), "actor": actor,
            "kind": kind, "target_id": target_id, "previous_mode": previous,
            "mode": mode, "action": "rollback",
        })
        _write_overlay(raw, self.path)
        return {"kind": kind, "target_id": target_id, "previous_mode": previous,
                "mode": mode, "applied": True, "release_file": str(self.path)}
