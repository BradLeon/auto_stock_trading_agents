"""Local inspection/repair entry; production writes still need their queue lease.

Example (read-only): uv run python scripts/reconcile_article_metadata.py
    --source semianalysis --as-of 2026-10-12T07:00:00+00:00
    --output tmp/information_metadata_review.json
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from ats.data.pipelines.unstructured.article_metadata import reconcile_article_metadata
from ats.data.stores.unstructured import PlatformUnstructuredRepository


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("var/data.sqlite"))
    parser.add_argument("--source", choices=("semianalysis", "trendforce_news"), required=True)
    parser.add_argument("--as-of", type=datetime.fromisoformat, required=True)
    parser.add_argument("--field-evidence", type=Path)
    parser.add_argument("--document-ids", type=Path, help="JSON array of document IDs to limit repair scope")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--apply", action="store_true", help="Apply on this database; default is read-only")
    args = parser.parse_args()
    if args.apply:
        from ats.data.runtime.repository import platform_data_db_path

        if args.database.resolve() == platform_data_db_path().resolve():
            from ats.data.persistent_queue import require_queue_worker

            require_queue_worker(args.source)
    if args.output.resolve() == args.database.resolve() or args.output.suffix != ".json":
        parser.error("output must be a separate JSON file")
    evidence = json.loads(args.field_evidence.read_text()) if args.field_evidence else None
    document_ids = json.loads(args.document_ids.read_text()) if args.document_ids else None
    repo = PlatformUnstructuredRepository(args.database, writable=args.apply)
    try:
        result = reconcile_article_metadata(args.source, now=args.as_of,
            repository=repo, dry_run=not args.apply, limit=args.limit, field_evidence=evidence,
            document_ids=document_ids)
    finally:
        repo.close()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({"source": args.source, "dry_run": not args.apply,
        "documents": len(result["rows"]), "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
