"""Data-only acceptance and reversible release records for third-party articles.

This module deliberately does *not* call ``chain.articles.collect_articles``.  That
function performs evidence extraction and can invoke an LLM; source acceptance only
discovers documents, reads their bodies, evaluates provenance/quality, and writes an
optional source-release overlay.  It therefore has no Agent, Workflow, order, or trade
side effects.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import yaml

from ....schemas.chain import ArticleRef, ArticleSourceDef


READ_MODES = {"legacy", "shadow", "platform", "fallback"}
_SUCCESS = {"succeeded", "no_change"}


def _repo_root() -> Path:
    from ....config import REPO_ROOT

    return REPO_ROOT


def default_policy_path() -> Path:
    return _repo_root() / "config" / "data" / "unstructured.yaml"


def default_release_path() -> Path:
    return Path(os.environ.get(
        "ATS_UNSTRUCTURED_RELEASE_FILE",
        _repo_root() / "var" / "data" / "unstructured" / "releases.yaml",
    ))


def _load_article_sources() -> dict[str, ArticleSourceDef]:
    """Read the unified registry without importing the Chain consumer."""
    from ....config import _config_dir, _load_yaml

    raw = _load_yaml(_config_dir() / "data" / "sources.yaml").get("article_sources", {}) or {}
    return {source_id: ArticleSourceDef(id=source_id, **(body or {}))
            for source_id, body in raw.items()}


def load_policy(source_id: str, *, path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path else default_policy_path()
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    policy = dict((raw.get("sources") or {}).get(source_id) or {})
    if not policy:
        raise ValueError(f"unstructured source not registered for acceptance: {source_id}")
    if policy.get("domain") != "unstructured":
        raise ValueError(f"source {source_id} is not an unstructured source")
    return policy


def _canonical_url(value: str) -> str:
    """Remove tracking query/fragment while preserving non-web native schemes."""
    if not value:
        return ""
    parsed = urlsplit(value)
    if parsed.scheme in {"http", "https"}:
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(),
                           parsed.path.rstrip("/"), "", ""))
    return value.strip()


def _adapter(source: ArticleSourceDef):
    return importlib.import_module(f"ats.data.articles.{source.adapter}")


def _adapter_provenance(adapter, ref: ArticleRef) -> dict[str, str]:
    provide = getattr(adapter, "provenance", None)
    if callable(provide):
        raw = provide(ref) or {}
        return {str(key): str(value) for key, value in raw.items() if value not in (None, "")}
    return {"native_id": ref.slug, "canonical_url": _canonical_url(ref.url)}


def _diagnostic_discover(adapter, source: ArticleSourceDef, *,
                         params: dict[str, Any] | None = None) -> tuple[list[ArticleRef], dict[str, Any]]:
    params = params if params is not None else dict(source.params)
    detailed = getattr(adapter, "discover_with_status", None)
    if callable(detailed):
        refs, status = detailed(pages=source.pages, **params)
        return list(refs), dict(status or {})
    refs = adapter.discover(pages=source.pages, **params)
    return list(refs), {"status": "succeeded", "diagnostic": "adapter_has_no_detailed_status"}


def _body_status(body: str, *, minimum: int) -> tuple[str, str]:
    text = (body or "").strip()
    if not text:
        return "unreadable", "body_missing"
    if len(text) < minimum:
        return "partial", f"body_shorter_than_{minimum}"
    return "accepted", ""


def _allows_partial_bodies(policy: dict[str, Any]) -> bool:
    """Whether an explicitly-labelled preview is publishable for this source.

    This is intentionally a source policy, not a global relaxation of document
    quality.  TrendForce and IBKR still require a complete usable body; SemiAnalysis
    may publish a known subscription preview because that preview is itself useful
    and its incompleteness remains first-class lineage.
    """
    return bool((policy.get("policy") or {}).get("allow_partial_bodies", False))


def fallback_plan(*, source_id: str, outcome: str, discovery: dict[str, Any],
                  policy: dict[str, Any]) -> dict[str, Any]:
    """Describe a failover decision without fetching or publishing the fallback.

    A successful IBKR sweep with zero headlines is real market information, not an
    outage.  Yahoo is therefore only eligible when the primary source is unavailable
    or when named historical-news slices failed.  The caller remains responsible for
    running the fallback through its own admission and release policy.
    """
    fallback = dict((policy.get("policy") or {}).get("fallback") or {})
    target = str(fallback.get("source_id") or "")
    if not target:
        return {"configured": False, "activate": False, "source_id": ""}
    activate_on = {str(value) for value in (fallback.get("activate_on") or [])}
    discovery_status = str(discovery.get("status") or "")
    failed_slices = list(discovery.get("failed_slices") or [])
    unavailable = outcome == "unreachable" or discovery_status in activate_on
    if unavailable:
        return {
            "configured": True, "activate": True, "source_id": target,
            "scope": "source", "reason": discovery_status or outcome,
        }
    if failed_slices:
        entities = sorted({str(item.get("symbol") or "") for item in failed_slices
                           if item.get("symbol")})
        return {
            "configured": True, "activate": True, "source_id": target,
            "scope": "failed_slices", "entities": entities,
            "reason": "historical_news_slice_failed",
        }
    return {
        "configured": True, "activate": False, "source_id": target,
        "scope": "none", "reason": "primary_completed_including_zero_news",
    }


def assess_article_source(source_id: str, *, now: datetime | None = None,
                          policy_path: str | Path | None = None,
                          human_review_approved: bool = False,
                          adapter_params: dict[str, Any] | None = None) -> dict[str, Any]:
    """Perform a source-only, read-only acceptance pass.

    Each discovered candidate becomes a ledger row in the returned report.  This is
    intentionally in-memory: callers persist the report explicitly, so an acceptance
    probe cannot mutate production document/evidence storage by accident.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    policy = load_policy(source_id, path=policy_path)
    sources = _load_article_sources()
    source = sources.get(source_id)
    if source is None:
        raise ValueError(f"article source missing from compatibility registry: {source_id}")
    if source.adapter != policy.get("adapter"):
        raise ValueError(f"source {source_id} adapter differs from acceptance policy")
    adapter = _adapter(source)
    minimum = int((policy.get("policy") or {}).get("minimum_body_chars") or source.min_body_chars)
    max_body_attempts = max(1, int((policy.get("policy") or {}).get("max_body_attempts") or 1))
    max_body_requests = max(1, int((policy.get("request_budget") or {}).get(
        "max_body_requests") or source.max_per_run))
    lookback_days = int((policy.get("policy") or {}).get("lookback_days") or 0)
    allow_partial_bodies = _allows_partial_bodies(policy)
    require_entity_verified = bool((policy.get("policy") or {}).get(
        "require_entity_verified", False))
    human_review_required = bool((policy.get("policy") or {}).get(
        "release_requires_human_title_url_review", False))
    since = now.date() - timedelta(days=lookback_days) if lookback_days else None
    try:
        refs, discovery = _diagnostic_discover(
            adapter, source, params={**dict(source.params), **dict(adapter_params or {})})
    except Exception as exc:  # source failure, not an empty publishing window
        refs, discovery = [], {"status": "unreachable", "error": f"{type(exc).__name__}:{exc}"}

    rows: list[dict[str, Any]] = []
    accepted_by_key: dict[tuple[str, str], str] = {}
    body_requests = 0
    for ref in refs:
        provenance = _adapter_provenance(adapter, ref)
        canonical = _canonical_url(provenance.get("canonical_url") or ref.url)
        row: dict[str, Any] = {
            "native_id": provenance.get("native_id") or ref.slug,
            "canonical_url": canonical,
            "source_url": ref.url,
            "title": ref.title,
            "published_at": ref.published_at.isoformat() if ref.published_at else "",
            "published_at_exact": provenance.get("published_at_exact", ""),
            "published_at_timezone": provenance.get("published_at_timezone", ""),
            "publisher": provenance.get("publisher", ""),
            "queried_entities": provenance.get("queried_entities", ""),
            "title_verified_entities": provenance.get("title_verified_entities", ""),
            "association_rejected_entities": provenance.get("association_rejected_entities", ""),
            "entity_association": provenance.get("entity_association", ""),
            "dedup_title": provenance.get("dedup_title", ""),
            "dedup_time": provenance.get("dedup_time", ""),
            "in_scope": bool(not since or not ref.published_at or ref.published_at >= since),
            "status": "pending", "reason": "", "content_hash": "",
            "provenance": provenance,
        }
        if not row["in_scope"]:
            row.update(status="out_of_scope", reason="published_before_acceptance_window")
            rows.append(row)
            continue
        if require_entity_verified and row["entity_association"] != "title_verified":
            row.update(status="association_rejected", reason="title_does_not_verify_queried_entity")
            rows.append(row)
            continue
        # Discover every headline to make coverage observable, but only retrieve bodies
        # within the configured source budget. A deferred row is deliberately neither
        # accepted nor a source failure: a later, cursor-based run must fetch it.
        if body_requests >= max_body_requests:
            row.update(status="deferred", reason="body_request_budget_exhausted")
            rows.append(row)
            continue
        body = ""
        errors: list[str] = []
        body_requests += 1
        for attempt in range(1, max_body_attempts + 1):
            try:
                body = adapter.fetch_body(ref.url)
                row["body_attempts"] = attempt
                break
            except Exception as exc:  # a document-level failure must stay local
                errors.append(f"{type(exc).__name__}:{exc}")
        if errors and not body:
            row["reason"] = f"body_fetch_failed_after_{max_body_attempts}_attempts:{errors[-1]}"
        status, reason = _body_status(body, minimum=minimum)
        if (body or "").strip():
            # Hash every retrieved body, including a partial/teaser.  A rejected
            # document still needs an auditable identity and may later be superseded
            # by a complete mail/RSS version.
            row["content_hash"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
        # A long subscriber email can still end at a paid-content boundary.  Size is
        # only a corruption guard; the acquisition pipeline's explicit completeness
        # classification is authoritative for newsletter/RSS assets.
        declared_completeness = str(provenance.get("completeness") or "full").lower()
        if status == "accepted" and declared_completeness != "full":
            status, reason = "partial", f"declared_{declared_completeness}_body"
        row["status"] = status
        row["reason"] = row["reason"] or reason
        if status == "accepted":
            # IBKR can expose the same wire through distinct provider/article IDs.
            # Its adapter supplies a normalised title key after preserving those
            # native IDs in provenance; other sources retain URL+hash semantics.
            key = (row["dedup_title"] or canonical, row["content_hash"])
            if key in accepted_by_key:
                row.update(status="duplicate", reason="dedup_key_and_content_hash_duplicate",
                           duplicate_of=accepted_by_key[key])
            else:
                accepted_by_key[key] = row["native_id"]
        rows.append(row)

    in_scope = [row for row in rows if row["in_scope"]]
    entity_eligible = [row for row in in_scope if row["status"] != "association_rejected"]
    counts = {key: sum(1 for row in rows if row["status"] == key)
              for key in ("accepted", "duplicate", "partial", "unreadable", "deferred",
                          "association_rejected", "out_of_scope")}
    discovery_status = str(discovery.get("status") or "succeeded")
    failed_slices = list(discovery.get("failed_slices") or [])
    partial_rejected = counts["partial"] > 0 and not allow_partial_bodies
    if discovery_status not in _SUCCESS:
        outcome, category = "unreachable", "unreachable"
    elif failed_slices or partial_rejected or counts["unreadable"]:
        outcome, category = "partial", "partial"
    elif not entity_eligible:
        # A successful, scoped IBKR sweep can legitimately have no headlines; other
        # sources remain no_coverage rather than pretending a quiet feed is validated.
        outcome = "no_change" if source_id == "ibkr_news" else "no_coverage"
        category = "equivalent" if source_id == "ibkr_news" else "partial"
    else:
        outcome = "succeeded"
        category = "partial" if counts["partial"] else "equivalent"
    checks = [
        {"check": "registered_policy", "passed": True, "detail": str(default_policy_path())},
        {"check": "adapter_identity", "passed": source.adapter == policy.get("adapter"),
         "detail": source.adapter},
        {"check": "discovery", "passed": discovery_status in _SUCCESS,
         "detail": discovery_status},
        {"check": "body_quality",
         "passed": counts["unreadable"] == 0 and not partial_rejected,
         "detail": {"minimum_body_chars": minimum,
                    "allow_partial_bodies": allow_partial_bodies, **counts}},
        {"check": "coverage", "passed": bool(entity_eligible) or source_id == "ibkr_news",
         "detail": {"in_scope_candidates": len(in_scope),
                    "entity_eligible_candidates": len(entity_eligible),
                    "lookback_days": lookback_days}},
        {"check": "entity_association",
         "passed": not require_entity_verified or all(
             row["entity_association"] == "title_verified"
             for row in entity_eligible if row["status"] not in {"out_of_scope"}),
         "detail": {"required": require_entity_verified,
                    "rejected": counts["association_rejected"]}},
        {"check": "title_url_human_review",
         "passed": not human_review_required or human_review_approved,
         "detail": {"required": human_review_required,
                    "approved": human_review_approved}},
        {"check": "body_request_budget", "passed": body_requests <= max_body_requests,
         "detail": {"used": body_requests, "max": max_body_requests,
                    "deferred": counts["deferred"]}},
        {"check": "slice_completeness", "passed": not failed_slices,
         "detail": failed_slices},
        {"check": "provenance", "passed": all(row["native_id"] and row["canonical_url"]
                                                   for row in in_scope),
         "detail": "native_id_and_canonical_url"},
    ]
    eligible = outcome in _SUCCESS and all(check["passed"] for check in checks)
    result = {
        "source_id": source_id, "evaluated_at": now.isoformat(),
        "outcome": outcome, "classification": category,
        "platform_eligible": eligible, "scope": {
            "publisher": source.entity, "adapter": source.adapter,
            "lookback_days": lookback_days, "since": since.isoformat() if since else "",
            "max_body_attempts": max_body_attempts,
            "max_body_requests": max_body_requests,
            "body_requests_used": body_requests,
            "allow_partial_bodies": allow_partial_bodies,
            "require_entity_verified": require_entity_verified,
            "human_review_required": human_review_required,
            "human_review_approved": human_review_approved,
            "separate_dataset": (policy.get("policy") or {}).get("separate_dataset", ""),
        },
        "discovery": discovery, "counts": counts, "checks": checks,
        "candidates": rows,
        "side_effects": {"llm": 0, "agent": 0, "workflow": 0,
                         "orders": 0, "trades": 0, "persistence": 0},
    }
    result["fallback"] = fallback_plan(
        source_id=source_id, outcome=outcome, discovery=discovery, policy=policy)
    return result


def assess_ibkr_news_with_fallback(*, now: datetime | None = None,
                                    policy_path: str | Path | None = None,
                                    human_review_approved: bool = False) -> dict[str, Any]:
    """Assess Yahoo only for IBKR's explicitly failed availability scope."""
    primary = assess_article_source(
        "ibkr_news", now=now, policy_path=policy_path,
        human_review_approved=human_review_approved)
    decision = dict(primary.get("fallback") or {})
    decision["attempted"] = False
    if not decision.get("activate"):
        primary["fallback"] = decision
        return primary

    params: dict[str, Any] = {}
    if decision.get("scope") == "failed_slices":
        params["symbols"] = list(decision.get("entities") or [])
    decision["attempted"] = True
    # Yahoo remains independently subject to title association and body quality gates.
    decision["assessment"] = assess_article_source(
        str(decision["source_id"]), now=now, policy_path=policy_path,
        human_review_approved=human_review_approved, adapter_params=params)
    primary["fallback"] = decision
    return primary


def write_acceptance_report(result: dict[str, Any], path: str | Path) -> Path:
    """Persist a human-readable report only at an explicit caller-provided path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    checks = result["checks"]
    lines = [
        f"# 第三方非结构化数据源验收：{result['source_id']}", "",
        f"- 评估时间：{result['evaluated_at']}",
        f"- 结果：`{result['outcome']}` / 分类：`{result['classification']}`",
        f"- 可发布到 platform：`{'是' if result['platform_eligible'] else '否'}`", "",
        "- 人工标题/URL 审阅：`{}`".format(
            "已确认" if result["scope"].get("human_review_approved") else
            ("待确认" if result["scope"].get("human_review_required") else "不要求")), "",
        "## 覆盖与质量", "",
        "| 已接纳 | 重复 | 正文不足 | 无法读取 | 预算延后 | 主体拒绝 | 范围外 |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        "| {accepted} | {duplicate} | {partial} | {unreadable} | {deferred} | {association_rejected} | {out_of_scope} |".format(**result["counts"]),
        "", "## 检查", "",
    ]
    lines.extend(f"- {'✅' if row['passed'] else '❌'} `{row['check']}`：{row['detail']}"
                 for row in checks)
    fallback = result.get("fallback") or {}
    if fallback.get("configured"):
        lines += ["", "## Fallback 决策", "",
                  f"- 启用：`{'是' if fallback.get('activate') else '否'}`",
                  f"- 目标来源：`{fallback.get('source_id', '')}`",
                  f"- 范围：`{fallback.get('scope', '')}` / 原因：`{fallback.get('reason', '')}`"]
        if fallback.get("entities"):
            lines.append(f"- 标的：`{', '.join(fallback['entities'])}`")
    lines += ["", "## 候选明细", "",
              "| 查询标的 | 标题通过标的 | 标题拒绝标的 | 标题 | Publisher | 精确发布时间 / 时区 | 主体判定 | 状态 | 原因 | URL |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for row in result["candidates"]:
        lines.append("| {queried_entities} | {title_verified_entities} | {association_rejected_entities} | {title} | {publisher} | {published_at_exact} / {published_at_timezone} | {entity_association} | {status} | {reason} | {canonical_url} |".format(
            **{key: str(value).replace("|", "\\|") for key, value in row.items()}))
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def load_release_overlay(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path else default_release_path()
    if not target.exists():
        return {"version": 1, "sources": {}, "history": []}
    raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    if int(raw.get("version", 1)) != 1:
        raise ValueError(f"unsupported unstructured release overlay: {target}")
    return {"version": 1, "sources": dict(raw.get("sources") or {}),
            "history": list(raw.get("history") or [])}


def publish_source(result: dict[str, Any], *, path: str | Path | None = None,
                   mode: str = "platform", actor: str = "cli") -> dict[str, Any]:
    """Write a reversible source mode only after a data-only passing assessment."""
    if mode not in READ_MODES:
        raise ValueError(f"invalid unstructured source mode: {mode}")
    if mode == "platform" and not result.get("platform_eligible"):
        raise ValueError("source acceptance failed; release overlay was not changed")
    target = Path(path) if path else default_release_path()
    raw = load_release_overlay(target)
    source_id = result["source_id"]
    previous = raw["sources"].get(source_id, "")
    raw["sources"][source_id] = mode
    raw["history"].append({
        "at": datetime.now(timezone.utc).isoformat(), "actor": actor,
        "source_id": source_id, "previous_mode": previous, "mode": mode,
        "classification": result.get("classification", ""),
        "outcome": result.get("outcome", ""),
        "action": "publish" if mode == "platform" else "set_mode",
    })
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target.parent,
                                     delete=False) as handle:
        yaml.safe_dump(raw, handle, allow_unicode=True, sort_keys=False)
        temporary = Path(handle.name)
    os.replace(temporary, target)
    return {"applied": True, "source_id": source_id, "previous_mode": previous,
            "mode": mode, "release_file": str(target)}


__all__ = [
    "assess_article_source", "assess_ibkr_news_with_fallback", "default_policy_path", "default_release_path",
    "fallback_plan", "load_policy", "load_release_overlay", "publish_source",
    "write_acceptance_report",
]
