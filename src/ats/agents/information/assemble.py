"""Input assembly for the information analyst (Phase D task 4.1).

Reads ADMITTED documents and neutral evidence only, through Workflow-Memory
store bridges into the data product layer. No provider module is imported
here (the architecture guard scans this package), and nothing in this package
ever initiates a retrieval — ingestion belongs to the data layer pipelines.
"""

from __future__ import annotations

from ats.workflow.evaluation_clock import now as evaluation_now

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger("ats.agents.information.assemble")

PROCESSOR_VERSION = "information-brief-v1"


def admitted_events(store, symbol: str, *, cutoff: datetime,
                    limit: int = 60) -> list[dict]:
    """Stored events for one target, filtered by PUBLISH time (4.10).

    `store.recent_events` returns the newest first by ingestion-agnostic
    published_at; the cutoff here is applied on the record's published time so
    late-arriving old material is excluded from "this window's new facts".
    """
    from ...workflow.isolation import verified_isolation_root
    from ...workflow.runtime_reads import current_read_context

    context = current_read_context()
    if context and (context.route == "target" or verified_isolation_root() is not None):
        from ...data.consumer_api import read_input

        packet = read_input(consumer="information", product="DOC_DATA",
                            scope={"entity": symbol, "limit": limit})
        if packet.status not in {"complete", "partial", "no_coverage"}:
            from ...workflow.cutover_routing import RouteUnavailable

            raise RouteUnavailable("Information admitted documents unavailable")
        return [{**row, "id": row["document_id"], "headline": row["title"],
                 "input_ref": f"{row['document_id']}@{row['version_id']}"}
                for row in packet.payload or []
                if row.get("published_at") and row["published_at"] >= cutoff.isoformat()]
    out = []
    for row in store.recent_events(symbol, limit=limit):
        record = dict(row)
        from .briefs import is_new_by_publish

        if is_new_by_publish(record, cutoff):
            out.append(record)
    return out


def admitted_articles(store, *, since: datetime, limit: int = 500) -> list:
    """Read admitted document versions through the public Data Products API.

    Workflow Memory remains the owner of processing leases and analyst outputs,
    but is no longer an external-document read API. Partial source material keeps
    its explicit completeness label when converted to the extraction contract.
    """
    from ...data.products.unstructured import admitted_documents
    from ...schemas.research import Article

    documents = admitted_documents(
        document_types=("article", "research", "research_article", "news", "newsletter"),
        published_since=since.astimezone(timezone.utc).isoformat(),
        limit=limit,
    )
    out = []
    for document in documents:
        try:
            published = datetime.fromisoformat(
                (document.published_at or document.fetched_at).replace("Z", "+00:00"))
        except ValueError:
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        out.append(Article(
            id=document.document_id,
            source=document.source,
            title=document.title or document.document_id,
            url=document.source_url,
            body=document.text,
            published_at=published,
            completeness=document.completeness,
        ))
    return out


def default_cutoff(lookback_days: int) -> datetime:
    return evaluation_now(timezone.utc) - timedelta(days=lookback_days)


def signal_chain_universe(targets: list[str]) -> tuple[str, dict[str, list[str]]]:
    """Universe card + {member: [targets it maps to]} from CONFIG ONLY.

    Deliberately does NOT read any fundamental dossier narrative — the
    information analyst consumes admitted documents and static configuration,
    not another analyst's opinion (the old research path read the dossier
    thesis into its universe card, which was a cross-role read).
    """
    from ...config import load_pead_config

    lines: list[str] = []
    ticker_to_targets: dict[str, list[str]] = {}
    for sym in targets:
        sym = sym.upper()
        try:
            cfg = load_pead_config(sym)
        except Exception as exc:  # noqa: BLE001 - a missing config just drops the target
            log.warning("information: no pead config for %s: %s", sym, exc)
            continue
        lines.append(f"- {sym} (target)")
        ticker_to_targets.setdefault(sym, []).append(sym)
        for sc in cfg.signal_chain:
            member = sc.symbol.upper()
            lines.append(f"- {member} ({sc.role} of {sym})")
            ticker_to_targets.setdefault(member, []).append(sym)
    return "\n".join(lines), ticker_to_targets
