"""Company disclosure metrics: locate -> model extraction -> code verification -> candidates.

A declared metric (config/data/disclosure_metrics.yaml) is looked for in one stored filing. Only the passages around its
anchors are given to the model, one call per metric. The model copies the printed number and the words around it; this
module checks those against the passage and computes the value itself. Nothing is published here: candidates wait for a
person (see stores/structured/disclosure_review.py). The model answer is cached by the exact request, so running the
same file again costs nothing and a change to the checks re-reads the cached answers.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

from ....config import REPO_ROOT, get_config, ledger_entity

DATASET_ID = SOURCE_ID = "company_disclosure_metrics"
METRIC_PREFIX = "disclosure."
ROLE = "disclosure_extract"
SKILL = "disclosure-extract"
CONFIG_PATH = REPO_ROOT / "config" / "data" / "disclosure_metrics.yaml"
UNITS = {"USD", "count", "percent"}
SCALES = {"none": 1, "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000,
          "trillion": 1_000_000_000_000}
BASIS_WORDING = {
    "quarter": r"three months ended|quarter",
    "ytd_6m": r"six months ended",
    "ytd_9m": r"nine months ended",
    "annual": r"years? ended",
    "trailing_12m": r"trailing|twelve months|TTM",
}
BASIS_MONTHS = {"quarter": 3, "ytd_6m": 6, "ytd_9m": 9, "annual": 12, "trailing_12m": 12}
_MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
_ROW_TOKEN = r"(?<![\w,.])(?:\$\s?)?(?:\(\d+(?:,\d{3})*(?:\.\d+)?\)|\d+(?:,\d{3})*(?:\.\d+)?)%?(?=\s|$)|(?<=\s)(?:\$\s?)?—"
_TABLE_ROW = re.compile(rf"(?:(?:{_ROW_TOKEN})\s+){{2,}}(?:{_ROW_TOKEN})")
_PLAIN = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', " ": " "})


@dataclass(frozen=True)
class Metric:
    key: str
    name: str
    unit: str
    period_type: str
    definition: str
    anchors: tuple[str, ...]
    must_mention: tuple[str, ...]
    dimension: str = ""

    @property
    def metric_id(self) -> str:
        return METRIC_PREFIX + self.key


@dataclass(frozen=True)
class Settings:
    chars_before: int
    chars_after: int
    max_passages: int
    max_matches_per_anchor: int


def load_config(path: Path | None = None) -> tuple[Settings, dict[str, list[Metric]]]:
    raw = yaml.safe_load((path or CONFIG_PATH).read_text(encoding="utf-8")) or {}
    settings = Settings(**raw["locate"])
    issuers: dict[str, list[Metric]] = {}
    for symbol, rows in (raw.get("issuers") or {}).items():
        metrics = []
        for row in rows or []:
            metric = Metric(
                key=row["key"], name=row["name"], unit=row["unit"], period_type=row["period_type"],
                definition=row["definition"], anchors=tuple(row["anchors"]),
                must_mention=tuple(row["must_mention"]), dimension=row.get("dimension", ""))
            if metric.unit not in UNITS or metric.period_type not in {"instant", "duration"}:
                raise ValueError(f"disclosure_metric_invalid:{symbol}:{metric.key}")
            for pattern in (*metric.anchors, *metric.must_mention):
                re.compile(pattern)
            metrics.append(metric)
        keys = [metric.key for metric in metrics]
        if len(keys) != len(set(keys)):
            raise ValueError(f"disclosure_metric_duplicate_key:{symbol}")
        issuers[str(symbol).upper()] = metrics
    return settings, issuers


def issuer_metrics(entity: str, keys: list[str] | None = None) -> tuple[Settings, list[Metric]]:
    settings, issuers = load_config()
    metrics = issuers.get(entity.strip().upper())
    if not metrics:
        raise ValueError(f"disclosure_issuer_not_declared:{entity.upper()}")
    if keys:
        unknown = sorted(set(keys) - {metric.key for metric in metrics})
        if unknown:
            raise ValueError("disclosure_metric_not_declared:" + ",".join(unknown))
        metrics = [metric for metric in metrics if metric.key in keys]
    return settings, metrics


# --------------------------------------------------------------------------- #
# The document
# --------------------------------------------------------------------------- #
def load_document(documents: Any, entity: str, *, form: str = "", document_id: str = "") -> dict[str, Any]:
    """One stored filing of `entity`: the latest of `form`, or the named document. `documents` is a read handle."""
    from ... import source_cache
    from ..unstructured import registration

    symbol = entity.strip().upper()
    filed: dict[str, Any] = {}
    if not document_id:
        chain = [item for item in registration.registration_filings(symbol, repository=documents)
                 if not form or item["form_type"] == form.strip().upper()]
        if not chain:
            raise ValueError(f"disclosure_document_not_found:{symbol}:{form or '-'}")
        filed = chain[-1]
        document_id = filed["document_id"]
    else:
        filed = next((item for item in registration.registration_filings(symbol, repository=documents)
                      if item["document_id"] == document_id), {})
    row = documents.conn.execute(
        "SELECT entity,doc_type,published_at,ok FROM data_documents WHERE document_id=?", (document_id,)).fetchone()
    if row is None or not row["ok"] or row["entity"].upper() != symbol:
        raise ValueError(f"disclosure_document_not_usable:{document_id}")
    version = documents.document_versions(document_id)[0]
    path = Path(version["local_path"])
    path = path if path.is_absolute() else REPO_ROOT / path
    _, body = source_cache._split_frontmatter(path.read_text(encoding="utf-8", errors="ignore"))
    filing_date = str(filed.get("filing_date") or row["published_at"])[:10]
    if not filing_date:
        raise ValueError(f"disclosure_document_without_filing_date:{document_id}")
    return {"entity": symbol, "document_id": document_id, "version_id": version["version_id"],
            "text": body.strip(), "filing_date": filing_date, "form_type": filed.get("form_type") or row["doc_type"],
            "accession_number": filed.get("accession_number", "")}


# --------------------------------------------------------------------------- #
# Locating
# --------------------------------------------------------------------------- #
def locate(text: str, metric: Metric, settings: Settings) -> list[tuple[int, int]]:
    """Windows of `text` around the metric's anchors, in anchor order for the budget, returned in document order."""
    windows: list[tuple[int, int]] = []
    for pattern in metric.anchors:
        taken = 0
        for match in re.finditer(pattern, text, re.IGNORECASE):
            if taken >= settings.max_matches_per_anchor or len(windows) >= settings.max_passages:
                break
            if any(start <= match.start() and match.end() + settings.chars_after // 2 <= end
                   for start, end in windows):
                continue
            start = max(0, match.start() - settings.chars_before)
            end = min(len(text), match.end() + settings.chars_after)
            if start:
                start = text.rfind(" ", 0, start) + 1
            if end < len(text):
                space = text.find(" ", end)
                end = space if space >= 0 else len(text)
            windows.append((start, end))
            taken += 1
    merged: list[tuple[int, int]] = []
    for start, end in sorted(windows):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def build_context(document: dict, metric: Metric, windows: list[tuple[int, int]]) -> str:
    lines = [
        f"发行人：{document['entity']}",
        f"文件：{document['form_type']}，申报日期 {document['filing_date']}",
        f"指标键：{metric.key}",
        f"指标名称：{metric.name}",
        f"口径：{metric.definition}",
        f"单位：{metric.unit}",
        f"期间类型：{metric.period_type}",
    ]
    if metric.dimension:
        lines.append(f"分维度记录：{metric.dimension}（写在 bucket 字段）")
    for number, (start, end) in enumerate(windows, 1):
        lines += ["", f'<passage index="{number}">', document["text"][start:end], "</passage>"]
    return "\n".join(lines)


def request_identity(context: str) -> str:
    """The exact request: model, instructions and content. The same identity is never sent twice."""
    skill = (REPO_ROOT / "src/ats/skills" / SKILL / "SKILL.md").read_text(encoding="utf-8")
    model = get_config().app.llm.for_role(ROLE).model
    return hashlib.sha256("\x00".join((model, skill, context)).encode("utf-8")).hexdigest()[:32]


# --------------------------------------------------------------------------- #
# Verification (pure)
# --------------------------------------------------------------------------- #
def plain(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").translate(_PLAIN)).strip()


def _core(value_text: str) -> str:
    return re.sub(r"[\s$%()]", "", str(value_text or ""))


def _number_in(core: str, text: str) -> re.Match | None:
    return re.search(rf"(?<![\d.,]){re.escape(core)}(?!\d|[.,]\d)", text)


def _header_labels(header: str, width: int) -> list[str]:
    dates = re.findall(rf"(?:{_MONTHS})\s+\d{{1,2}},\s*\d{{4}}", header)
    if len(dates) == width:
        return dates
    return re.findall(r"\bTotal\b|\bThereafter\b|\b(?:19|20)\d{2}\b", header)


def _column_ok(quote: str, header: str, index: int, core: str, bucket: str, end: date, dimension: str) -> bool:
    row = re.sub(r"(?<=[A-Za-z)])\(\d{1,2}\)", "", quote)          # footnote marks attached to a row label
    cells = [match.group(0) for match in re.finditer(_ROW_TOKEN, row)]
    labels = _header_labels(header, len(cells))
    if len(labels) != len(cells) or not 0 <= index < len(cells) or _core(cells[index]) != core:
        return False
    label = labels[index]
    if dimension:
        return label.casefold() == bucket.casefold() != "total"
    if label == "Total":
        return True                       # the total column of a due-by-year table, stated for one date
    if re.match(rf"(?:{_MONTHS})", label):
        return datetime.strptime(re.sub(r"\s+", " ", label), "%B %d, %Y").date() == end
    return label == str(end.year)


def verify(reading: dict, metric: Metric, document: dict, windows: list[tuple[int, int]]) -> dict:
    """Check one model reading against the passages. Returns the candidate fields and why it cannot be accepted.

    `value` is None when the printed number cannot even be parsed; such a reading yields no candidate at all.
    """
    text = document["text"]
    passages = [(start, text[start:end].translate(_PLAIN)) for start, end in windows]
    declared = int(reading.get("passage") or 0) - 1
    if 0 <= declared < len(passages):
        passages = [passages[declared], *passages[:declared], *passages[declared + 1:]]
    quote = plain(reading.get("quote"))
    home, char_start, char_end = "", 0, 0
    for start, body in passages:
        at = body.find(quote) if quote else -1
        if at >= 0:
            home, char_start, char_end = body, start + at, start + at + len(quote)
            break
    reasons: list[str] = []
    if not home:
        reasons.append("quote_not_found")
    extras: dict[str, str] = {}
    for name in ("scale_quote", "period_quote", "header_quote"):
        extra = plain(reading.get(name))
        if extra and home and extra not in home:
            reasons.append(f"{name}_not_found")
            extra = ""
        extras[name] = extra

    value_text = str(reading.get("value_text") or "").strip()
    core = _core(value_text)
    try:
        number = float(core.replace(",", ""))
    except ValueError:
        return {"value": None, "reasons": ["value_unparseable"], "quote": quote}
    if value_text.startswith("(") or value_text.startswith("$("):
        number = -number
    if home and not _number_in(core, quote):
        reasons.append("value_not_in_quote")

    scale = str(reading.get("scale") or "none")
    if scale not in SCALES:
        return {"value": None, "reasons": ["scale_unknown"], "quote": quote}
    printed = re.escape(core)
    context = " ".join((extras["scale_quote"], extras["header_quote"]))
    if home:
        if scale != "none":
            if not (re.search(rf"{printed}\)?\s*{scale}", quote, re.I) or re.search(rf"in {scale}s", context, re.I)):
                reasons.append("scale_unverified")
        elif re.search(rf"{printed}\)?\s*(thousand|million|billion|trillion)", quote, re.I) or (
                metric.unit == "USD" and re.search(r"\(in (thousands|millions|billions)", home, re.I)):
            reasons.append("scale_unverified")
        percent = re.search(rf"{printed}\)?\s*(%|percent)", quote, re.I)
        if metric.unit == "percent" and (not percent or scale != "none"):
            reasons.append("unit_mismatch")
        elif metric.unit == "USD" and ("$" not in home or percent):
            reasons.append("unit_mismatch")
        elif metric.unit == "count" and (percent or re.search(rf"\$\s?{printed}(?!\d|,\d)", quote)):
            reasons.append("unit_mismatch")

    evidence = " ".join((quote, extras["period_quote"], extras["header_quote"]))
    basis = str(reading.get("period_basis") or "")
    bucket = plain(reading.get("bucket")) if metric.dimension else ""
    end: date | None = None
    try:
        end = date.fromisoformat(str(reading.get("period_end") or ""))
    except ValueError:
        reasons.append("period_invalid")
    if end is not None:
        if end.isoformat() > document["filing_date"]:
            reasons.append("period_after_filing")
        if str(end.year) not in evidence or f"{end.strftime('%B')} {end.day}" not in evidence:
            reasons.append("period_unverified")
    if metric.period_type == "instant":
        if basis != "instant":
            reasons.append("period_type_mismatch")
    elif basis not in BASIS_WORDING:
        reasons.append("period_type_mismatch")
    elif not re.search(BASIS_WORDING[basis], evidence, re.I):
        reasons.append("period_basis_unverified")
    labelled = " ".join((quote, extras["header_quote"]))
    if any(not re.search(pattern, labelled, re.I) for pattern in metric.must_mention):
        reasons.append("metric_label_mismatch")
    if metric.dimension and (not bucket or bucket.casefold() not in labelled.casefold()):
        reasons.append("dimension_unverified")
    index = reading.get("column_index")
    index = -1 if index is None else int(index)
    if extras["header_quote"] and index >= 0:
        if end is None or not _column_ok(quote, extras["header_quote"], index, core, bucket, end, metric.dimension):
            reasons.append("table_column_unverified")
    elif _TABLE_ROW.search(quote):
        reasons.append("table_column_unverified")          # a table row without a checkable column
    if {"quote_not_found", "value_not_in_quote"} & set(reasons):
        reasons.insert(0, "evidence_incomplete")
    return {
        "value": number * SCALES[scale], "reasons": reasons, "quote": quote, "char_start": char_start,
        "char_end": char_end, "period_end": end.isoformat() if end else "", "period_basis": basis, "bucket": bucket,
        "value_text": value_text, "scale": scale, "column_index": index,
        "qualifier": plain(reading.get("qualifier")), **extras,
    }


# --------------------------------------------------------------------------- #
# Candidates
# --------------------------------------------------------------------------- #
def save_candidate(repository: Any, document: dict, metric: Metric, checked: dict, *, extraction_id: str,
                   method: str, corrects: str = "", now: datetime | None = None) -> str:
    """Store one verified reading as a candidate. A candidate a person already reviewed is left as it is."""
    entity = ledger_entity(document["entity"])
    identity = [entity, metric.metric_id, checked["period_end"], checked["period_basis"], checked["bucket"],
                checked["value"], metric.unit, document["document_id"], document["version_id"], corrects]
    candidate_id = hashlib.sha256(json.dumps(identity).encode("utf-8")).hexdigest()[:24]
    held = repository.evidence_candidate(candidate_id)
    if held and held["status"] != "needs_evidence":
        return candidate_id
    event_id = repository.ensure_event(
        dataset_id=DATASET_ID, entity_id=entity, event_type="filing", event_date=document["filing_date"],
        event_label=f"{document['form_type']} {document['accession_number']}".strip())
    raw = {key: checked.get(key, "") for key in (
        "quote", "value_text", "scale", "scale_quote", "period_basis", "period_quote", "header_quote",
        "column_index", "bucket", "qualifier")}
    raw.update(form_type=document["form_type"], filing_date=document["filing_date"],
               accession_number=document["accession_number"], security=document["entity"],
               extraction_id=extraction_id, dimension=metric.dimension, corrects=corrects)
    repository.save_evidence_candidate({
        "candidate_id": candidate_id, "event_id": event_id, "source_id": SOURCE_ID, "dataset_id": DATASET_ID,
        "entity_id": entity, "metric_id": metric.metric_id, "period": checked["period_end"],
        "event_time": f"{document['filing_date']}T00:00:00+00:00",
        "published_at": f"{document['filing_date']}T00:00:00+00:00", "value": checked["value"], "unit": metric.unit,
        "currency": "USD" if metric.unit == "USD" else "", "document_id": document["document_id"],
        "version_id": document["version_id"], "char_start": checked["char_start"], "char_end": checked["char_end"],
        "extraction_method": method, "source_tier": "regulatory", "confidence": None, "status": "needs_evidence",
        "reason_codes": checked["reasons"], "raw": raw, "at": now or datetime.now(UTC),
    })
    return candidate_id


def default_extractor(context: str) -> dict:
    from ....agents.disclosure.extract import extract

    return extract(context)


def plan(repository: Any, document: dict, settings: Settings, metrics: list[Metric]) -> list[dict]:
    """What a run would do, without a model call or a write: passages per metric and whether the answer is cached."""
    rows = []
    for metric in metrics:
        windows = locate(document["text"], metric, settings)
        context = build_context(document, metric, windows)
        identity = request_identity(context) if windows else ""
        held = repository.document_extraction(identity) if repository is not None and identity else None
        rows.append({"metric": metric.key, "passages": len(windows),
                     "characters": sum(end - start for start, end in windows),
                     "state": "not_located" if not windows else
                     "cached" if held and held["status"] != "failed" else "needs_request"})
    return rows


def extract_document(repository: Any, document: dict, settings: Settings, metrics: list[Metric], *,
                     extractor: Callable[[str], dict] | None = None, now: datetime | None = None) -> dict:
    """Locate, ask (or reuse), verify and store candidates for each metric of one filing."""
    extractor = extractor or default_extractor
    results, requests = [], 0
    for metric in metrics:
        windows = locate(document["text"], metric, settings)
        record = {"dataset_id": DATASET_ID, "entity_id": ledger_entity(document["entity"]),
                  "metric_id": metric.metric_id, "document_id": document["document_id"],
                  "version_id": document["version_id"], "passages": windows, "model": "", "response": {}}
        outcome: dict[str, Any] = {"metric": metric.key, "candidates": [], "dropped": []}
        if not windows:
            identity = hashlib.sha256(json.dumps(
                [document["version_id"], metric.metric_id, metric.anchors]).encode("utf-8")).hexdigest()[:32]
            repository.save_document_extraction(
                {**record, "extraction_id": identity, "status": "not_located"}, at=now)
            results.append({**outcome, "state": "not_located"})
            continue
        context = build_context(document, metric, windows)
        identity = request_identity(context)
        held = repository.document_extraction(identity)
        if held and held["status"] != "failed":
            response, outcome["cached"] = json.loads(held["response_json"]), True
        else:
            requests += 1
            record["model"] = get_config().app.llm.for_role(ROLE).model
            try:
                response = extractor(context)
            except Exception as exc:  # noqa: BLE001 - one metric's failure must not lose the others
                repository.save_document_extraction(
                    {**record, "extraction_id": identity, "status": "failed",
                     "response": {"error": f"{type(exc).__name__}: {exc}"[:500]}}, at=now)
                results.append({**outcome, "state": "failed", "error": type(exc).__name__})
                continue
            outcome["cached"] = False
        readings = list(response.get("readings") or []) if response.get("disclosed") else []
        state = "extracted" if readings else "not_disclosed"
        if not outcome["cached"]:
            repository.save_document_extraction(
                {**record, "extraction_id": identity, "status": state, "response": response}, at=now)
        for reading in readings:
            checked = verify(reading, metric, document, windows)
            if checked["value"] is None:
                outcome["dropped"].append({"reasons": checked["reasons"], "quote": checked["quote"][:200]})
                continue
            candidate_id = save_candidate(
                repository, document, metric, checked, extraction_id=identity, method=f"model:{ROLE}", now=now)
            if candidate_id not in [item["candidate_id"] for item in outcome["candidates"]]:
                outcome["candidates"].append({"candidate_id": candidate_id, "reasons": checked["reasons"]})
        results.append({**outcome, "state": state, "note": str(response.get("note") or "")})
    failed = [item["metric"] for item in results if item["state"] == "failed"]
    candidates = [candidate for item in results for candidate in item["candidates"]]
    return {
        "status": "succeeded" if not failed else "partial" if len(failed) < len(results) else "failed",
        "entity": document["entity"], "form_type": document["form_type"], "filing_date": document["filing_date"],
        "document_id": document["document_id"], "model_requests": requests,
        "pending_confirmation": sum(not item["reasons"] for item in candidates),
        "evidence_incomplete": sum(bool(item["reasons"]) for item in candidates),
        "failed_metrics": failed, "metrics": results,
    }
