"""Data-only acquisition and admission for registered article sources.

The Chain article collector historically fetched a page and immediately invoked
LLM evidence extraction.  Scheduled refresh must be able to acquire and admit
immutable documents without running an Agent or a model, so this module owns that
first half of the path.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ....schemas.chain import ArticleRef
from ...admission import ValidationIssue, ValidationResult
from ...document_assets import ingest as ingest_document
from ...document_assets import stable_key
from ...document_types import semantic_type
from ...source_cache import root as source_cache_root
from ...stores.unstructured import get_platform_unstructured_store
from .source_acceptance import load_policy


@dataclass(frozen=True)
class ArticleCandidate:
    expected_entity: str
    claimed_entity: str
    target_period: str
    claimed_period: str
    expected_semantic: str
    claimed_semantic: str
    text: str
    source: str
    source_url: str
    external_id: str
    title: str
    published_at: str
    carrier_format: str
    completeness: str
    min_chars: int
    allow_partial: bool
    related_entities: tuple[str, ...]
    metadata: dict[str, Any]
    discovered_at: str
    candidate_id: str
    content_hash: str


@dataclass(frozen=True)
class Material:
    ref: ArticleRef
    body: str
    provenance: dict[str, str]
    status: str = "fetched"
    reason: str = ""


def _article_sources() -> dict[str, Any]:
    from ....config import _config_dir, _load_yaml
    from ....schemas.chain import ArticleSourceDef

    rows = (_load_yaml(_config_dir() / "data" / "unstructured.yaml").get(
        "sources", {}) or {})
    output = {}
    for source_id, row in rows.items():
        row = row or {}
        if row.get("source_kind", "article") != "article":
            continue
        ingestion = row.get("ingestion")
        if not isinstance(ingestion, dict):
            raise ValueError(
                f"unstructured source {source_id!r} lacks its governed ingestion contract")
        output[source_id] = ArticleSourceDef(
            id=source_id, label=str(row.get("provider") or ""),
            adapter=str(row.get("adapter") or ""),
            cadence=str(row.get("cadence") or "weekly"), **ingestion)
    return output


def _provenance(adapter: Any, ref: ArticleRef) -> dict[str, str]:
    callback = getattr(adapter, "provenance", None)
    raw = callback(ref) or {} if callable(callback) else {}
    output = {str(key): str(value) for key, value in raw.items()
              if value not in (None, "")}
    output.setdefault("native_id", ref.slug)
    output.setdefault("canonical_url", ref.url)
    return output


def _matches(ref: ArticleRef, source: Any) -> bool:
    if not source.match:
        return True
    haystack = re.sub(r"[^a-z0-9]+", " ", f"{ref.slug} {ref.title}".lower())
    return any(re.search(rf"\b{re.escape(re.sub(r'[^a-z0-9]+', ' ', word))}\b",
                         haystack) for word in source.match)


def _quarantine(candidate: ArticleCandidate) -> str:
    base = source_cache_root()
    if base is None or not candidate.text:
        return ""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", candidate.source).strip("-") or "source"
    path = base / ".quarantine" / slug / f"{candidate.candidate_id}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(candidate.text, encoding="utf-8")
    return str(path)


def _managed_document(row: dict[str, Any] | None, *, store: Any) -> bool:
    """Do not mistake a legacy/externally housed body for target publication."""
    if not row:
        return False
    root = source_cache_root()
    path = Path(str(row.get("local_path") or ""))
    if root is None or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        return False
    from ...document_assets import read_document

    return bool(read_document(str(row.get("document_id") or ""), store=store))


def _candidate(source: Any, material: Material, body: str, *, discovered_at: datetime,
               policy: dict[str, Any]) -> ArticleCandidate:
    from ....config import load_entities
    from ...articles.material_quality import visible_analysis_view, association_evidence, POLICY_VERSION

    provenance = dict(material.provenance)
    title = provenance.get("title") or material.ref.title
    view = visible_analysis_view(body, title=title, source=source.id)
    evidence = association_evidence({"title": title,
        "lede": provenance.get("lede", ""), "subtitle": provenance.get("subtitle", ""),
        "body": view.text}, list(load_entities()))
    for row in evidence:
        if row["field"] == "body":
            row.update(view.locator(row["start"], row["end"]))
    native_id = provenance.get("native_id") or material.ref.slug
    canonical_url = provenance.get("canonical_url") or material.ref.url
    digest = hashlib.sha256((body or "").strip().encode("utf-8")).hexdigest()
    identity = "|".join((source.id, native_id, canonical_url, digest))
    completeness = str(provenance.get("completeness") or "full").lower()
    visibility = {}
    if source.id == "semianalysis":
        from ...articles.material_quality import completeness_assessment

        visibility = completeness_assessment(body, stored=completeness)
        completeness = visibility["completeness"]
        provenance["truncation_reason"] = visibility["reason"] or provenance.get("truncation_reason", "")
        provenance["completeness"] = completeness
    source_policy = policy.get("policy") or {}
    minimum = int(source_policy.get("minimum_body_chars") or source.min_body_chars)
    if completeness == "partial" and source_policy.get("allow_partial_bodies"):
        minimum = int(source_policy.get("minimum_partial_body_chars") or minimum)
    # Recompute old associations as well: legacy labels are lineage, not evidence.
    related = tuple(sorted({row["entity"] for row in evidence}))
    semantic = semantic_type(source.doc_type).value
    return ArticleCandidate(
        expected_entity=source.entity, claimed_entity=source.entity,
        target_period="", claimed_period="", expected_semantic=semantic,
        claimed_semantic=semantic, text=body, source=source.id,
        source_url=canonical_url, external_id=native_id, title=title,
        published_at=(provenance.get("published_at") or (
            material.ref.published_at.isoformat() if material.ref.published_at else "")),
        carrier_format=("email" if provenance.get("source_carrier") == "imap" else "html"),
        completeness=completeness,
        min_chars=minimum,
        allow_partial=bool((policy.get("policy") or {}).get("allow_partial_bodies")),
        related_entities=related, metadata={"provenance": provenance,
            "policy_version": POLICY_VERSION, "entity_evidence": evidence,
            "effective_chars": len(view.text), "cleaner_version": view.version,
            "original_hash": view.original_hash, "visibility": visibility},
        discovered_at=discovered_at.isoformat(),
        candidate_id=hashlib.sha1(identity.encode("utf-8")).hexdigest()[:24],
        content_hash=digest,
    )


def _admission(candidate: ArticleCandidate, material: Material, *,
               source: Any, policy: dict[str, Any], human_review_approved: bool,
               source_eligible: bool) -> tuple[ValidationResult, str]:
    issues: list[ValidationIssue] = []
    if material.status == "budget_deferred":
        issue = ValidationIssue("acquisition", "budget_deferred", material.reason)
        return ValidationResult("deferred", (issue,), {"body_acquired": False}), "deferred"
    if material.status != "fetched":
        issues.append(ValidationIssue("acquisition", material.status,
                                      material.reason or "article body was not fetched"))
    if not candidate.external_id:
        issues.append(ValidationIssue("identity", "native_id_missing"))
    if not candidate.source_url:
        issues.append(ValidationIssue("provenance", "canonical_url_missing"))
    if not candidate.title.strip():
        issues.append(ValidationIssue("provenance", "title_missing"))
    if not candidate.published_at:
        issues.append(ValidationIssue("provenance", "published_at_missing"))
    if candidate.metadata.get("effective_chars", len(candidate.text.strip())) < candidate.min_chars:
        issues.append(ValidationIssue("quality", "body_below_minimum",
                                      f"{len(candidate.text.strip())} < {candidate.min_chars}"))
    if candidate.completeness in {"partial", "teaser"} and not candidate.allow_partial:
        issues.append(ValidationIssue("quality", f"body_{candidate.completeness}_not_allowed"))
    if candidate.completeness == "teaser":
        issues.append(ValidationIssue("quality", "teaser_not_fact_input"))
    if candidate.completeness == "partial" and (policy.get("policy") or {}).get("require_substantive_partial"):
        from ...articles.material_quality import substantive_preview

        if not substantive_preview(candidate.text, minimum_chars=candidate.min_chars):
            issues.append(ValidationIssue("quality", "partial_not_substantive"))
    if candidate.completeness not in {"full", "partial", "teaser"}:
        issues.append(ValidationIssue("quality", "body_completeness_unknown"))
    provenance = material.provenance
    allowed_evidence = (policy.get("policy") or {}).get(
        "entity_evidence_fields", ["title"])
    verified = any(row["field"] in allowed_evidence and
                   row["entity"] != candidate.expected_entity for row in
                   candidate.metadata.get("entity_evidence", []))
    if (policy.get("policy") or {}).get("require_entity_verified") and not verified:
        issues.append(ValidationIssue("identity", "entity_association_not_verified"))
    review_required = bool((policy.get("policy") or {}).get(
        "release_requires_human_title_url_review"))
    if review_required and not human_review_approved:
        issues.append(ValidationIssue("review", "human_title_url_review_required"))
    if not source_eligible:
        issues.append(ValidationIssue("source_gate", "source_batch_not_eligible"))
    state = "pending_human_review" if review_required and not human_review_approved \
        and not any(issue.category != "review" for issue in issues) else ""
    if state:
        return ValidationResult(state, tuple(issues), {"human_review": False}), state
    if issues:
        return ValidationResult("quarantined", tuple(issues), {issue.code: False for issue in issues}), "quarantined"
    return ValidationResult("accepted", (), {"article_quality": True}), "accepted"


def _materials_from_adapter(source: Any, policy: dict[str, Any], *,
                            now: datetime, adapter_params: dict[str, Any] | None = None,
                            store: Any = None
                            ) -> tuple[list[Material], dict[str, Any], bool]:
    started = time.monotonic()
    adapter = importlib.import_module(f"ats.data.articles.{source.adapter}")
    options = {**dict(source.params), **dict(adapter_params or {})}
    discover = getattr(adapter, "discover_with_status", None)
    try:
        if callable(discover):
            refs, status = discover(pages=source.pages, **options)
            status = dict(status or {})
        else:
            refs = adapter.discover(pages=source.pages, **options)
            status = {"status": "succeeded", "failed_slices": []}
    except Exception as exc:
        return [], {"status": "unreachable", "reason": f"{type(exc).__name__}",
                    "failed_slices": [], "source_id": source.id}, False
    refs = list(refs or [])
    lookback = int((policy.get("policy") or {}).get("lookback_days") or 0)
    since = now.date() - timedelta(days=lookback) if lookback else None
    body_budget = int((policy.get("request_budget") or {}).get(
        "max_body_requests") or source.max_per_run)
    max_attempts = max(1, int((policy.get("policy") or {}).get("max_body_attempts") or 1))
    materials: list[Material] = []
    body_requests = 0
    cache_hits = 0
    time_budget = float((policy.get("request_budget") or {}).get("max_elapsed_seconds") or 300)
    seen: set[str] = set()
    prepared = []
    exploration_limit = int((policy.get("request_budget") or {}).get("max_exploration_candidates") or 0)
    if not 0 <= exploration_limit <= body_budget:
        raise ValueError("article exploration budget must fit the body request budget")
    exploration_used = 0
    symbols = list(options.get("symbols") or (policy.get("policy") or {}).get("symbols") or [])
    for ref in refs:
        ref = ref if isinstance(ref, ArticleRef) else ArticleRef.model_validate(ref)
        if since and ref.published_at and ref.published_at < since:
            continue
        from ...articles.entity_association import title_mentions_entity
        direct = [symbol for symbol in symbols if title_mentions_entity(ref.title, symbol)]
        matches = _matches(ref, source)
        if not direct and not matches and not exploration_limit:
            continue
        provenance = _provenance(adapter, ref)
        identity = provenance.get("canonical_url") or provenance.get("native_id") or ref.slug
        if identity in seen:
            continue
        seen.add(identity)
        if not direct and not matches:
            provenance["selection_reason"] = "bounded_missing_entity_or_topic_exploration"
        else:
            provenance["selection_reason"] = "direct_title" if direct else "configured_keyword"
        prepared.append((ref, provenance, direct))
    def publication_order(row):
        stamp = row[1].get("published_at") or (row[0].published_at.isoformat() if row[0].published_at else "")
        try:
            point = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            return (point.replace(tzinfo=timezone.utc) if point.tzinfo is None else point).timestamp()
        except ValueError:
            return float("-inf")
    prepared.sort(key=lambda row: row[1].get("canonical_url") or row[0].slug)
    prepared.sort(key=publication_order, reverse=True)
    # One opportunity per configured issuer before the shared latest-first pool.
    ordered = []
    remaining = list(prepared)
    for symbol in symbols:
        item = next((row for row in remaining if symbol in row[2]), None)
        if item is not None:
            ordered.append(item)
            remaining.remove(item)
    ordered.extend(remaining)
    coverage = {symbol: {"candidates": sum(symbol in row[2] for row in prepared),
                         "requests": 0, "retries": 0, "cache_hits": 0,
                         "fetched": 0, "failed": 0, "deferred": 0} for symbol in symbols}
    for ref, provenance, direct in ordered:
        is_exploration = provenance.get("selection_reason") == "bounded_missing_entity_or_topic_exploration"
        if store is not None:
            prior = store.document_by_external_id(provenance.get("native_id") or ref.slug)
            version = store.latest_document_version(prior["document_id"]) if prior else None
            try:
                cached_at = datetime.fromisoformat((version or {}).get("fetched_at", ""))
                cached_at = cached_at.replace(tzinfo=timezone.utc) if cached_at.tzinfo is None else cached_at
            except ValueError:
                cached_at = None
            # A bounded reuse window leaves regular opportunities to check updates.
            reuse_seconds = float((policy.get("request_budget") or {}).get("cache_recheck_seconds") or 86400)
            if prior and prior.get("source") == source.id and cached_at and \
                    0 <= (now-cached_at).total_seconds() < reuse_seconds and \
                    prior.get("completeness") == "full" and _managed_document(prior, store=store):
                from ...document_assets import read_document

                body = read_document(prior["document_id"], store=store)
                metadata_reader = getattr(store, "latest_document_metadata", None)
                if callable(metadata_reader):
                    saved = metadata_reader(prior["document_id"]).get("quality", {})
                    provenance.update(saved.get("provenance", {}))
                provenance.update(title=prior.get("title", ref.title),
                                  published_at=prior.get("published_at", ""),
                                  completeness=prior.get("completeness", "full"))
                materials.append(Material(ref, body, provenance))
                cache_hits += 1
                for symbol in direct:
                    coverage[symbol]["cache_hits"] += 1
                continue
        if is_exploration and exploration_used >= exploration_limit:
            materials.append(Material(ref, "", provenance, "budget_deferred", "exploration_budget_exhausted"))
            continue
        if body_requests >= body_budget or time.monotonic()-started >= time_budget:
            materials.append(Material(ref, "", provenance, "budget_deferred",
                "body_request_budget_exhausted" if body_requests >= body_budget else
                "body_elapsed_budget_exhausted"))
            for symbol in direct:
                coverage[symbol]["deferred"] += 1
            continue
        body = ""
        failure = ""
        exploration_used += int(is_exploration)
        for _attempt in range(max_attempts):
            if body_requests >= body_budget or time.monotonic()-started >= time_budget:
                failure = "body_request_budget_exhausted"
                break
            body_requests += 1
            for symbol in direct:
                coverage[symbol]["requests"] += 1
                coverage[symbol]["retries"] += int(_attempt > 0)
            try:
                fetch_material = getattr(adapter, "fetch_material", None)
                if callable(fetch_material):
                    body, parsed = fetch_material(ref.url)
                    provenance.update({k: str(v) for k, v in parsed.items() if v})
                else:
                    body = str(adapter.fetch_body(ref.url) or "")
                if body.strip():
                    break
                failure = "body_empty"
            except Exception as exc:  # keep source errors in the run record
                failure = f"body_fetch_{type(exc).__name__}"
        materials.append(Material(ref, body, provenance,
                                  "fetched" if body.strip() else "body_unavailable",
                                  "" if body.strip() else failure))
        for symbol in direct:
            coverage[symbol]["fetched" if body.strip() else "failed"] += 1
    for row in coverage.values():
        row["status"] = ("no_direct_title_candidate" if not row["candidates"] else
            "fetched_pending_admission" if row["fetched"] else "cache_reused_pending_admission" if row["cache_hits"] else
            "deferred" if row["deferred"] else "failed")
    status["body_requests"] = body_requests
    status["body_request_budget"] = body_budget
    status.update(discovered_count=len(refs), selected_count=len(prepared), cache_hits=cache_hits,
                  elapsed_seconds=time.monotonic()-started,
                  max_elapsed_seconds=time_budget, entity_coverage=coverage,
                  exploration_candidates=exploration_used,
                  elapsed_policy="discovery_inclusive_dispatch_deadline",
                  selection_policy="issuer-opportunity-then-shared-v2")
    return materials, status, bool(refs)


def _semianalysis_materials(source: Any, policy: dict[str, Any], *, now: datetime,
                            store: Any) -> tuple[list[tuple[Any, str]], dict[str, Any], Any]:
    from ... import research

    lookback = int((policy.get("policy") or {}).get("lookback_days") or 30)
    transport = policy.get("transport")
    if not isinstance(transport, dict) or not transport.get("imap") or not transport.get(
            "research_feeds"):
        raise ValueError("semianalysis requires a registered transport contract")
    batch = research.fetch_batch(
        now - timedelta(days=lookback), store=store, source_config=transport)
    selected = [article for article in batch.articles
                if "semianalysis" in str(article.source or "").lower()]
    budget = int((policy.get("request_budget") or {}).get("max_items_per_run") or 0)
    if budget <= 0 or len(selected) > budget:
        raise ValueError("semianalysis_item_budget_exceeded")
    return selected, {"status": "succeeded" if batch.complete else "partial",
                      "transport_status": batch.transport_status,
                      "candidate_count": batch.candidate_count,
                      "duplicate_count": batch.duplicate_count}, batch


def ingest_article_source(source_id: str, *, now: datetime | None = None,
                          human_review_approved: bool = False,
                          adapter_params: dict[str, Any] | None = None,
                          store: Any = None,
                          _fallback_scope: list[str] | None = None) -> dict[str, Any]:
    """Acquire one prose source, persist candidates, admit safe bodies, and log a run."""
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    from pathlib import Path

    from ...runtime.repository import platform_data_db_path

    store_path = getattr(store, "path", None) or getattr(getattr(store, "data", None), "path", None)
    writes_platform = store is None or (store_path and
        Path(str(store_path)).expanduser().resolve() == platform_data_db_path().resolve())
    if writes_platform:
        from ...persistent_queue import require_queue_worker

        require_queue_worker(source_id)
    sources = _article_sources()
    source = sources.get(source_id)
    if source is None:
        raise ValueError(f"article source is not registered: {source_id}")
    if source_id == "yfinance_live_news" and _fallback_scope is None:
        raise ValueError("yfinance_live_news may only run as an IBKR fallback")
    policy = load_policy(source_id)
    owns_store = store is None
    store = store or get_platform_unstructured_store()
    try:
        store.register_data_source(source, kind="unstructured", at=now)
        run_id = store.begin_ingestion(source_id, kind="article_refresh", at=now)
        discovered = accepted = quarantined = pending = deferred = 0
        reason_counts: dict[str, int] = {}
        document_ids: list[str] = []
        candidate_ids: list[str] = []
        run_ids: list[str] = [run_id]
        try:
            if source_id == "semianalysis":
                selected, discovery, newsletter_batch = _semianalysis_materials(
                    source, policy, now=now, store=store)
                materials: list[tuple[Any, dict[str, Any], ArticleRef, str,
                                      dict[str, str], str, str]] = []
                for article in selected:
                    ref = ArticleRef(url=article.url, slug=article.id,
                                     title=article.title,
                                     published_at=article.published_at.date())
                    provenance = {
                        **article.source_fields,
                        "native_id": article.id,
                        "canonical_url": article.url,
                        "source_carrier": "imap" if article.id.startswith("imap:") else "rss",
                        "completeness": article.completeness,
                        "published_at": article.published_at.isoformat(),
                        "truncation_reason": article.truncation_reason,
                    }
                    materials.append((source, policy, ref, article.body, provenance,
                                      "fetched", ""))
                transport_complete = bool(newsletter_batch.complete)
            else:
                raw_materials, discovery, has_refs = _materials_from_adapter(
                    source, policy, now=now, adapter_params=adapter_params, store=store)
                materials = [(source, policy, m.ref, m.body, m.provenance,
                              m.status, m.reason) for m in raw_materials]
                transport_complete = discovery.get("status") == "succeeded" and \
                    not discovery.get("failed_slices")
                if not has_refs and discovery.get("status") == "succeeded":
                    discovery["status"] = "no_change"
                if source_id == "ibkr_news":
                    from .source_acceptance import fallback_plan

                    primary_outcome = ("unreachable" if discovery.get("status") == "unreachable"
                                       else "partial" if discovery.get("failed_slices")
                                       else "succeeded")
                    fallback = fallback_plan(source_id=source_id, outcome=primary_outcome,
                                             discovery=discovery, policy=policy)
                    discovery["fallback"] = fallback
                    if fallback.get("activate"):
                        fallback_source = sources[str(fallback["source_id"])]
                        fallback_policy = load_policy(fallback_source.id)
                        fallback_scope = list(fallback.get("entities") or [])
                        if fallback.get("scope") == "source":
                            fallback_scope = list((policy.get("policy") or {}).get(
                                "symbols") or source.params.get("symbols") or [])
                        if fallback_scope:
                            fallback_materials, fallback_discovery, _ = _materials_from_adapter(
                                fallback_source, fallback_policy, now=now,
                                adapter_params={"symbols": fallback_scope}, store=store)
                            discovery["fallback"]["attempted"] = True
                            discovery["fallback"]["discovery"] = fallback_discovery
                            discovery["fallback"]["complete"] = (
                                fallback_discovery.get("status") == "succeeded" and
                                not fallback_discovery.get("failed_slices"))
                            materials.extend((fallback_source, fallback_policy, item.ref,
                                              item.body, item.provenance, item.status, item.reason)
                                             for item in fallback_materials)
                        else:
                            discovery["fallback"]["attempted"] = False
                            discovery["fallback"]["reason"] = "empty_fallback_scope"
            discovered = len(materials)
            for candidate_source, candidate_policy, ref, body, provenance, \
                    material_status, material_reason in materials:
                # Match the immutable asset's canonical body boundaries so all
                # evidence offsets resolve against its exact version bytes.
                body = body.strip()
                material = Material(ref, body, provenance, material_status, material_reason)
                candidate = _candidate(candidate_source, material, body, discovered_at=now,
                                       policy=candidate_policy)
                candidate_ids.append(candidate.candidate_id)
                source_eligible = transport_complete or (
                    source_id == "ibkr_news" and
                    candidate_source.id == "ibkr_news" and
                    discovery.get("status") == "succeeded") or (
                    source_id == "ibkr_news" and
                    candidate_source.id == "yfinance_live_news" and
                    bool(discovery.get("fallback", {}).get("complete"))) or (
                    source_id == "semianalysis" and
                    candidate_source.id == "semianalysis" and
                    (discovery.get("transport_status", {}).get(
                        provenance.get("source_carrier", "")) or {}).get(
                        "status") == "succeeded")
                validation, state = _admission(
                    candidate, material, source=candidate_source, policy=candidate_policy,
                    human_review_approved=human_review_approved,
                    source_eligible=source_eligible)
                if state == "deferred":
                    deferred += 1
                    store.save_document_candidate(candidate, validation)
                    reason_counts["budget_deferred"] = reason_counts.get("budget_deferred", 0) + 1
                    continue
                if state in {"pending_human_review", "quarantined"}:
                    raw_path = _quarantine(candidate)
                    if candidate.text and not raw_path:
                        validation = ValidationResult(
                            "quarantined",
                            (*validation.issues, ValidationIssue(
                                "persistence", "review_material_storage_unavailable")),
                            {**validation.checks, "review_material_storage": False})
                        state = "quarantined"
                    if state == "pending_human_review":
                        pending += 1
                    else:
                        quarantined += 1
                    store.save_document_candidate(candidate, validation, raw_path=raw_path)
                    for issue in validation.issues:
                        reason_counts[issue.code] = reason_counts.get(issue.code, 0) + 1
                    continue
                identity = candidate.external_id or candidate.source_url
                prior = store.document_by_external_id(identity) if identity else None
                prior_hash = (prior or {}).get("content_hash") or (prior or {}).get("sha256")
                if prior and prior_hash == candidate.content_hash and \
                        prior.get("source") == candidate_source.id and \
                        _managed_document(prior, store=store):
                    revise = getattr(store, "revise_document_metadata", None)
                    if callable(revise):
                        revise(prior["document_id"], metadata={
                            "title": candidate.title, "published_at": candidate.published_at,
                            "completeness": candidate.completeness,
                            "truncation_reason": candidate.metadata["provenance"].get("truncation_reason", ""),
                            "related_entities": list(candidate.related_entities),
                            "quality": candidate.metadata}, known_at=now)
                    validation = ValidationResult("no_change", (), {"unchanged": True})
                    store.save_document_candidate(candidate, validation,
                                                  document_id=prior.get("document_id", ""))
                    continue
                prior_content = (store.document_by_content_hash(candidate.content_hash)
                                 if candidate.content_hash else None)
                if prior_content and _managed_document(prior_content, store=store):
                    store.save_document_alias(
                        prior_content["document_id"], source=candidate_source.id,
                        source_url=candidate.source_url, external_id=candidate.external_id,
                        title=candidate.title, published_at=candidate.published_at,
                        metadata={"deduplication": "exact_content_hash",
                                  "candidate_id": candidate.candidate_id})
                    validation = ValidationResult("no_change", (),
                                                  {"duplicate_content": True})
                    store.save_document_candidate(
                        candidate, validation, document_id=prior_content["document_id"])
                    continue
                doc_id = ""
                raw_path = ""
                if state == "accepted":
                    document = ingest_document(
                        entity=source.entity,
                        key=stable_key(f"managed:{candidate_source.id}:" +
                                       (candidate.external_id or candidate.source_url or ref.slug),
                                       prefix="managed"),
                        doc_type=semantic_type(candidate_source.doc_type).value,
                        text=body, source=candidate_source.id, source_url=candidate.source_url,
                        external_id=candidate.external_id, title=candidate.title,
                        published_at=candidate.published_at,
                        related_entities=candidate.related_entities,
                        completeness=candidate.completeness,
                        truncation_reason=candidate.metadata["provenance"].get("truncation_reason", ""),
                        carrier_format=candidate.carrier_format,
                        min_chars=candidate.min_chars, period="", now=now, store=store)
                    doc_id = document.document_id if document else ""
                    if doc_id:
                        revise = getattr(store, "revise_document_metadata", None)
                        if callable(revise):
                            revise(doc_id, metadata={"quality": candidate.metadata}, known_at=now)
                        accepted += 1
                        document_ids.append(doc_id)
                    else:
                        validation = ValidationResult(
                            "quarantined", (ValidationIssue("persistence", "document_write_failed"),),
                            {"persistence": False})
                        state = "quarantined"
                        raw_path = _quarantine(candidate)
                store.save_document_candidate(candidate, validation,
                                              raw_path=raw_path, document_id=doc_id)
                for issue in validation.issues:
                    reason_counts[issue.code] = reason_counts.get(issue.code, 0) + 1
            if source_id == "semianalysis" and newsletter_batch.complete:
                if quarantined == 0 and pending == 0:
                    for cursor in newsletter_batch.cursor_updates:
                        store.save_newsletter_cursor(**cursor.__dict__)
            status = ("unreachable" if discovery.get("status") == "unreachable" and
                      not discovery.get("fallback", {}).get("attempted") else
                      "pending_human_review" if pending else
                      "partial" if deferred or discovery.get("failed_slices") or not transport_complete else
                      "succeeded" if accepted else
                      "quarantined" if quarantined else "no_change")
            published_versions: list[str] = []
            for document_id in document_ids:
                published_versions.extend(
                    str(row.get("version_id") or "") for row in
                    store.document_versions(document_id) if row.get("version_id"))
            store.finish_ingestion(run_id, status=status, discovered=discovered,
                                   accepted=accepted, quarantined=quarantined + pending,
                                   reason_codes=reason_counts,
                                   note=json.dumps({"discovery": discovery, "deferred": deferred,
                                                    "document_ids": document_ids},
                                                   ensure_ascii=False, sort_keys=True), at=now)
            return {"run_id": run_id, "run_ids": run_ids,
                    "source_id": source_id, "status": status,
                    "discovered": discovered, "accepted": accepted,
                    "quarantined": quarantined, "pending_human_review": pending,
                    "deferred": deferred,
                    "reason_codes": reason_counts,
                    "candidate_ids": candidate_ids,
                    "document_ids": document_ids,
                    "published_version_ids": sorted(set(published_versions)),
                    "discovery": discovery,
                    "side_effects": {"llm": 0, "agent": 0, "workflow": 0,
                                     "orders": 0, "trades": 0}}
        except Exception as exc:
            store.finish_ingestion(run_id, status="failed", discovered=discovered,
                                   accepted=accepted,
                                   quarantined=quarantined + pending,
                                   reason_codes={f"{type(exc).__name__}": 1},
                                   note=f"article_refresh_failed:{type(exc).__name__}", at=now)
            raise
    finally:
        if owns_store:
            store.close()


__all__ = ["ingest_article_source"]
