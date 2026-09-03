"""Small durable audit log for reversible consumer cutovers."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3


def record_consumer_comparison(*, consumer: str, entity: str, data_db: str | Path,
                               status: str, details: dict) -> str:
    """Persist sanitized comparison metadata without changing routed data."""
    identity = "|".join((consumer, entity, status,
                           json.dumps(details, sort_keys=True, default=str)))
    comparison_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    path = Path(data_db)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS structured_consumer_comparisons "
            "(comparison_id TEXT PRIMARY KEY, consumer TEXT NOT NULL, entity TEXT NOT NULL, "
            "status TEXT NOT NULL, details_json TEXT NOT NULL, created_at TEXT NOT NULL)")
        connection.execute(
            "INSERT OR REPLACE INTO structured_consumer_comparisons VALUES (?,?,?,?,?,?)",
            (comparison_id, consumer, entity, status,
             json.dumps(details, ensure_ascii=False, default=str),
             datetime.now(timezone.utc).isoformat()))
    return comparison_id


__all__ = ["record_consumer_comparison"]
