"""Consumer-level data-product health check for the research-read switch.

Replaces per-scope raw qualification events as the switch precondition
(Phase F simplification, 2026-10-15). It reads the data products directly and
read-only, never through a consumer's read gate: the gate itself is what the
switch changes, so checking through it before switching cannot work.

A check fails only on things that make the new route unsafe to rely on: an
unreadable product, malformed rows (no version, hash, source or lineage), or a
configured universe with nothing at all inside the lookback. A single ticker with
no fresh material is a warning, not a failure: quiet tickers are normal.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

DOCUMENT_LOOKBACK_DAYS = 7
BRIEF_FRESH_DAYS = 3


def _universe() -> list[str]:
    from .runtime_reads import configured_entities

    return list(configured_entities("portfolio", "portfolio"))


def _latest_brief(entity: str) -> dict | None:
    """Newest published Information brief envelope, over a read-only connection.

    Deliberately not `get_store()`: opening the memory store applies pending additive
    schema, which would make a "read-only" health check write to the production file.
    """
    import json
    import sqlite3
    from contextlib import closing
    from pathlib import Path

    from ..memory import _db_path

    path = Path(_db_path()).resolve()
    if not path.is_file():
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='task_projection_envelopes'"
                            ).fetchone():
            return None
        row = conn.execute(
            "SELECT as_of, input_refs FROM task_projection_envelopes WHERE agent_role='information_brief' "
            "AND scope_kind='entity' AND scope_id=? AND status='published' ORDER BY created_at DESC LIMIT 1",
            (entity,)).fetchone()
    if row is None:
        return None
    try:
        refs = json.loads(row["input_refs"] or "[]")
    except json.JSONDecodeError:
        refs = []
    return {"as_of": row["as_of"], "input_refs": refs}


def _result(consumer: str, problems: list[str], warnings: list[str], detail: dict[str, Any]) -> dict[str, Any]:
    return {"consumer_id": consumer, "status": "ok" if not problems else "failed",
            "checked_at": datetime.now(UTC).isoformat(), "problems": problems,
            "warnings": warnings, "detail": detail}


def _information(now: datetime) -> dict[str, Any]:
    from ..data.products import get_platform_data_products
    from ..data.products.unstructured import admitted_documents

    problems, warnings, detail = [], [], {}
    since = (now - timedelta(days=DOCUMENT_LOOKBACK_DAYS)).isoformat()
    products = get_platform_data_products(readonly=True)
    try:
        total = 0
        for entity in _universe():
            try:
                rows = admitted_documents(entities=[entity], published_since=since, as_of=now,
                                          repository=products.unstructured, limit=200)
            except Exception as exc:  # noqa: BLE001 - unreadable product is the failure we look for
                problems.append(f"{entity}: admitted documents unreadable ({type(exc).__name__}: {exc})")
                continue
            malformed = [getattr(r, "document_id", "?") for r in rows
                         if not (getattr(r, "version_id", "") and getattr(r, "content_hash", "")
                                 and getattr(r, "source", ""))]
            if malformed:
                problems.append(f"{entity}: {len(malformed)} documents without version/hash/source")
            if not rows:
                warnings.append(f"{entity}: no admitted documents in the last {DOCUMENT_LOOKBACK_DAYS} days")
            detail[entity] = {"documents": len(rows)}
            total += len(rows)
        if total == 0:
            problems.append(f"no admitted documents for the whole universe in the last {DOCUMENT_LOOKBACK_DAYS} days")
    finally:
        products.structured.close()
    return _result("information", problems, warnings, detail)


def _fundamental(now: datetime) -> dict[str, Any]:
    from ..data.products import get_platform_data_products

    problems, warnings, detail = [], [], {}
    products = get_platform_data_products(readonly=True)
    try:
        financial_total, fresh_briefs = 0, 0
        for entity in _universe():
            row_detail: dict[str, Any] = {}
            try:
                snapshot = products.company_financial_snapshot(entity=entity, as_of=now)
                rows = snapshot.get("rows") or []
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{entity}: financial snapshot unreadable ({type(exc).__name__}: {exc})")
                rows = []
            unlinked = [r.get("observation_id") for r in rows[:5]
                        if not products.structured.lineage(r.get("observation_id"))]
            if unlinked:
                problems.append(f"{entity}: financial rows without lineage {unlinked}")
            if not rows:
                warnings.append(f"{entity}: no financial rows")
            row_detail["financial_rows"] = len(rows)
            financial_total += len(rows)
            brief = _latest_brief(entity)
            fresh = bool(brief and brief.get("input_refs") and brief.get("as_of")
                         and datetime.fromisoformat(brief["as_of"]) >= now - timedelta(days=BRIEF_FRESH_DAYS))
            row_detail["fresh_information_brief_with_lineage"] = fresh
            fresh_briefs += int(fresh)
            if not fresh:
                warnings.append(f"{entity}: no Information brief with lineage in the last {BRIEF_FRESH_DAYS} days")
            detail[entity] = row_detail
        if financial_total == 0:
            problems.append("no financial rows for the whole universe")
        # Fundamental reads Information's briefs (its only declared upstream). With none
        # at all from the new entry, the switched Fundamental would run on nothing new.
        if fresh_briefs == 0:
            problems.append(f"no Information brief with lineage in the last {BRIEF_FRESH_DAYS} days "
                            "(switch Information first and let it run)")
    finally:
        products.structured.close()
    return _result("fundamental", problems, warnings, detail)


CHECKS = {"information": _information, "fundamental": _fundamental}


def check(consumer_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Read-only health of the data products one research consumer depends on."""
    if consumer_id not in CHECKS:
        return _result(consumer_id, [f"no health check defined for {consumer_id}; it cannot switch yet"], [], {})
    return CHECKS[consumer_id](now or datetime.now(UTC))
