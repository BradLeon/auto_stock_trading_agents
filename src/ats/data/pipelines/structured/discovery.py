"""Registry-driven, source-native release discovery for structured sources."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid
from typing import Protocol

from ...adapters.structured.registry import build_ingestion, ingest_source, runtime_spec
from ...catalog.structured import StructuredCatalog
from ...core.structured_models import DiscoveryResult, DiscoveryStatus, FetchRequest


class DiscoveringAdapter(Protocol):
    def discover(self, request: FetchRequest) -> DiscoveryResult: ...


def _failure_result(*, source_id: str, dataset_id: str, exc: Exception) -> DiscoveryResult:
    """Keep transport failure distinct from schema/methodology failure."""
    name = type(exc).__name__.casefold()
    status = (DiscoveryStatus.UNREACHABLE if any(token in name for token in
              ("connect", "timeout", "network", "transport", "ssl"))
              else DiscoveryStatus.VALIDATION_FAILED)
    return DiscoveryResult(source_id=source_id, dataset_id=dataset_id, checked_at=_now(),
                           status=status, diagnostics={"error": f"{type(exc).__name__}:{exc}"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_due(repository, source_id: str, dataset_id: str, *, force: bool) -> bool:
    if force:
        return True
    # The scheduler calls weekly.  A source checked in the preceding six days is
    # therefore not due unless the operator requests a forced check.
    checks = repository.source_checks(source_id=source_id, dataset_id=dataset_id, limit=1)
    if not checks:
        return True
    last = datetime.fromisoformat(checks[0]["checked_at"])
    return (_now() - last).total_seconds() >= 6 * 24 * 3600


def discover_source(repository, source_id: str, *, catalog: StructuredCatalog | None = None,
                    force: bool = False) -> dict:
    """Check one governed source and persist the outcome even when nothing is new."""
    catalog = catalog or StructuredCatalog.load()
    adapter, request = build_ingestion(source_id, catalog=catalog)
    if not _is_due(repository, source_id, request.dataset_id, force=force):
        return {"source_id": source_id, "dataset_id": request.dataset_id, "status": "not_due"}
    if not hasattr(adapter, "discover"):
        result = DiscoveryResult(source_id=source_id, dataset_id=request.dataset_id,
                                 checked_at=_now(), status=DiscoveryStatus.NEW_RELEASE,
                                 diagnostics={"legacy_fetch_only": True})
    else:
        try:
            result = adapter.discover(request)
        except (ConnectionError, TimeoutError) as exc:
            result = _failure_result(source_id=source_id, dataset_id=request.dataset_id, exc=exc)
        except Exception as exc:  # discovery schema errors must be explicit and isolated
            result = _failure_result(source_id=source_id, dataset_id=request.dataset_id, exc=exc)
    previous = repository.source_checks(source_id=source_id, dataset_id=request.dataset_id, limit=1)
    # A release which was merely discovered is not necessarily ingested. Only
    # suppress it when that immutable identity has completed ingestion.
    if (result.status == DiscoveryStatus.NEW_RELEASE and previous
            and result.latest_upstream_identity
            and result.latest_upstream_identity == previous[0]["latest_ingested_identity"]):
        result = result.model_copy(update={"status": DiscoveryStatus.NO_CHANGE, "candidates": []})
    repository.save_source_check(
        source_id=source_id, dataset_id=request.dataset_id, status=result.status.value,
        latest_upstream_identity=result.latest_upstream_identity,
        latest_ingested_identity=(previous[0]["latest_ingested_identity"] if previous else ""),
        latest_available_period=result.latest_available_period,
        candidates=[item.model_dump(mode="json") for item in result.candidates],
        request_identity={"adapter": type(adapter).__name__}, diagnostics=result.diagnostics,
        at=result.checked_at)
    return {"source_id": source_id, "dataset_id": request.dataset_id,
            "status": result.status.value, "candidates": [item.model_dump(mode="json") for item in result.candidates],
            "latest_upstream_identity": result.latest_upstream_identity,
            "latest_available_period": result.latest_available_period,
            "diagnostics": result.diagnostics}


def release_check(repository, *, group: str = "", source_ids: list[str] | None = None,
                  ingest_new: bool = False, force: bool = False,
                  dataset_id: str = "", catalog: StructuredCatalog | None = None) -> dict:
    """Run due checks independently; a source failure never blocks another source."""
    catalog = catalog or StructuredCatalog.load()
    rows = catalog.raw.get("sources", {}) or {}
    selected = source_ids or [source_id for source_id, row in rows.items()
                              if (not group or row.get("discovery_group") == group)
                              and (not dataset_id or dataset_id in (row.get("datasets") or []))]
    outcomes: list[dict] = []
    for source_id in selected:
        try:
            outcome = discover_source(repository, source_id, catalog=catalog, force=force)
            if ingest_new and outcome["status"] == DiscoveryStatus.NEW_RELEASE.value:
                identity = outcome.get("latest_upstream_identity", "")
                owner = str(uuid.uuid4())
                if identity and not repository.claim_discovery_candidate(
                        source_id=source_id, candidate_identity=identity, owner_id=owner):
                    outcome.update({"status": "no_change", "diagnostics": {
                        **outcome.get("diagnostics", {}), "candidate_already_claimed": identity}})
                    outcomes.append(outcome)
                    continue
                ingested = ingest_source(repository, source_id, catalog=catalog, force=True,
                                         query_scope={"discovery_candidates": outcome["candidates"]})
                outcome["ingestion"] = ingested
                if ingested.get("status") in {"succeeded", "no_change", "partial"}:
                    repository.save_source_check(
                        source_id=source_id, dataset_id=outcome["dataset_id"],
                        status=("succeeded" if ingested.get("status") == "succeeded"
                                else ingested["status"]),
                        latest_upstream_identity=outcome.get("latest_upstream_identity", ""),
                        latest_ingested_identity=outcome.get("latest_upstream_identity", ""),
                        latest_available_period=outcome.get("latest_available_period", ""),
                        candidates=outcome.get("candidates", []),
                        request_identity={"adapter": "release_check", "ingest_new": True},
                        diagnostics={"ingestion_run_id": ingested.get("run_id", ""),
                                     "ingestion_status": ingested.get("status", "")},
                    )
            outcomes.append(outcome)
        except Exception as exc:  # registration errors are source-local too
            outcomes.append({"source_id": source_id, "status": "validation_failed",
                             "diagnostics": {"error": f"{type(exc).__name__}:{exc}"}})
    failed = [item for item in outcomes if item["status"] in {
        "unreachable", "validation_failed", "methodology_break"}]
    updated = [item for item in outcomes if item["status"] == "new_release"]
    return {"status": "partial" if failed and len(failed) != len(outcomes)
            else ("validation_failed" if failed else ("succeeded" if updated else "no_change")),
            "sources": outcomes}
