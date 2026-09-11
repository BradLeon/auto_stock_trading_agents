"""ONS BICS AI conditional-module adapter.

The publication format is workbook-first and changes by wave.  This adapter only
admits rows whose full question/routing fingerprint is explicitly recognised;
keyword matches are reported as methodology drift rather than silently ingested.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import json
import math
import re
from typing import Any

from ..core.structured_models import (
    AdapterArtifact, AdapterBatch, AdapterFailure, DiscoveryResult, DiscoveryStatus, FetchRequest,
    IngestionStatus, NativeRecord, ReleaseCandidate,
)


ONS_BICS_URL = "https://www.ons.gov.uk/economy/economicoutputandproductivity/output/datasets/businessinsightsandimpactontheukeconomy"
ONS_AI_ARTICLE = "https://www.ons.gov.uk/businessindustryandtrade/business/businessservices/articles/artificialintelligenceinukbusinesses/2023to2026"
ONS_AI_CHARTS = {
    "ai_use_pct": ONS_AI_ARTICLE + "/808491a0&format=csv",
    "ai_technologies_mean": ONS_AI_ARTICLE + "/7296b513&format=csv",
}
# ONS chart downloads use /generator?uri=<article path/hash>&format=csv.
ONS_AI_CHARTS = {field: url.replace(ONS_AI_ARTICLE + "/", "https://www.ons.gov.uk/generator?uri=/businessindustryandtrade/business/businessservices/articles/artificialintelligenceinukbusinesses/2023to2026/")
                 for field, url in ONS_AI_CHARTS.items()}
AI_MARKERS = ("artificial intelligence", " ai ", "generative ai")
FIELD_BY_LABEL = {
    "use at least one artificial intelligence technology": "ai_use_pct",
    "average number of artificial intelligence technologies": "ai_technologies_mean",
    "extensive use": "extensive_use_pct",
    "limited use": "limited_use_pct",
    "pilot": "pilot_use_pct",
    "daily use": "employee_daily_use_pct",
}
APPROVED_QUESTION_PREFIXES = (
    "which of the following artificial intelligence technologies, if any, does your business currently use?",
    "to what extent does your business use artificial intelligence technologies in its business operations?",
    "approximately, what proportion of your workforce, if any, currently use artificial intelligence technologies as part of their daily work?",
    "what does your business currently use artificial intelligence technologies for?",
    "how did your business adopt these artificial intelligence technologies?",
    "what has been your business's approach to integrating artificial intelligence technology related skills into your workforce?",
    "what impact, if any, have artificial intelligence technologies had on your business's overall workforce headcount?",
)


def _norm(value: Any) -> str:
    return " ".join(str(value or "").casefold().replace("–", "-").split())


def question_fingerprint(*, question: str, answers: list[str], routing: str, universe: str) -> str:
    canonical = "|".join((_norm(question), "|".join(sorted(_norm(item) for item in answers)),
                          _norm(routing), _norm(universe)))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _links(html: str) -> list[str]:
    return re.findall(r'''href=["']([^"']+)["']''', html, flags=re.I)


def _absolute(url: str) -> str:
    return url if url.startswith("http") else "https://www.ons.gov.uk" + (url if url.startswith("/") else "/" + url)


def _wave(url: str) -> str:
    match = re.search(r"wave[-_ ]?(\d+)", url, re.I)
    return match.group(1) if match else "unknown"


def _table_rows(payload: bytes, url: str) -> list[dict[str, Any]]:
    """Read a normalized CSV fixture or find a header row in an ONS workbook."""
    if url.casefold().endswith(".csv"):
        import csv
        return list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError("pandas/openpyxl are required to parse ONS BICS workbooks") from exc
    book = pd.ExcelFile(io.BytesIO(payload))
    rows: list[dict[str, Any]] = []
    for sheet in book.sheet_names:
        raw = pd.read_excel(book, sheet_name=sheet, header=None)
        header_idx = next((idx for idx in range(min(30, len(raw)))
                           if any("question" in _norm(value) or "estimate" in _norm(value)
                                  for value in raw.iloc[idx].tolist())), None)
        if header_idx is None:
            continue
        frame = pd.read_excel(book, sheet_name=sheet, header=header_idx).dropna(how="all")
        for item in frame.to_dict(orient="records"):
            item["_sheet"] = sheet
            rows.append({str(key): value for key, value in item.items()})
    return rows


def _get(row: dict[str, Any], *keywords: str) -> Any:
    wanted = " ".join(keywords)
    for key, value in row.items():
        if not str(key).startswith("_") and _norm(key).replace("_", " ") == wanted:
            return value
    for key, value in row.items():
        if str(key).startswith("_"):
            continue
        label = _norm(key)
        if all(token in label for token in keywords):
            return value
    return ""


def _number(value: Any) -> float | None:
    try:
        number = float(str(value).replace("%", "").replace(",", "").strip())
        return number if math.isfinite(number) else None
    except ValueError:
        return None


def _field(question: str, answer: str) -> str:
    text = f"{_norm(question)} {_norm(answer)}"
    if "used extensively" in text:
        return "extensive_use_pct"
    if "used on a limited basis" in text:
        return "limited_use_pct"
    for marker, field in FIELD_BY_LABEL.items():
        if marker in text:
            return field
    return "ons_response_share"


def _entity(row: dict[str, Any]) -> str:
    sic = str(_get(row, "sic") or _get(row, "industry") or "").strip()
    size = str(_get(row, "size") or _get(row, "employment") or "").strip()
    stratum = str(row.get("stratum", "")).strip()
    if stratum and any(token in stratum for token in (" - ", "+")):
        size = size or stratum
    elif stratum and stratum.casefold() not in {"all businesses", "all size bands", "uk", "total"}:
        sic = sic or stratum
    if sic and size:
        return f"BUSINESS_POP:UK:SIC:{sic}:EMP:{size}"
    if sic:
        return f"BUSINESS_POP:UK:SIC:{sic}"
    if size:
        return f"BUSINESS_POP:UK:EMP:{size}"
    return "BUSINESS_POP:UK:ALL"


def _workbook_questionnaire_url(payload: bytes) -> str:
    try:
        import openpyxl
        book = openpyxl.load_workbook(io.BytesIO(payload), read_only=False, data_only=True)
        for sheet in book.worksheets[:3]:
            for row in sheet.iter_rows(min_row=1, max_row=20, max_col=10):
                for cell in row:
                    target = getattr(getattr(cell, "hyperlink", None), "target", "")
                    if target and ("surveyquestions" in target or "questionsonthe" in target):
                        return target
    except Exception:
        return ""
    return ""


def _workbook_release_metadata(payload: bytes) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    try:
        import openpyxl
        book = openpyxl.load_workbook(io.BytesIO(payload), read_only=True, data_only=True)
        values = [str(cell.value) for row in book[book.sheetnames[0]].iter_rows(min_row=1, max_row=20, max_col=3)
                  for cell in row if cell.value]
        for value in values:
            if value.startswith("Released "):
                metadata["released"] = value.removeprefix("Released ")
            if value.startswith("Survey reference period:"):
                metadata["reference_window"] = value.split(":", 1)[1].strip()
            match = re.search(r"around ([\d.]+)% \(([\d,]+)\).+responded", value)
            if match:
                metadata["response_rate_pct"] = float(match.group(1))
                metadata["respondent_count"] = int(match.group(2).replace(",", ""))
    except Exception:
        pass
    return metadata


def _normalized_workbook_rows(payload: bytes, *, target_wave: str = "") -> list[dict[str, Any]]:
    """Convert approved ONS AI time-series sheets from wide ratios to cells."""
    import pandas as pd
    book = pd.ExcelFile(io.BytesIO(payload))
    rows: list[dict[str, Any]] = []
    for sheet in book.sheet_names:
        if not (sheet.startswith("AI ") and "TS (WTD)" in sheet):
            continue
        raw = pd.read_excel(book, sheet_name=sheet, header=None)
        if raw.empty:
            continue
        question = str(raw.iloc[0, 0]).removeprefix("Question:").strip()
        if not any(_norm(question).startswith(prefix) for prefix in APPROVED_QUESTION_PREFIXES):
            continue
        header_idx = next((idx for idx in range(min(15, len(raw)))
                           if "dates" in _norm(raw.iloc[idx, 0]) and "wave" in _norm(raw.iloc[idx, 1])), None)
        if header_idx is None:
            continue
        headers = [str(value).strip() for value in raw.iloc[header_idx].tolist()]
        answer_options = [value for value in headers[3:] if value and value != "nan"]
        universe = re.split(r",\s*UK\s*,", str(raw.iloc[1, 0]), flags=re.I)[0]
        for _, source in raw.iloc[header_idx + 1:].iterrows():
            wave = str(source.iloc[1]).replace("Wave", "").strip()
            if not wave.isdigit() or (target_wave and wave != target_wave):
                continue
            stratum = str(source.iloc[2]).strip()
            if not stratum or stratum == "nan":
                continue
            base = {"question_text": question, "universe": universe, "routing": universe,
                    "date": str(source.iloc[0]), "wave": wave, "stratum": stratum,
                    "_sheet": sheet, "_answer_options": answer_options}
            values: dict[str, float] = {}
            for index, answer in enumerate(headers[3:], start=3):
                if not answer or answer == "nan" or index >= len(source):
                    continue
                numeric = _number(source.iloc[index])
                if numeric is None:
                    rows.append({**base, "answer_text": answer, "estimate": None,
                                 "suppression_code": str(source.iloc[index])})
                    continue
                percentage = numeric * 100 if 0 <= numeric <= 1 else numeric
                values[_norm(answer)] = percentage
                rows.append({**base, "answer_text": answer, "estimate": percentage})
            if (sheet.startswith(("AI Extent Use", "AI Workforce Use"))
                    and len(values) == len(answer_options) and abs(sum(values.values()) - 100.0) > 0.25):
                raise ValueError(f"ons_answer_sum_failed:{sheet}:{wave}:{stratum}:{sum(values.values())}")
            if sheet.startswith("AI Current Usage"):
                no_use = next((value for answer, value in values.items()
                               if "does not currently use" in answer), None)
                unsure = next((value for answer, value in values.items() if answer == "not sure"), 0.0)
                if no_use is not None:
                    use_share = 100.0 - no_use - unsure
                    rows.append({**base, "answer_text": "Use at least one artificial intelligence technology",
                                 "estimate": use_share})
                    technology_sum = sum(value for answer, value in values.items()
                                         if "does not currently use" not in answer and answer != "not sure")
                    if use_share > 0:
                        rows.append({**base, "answer_text": "Average number of artificial intelligence technologies",
                                     "estimate": technology_sum / use_share})
            if sheet.startswith("AI Workforce Use"):
                over_half = sum(value for answer, value in values.items()
                                if any(token in answer for token in ("between 50%", "more than 75%",
                                                                    "more than half", "over half", "76%")))
                if over_half:
                    rows.append({**base, "answer_text": "Daily use by more than half of workforce",
                                 "estimate": over_half})
    return rows


def _unapproved_current_wave_questions(payload: bytes, target_wave: str) -> list[str]:
    """Find AI-labelled current-wave tables whose exact question is not approved."""
    import pandas as pd
    book = pd.ExcelFile(io.BytesIO(payload))
    unknown: list[str] = []
    for sheet in book.sheet_names:
        if not (sheet.startswith("AI ") and "TS (WTD)" in sheet):
            continue
        raw = pd.read_excel(book, sheet_name=sheet, header=None)
        if raw.empty:
            continue
        question = str(raw.iloc[0, 0]).removeprefix("Question:").strip()
        approved = any(_norm(question).startswith(prefix) for prefix in APPROVED_QUESTION_PREFIXES)
        has_target = any(str(value).replace("Wave", "").strip() == target_wave
                         for value in raw.iloc[:, 1].tolist()) if raw.shape[1] > 1 else False
        if has_target and any(marker in f" {_norm(question)} " for marker in AI_MARKERS) and not approved:
            unknown.append(question)
    return sorted(set(unknown))


def _article_chart_latest(payload: bytes) -> float:
    import csv
    rows = list(csv.reader(io.StringIO(payload.decode("utf-8-sig"))))
    header = next(index for index, row in enumerate(rows) if row and _norm(row[0]) == "date")
    values = [_number(row[1]) for row in rows[header + 1:] if len(row) > 1]
    numeric = [value for value in values if value is not None]
    if not numeric:
        raise ValueError("ons_article_chart_has_no_values")
    return numeric[-1]


def parse_ons_rows(*, rows: list[dict[str, Any]], candidate: ReleaseCandidate,
                   fetched_at: datetime, approved_fingerprints: set[str] | None = None) -> tuple[list[NativeRecord], bytes, set[str]]:
    records: list[NativeRecord] = []
    unknown: set[str] = set()
    accepted_rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    try:
        published_at = datetime.strptime(str(candidate.metadata.get("released", "")), "%d %B %Y").replace(tzinfo=timezone.utc)
    except ValueError:
        published_at = None
    for row in rows:
        question = str(_get(row, "question") or row.get("question_text") or "")
        answer = str(_get(row, "answer") or row.get("answer_text") or "")
        if not any(marker in f" {_norm(question)} " for marker in AI_MARKERS):
            continue
        universe = str(_get(row, "universe") or row.get("universe") or "all in-scope businesses")
        routing = str(_get(row, "route") or row.get("routing") or "")
        answers = list(row.get("_answer_options") or [answer])
        fingerprint = question_fingerprint(question=question, answers=answers, routing=routing, universe=universe)
        # No fingerprint is implicitly approved.  A new/changed questionnaire is
        # evidence for review, not permission to silently join a time series.
        semantically_approved = any(_norm(question).startswith(prefix) for prefix in APPROVED_QUESTION_PREFIXES)
        if ((approved_fingerprints is not None and fingerprint not in approved_fingerprints)
                or (approved_fingerprints is None and not semantically_approved)):
            unknown.add(fingerprint)
            continue
        accepted_rows.append(row)  # includes suppressed cells; missing never becomes zero
        field = _field(question, answer)
        raw_estimate = row.get("estimate") if "estimate" in row else _get(row, "estimate")
        if raw_estimate in (None, ""):
            raw_estimate = _get(row, "value")
        value = _number(raw_estimate)
        if value is None:
            continue
        if not 0 <= value <= 100 and field != "ai_technologies_mean":
            raise ValueError(f"ons_percent_out_of_range:{field}")
        if field == "ai_technologies_mean" and value < 0:
            raise ValueError("ons_average_technologies_negative")
        dimensions = {
            "statistical_unit": "UK business", "denominator_scope": universe,
            "routing": routing, "question_text": question, "question_fingerprint": fingerprint,
            "question_regime": candidate.methodology_fingerprint, "uk_supplement": True,
            "sic": _get(row, "sic") or _get(row, "industry") or "", "employment_size": _get(row, "size") or "",
            "answer": answer, "wave": row.get("wave", candidate.metadata.get("wave", "")),
            "official_statistics_status": "official_statistics_in_development",
            "questionnaire_url": candidate.metadata.get("questionnaire", ""),
            "response_rate_pct": candidate.metadata.get("response_rate_pct"),
            "respondent_count": candidate.metadata.get("respondent_count"),
            "quality_notes": candidate.metadata.get("quality_notes", "voluntary survey; suppression code [c] retained in artifact"),
        }
        period = f"wave-{row.get('wave') or candidate.metadata.get('wave') or candidate.period.removeprefix('wave-')}"
        duplicate = (_entity(row), period, field, fingerprint, answer)
        if duplicate in seen:
            raise ValueError(f"ons_duplicate_cell:{duplicate}")
        seen.add(duplicate)
        records.append(NativeRecord(entity_id=_entity(row), provider_field=field, period=period,
                                    value=value, unit="count" if field == "ai_technologies_mean" else "percent",
                                    period_basis="survey_wave", period_start=str(candidate.metadata.get("reference_start", "")),
                                    period_end=str(candidate.metadata.get("reference_end", "")), published_at=published_at,
                                    dimensions=dimensions, raw={"provider_row": row, "candidate": candidate.model_dump(mode="json")},
                                    slice_key=f"ons:{candidate.period}"))
    payload = "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) for row in accepted_rows).encode()
    return records, payload, unknown


class ONSBICSAIAdapter:
    source_id = "ons_bics_ai"
    dataset_id = "ai_enterprise_adoption_uk"

    def __init__(self, *, client=None, clock=None, approved_fingerprints: set[str] | None = None):
        self.client = client
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.approved_fingerprints = approved_fingerprints

    def _content(self, client, url: str) -> bytes:
        response = client.get(url, timeout=90)
        response.raise_for_status()
        return bytes(response.content)

    def discover(self, request: FetchRequest) -> DiscoveryResult:
        import httpx
        checked = self.clock().astimezone(timezone.utc)
        client = self.client or httpx.Client(follow_redirects=True)
        close = self.client is None
        try:
            html = self._content(client, ONS_BICS_URL).decode("utf-8", errors="replace")
            urls = [_absolute(url) for url in _links(html) if url.casefold().endswith((".xlsx", ".csv"))]
            if not urls:
                return DiscoveryResult(source_id=request.source_id, dataset_id=request.dataset_id, checked_at=checked,
                                       status=DiscoveryStatus.NOT_YET_PUBLISHED, diagnostics={"reason": "no_workbook_link"})
            # URL lexical order places wave-99 after wave-163.  Wave is numeric;
            # choose its newest known value and use URL only as a tie-breaker.
            workbook = max(urls, key=lambda url: (int(_wave(url)) if _wave(url).isdigit() else -1, url))
            wave = _wave(workbook)
            payload = self._content(client, workbook)
            digest = hashlib.sha256(payload).hexdigest()
            rows = _normalized_workbook_rows(payload, target_wave=wave)
            unapproved = _unapproved_current_wave_questions(payload, wave)
            has_ai = bool(rows)
            questionnaire = _workbook_questionnaire_url(payload)
            release_metadata = _workbook_release_metadata(payload)
            if not has_ai and unapproved:
                return DiscoveryResult(source_id=request.source_id, dataset_id=request.dataset_id, checked_at=checked,
                                       status=DiscoveryStatus.METHODOLOGY_BREAK,
                                       latest_upstream_identity=f"ONS:{wave}:{digest}",
                                       diagnostics={"wave": wave, "workbook": workbook,
                                                    "questionnaire": questionnaire, **release_metadata,
                                                    "unapproved_ai_questions": unapproved})
            if not has_ai:
                return DiscoveryResult(source_id=request.source_id, dataset_id=request.dataset_id, checked_at=checked,
                                       status=DiscoveryStatus.QUESTION_NOT_FIELDED,
                                       latest_upstream_identity=f"ONS:{wave}:{digest}",
                                       diagnostics={"wave": wave, "workbook": workbook,
                                                    "questionnaire": questionnaire, **release_metadata,
                                                    "reason": "ai_question_not_fielded"})
            fingerprints = sorted({question_fingerprint(
                question=str(row["question_text"]), answers=list(row.get("_answer_options") or []),
                routing=str(row.get("routing", "")), universe=str(row.get("universe", ""))) for row in rows})
            fingerprint = hashlib.sha256("|".join(fingerprints).encode()).hexdigest()
            candidate = ReleaseCandidate(identity=f"ONS:{wave}:{workbook}:{digest}", period=f"wave-{wave}",
                                         urls=[workbook] + ([questionnaire] if questionnaire else []),
                                         methodology_fingerprint=fingerprint,
                                         metadata={"wave": wave, "workbook": workbook,
                                                   "workbook_sha256": digest, "questionnaire": questionnaire,
                                                   **release_metadata,
                                                   "question_fingerprints": fingerprints,
                                                   "reference_start": str(rows[0].get("date", "")),
                                                   "reference_end": str(rows[0].get("date", ""))})
            return DiscoveryResult(source_id=request.source_id, dataset_id=request.dataset_id, checked_at=checked,
                                   status=DiscoveryStatus.NEW_RELEASE, latest_upstream_identity=candidate.identity,
                                   latest_available_period=candidate.period, candidates=[candidate])
        finally:
            if close:
                client.close()

    def fetch(self, request: FetchRequest) -> AdapterBatch:
        import httpx
        fetched = self.clock().astimezone(timezone.utc)
        client = self.client or httpx.Client(follow_redirects=True)
        close = self.client is None
        try:
            supplied = (request.query_scope or {}).get("discovery_candidates") or []
            if supplied:
                candidate = ReleaseCandidate.model_validate(supplied[-1])
            else:
                discovery = self.discover(request)
                if not discovery.candidates:
                    status = IngestionStatus.NO_COVERAGE if discovery.status == DiscoveryStatus.QUESTION_NOT_FIELDED else IngestionStatus.NOT_YET_PUBLISHED
                    return AdapterBatch(source_id=request.source_id, dataset_id=request.dataset_id, status=status, fetched_at=fetched)
                candidate = discovery.candidates[-1]
            payload = self._content(client, candidate.urls[0])
            digest = hashlib.sha256(payload).hexdigest()
            if candidate.metadata.get("workbook_sha256") and digest != candidate.metadata["workbook_sha256"]:
                raise ValueError("ons_workbook_changed_after_discovery")
            rows = _normalized_workbook_rows(payload)
            records, filtered, unknown = parse_ons_rows(rows=rows, candidate=candidate, fetched_at=fetched,
                                                         approved_fingerprints=self.approved_fingerprints)
            if unknown:
                return AdapterBatch(source_id=request.source_id, dataset_id=request.dataset_id,
                                    status=IngestionStatus.VALIDATION_FAILED, fetched_at=fetched,
                                    provider_metadata={"unknown_question_fingerprints": sorted(unknown)})
            artifacts = [AdapterArtifact(artifact_key=f"ons:{candidate.period}", payload=filtered,
                                                           query_scope={"wave": candidate.period}, source_url=candidate.urls[0],
                                                           source_version=candidate.identity, media_type="application/x-ndjson",
                                                           retention="query_slice", storage_mode="filtered", pointer=candidate.urls[0],
                                                           metadata={"candidate": candidate.model_dump(mode="json"),
                                                                     "workbook_sha256": digest,
                                                                     "questionnaire_url": candidate.metadata.get("questionnaire", ""),
                                                                     "suppressed_cells": sum(row.get("estimate") is None for row in rows),
                                                                     "parser_version": "v2"})]
            reconciliation: dict[str, Any] = {}
            failures: list[AdapterFailure] = []
            for field, url in ONS_AI_CHARTS.items():
                try:
                    chart = self._content(client, url)
                    chart_value = _article_chart_latest(chart)
                    artifacts.append(AdapterArtifact(artifact_key=f"ons:{candidate.period}:article:{field}",
                        payload=chart, query_scope={"wave": candidate.period, "supporting_metric": field},
                        source_url=url, source_version=hashlib.sha256(chart).hexdigest(), media_type="text/csv",
                        retention="query_slice", storage_mode="full", pointer=url,
                        metadata={"role": "official_article_reconciliation", "metric": field}))
                    workbook_value = next((float(record.value) for record in records
                        if record.period == candidate.period and record.provider_field == field
                        and record.entity_id == "BUSINESS_POP:UK:EMP:ALL SIZE BANDS EXCLUDING 0 - 9"), None)
                    difference = abs(workbook_value - chart_value) if workbook_value is not None else None
                    reconciliation[field] = {"workbook": workbook_value, "article": chart_value,
                                             "absolute_difference": difference}
                    if difference is not None and difference > 0.05:
                        records = [record for record in records if record.provider_field != field]
                        failures.append(AdapterFailure(status=IngestionStatus.VALIDATION_FAILED,
                            message=f"ons_article_workbook_conflict:{field}:{workbook_value}:{chart_value}",
                            slice_key=f"ons:{candidate.period}:article:{field}"))
                except Exception as exc:
                    reconciliation[field] = {"status": "unavailable", "error": f"{type(exc).__name__}:{exc}"}
            return AdapterBatch(source_id=request.source_id, dataset_id=request.dataset_id,
                                status=(IngestionStatus.PARTIAL if failures and records else
                                        IngestionStatus.SUCCEEDED if records else IngestionStatus.ZERO_MATCH),
                                fetched_at=fetched, records=records, artifacts=artifacts, failures=failures,
                                provider_metadata={"reconciliation": reconciliation,
                                                   "warnings": ["ons_reconciliation_conflict"] if failures else []})
        finally:
            if close:
                client.close()
