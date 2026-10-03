"""Version-pinned DefeatBeta dataset discovery and document queries."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import httpx

_REVISION = re.compile(r"^[0-9a-f]{40}$")

def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def snapshot(policy: dict[str, Any]) -> dict[str, str]:
    repo = str(policy["repo"])
    path = str(policy["file"])
    if repo != "defeatbeta/yahoo-finance-data" or not path.startswith("data/US/"):
        raise ValueError("unapproved_fixed_dataset")
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        response = client.get(f"https://huggingface.co/api/datasets/{repo}")
        response.raise_for_status()
        revision = str(response.json().get("sha") or "")
        if not _REVISION.fullmatch(revision):
            raise ValueError("dataset_revision_unpinned")
        spec_url = f"https://huggingface.co/datasets/{repo}/resolve/{revision}/spec.json"
        response = client.get(spec_url)
        response.raise_for_status()
        raw_spec = response.content
    spec = json.loads(raw_spec)
    key = path.removeprefix("data/")
    file_hash = str((spec.get("hashes") or {}).get(key) or "")
    updated = str((spec.get("files") or {}).get(key) or "")
    if not re.fullmatch(r"[0-9a-f]{64}", file_hash) or not updated:
        raise ValueError("dataset_file_manifest_missing")
    return {"revision": revision, "spec_sha256": _digest(raw_spec),
            "file_sha256": file_hash, "file_updated_at": updated,
            "source_url": f"https://huggingface.co/datasets/{repo}/resolve/{revision}/{path}"}


def query_documents(snapshot: dict[str, str], source_id: str, *, symbols: list[str],
              limit: int, per_symbol_limit: int = 4) -> list[dict[str, Any]]:
    if not symbols:
        return []
    import duckdb

    con = duckdb.connect()
    try:
        con.execute("SET enable_progress_bar=false")
        if snapshot["source_url"].startswith("https://"):
            con.execute("LOAD httpfs")
        placeholders = ",".join("?" for _ in symbols)
        columns = ("symbol, cik, accession_number, company_name, form_type, "
                   "filing_date, report_date, acceptance_date_time, filing_url"
                   if source_id == "defeatbeta_sec_filing_index" else
                   "symbol, fiscal_year, fiscal_quarter, report_date, transcripts, transcripts_id")
        filing = source_id == "defeatbeta_sec_filing_index"
        forms = ("10-K", "10-Q", "8-K", "20-F", "6-K")
        form_filter = (" AND form_type IN (" + ",".join("?" for _ in forms) + ")"
                       if filing else "")
        query = (f"SELECT {columns} FROM (SELECT {columns}, "
                 f"row_number() OVER (PARTITION BY symbol ORDER BY report_date DESC) AS rn "
                 f"FROM read_parquet(?) WHERE symbol IN ({placeholders}){form_filter}) "
                 f"WHERE rn<=? ORDER BY report_date DESC LIMIT ?")
        rows = con.execute(query, [snapshot["source_url"], *symbols,
                                   *(forms if filing else ()), per_symbol_limit, limit]).fetchall()
        names = [column[0] for column in con.description]
        return [dict(zip(names, row, strict=True)) for row in rows]
    finally:
        con.close()
