"""Model release registry: when a lab officially released a model.

Records live in ``config/data/model_releases.yaml``, maintained by a person and
versioned by git. Only a ``confirmed`` record backed by the lab's own
announcement may start a clock. Benchmark score dates, fetch dates and
repository creation times are never release dates.

Every query takes ``as_of`` and answers with what the registry knew then: a
record registered later is invisible, and a later correction is rolled back.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from ..config import _config_dir

REGISTRY_FILE = "data/model_releases.yaml"
TIERS = ("general_flagship", "other")
STATUSES = ("confirmed", "candidate")
UNREGISTERED_LABEL = "发布日期未登记"
CORRECTABLE_FIELDS = ("display_name", "tier", "release_date", "announcement_url")
_FIELDS = frozenset({
    "lab_id", "model_id", "display_name", "tier", "release_date", "announcement_url",
    "status", "registered_at", "source_aliases", "corrections",
})
# Evaluation settings that public leaderboards append to a model name. Only this
# closed vocabulary is removed; product names such as ``flash`` or ``lite`` stay.
_EFFORT_SUFFIX = re.compile(r"-(?:xhigh|max|high|medium|low)(?:-effort)?(?:-with-fallback)?$")


class ModelReleaseError(ValueError):
    """The registry cannot be trusted as written."""


def model_key(value: Any) -> str:
    """Normalize a model name or slug for exact comparison."""
    return re.sub(r"[^a-z0-9._/-]+", "-", str(value or "").strip().lower()).strip("-")


def _moment(value: datetime | date | str | None) -> datetime:
    """Read ``as_of``: a bare date means the end of that UTC day."""
    if value is None or value == "":
        return datetime.now(UTC)
    if isinstance(value, str):
        value = (date.fromisoformat(value) if len(value) == 10
                 else datetime.fromisoformat(value))
    if not isinstance(value, datetime):
        return datetime.combine(value, time.max, tzinfo=UTC)
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _timestamp(value: Any, *, where: str) -> datetime:
    try:
        parsed = (value if isinstance(value, datetime)
                  else datetime.fromisoformat(str(value or "")))
    except ValueError as exc:
        raise ModelReleaseError(f"{where}: invalid timestamp {value!r}") from exc
    if parsed.tzinfo is None:
        raise ModelReleaseError(f"{where}: timestamp needs a timezone, got {value!r}")
    return parsed.astimezone(UTC)


def _iso_date(value: Any, *, where: str) -> str:
    text = value.isoformat() if isinstance(value, date) else str(value or "")
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise ModelReleaseError(f"{where}: release_date must be YYYY-MM-DD, got {value!r}") from exc


def _official(url: str, domains: Iterable[str]) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = f"{host}{parts.path}".lower()
    for domain in domains:
        domain = str(domain).strip().lower().rstrip("/")
        if "/" in domain:  # an organisation page on a shared host, e.g. github.com/<org>
            if path == domain or path.startswith(domain + "/"):
                return parts.scheme == "https"
        elif host == domain or host.endswith("." + domain):
            return parts.scheme == "https"
    return False


@dataclass(frozen=True)
class Correction:
    corrected_at: datetime
    reason: str
    previous: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ModelRelease:
    lab_id: str
    model_id: str
    display_name: str
    tier: str
    release_date: str
    announcement_url: str
    status: str
    registered_at: datetime
    source_aliases: tuple[str, ...] = ()
    corrections: tuple[Correction, ...] = ()

    @property
    def keys(self) -> frozenset[str]:
        return frozenset(model_key(value) for value in (self.model_id, *self.source_aliases))

    def known_at(self, moment: datetime) -> ModelRelease | None:
        """The record as the registry held it at ``moment``."""
        if self.registered_at > moment:
            return None
        record = self
        later = [item for item in self.corrections if item.corrected_at > moment]
        for correction in reversed(later):
            record = replace(record, **dict(correction.previous))
        return replace(record, corrections=tuple(
            item for item in self.corrections if item.corrected_at <= moment))


class ModelReleaseRegistry:
    def __init__(self, records: Iterable[ModelRelease] = ()):
        self.records = tuple(records)
        seen: dict[tuple[str, str], str] = {}
        for record in self.records:
            for key in record.keys:
                other = seen.setdefault((record.lab_id, key), record.model_id)
                if other != record.model_id:
                    raise ModelReleaseError(
                        f"{record.lab_id}: {key!r} names both {other} and {record.model_id}")
        identities = [(record.lab_id, record.model_id) for record in self.records]
        duplicated = sorted({item for item in identities if identities.count(item) > 1})
        if duplicated:
            raise ModelReleaseError(f"model registered more than once: {duplicated}")

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ModelReleaseRegistry:
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ModelReleaseError("registry needs version: 1")
        domains = payload.get("official_domains") or {}
        records = []
        for index, item in enumerate(payload.get("releases") or []):
            where = f"releases[{index}]"
            if not isinstance(item, dict) or set(item) - _FIELDS:
                raise ModelReleaseError(f"{where}: unknown fields {sorted(set(item or {}) - _FIELDS)}")
            lab = str(item.get("lab_id") or "")
            model_id = str(item.get("model_id") or "")
            tier = str(item.get("tier") or "")
            status = str(item.get("status") or "")
            where = f"{where} {lab}:{model_id}"
            if lab not in domains or not model_id:
                raise ModelReleaseError(f"{where}: lab_id must be listed in official_domains and model_id set")
            if tier not in TIERS or status not in STATUSES:
                raise ModelReleaseError(f"{where}: tier must be one of {TIERS}, status one of {STATUSES}")
            url = str(item.get("announcement_url") or "")
            released = (_iso_date(item.get("release_date"), where=where)
                        if item.get("release_date") or status == "confirmed" else "")
            if status == "confirmed" and not _official(url, domains[lab]):
                raise ModelReleaseError(
                    f"{where}: a confirmed record needs an https announcement on the lab's own domain")
            registered = _timestamp(item.get("registered_at"), where=where)
            corrections = []
            last = registered
            for change in item.get("corrections") or []:
                at = _timestamp(change.get("corrected_at"), where=where)
                reason = str(change.get("reason") or "").strip()
                previous = change.get("previous") or {}
                if at <= last or not reason or not previous or set(previous) - set(CORRECTABLE_FIELDS):
                    raise ModelReleaseError(
                        f"{where}: corrections must be in time order and give reason and previous "
                        f"values of {CORRECTABLE_FIELDS}")
                values = {key: (_iso_date(value, where=where) if key == "release_date" else str(value))
                          for key, value in previous.items()}
                corrections.append(Correction(at, reason, tuple(sorted(values.items()))))
                last = at
            records.append(ModelRelease(
                lab_id=lab, model_id=model_id,
                display_name=str(item.get("display_name") or model_id), tier=tier,
                release_date=released, announcement_url=url, status=status,
                registered_at=registered,
                source_aliases=tuple(str(value) for value in item.get("source_aliases") or []),
                corrections=tuple(corrections)))
        return cls(records)

    @classmethod
    def load(cls, path: str | Path | None = None) -> ModelReleaseRegistry:
        source = Path(path) if path else registry_path()
        return cls.from_payload(yaml.safe_load(source.read_text(encoding="utf-8")) or {})

    def confirmed(self, *, as_of: datetime | date | str | None = None,
                  lab_id: str = "") -> list[ModelRelease]:
        """Confirmed releases known at ``as_of``, oldest release first."""
        moment = _moment(as_of)
        known = (record.known_at(moment) for record in self.records)
        return sorted((record for record in known if record is not None
                       and record.status == "confirmed" and (not lab_id or record.lab_id == lab_id)),
                      key=lambda record: (record.release_date, record.lab_id, record.model_id))

    def flagship(self, lab_id: str, *,
                 as_of: datetime | date | str | None = None) -> ModelRelease | None:
        """The lab's latest confirmed general flagship already released at ``as_of``."""
        day = _moment(as_of).date().isoformat()
        eligible = [record for record in self.confirmed(as_of=as_of, lab_id=lab_id)
                    if record.tier == "general_flagship" and record.release_date <= day]
        return max(eligible, key=lambda record: (record.release_date, record.registered_at),
                   default=None)

    def resolve(self, lab_id: str, identifiers: Iterable[Any], *,
                as_of: datetime | date | str | None = None) -> ModelRelease | None:
        """Match source names to one confirmed release; never by prefix or similarity.

        A name is compared as written first; only then is a trailing evaluation
        setting removed from it. Registered names are never shortened.
        """
        keys = [model_key(value) for value in identifiers if value]
        keys = [key for key in keys if key]
        records = self.confirmed(as_of=as_of, lab_id=lab_id)
        for candidates in (keys, [_EFFORT_SUFFIX.sub("", key) for key in keys]):
            matches = {record.model_id: record for record in records
                       if record.keys.intersection(candidates)}
            if matches:
                return next(iter(matches.values())) if len(matches) == 1 else None
        return None


def release_date_fields(release: ModelRelease | None) -> dict[str, str]:
    """How readers state a release date: it exists only when the registry confirmed it."""
    if release is None:
        return {"release_date": "", "release_date_status": "unregistered",
                "release_date_label": UNREGISTERED_LABEL, "announcement_url": ""}
    return {"release_date": release.release_date, "release_date_status": "registered",
            "release_date_label": release.release_date,
            "announcement_url": release.announcement_url}


def registry_path() -> Path:
    return _config_dir() / REGISTRY_FILE


def load_model_releases(path: str | Path | None = None) -> ModelReleaseRegistry:
    return ModelReleaseRegistry.load(path)
