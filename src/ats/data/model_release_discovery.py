"""Model release candidates: models newly listed by OpenRouter that nobody has decided on yet.

Computed on read from the stored daily model lists: no table, no job, no model
call.  Code only merges list entries mechanically.  Whether a model is a
flagship, when it was released and where the lab announced it are decided by the
person who confirms it; the listing date is a hint and is never written as the
release date.

Confirming appends to the registry file and dismissing appends to its own file,
so both decisions are versioned by git.
"""
from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

from ..config import _config_dir
from .model_releases import ModelReleaseError, ModelReleaseRegistry, model_key, registry_path
from .sources.openrouter_pricing import DATASET_ID, SOURCE_ID

DISMISSED_FILE = "data/model_release_dismissed.yaml"
LIST_COMMAND = "ats data model-release-candidates"
NOTE_LIMIT = 10
_UNLIMITED = 1_000_000
_REASONS = {
    "no_snapshot": "存储里没有 OpenRouter 模型清单",
    "no_baseline_snapshot": "没有基线日期及以前的清单，无法区分新型号",
}


@dataclass(frozen=True)
class DiscoverySettings:
    baseline_date: str
    labs: tuple[tuple[str, str], ...]  # (OpenRouter author prefix, lab_id)

    @property
    def authors(self) -> dict[str, str]:
        return dict(self.labs)


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_settings(path: str | Path | None = None) -> DiscoverySettings:
    """The declared scope: which labs to watch and from which stored list on."""
    payload = _read_yaml(Path(path) if path else registry_path())
    block = payload.get("discovery") or {}
    authors = block.get("openrouter_authors") or {}
    domains = payload.get("official_domains") or {}
    unknown = sorted(set(authors) - set(domains))
    if not authors or unknown:
        raise ModelReleaseError(
            f"discovery.openrouter_authors must name labs listed in official_domains; unknown {unknown}")
    try:
        baseline = date.fromisoformat(str(block.get("baseline_date") or "")).isoformat()
    except ValueError as exc:
        raise ModelReleaseError("discovery.baseline_date must be YYYY-MM-DD") from exc
    return DiscoverySettings(baseline, tuple(sorted(
        (str(prefix).casefold(), str(lab)) for lab, prefix in authors.items())))


def dismissed_path() -> Path:
    return _config_dir() / DISMISSED_FILE


def load_dismissed(path: str | Path | None = None) -> list[dict[str, Any]]:
    source = Path(path) if path else dismissed_path()
    payload = _read_yaml(source)
    items = payload.get("dismissed") or []
    if payload.get("version") != 1 or not all(
            isinstance(item, dict) and item.get("candidate_id") for item in items):
        raise ModelReleaseError(f"{source.name}: needs version: 1 and a candidate_id on every entry")
    return items


def _entry_id(row: dict[str, Any]) -> str:
    """The model a list entry stands for, or "" for an entry that is only a pointer."""
    full = str(row.get("id") or "").strip().casefold()
    base = full.partition(":")[0]
    if row.get("alias_target") or full.startswith("~") or base.endswith("-latest") or "/" not in base:
        return ""
    return base


def snapshots(repository: Any) -> list[tuple[str, list[dict[str, Any]]]]:
    """Every stored model list as (snapshot date, rows), oldest first."""
    found = []
    for artifact in repository.artifacts_for(source_id=SOURCE_ID, dataset_id=DATASET_ID, limit=_UNLIMITED):
        day = str(json.loads(artifact["query_scope_json"] or "{}").get("snapshot_date")
                  or artifact["fetched_at"][:10])
        body = json.loads(repository.artifacts.read(artifact["relative_path"]))
        found.append((day, artifact["fetched_at"], body["data"]))
    return [(day, rows) for day, _, rows in sorted(found, key=lambda item: item[:2])]


def candidates(repository: Any, *, registry: ModelReleaseRegistry, settings: DiscoverySettings,
               dismissed: Iterable[dict[str, Any]] = ()) -> dict[str, Any]:
    """Models absent from every list up to the baseline date, not registered and not dismissed."""
    stored = snapshots(repository)
    report: dict[str, Any] = {
        "status": "ok", "reason_code": "", "baseline_date": settings.baseline_date,
        "latest_snapshot": stored[-1][0] if stored else "", "candidates": []}
    if not stored or stored[0][0] > settings.baseline_date:
        reason = "no_snapshot" if not stored else "no_baseline_snapshot"
        report.update(status="unavailable", reason_code=reason)
        return report
    authors = settings.authors
    known = {_entry_id(row) for day, rows in stored if day <= settings.baseline_date for row in rows}
    skip = known | {str(item["candidate_id"]).casefold() for item in dismissed} | {""}
    registered = {(record.lab_id, key) for record in registry.records for key in record.keys}
    found: dict[str, dict[str, Any]] = {}
    for day, rows in stored:
        if day <= settings.baseline_date:
            continue
        for row in rows:
            identity = _entry_id(row)
            author, _, slug = identity.partition("/")
            lab = authors.get(author)
            if identity in skip or not lab:
                continue
            if (lab, model_key(slug)) in registered or (lab, model_key(identity)) in registered:
                continue
            item = found.setdefault(identity, {
                "candidate_id": identity, "lab_id": lab, "model_id": slug, "name": "",
                "listed_date": "", "first_seen": day, "variants": []})
            full = str(row["id"]).casefold()
            if full not in item["variants"]:
                item["variants"].append(full)
            if row.get("name") and (full == identity or not item["name"]):
                item["name"] = str(row["name"])
            if isinstance(row.get("created"), (int, float)):
                listed = datetime.fromtimestamp(row["created"], UTC).date().isoformat()
                item["listed_date"] = min(item["listed_date"] or listed, listed)
    for item in found.values():
        item["variants"].sort()
    report["candidates"] = sorted(
        found.values(), key=lambda item: (item["listed_date"], item["candidate_id"]))
    return report


def current_report() -> dict[str, Any]:
    """The report from the platform store and the committed files; opens the store read-only."""
    from .runtime.repository import platform_artifact_root, platform_data_db_path
    from .stores.structured.repository import SQLiteStructuredRepository

    repository = SQLiteStructuredRepository(
        platform_data_db_path(), artifact_root=platform_artifact_root(), readonly=True)
    try:
        return candidates(repository, registry=ModelReleaseRegistry.load(), settings=load_settings(),
                          dismissed=load_dismissed())
    finally:
        repository.close()


def describe_reason(reason_code: str) -> str:
    return _REASONS.get(reason_code, reason_code)


def digest_note(report: dict[str, Any]) -> str:
    """What the daily digest says; empty only when there is truly nothing to decide."""
    if report["status"] != "ok":
        return f"模型发布候选：无法判断（{describe_reason(report['reason_code'])}）"
    items = report["candidates"]
    if not items:
        return ""
    lines = [f"模型发布待确认 {len(items)} 个："]
    lines += [f"- {item['candidate_id']}（上架 {item['listed_date'] or '日期未知'}）"
              for item in items[:NOTE_LIMIT]]
    if len(items) > NOTE_LIMIT:
        lines.append(f"- …另 {len(items) - NOTE_LIMIT} 个")
    lines.append(f"查看：{LIST_COMMAND}")
    return "\n".join(lines)


def pending_note() -> str:
    """`digest_note` for the live store.  A failure is reported in the digest, never raised."""
    try:
        return digest_note(current_report())
    except Exception as exc:  # noqa: BLE001 - the digest must still go out
        return f"模型发布候选读取失败：{type(exc).__name__}: {exc}"[:300]


def _append(path: Path, section: str, items: list[dict[str, Any]], check) -> None:
    """Append list items at the end of a hand-maintained file, keeping its comments.

    ``check`` sees the old and new parsed content and raises if the file would not
    read back as intended; nothing is written unless it passes.
    """
    before = path.read_text(encoding="utf-8")
    block = "".join(
        "".join(f"  {line}\n" for line in yaml.safe_dump(
            [item], allow_unicode=True, sort_keys=False, default_flow_style=False).splitlines())
        for item in items)
    after = before.rstrip("\n") + "\n" + block
    try:
        old, new = yaml.safe_load(before) or {}, yaml.safe_load(after) or {}
    except yaml.YAMLError as exc:
        raise ModelReleaseError(f"{path.name}: appending to `{section}` would break the file") from exc
    if (new.get(section) or []) != [*(old.get(section) or []), *items]:
        raise ModelReleaseError(f"{path.name}: `{section}` must be the last block of the file")
    check(new)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(after, encoding="utf-8")
    os.replace(temporary, path)


def _stamp(moment: datetime | None) -> str:
    return (moment or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def register(*, lab_id: str, model_id: str, tier: str, release_date: str = "", announcement_url: str = "",
             display_name: str = "", status: str = "confirmed", source_aliases: Iterable[str] = (),
             registered_at: datetime | None = None, path: str | Path | None = None) -> dict[str, Any]:
    """Append one record to the registry file; the registry's own rules decide whether it is accepted."""
    if release_date:
        try:
            released: date | None = date.fromisoformat(release_date)
        except ValueError as exc:
            raise ModelReleaseError(f"release_date must be YYYY-MM-DD, got {release_date!r}") from exc
    else:
        released = None
    record: dict[str, Any] = {
        "lab_id": lab_id, "model_id": model_id, "display_name": display_name or model_id, "tier": tier,
        **({"release_date": released} if released else {}),
        **({"announcement_url": announcement_url} if announcement_url else {}),
        "status": status, "registered_at": _stamp(registered_at)}
    aliases = list(dict.fromkeys(
        value for value in source_aliases if value and model_key(value) != model_key(model_id)))
    if aliases:
        record["source_aliases"] = aliases
    _append(Path(path) if path else registry_path(), "releases", [record],
            ModelReleaseRegistry.from_payload)
    return record


def dismiss(candidate_ids: Iterable[str], *, reason: str = "", moment: datetime | None = None,
            path: str | Path | None = None) -> list[dict[str, Any]]:
    items = [{"candidate_id": value, "dismissed_at": _stamp(moment), **({"reason": reason} if reason else {})}
             for value in dict.fromkeys(candidate_ids)]
    _append(Path(path) if path else dismissed_path(), "dismissed", items, lambda _: None)
    return items
