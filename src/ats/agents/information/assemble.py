"""Input assembly for the information analyst (Phase D task 4.1).

Reads ADMITTED documents and neutral evidence only, through Workflow-Memory
store bridges into the data product layer. No provider module is imported
here (the architecture guard scans this package), and nothing in this package
ever initiates a retrieval — ingestion belongs to the data layer pipelines.
"""

from __future__ import annotations

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
    out = []
    for row in store.recent_events(symbol, limit=limit):
        record = dict(row)
        from .briefs import is_new_by_publish

        if is_new_by_publish(record, cutoff):
            out.append(record)
    return out


def admitted_articles(store, *, since: datetime, limit: int = 500) -> list:
    """Admitted research articles via the Workflow-Memory store bridge.

    The article-level consumer policy (full texts, or intentional partial
    previews) lives in the data layer behind the bridge — this assembly only
    adds the time window and never imports a provider module.
    """
    return store.admitted_research_articles(since, limit=limit,
                                            allow_incomplete=True)


def default_cutoff(lookback_days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=lookback_days)


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
