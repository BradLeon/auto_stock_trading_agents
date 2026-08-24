#!/usr/bin/env python3
"""Run an isolated real-source audit for SEC releases and periodic filings only."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--anchors",
        default=str(ROOT / "var/phase1_inspection_20260824_hardened/run_results.json"),
        help="Prior audit JSON containing steps.official event anchors (read-only).",
    )
    parser.add_argument("--output", required=True, help="New, empty audit output directory.")
    parser.add_argument("--symbols", nargs="*", help="Optional symbol subset.")
    return parser.parse_args()


def _load_anchors(path: Path, symbols: set[str] | None) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw = ((payload.get("steps") or {}).get("official") or {})
    anchors = []
    for symbol, item in raw.items():
        if symbols and symbol.upper() not in symbols:
            continue
        period = str(item.get("period") or "")
        report_date = str(item.get("report_date") or "")[:10]
        if period and report_date:
            anchors.append({"symbol": symbol.upper(), "period": period,
                            "report_date": report_date})
    return anchors


def _health(store, source_id: str) -> dict:
    return next((row for row in store.data_source_health()
                 if row["source_id"] == source_id), {})


def _row_details(row: dict, *, store) -> dict:
    from ats.data import document_assets

    body = document_assets.read_document(row["document_id"], store=store)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest() if body else ""
    aliases = store.document_aliases(row["document_id"])
    sec_alias = next((alias for alias in aliases
                      if alias["source"].startswith("sec_metadata")), {})
    try:
        metadata = json.loads(sec_alias.get("metadata_json") or "{}")
    except json.JSONDecodeError:
        metadata = {}
    return {
        "document_id": row["document_id"], "doc_type": row["doc_type"],
        "entity": row["entity"], "period": row["period"], "title": row["title"],
        "published_at": row["published_at"], "source_url": row["source_url"],
        "external_id": row["external_id"], "form_type": metadata.get("form_type", ""),
        "cik": metadata.get("cik", ""), "report_date": metadata.get("report_date", ""),
        "filing_regime": metadata.get("filing_regime", ""),
        "chars": len(body), "readable": bool(body),
        "hash_matches": bool(body) and digest == row["sha256"],
        "local_path": row["local_path"],
    }


def _render_report(result: dict) -> str:
    summary = result["summary"]
    lines = [
        "# SEC 正式披露专项真实采集报告",
        "",
        f"- 运行时间：{result['completed_at']}",
        f"- 事件锚点：{summary['anchors']}",
        f"- 接受的 earnings release：{summary['company_release']}",
        f"- 接受的 periodic regulatory filing：{summary['regulatory_filing']}",
        f"- 可读且哈希一致：{summary['read_hash_passed']}/{summary['documents']}",
        f"- 非本任务文档类型：{summary['out_of_scope_documents']}",
        "",
        "本次只调用 SEC earnings release 与 periodic filing 路径；deck、transcript、新闻、研报均未运行。",
        "",
        "## 按公司结果",
        "",
        "| Symbol | Period | Release | Periodic filing | Documents | Note |",
        "|---|---|---|---|---:|---|",
    ]
    for item in result["entities"]:
        release = item["release_health"].get("status", "not_run")
        filing = item["filing_health"].get("status", "not_run")
        note = str(item.get("error") or "").replace("|", "\\|")
        lines.append(
            f"| {item['symbol']} | {item['period']} | {release} | {filing} | "
            f"{len(item['documents'])} | {note} |")
    lines += [
        "", "## 已接受文件", "",
        "| Type | Entity | Form | Accession | Chars | Path |",
        "|---|---|---|---|---:|---|",
    ]
    for row in result["documents"]:
        lines.append(
            f"| {row['doc_type']} | {row['entity']} | {row['form_type']} | "
            f"{row['external_id']} | {row['chars']} | `{row['local_path']}` |")
    return "\n".join(lines) + "\n"


def main() -> int:
    args = _arguments()
    output = Path(args.output).resolve()
    if output.exists():
        raise SystemExit(f"output must not already exist: {output}")
    output.mkdir(parents=True)
    documents_root = output / "documents"
    os.environ["ATS_DOCS_ROOT"] = str(documents_root)
    os.environ["ATS_DB_PATH"] = str(output / "catalog.sqlite")

    from ats.data import documents
    from ats.memory import get_store, reset_store_cache

    reset_store_cache()
    store = get_store()
    requested = {symbol.upper() for symbol in args.symbols} if args.symbols else None
    anchors = _load_anchors(Path(args.anchors), requested)
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    entities = []
    all_documents = []
    allowed_types = {"company_release", "regulatory_filing"}
    for index, anchor in enumerate(anchors, 1):
        symbol = anchor["symbol"]
        print(f"[{index}/{len(anchors)}] {symbol} {anchor['period']}", flush=True)
        error = ""
        try:
            documents.gather(
                symbol, period=anchor["period"], report_date=anchor["report_date"],
                store=store, include_deck=False, include_periodic_filings=True)
        except Exception as exc:  # noqa: BLE001 - audit must report remaining symbols
            error = f"{type(exc).__name__}: {exc}"
        rows = [row for row in store.documents(entity=symbol, limit=50)
                if row["period"] == anchor["period"] and row["doc_type"] in allowed_types]
        details = [_row_details(row, store=store) for row in rows]
        all_documents.extend(details)
        entities.append({
            **anchor, "documents": details, "error": error,
            "release_health": _health(store, f"sec_official:earnings_release:{symbol}"),
            "filing_health": _health(store, f"sec_official:periodic_filing:{symbol}"),
        })

    catalog = store.documents(limit=10000)
    counts = Counter(row["doc_type"] for row in all_documents)
    result = {
        "started_at": started,
        "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope": ["company_release", "regulatory_filing"],
        "anchors_source": str(Path(args.anchors).resolve()),
        "documents_root": str(documents_root),
        "summary": {
            "anchors": len(anchors), "documents": len(all_documents),
            "company_release": counts["company_release"],
            "regulatory_filing": counts["regulatory_filing"],
            "read_hash_passed": sum(row["readable"] and row["hash_matches"]
                                    for row in all_documents),
            "out_of_scope_documents": sum(row["doc_type"] not in allowed_types for row in catalog),
            "release_statuses": dict(Counter(
                item["release_health"].get("status", "not_run") for item in entities)),
            "filing_statuses": dict(Counter(
                item["filing_health"].get("status", "not_run") for item in entities)),
        },
        "entities": entities, "documents": all_documents,
    }
    (output / "run_results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "REPORT.md").write_text(_render_report(result), encoding="utf-8")
    with (output / "DOCUMENT_INDEX.csv").open("w", encoding="utf-8", newline="") as handle:
        fields = list(all_documents[0]) if all_documents else [
            "document_id", "doc_type", "entity", "period", "title", "published_at",
            "source_url", "external_id", "form_type", "cik", "report_date", "chars",
            "filing_regime", "readable", "hash_matches", "local_path"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(all_documents)
    print(json.dumps(result["summary"], ensure_ascii=False, sort_keys=True), flush=True)
    return 0 if result["summary"]["read_hash_passed"] == len(all_documents) else 2


if __name__ == "__main__":
    raise SystemExit(main())
