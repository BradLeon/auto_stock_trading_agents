"""Governed adapter for the public Anthropic Economic Index release files.

The Job Explorer UI is intentionally not an input.  This adapter discovers the
official Hugging Face repository, pins every download to the resolved commit,
streams source files to temporary storage, and persists only deterministic Global
SOC/O*NET query slices.
"""

from __future__ import annotations

import csv
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable
from urllib.parse import quote

from ..structured import (
    AdapterArtifact, AdapterBatch, AdapterFailure, EntityRelationInput,
    FetchRequest, IngestionStatus, NativeRecord, ReferenceEntityInput,
)


REPOSITORY = "Anthropic/EconomicIndex"
HF_API = f"https://huggingface.co/api/datasets/{REPOSITORY}"
HF_RESOLVE = f"https://huggingface.co/datasets/{REPOSITORY}/resolve"
RELEASE_RE = re.compile(r"^release_(\d{4}_\d{2}_\d{2})/")
REQUIRED_COLUMNS = {
    "date_start", "date_end", "geo_id", "geo_level", "category_name",
    "hierarchy_level", "metric_id", "value", "node_name", "node_external_id",
}
ALLOWED_METRICS = {
    "pct", "collaboration_bucket_automation_pct", "collaboration_bucket_augmentation_pct",
    "collaboration_directive_pct", "collaboration_feedback_loop_pct",
    "collaboration_task_iteration_pct", "collaboration_validation_pct",
    "collaboration_learning_pct", "collaboration_none_pct", "use_case_work_pct",
    "ai_autonomy_mean",
}
TAXONOMY_FILENAMES = ("onet_task_statements.csv", "soc_structure.csv")
EXPOSURE_FILES = ("labor_market_impacts/job_exposure.csv",
                  "labor_market_impacts/task_penetration.csv")
USER_AGENT = "ats-research-data/1.0"
DOWNLOAD_ATTEMPTS = 3


def _iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _siblings(metadata: dict[str, Any]) -> set[str]:
    out = set()
    for value in metadata.get("siblings") or []:
        if isinstance(value, str):
            out.add(value)
        elif isinstance(value, dict) and value.get("rfilename"):
            out.add(str(value["rfilename"]))
    return out


def discover_releases(metadata: dict[str, Any]) -> list[dict[str, str]]:
    """Return complete releases newest first, never inferring a missing file."""
    files = _siblings(metadata)
    commit = str(metadata.get("sha") or metadata.get("id") or "").strip()
    if not commit:
        raise ValueError("metadata_missing_commit_sha")
    releases = sorted({match.group(1) for path in files if (match := RELEASE_RE.match(path))}, reverse=True)
    complete = []
    for release in releases:
        base = f"release_{release}"
        documentation = f"{base}/data_documentation.md"
        claude_matches = sorted(path for path in files
                                if path.startswith(f"{base}/") and "/aei_claude_ai_" in path
                                and path.endswith(".csv"))
        api_matches = sorted(path for path in files
                             if path.startswith(f"{base}/") and "/aei_1p_api_" in path
                             and path.endswith(".csv"))
        if documentation in files and claude_matches and api_matches:
            complete.append({"release": release, "directory": base, "commit": commit,
                             "claude_path": claude_matches[-1], "api_path": api_matches[-1]})
    return complete


def _resolve_url(commit: str, path: str) -> str:
    return f"{HF_RESOLVE}/{quote(commit, safe='')}/{quote(path, safe='/')}"


def discover_taxonomy_paths(metadata: dict[str, Any]) -> dict[str, str]:
    """Use the newest available official taxonomy files independently of a monthly release."""
    files = _siblings(metadata)
    selected: dict[str, str] = {}
    for filename in TAXONOMY_FILENAMES:
        matches = sorted(path for path in files if path.endswith(f"/{filename}") or path == filename)
        if matches:
            selected[filename] = matches[-1]
    return selected


def _normalized_json_lines(rows: Iterable[dict[str, str]]) -> bytes:
    return b"".join(
        json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
        for row in rows
    )


def _month(start: str) -> str:
    value = start.strip()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        raise ValueError(f"invalid_date_start:{value}")
    return value[:7]


def _is_global_work_row(row: dict[str, str]) -> bool:
    if row.get("geo_id") != "GLOBAL" or row.get("geo_level") != "global":
        return False
    category = row.get("category_name")
    try:
        level = int(row.get("hierarchy_level", ""))
    except ValueError:
        return False
    return (category == "soc_occupation" and level in {0, 1}) or (category == "onet" and level == 0)


def _entity_for_row(row: dict[str, str]) -> str:
    category = row["category_name"]
    external = row["node_external_id"].strip()
    if category == "onet":
        return f"ONET_TASK:{external}"
    return f"SOC:{external}"


def _major_group_code(code: str) -> str:
    match = re.match(r"^(\d{2})-", code)
    return f"{match.group(1)}-0000" if match else ""


def _metric_unit(metric_id: str) -> str:
    return "scale_1_5" if metric_id == "ai_autonomy_mean" else "percent"


def _failure_status(exc: Exception) -> IngestionStatus:
    """Map transport failures to an operational state without masking schema errors."""
    message = str(exc).casefold()
    if "404" in message:
        return IngestionStatus.NOT_YET_PUBLISHED
    if "401" in message or "403" in message:
        return IngestionStatus.UNAUTHORIZED
    if any(token in message for token in ("429", "500", "502", "503", "504", "timeout", "connect")):
        return IngestionStatus.UNREACHABLE
    return IngestionStatus.VALIDATION_FAILED


def parse_monthly_csv(path: Path, *, source_product: str, release: str,
                      commit: str, published_at: datetime | None,
                      slice_key: str) -> tuple[list[NativeRecord], bytes, dict[str, int]]:
    """Stream a monthly CSV and return only normalized Global job/task rows."""
    records: list[NativeRecord] = []
    artifact_rows: list[dict[str, str]] = []
    counts = {"source_rows": 0, "eligible_rows": 0, "normalized_records": 0,
              "excluded_metrics": 0, "non_global_rows": 0}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not REQUIRED_COLUMNS <= set(reader.fieldnames):
            missing = sorted(REQUIRED_COLUMNS - set(reader.fieldnames or []))
            raise ValueError(f"monthly_schema_missing:{','.join(missing)}")
        for raw in reader:
            counts["source_rows"] += 1
            row = {str(key): (value or "") for key, value in raw.items()}
            if not _is_global_work_row(row):
                if row.get("geo_id") != "GLOBAL" or row.get("geo_level") != "global":
                    counts["non_global_rows"] += 1
                continue
            counts["eligible_rows"] += 1
            if row["metric_id"] not in ALLOWED_METRICS:
                counts["excluded_metrics"] += 1
                continue
            try:
                value = float(row["value"])
                hierarchy_level = int(row["hierarchy_level"])
            except ValueError as exc:
                raise ValueError(f"monthly_value_invalid:{row.get('metric_id','')}") from exc
            entity_id = _entity_for_row(row)
            dimensions = {
                "source_product": source_product,
                "classification": row["category_name"],
                "hierarchy_level": hierarchy_level,
                "node_external_id": row["node_external_id"],
                "node_name": row["node_name"],
                "release_date": release.replace("_", "-"),
                "methodology_version": f"release_{release}",
                "taxonomy_version": commit,
            }
            if row["category_name"] == "soc_occupation":
                dimensions["soc_code"] = row["node_external_id"]
                dimensions["soc_major_group_code"] = _major_group_code(row["node_external_id"])
            else:
                dimensions["task_id"] = row["node_external_id"]
                dimensions["task_name"] = row["node_name"]
            records.append(NativeRecord(
                entity_id=entity_id, provider_field=row["metric_id"], period=_month(row["date_start"]),
                value=value, unit=_metric_unit(row["metric_id"]), period_basis="calendar_month",
                period_start=row["date_start"], period_end=row["date_end"],
                published_at=published_at, dimensions=dimensions,
                raw={"provider_row": row, "source_product": source_product,
                     "release": release, "commit": commit}, slice_key=slice_key,
            ))
            artifact_rows.append(row)
            counts["normalized_records"] += 1
    return records, _normalized_json_lines(artifact_rows), counts


def validate_monthly_slice(records: list[NativeRecord], *, mapped_task_ids: set[str] | None = None) -> list[str]:
    """Return source-specific release-gate errors without mutating provider values."""
    errors: list[str] = []
    grouped: dict[tuple[str, str], dict[str, NativeRecord]] = {}
    for record in records:
        if record.unit == "percent" and not 0 <= float(record.value) <= 100:
            errors.append(f"percent_out_of_range:{record.entity_id}:{record.provider_field}")
        if record.unit == "scale_1_5" and not 1 <= float(record.value) <= 5:
            errors.append(f"autonomy_out_of_range:{record.entity_id}")
        key = (record.entity_id, record.period)
        previous = grouped.setdefault(key, {}).get(record.provider_field)
        if previous is not None and float(previous.value) != float(record.value):
            errors.append(f"duplicate_metric_conflict:{record.entity_id}:{record.provider_field}:{record.period}")
        grouped[key][record.provider_field] = record
    patterns = {
        "collaboration_directive_pct", "collaboration_feedback_loop_pct",
        "collaboration_task_iteration_pct", "collaboration_validation_pct",
        "collaboration_learning_pct", "collaboration_none_pct",
    }
    for (entity, period), values in grouped.items():
        buckets = {key: values[key] for key in {
            "collaboration_bucket_automation_pct", "collaboration_bucket_augmentation_pct"} if key in values}
        if len(buckets) == 2 and abs(sum(float(item.value) for item in buckets.values()) - 100) > 0.15:
            errors.append(f"automation_augmentation_sum_invalid:{entity}:{period}")
        if patterns <= set(values) and abs(sum(float(values[key].value) for key in patterns) - 100) > 0.15:
            errors.append(f"collaboration_pattern_sum_invalid:{entity}:{period}")
    # Mapping coverage is reported by the adapter as a warning.  An unmapped
    # public task is still a valid task observation and must not drop its slice.
    return errors


def _find_field(fieldnames: list[str], *candidates: str) -> str:
    normalized = {name.casefold().replace("*", "").replace("_", " ").strip(): name
                  for name in fieldnames}
    for candidate in candidates:
        key = candidate.casefold().replace("*", "").replace("_", " ").strip()
        if key in normalized:
            return normalized[key]
    return ""


def parse_taxonomy_csvs(task_path: Path, soc_path: Path, *, source_version: str,
                        slice_key_tasks: str, slice_key_soc: str) -> tuple[list[ReferenceEntityInput], list[EntityRelationInput], bytes, bytes]:
    """Build SOC/task relations using stable identifiers only."""
    entities: dict[str, ReferenceEntityInput] = {}
    relations: list[EntityRelationInput] = []
    with soc_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("soc_structure_missing_header")
        code_fields = [field for candidate in (
            "Major Group", "Minor Group", "Broad Occupation", "Detailed Occupation", "Detailed O*NET-SOC",
        ) if (field := _find_field(reader.fieldnames, candidate))]
        scalar_code_field = _find_field(reader.fieldnames, "SOC Code", "O*NET-SOC Code", "code")
        if scalar_code_field:
            code_fields.append(scalar_code_field)
        name_field = _find_field(reader.fieldnames, "Title", "Occupation", "Name", "SOC Title",
                                 "SOC or O*NET-SOC 2019 Title")
        if not code_fields or not name_field:
            raise ValueError("soc_structure_schema_missing_stable_id")
        soc_rows = [{str(k): (v or "") for k, v in row.items()} for row in reader]
    for row in soc_rows:
        # The official structure is a wide hierarchy. A leaf row can contain
        # both its SOC occupation and Detailed O*NET-SOC code, so every
        # non-empty stable code must become addressable (not merely the first).
        for code in dict.fromkeys(row[field].strip() for field in code_fields if row[field].strip()):
            entity_id = f"SOC:{code}"
            kind = "soc_major_group" if code.endswith("-0000") else "soc_occupation"
            entities[entity_id] = ReferenceEntityInput(entity_id=entity_id, kind=kind,
                                                        canonical_name=row[name_field].strip() or code,
                                                        metadata={"soc_code": code, "source_version": source_version})
            parent = _major_group_code(code)
            if kind == "soc_occupation" and parent and parent != code:
                entities.setdefault(f"SOC:{parent}", ReferenceEntityInput(
                    entity_id=f"SOC:{parent}", kind="soc_major_group", canonical_name=parent,
                    metadata={"soc_code": parent, "source_version": source_version,
                              "synthetic_from_detailed_occupation": True}))
                relations.append(EntityRelationInput(parent_entity_id=f"SOC:{parent}", child_entity_id=entity_id,
                                                     relation_type="contains_occupation", source_version=source_version,
                                                     slice_key=slice_key_soc, metadata={"soc_code": code}))
    with task_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("onet_tasks_missing_header")
        task_id_field = _find_field(reader.fieldnames, "Task ID", "task_id")
        task_name_field = _find_field(reader.fieldnames, "Task", "Task Statement", "task_statement")
        task_type_field = _find_field(reader.fieldnames, "Task Type", "task_type", "Type")
        occupation_field = _find_field(reader.fieldnames, "O*NET-SOC Code", "SOC Code", "onet soc code")
        if not task_id_field or not occupation_field:
            raise ValueError("onet_tasks_schema_missing_stable_id")
        task_rows = [{str(k): (v or "") for k, v in row.items()} for row in reader]
    for row in task_rows:
        task_id = row[task_id_field].strip()
        occupation = row[occupation_field].strip()
        if not task_id:
            continue
        task_entity = f"ONET_TASK:{task_id}"
        entities[task_entity] = ReferenceEntityInput(
            entity_id=task_entity, kind="onet_task",
            canonical_name=(row.get(task_name_field, "").strip() if task_name_field else task_id),
            metadata={"task_id": task_id, "task_type": row.get(task_type_field, "").strip()
                      if task_type_field else "", "source_version": source_version,
                      "occupation_mapping_status": "mapped" if occupation else "unmapped"},
        )
        if occupation:
            occupation_entity = f"SOC:{occupation}"
            entities.setdefault(occupation_entity, ReferenceEntityInput(
                entity_id=occupation_entity, kind="soc_occupation", canonical_name=occupation,
                metadata={"soc_code": occupation, "source_version": source_version,
                          "declared_by_onet_task_mapping": True}))
            major_group = _major_group_code(occupation)
            if major_group and major_group != occupation:
                entities.setdefault(f"SOC:{major_group}", ReferenceEntityInput(
                    entity_id=f"SOC:{major_group}", kind="soc_major_group", canonical_name=major_group,
                    metadata={"soc_code": major_group, "source_version": source_version,
                              "synthetic_from_detailed_occupation": True}))
                relations.append(EntityRelationInput(
                    parent_entity_id=f"SOC:{major_group}", child_entity_id=occupation_entity,
                    relation_type="contains_occupation", source_version=source_version,
                    slice_key=slice_key_soc,
                    metadata={"soc_code": occupation, "declared_by_onet_task_mapping": True}))
            relations.append(EntityRelationInput(parent_entity_id=f"SOC:{occupation}", child_entity_id=task_entity,
                                                 relation_type="has_task", source_version=source_version,
                                                 slice_key=slice_key_tasks, metadata={"task_id": task_id}))
    return list(entities.values()), relations, _normalized_json_lines(task_rows), _normalized_json_lines(soc_rows)


def parse_research_snapshot_csv(path: Path, *, metric_field: str, entity_prefix: str,
                                release_date: str, published_at: datetime | None,
                                slice_key: str) -> tuple[list[NativeRecord], bytes]:
    """Normalize an exposure/penetration research file without inventing months."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("research_snapshot_missing_header")
        if entity_prefix == "SOC":
            id_field = _find_field(reader.fieldnames, "SOC Code", "soc_code", "SOC", "O*NET-SOC Code",
                                   "occ_code")
        else:
            id_field = _find_field(reader.fieldnames, "Task ID", "task_id", "node_external_id", "task")
        value_field = _find_field(reader.fieldnames, metric_field, "value")
        if not id_field or not value_field:
            raise ValueError("research_snapshot_schema_missing_stable_id_or_value")
        rows = [{str(key): (value or "") for key, value in row.items()} for row in reader]
    period = (published_at.date().isoformat() if published_at else release_date.replace("_", "-"))
    records = []
    for row in rows:
        source_entity = row[id_field].strip()
        if not source_entity:
            continue
        has_stable_task_id = entity_prefix != "RESEARCH_TASK" or id_field != _find_field(reader.fieldnames, "task")
        entity = (source_entity if has_stable_task_id else
                  hashlib.sha256(source_entity.encode("utf-8")).hexdigest()[:24])
        try:
            value = float(row[value_field])
        except ValueError as exc:
            raise ValueError(f"research_snapshot_value_invalid:{entity}") from exc
        if not 0 <= value <= 1:
            raise ValueError(f"research_snapshot_ratio_out_of_range:{entity}")
        records.append(NativeRecord(
            entity_id=f"{entity_prefix}:{entity}", provider_field=metric_field, period=period,
            value=value, unit="ratio_0_1", period_basis="research_snapshot",
            period_start=period, period_end=period, published_at=published_at,
            dimensions={"classification": ("soc_occupation" if entity_prefix == "SOC" else
                                               "research_task" if entity_prefix == "RESEARCH_TASK" else "onet"),
                        "release_date": release_date.replace("_", "-"),
                        "period_basis": "research_snapshot", "node_external_id": entity,
                        "task_name": source_entity if entity_prefix == "RESEARCH_TASK" else "",
                        "stable_onet_mapping": "unavailable" if entity_prefix == "RESEARCH_TASK" else "available"},
            raw={"provider_row": row, "research_snapshot": True,
                 "source_native_identifier": source_entity}, slice_key=slice_key,
        ))
    return records, _normalized_json_lines(rows)


class AnthropicEconomicIndexAdapter:
    source_id = "anthropic_economic_index"
    dataset_id = "ai_work_adoption"

    def __init__(self, *, client=None, clock=None, max_file_bytes: int = 367001600,
                 local_file_overrides: dict[str, Path] | None = None):
        self.client = client
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.max_file_bytes = max_file_bytes
        self.local_file_overrides = {name: Path(path) for name, path in (local_file_overrides or {}).items()}

    def _download(self, client, url: str) -> tuple[Path, str, int, dict[str, str]]:
        """Stream a remote file into a temporary file while calculating SHA-256."""
        override = self.local_file_overrides.get(Path(url).name)
        if override is not None:
            size = override.stat().st_size
            if size > self.max_file_bytes:
                raise ValueError("upstream_file_exceeds_max_bytes")
            digest = hashlib.sha256()
            temporary = tempfile.NamedTemporaryFile(prefix="ats-aei-", suffix=".download", delete=False)
            destination = Path(temporary.name)
            try:
                with override.open("rb") as source, temporary:
                    while chunk := source.read(1024 * 1024):
                        digest.update(chunk)
                        temporary.write(chunk)
            except Exception:
                destination.unlink(missing_ok=True)
                raise
            return destination, digest.hexdigest(), size, {
                "content-length": str(size), "x-ats-local-override": str(override)
            }
        last_error: Exception | None = None
        digest = hashlib.sha256()
        total = 0
        expected_total: int | None = None
        response_headers: dict[str, str] = {}
        temporary = tempfile.NamedTemporaryFile(prefix="ats-aei-", suffix=".download", delete=False)
        destination = Path(temporary.name)
        temporary.close()
        for _attempt in range(DOWNLOAD_ATTEMPTS):
            request_headers = {"User-Agent": USER_AGENT}
            if total:
                request_headers["Range"] = f"bytes={total}-"
            try:
                with client.stream("GET", url, headers=request_headers, timeout=300,
                                   follow_redirects=True) as response:
                    response.raise_for_status()
                    response_headers = dict(getattr(response, "headers", {}) or {})
                    declared = response_headers.get("content-length", "")
                    status_code = int(getattr(response, "status_code", 200))
                    if total and status_code != 206:
                        # Server ignored Range. Restart explicitly so bytes are
                        # never duplicated and the final hash remains meaningful.
                        total, expected_total, digest = 0, None, hashlib.sha256()
                        destination.write_bytes(b"")
                    content_range = response_headers.get("content-range", "")
                    if "/" in content_range and content_range.rsplit("/", 1)[1].isdigit():
                        expected_total = int(content_range.rsplit("/", 1)[1])
                    elif declared and total == 0:
                        expected_total = int(declared)
                    if expected_total is not None and expected_total > self.max_file_bytes:
                        raise ValueError("upstream_file_exceeds_max_bytes")
                    with destination.open("ab") as handle:
                        for chunk in response.iter_bytes():
                            total += len(chunk)
                            if total > self.max_file_bytes:
                                raise ValueError("upstream_file_exceeds_max_bytes")
                            digest.update(chunk)
                            handle.write(chunk)
                if expected_total is not None and total != expected_total:
                    raise ValueError("content_length_mismatch")
                return destination, digest.hexdigest(), total, response_headers
            except Exception as exc:
                last_error = exc
                if _failure_status(exc) == IngestionStatus.VALIDATION_FAILED \
                        and "content_length_mismatch" not in str(exc):
                    break
        assert last_error is not None
        destination.unlink(missing_ok=True)
        raise last_error

    def fetch(self, request: FetchRequest) -> AdapterBatch:
        import httpx

        fetched_at = self.clock().astimezone(timezone.utc)
        client = self.client or httpx.Client(follow_redirects=True)
        close_client = self.client is None
        temporary_paths: list[Path] = []
        try:
            response = client.get(HF_API, headers={"User-Agent": USER_AGENT}, timeout=60)
            response.raise_for_status()
            metadata = response.json()
            releases = discover_releases(metadata)
            if not releases:
                return AdapterBatch(source_id=request.source_id, dataset_id=request.dataset_id,
                                    status=IngestionStatus.NOT_YET_PUBLISHED, fetched_at=fetched_at)
            release = releases[0]
            commit = release["commit"]
            release_id = release["release"]
            published_at = _iso_datetime(metadata.get("lastModified"))
            artifacts: list[AdapterArtifact] = []
            records: list[NativeRecord] = []
            entities: list[ReferenceEntityInput] = []
            relations: list[EntityRelationInput] = []
            failures: list[AdapterFailure] = []
            warnings: list[str] = []

            for product, path in (("claude_ai", release["claude_path"]),
                                  ("1p_api", release["api_path"])):
                filename = Path(path).name
                key = f"{product}:{release_id}"
                try:
                    downloaded, file_hash, size, _headers = self._download(client, _resolve_url(commit, path))
                    temporary_paths.append(downloaded)
                    parsed, payload, counts = parse_monthly_csv(
                        downloaded, source_product=product, release=release_id, commit=commit,
                        published_at=published_at, slice_key=key)
                    if counts["eligible_rows"] != counts["normalized_records"] + counts["excluded_metrics"]:
                        raise ValueError("eligible_normalized_reconciliation_failed")
                    requested = set(request.periods)
                    if requested:
                        parsed = [record for record in parsed if record.period in requested]
                    records.extend(parsed)
                    artifacts.append(AdapterArtifact(
                        artifact_key=key, payload=payload, query_scope={"release": release_id, "source_product": product,
                                                                         "geo_id": "GLOBAL", "classification": ["soc_occupation", "onet"]},
                        source_url=_resolve_url(commit, path),
                        source_version=f"{commit}:{release['directory']}:{filename}:{file_hash}",
                        media_type="application/x-ndjson", retention="query_slice",
                        storage_mode="filtered", pointer=_resolve_url(commit, path),
                        metadata={"repository_commit": commit, "release": release_id, "file_path": path,
                                  "upstream_sha256": file_hash, "upstream_bytes": size,
                                  "filtered_sha256": hashlib.sha256(payload).hexdigest(), "filtered_bytes": len(payload),
                                  "filter": "GLOBAL × (soc_occupation:0,1 | onet:0)",
                                  "source_product": product, "license": "CC-BY", "parser_version": "v1",
                                  "schema_version": "aei_v6", "counts": counts,
                                  "data_documentation_url": _resolve_url(commit, f"{release['directory']}/data_documentation.md")},
                    ))
                except Exception as exc:
                    failures.append(AdapterFailure(status=_failure_status(exc), message=str(exc), slice_key=key))

            taxonomy_paths: dict[str, tuple[Path, str, int, str]] = {}
            taxonomy_files = discover_taxonomy_paths(metadata)
            for filename in TAXONOMY_FILENAMES:
                path = taxonomy_files.get(filename, "")
                if not path:
                    failures.append(AdapterFailure(status=IngestionStatus.NO_COVERAGE,
                                                   message=f"taxonomy_file_not_published:{filename}", slice_key=filename))
                    continue
                try:
                    downloaded, file_hash, size, _ = self._download(client, _resolve_url(commit, path))
                    temporary_paths.append(downloaded)
                    taxonomy_paths[filename] = (downloaded, file_hash, size, _resolve_url(commit, path))
                except Exception as exc:
                    failures.append(AdapterFailure(status=_failure_status(exc),
                                                   message=str(exc), slice_key=path))
            if set(taxonomy_paths) == set(TAXONOMY_FILENAMES):
                try:
                    task, task_hash, task_size, task_url = taxonomy_paths[TAXONOMY_FILENAMES[0]]
                    soc, soc_hash, soc_size, soc_url = taxonomy_paths[TAXONOMY_FILENAMES[1]]
                    task_key, soc_key = f"onet_tasks:{commit}", f"soc_structure:{commit}"
                    source_version = f"{commit}:taxonomy"
                    entities, relations, task_payload, soc_payload = parse_taxonomy_csvs(
                        task, soc, source_version=source_version, slice_key_tasks=task_key, slice_key_soc=soc_key)
                    artifacts.extend([
                        AdapterArtifact(artifact_key=task_key, payload=task_payload, query_scope={"taxonomy": "onet_tasks"},
                                        source_url=task_url, source_version=f"{commit}:{taxonomy_files[TAXONOMY_FILENAMES[0]]}:{task_hash}",
                                        media_type="application/x-ndjson", retention="query_slice", storage_mode="filtered", pointer=task_url,
                                        metadata={"upstream_sha256": task_hash, "upstream_bytes": task_size, "taxonomy_version": commit}),
                        AdapterArtifact(artifact_key=soc_key, payload=soc_payload, query_scope={"taxonomy": "soc_structure"},
                                        source_url=soc_url, source_version=f"{commit}:{taxonomy_files[TAXONOMY_FILENAMES[1]]}:{soc_hash}",
                                        media_type="application/x-ndjson", retention="query_slice", storage_mode="filtered", pointer=soc_url,
                                        metadata={"upstream_sha256": soc_hash, "upstream_bytes": soc_size, "taxonomy_version": commit}),
                    ])
                    mapped_task_ids = {relation.child_entity_id.removeprefix("ONET_TASK:")
                                       for relation in relations if relation.relation_type == "has_task"}
                    for product in ("claude_ai", "1p_api"):
                        product_records = [record for record in records
                                           if record.dimensions.get("source_product") == product]
                        task_ids = {record.dimensions.get("task_id", "") for record in product_records
                                    if record.dimensions.get("classification") == "onet"}
                        unmapped = sorted(task_ids - mapped_task_ids - {""})
                        if unmapped:
                            warnings.append(f"unmapped_task_ids:{product}:{','.join(unmapped)}")
                        if task_ids:
                            coverage = len(task_ids & mapped_task_ids) / len(task_ids)
                            if coverage < 0.99:
                                warnings.append(f"task_relation_coverage_below_threshold:{product}:{coverage:.4f}")
                        errors = validate_monthly_slice(product_records)
                        if errors:
                            records = [record for record in records if record not in product_records]
                            failures.append(AdapterFailure(status=IngestionStatus.VALIDATION_FAILED,
                                                           message=";".join(errors),
                                                           slice_key=f"{product}:{release_id}"))
                except Exception as exc:
                    failures.append(AdapterFailure(status=_failure_status(exc),
                                                   message=str(exc), slice_key="taxonomy"))

            files = _siblings(metadata)
            for path, provider_field, prefix, artifact_kind in (
                (EXPOSURE_FILES[0], "observed_exposure", "SOC", "observed_exposure"),
                (EXPOSURE_FILES[1], "penetration", "RESEARCH_TASK", "task_penetration"),
            ):
                key = f"{artifact_kind}:{commit}"
                if path not in files:
                    failures.append(AdapterFailure(status=IngestionStatus.NO_COVERAGE,
                                                   message=f"snapshot_file_not_published:{path}", slice_key=key))
                    continue
                try:
                    downloaded, file_hash, size, _ = self._download(client, _resolve_url(commit, path))
                    temporary_paths.append(downloaded)
                    parsed, payload = parse_research_snapshot_csv(
                        downloaded, metric_field=provider_field, entity_prefix=prefix,
                        release_date=release_id, published_at=published_at, slice_key=key)
                    records.extend(parsed)
                    artifacts.append(AdapterArtifact(
                        artifact_key=key, payload=payload, query_scope={"research_snapshot": artifact_kind},
                        source_url=_resolve_url(commit, path), source_version=f"{commit}:{path}:{file_hash}",
                        media_type="application/x-ndjson", retention="query_slice", storage_mode="filtered",
                        pointer=_resolve_url(commit, path),
                        metadata={"repository_commit": commit, "file_path": path,
                                  "upstream_sha256": file_hash, "upstream_bytes": size,
                                  "filtered_sha256": hashlib.sha256(payload).hexdigest(),
                                  "period_basis": "research_snapshot", "license": "CC-BY", "parser_version": "v1"},
                    ))
                except Exception as exc:
                    failures.append(AdapterFailure(status=_failure_status(exc),
                                                   message=str(exc), slice_key=key))

            status = IngestionStatus.PARTIAL if failures and (records or relations) else (
                IngestionStatus.SUCCEEDED if (records or relations) else IngestionStatus.VALIDATION_FAILED)
            return AdapterBatch(source_id=request.source_id, dataset_id=request.dataset_id, status=status,
                                fetched_at=fetched_at, records=records, artifacts=artifacts, entities=entities,
                                relations=relations, failures=failures,
                                provider_metadata={"repository": REPOSITORY, "repository_commit": commit,
                                                   "release": release_id, "published_at": published_at.isoformat() if published_at else "",
                                                   "warnings": warnings})
        finally:
            for path in temporary_paths:
                path.unlink(missing_ok=True)
            if close_client:
                client.close()
