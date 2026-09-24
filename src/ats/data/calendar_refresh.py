"""Refresh persisted schedule events from configured calendar sources.

The adapters store only timing, identity, release state and provenance. Parsing or
network failures are recorded per source and never delete the last accepted version.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
import os
from pathlib import Path
import re
from typing import Any, Iterable
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import yaml

from ..config import REPO_ROOT, load_pead_global
from .stores.schedule_calendar import (ScheduleCalendarStore, ScheduleEventCandidate,
                                       earnings_identity, fomc_identity, macro_identity)

ET = ZoneInfo("America/New_York")
MONTHS = {name.lower(): index for index, name in enumerate((
    "", "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December")) if name}
MONTHS.update({key[:3]: value for key, value in list(MONTHS.items())})


class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() == "tr":
            self.row = []
        elif tag.lower() in {"td", "th"} and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        value = " ".join(data.split())
        if value:
            self.parts.append(value)
            if self.cell is not None:
                self.cell.append(value)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"td", "th"} and self.cell is not None and self.row is not None:
            self.row.append(" ".join(self.cell))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.row:
                self.rows.append(self.row)
            self.row = None


def _html_text(html: str) -> tuple[str, list[list[str]]]:
    parser = _TextParser()
    parser.feed(html)
    return " ".join(parser.parts), parser.rows


def parse_fomc_html(html: str, *, years: Iterable[int]) -> list[ScheduleEventCandidate]:
    text, _ = _html_text(html)
    normalized = re.sub(r"\s+", " ", text)
    candidates = []
    for year in years:
        marker = re.search(rf"\b{year}\s+FOMC Meetings\b", normalized, re.I)
        if not marker:
            continue
        following = re.search(r"\b20\d{2}\s+FOMC Meetings\b", normalized[marker.end():], re.I)
        section = normalized[marker.end():marker.end() + following.start()] if following else normalized[marker.end():]
        meetings = list(re.finditer(
            r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+"
            r"(\d{1,2})\s*[-–]\s*(\d{1,2})\b", section, re.I))
        for index, match in enumerate(meetings):
            month = MONTHS[match.group(1).lower()]
            meeting_start = date(int(year), month, int(match.group(2)))
            decision_date = date(int(year), month, int(match.group(3)))
            next_start = meetings[index + 1].start() if index + 1 < len(meetings) else len(section)
            meeting_detail = section[match.end():next_start]
            subevents = [("meeting_start", meeting_start, "meeting start")]
            subevents.append(("statement", decision_date, "policy decision"))
            # The official calendar adds a Press Conference link only for meetings
            # where one is scheduled/posted; don't infer one from the meeting cadence.
            if re.search(r"\bPress Conference\b", meeting_detail, re.I):
                subevents.append(("press_conference", decision_date, "press conference"))
            for subevent, when, label in subevents:
                identity = fomc_identity(int(year), month, subevent)
                candidates.append(ScheduleEventCandidate(
                    source_id="federal_reserve_fomc",
                    source_url="https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
                    source_event_ref=identity, event_type="fomc", stable_identity=identity,
                    label=f"FOMC {when:%B %Y} {label}",
                    reference_period=f"{year}-{month:02d}", event_date=when,
                    time_precision="date", market_session="unknown",
                    metadata={"meeting_start_date": meeting_start.isoformat(),
                              "decision_date": decision_date.isoformat(),
                              "subevent": subevent}))
    if not candidates:
        raise ValueError("FOMC calendar parser found no year/month meeting rows (possible parser drift)")
    return candidates


def _ics_unfold(text: str) -> list[str]:
    lines: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def _ics_datetime(line: str) -> tuple[date, str, str, str, str]:
    name, value = line.split(":", 1)
    params = name.split(";")[1:]
    tzid = next((item.split("=", 1)[1] for item in params if item.startswith("TZID=")), "")
    if any(item == "VALUE=DATE" for item in params):
        return date.fromisoformat(f"{value[:4]}-{value[4:6]}-{value[6:8]}"), "", "", "", "date"
    if not re.fullmatch(r"\d{8}T\d{6}Z?", value):
        raise ValueError(f"unsupported ICS date/time value {value!r}")
    parsed = datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S")
    if value.endswith("Z"):
        aware = parsed.replace(tzinfo=timezone.utc).astimezone(ET)
    elif tzid:
        aware = parsed.replace(tzinfo=ZoneInfo(tzid)).astimezone(ET)
    else:
        raise ValueError("ICS event has a clock time but no timezone")
    return aware.date(), aware.strftime("%H:%M"), "America/New_York", aware.isoformat(), "minute"


def parse_bls_ics(text: str) -> list[ScheduleEventCandidate]:
    events: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in _ics_unfold(text):
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT":
            if current is not None:
                events.append(current)
            current = None
        elif current is not None and ":" in line:
            key, value = line.split(":", 1)
            current[key] = value.replace(r"\,", ",").replace(r"\;", ";").replace(r"\n", " ")
    candidates = []
    for item in events:
        title = next((value for key, value in item.items() if key.startswith("SUMMARY")), "")
        normalized_title = title.lower()
        if "consumer price index" in normalized_title or re.search(r"\bcpi\b", normalized_title):
            event_type, series = "cpi", "CPI"
        elif "employment situation" in normalized_title or "nonfarm payroll" in normalized_title:
            event_type, series = "nfp", "NFP"
        else:
            continue
        reference = re.search(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d{2})\b",
                              title, re.I)
        if not reference:
            continue
        period = f"{int(reference.group(2)):04d}-{MONTHS[reference.group(1).lower()]:02d}"
        start_key = next((line for line in item if line.startswith("DTSTART")), "")
        if not start_key:
            continue
        start_line = f"{start_key}:{item[start_key]}"
        event_date, local_time, zone, utc_at, precision = _ics_datetime(start_line)
        phase = "initial"
        identity = macro_identity(series, period, phase)
        status = "cancelled" if item.get("STATUS") == "CANCELLED" else "planned"
        candidates.append(ScheduleEventCandidate(
            source_id="bls_release_calendar", source_url="https://www.bls.gov/schedule/news_release/bls.ics",
            source_event_ref=item.get("UID", identity), event_type=event_type,
            stable_identity=identity, label=title, reference_period=period,
            event_date=event_date, local_time=local_time, timezone=zone, utc_at=utc_at,
            time_precision=precision, market_session="intraday" if precision == "minute" else "unknown",
            status=status, announced_at=item.get("DTSTAMP", ""),
            metadata={"calendar_uid": item.get("UID", "")}))
    if not candidates:
        raise ValueError("BLS ICS parser found no CPI or Employment Situation events")
    return candidates


def _parse_month_period(text: str) -> str:
    match = re.search(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)[, ]+(20\d{2})\b",
                      text, re.I)
    return f"{int(match.group(2)):04d}-{MONTHS[match.group(1).lower()]:02d}" if match else ""


def parse_bea_schedule_html(html: str, *, year: int | None = None) -> list[ScheduleEventCandidate]:
    _, rows = _html_text(html)
    year = year or datetime.now(timezone.utc).year
    candidates = []
    for row in rows:
        text = " ".join(row)
        release = re.search(r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})\s+(\d{1,2}:\d{2})\s*(AM|PM)\b",
                            text, re.I)
        if not release:
            continue
        if "Personal Income and Outlays" in text:
            event_type, series = "pce", "PCE"
            reference = _parse_month_period(text[text.find("Personal Income and Outlays"):])
            if not reference:
                continue
            phase = "initial"
        elif re.search(r"\bGDP\b", text):
            event_type, series = "gdp", "GDP"
            q = re.search(r"\b([1-4])(?:st|nd|rd|th)\s+Quarter\s+(20\d{2})\b", text, re.I)
            stage = re.search(r"\b(Advance|Second|Third) Estimate\b", text, re.I)
            if not q or not stage:
                continue
            reference = f"{q.group(2)}-Q{q.group(1)}"
            phase = stage.group(1).lower()
        else:
            continue
        month, day, clock, meridian = release.groups()
        local = datetime.strptime(f"{clock} {meridian}", "%I:%M %p").time()
        ref_year = int(reference[:4]) if reference[:4].isdigit() else year
        event_date = date(ref_year, MONTHS[month.lower()], int(day))
        local_dt = datetime.combine(event_date, local).replace(tzinfo=ET)
        identity = macro_identity(series, reference, phase)
        candidates.append(ScheduleEventCandidate(
            source_id="bea_release_schedule", source_url="https://www.bea.gov/news/schedule",
            source_event_ref=identity or f"{event_type}:{event_date}", event_type=event_type,
            stable_identity=identity or "", label=text[:300], reference_period=reference,
            event_date=event_date, local_time=local.strftime("%H:%M"),
            timezone="America/New_York", utc_at=local_dt.astimezone(timezone.utc).isoformat(),
            time_precision="minute", market_session="intraday", status="planned",
            metadata={"release_phase": phase}))
    if not candidates:
        raise ValueError("BEA schedule parser found no GDP estimate or Personal Income and Outlays rows")
    return candidates


def _fetch_text(url: str, *, timeout: int = 20) -> str:
    request = Request(url, headers={"User-Agent": "auto-stock-trading-agents schedule calendar/1.0"})
    with urlopen(request, timeout=timeout) as response:
        return response.read(10_000_001).decode("utf-8", errors="replace")


def _load_source_config(config_dir: Path) -> dict[str, Any]:
    path = config_dir / "data" / "schedule_calendar.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _session(value: str) -> str:
    value = (value or "").lower()
    return {"dmh": "intraday", "bmo": "bmo", "amc": "amc"}.get(value, "unknown")


def _earnings_candidates(source_id: str, *, now: datetime, symbols: list[str]
                         ) -> tuple[list[ScheduleEventCandidate], list[str]]:
    from .earnings_calendar import ET as EARNINGS_ET, _finnhub_window, _session_from_clock, _yf_prints

    start, end = now.astimezone(EARNINGS_ET).date() - timedelta(days=14), now.astimezone(EARNINGS_ET).date() + timedelta(days=180)
    finnhub: dict[str, list[dict[str, Any]]] = {}
    yahoo: dict[str, list[dict[str, Any]]] = {}
    errors = []
    for symbol in symbols:
        if source_id in {"finnhub_earnings", "yfinance_earnings"}:
            try:
                finnhub[symbol] = _finnhub_window(symbol, start, end)
            except Exception as exc:
                errors.append(f"finnhub {symbol}: {type(exc).__name__}")
        if source_id == "yfinance_earnings":
            try:
                yahoo[symbol] = _yf_prints(symbol, start, end)
            except Exception as exc:
                errors.append(f"yfinance {symbol}: {type(exc).__name__}")
    output = []
    for symbol in symbols:
        primary = finnhub.get(symbol, [])
        for item in primary if source_id == "finnhub_earnings" else ():
            identity = earnings_identity(symbol, item.get("year"), item.get("quarter"))
            session = _session(item.get("hour", ""))
            output.append(ScheduleEventCandidate(
                source_id="finnhub_earnings_calendar",
                source_url="https://finnhub.io/api/v1/calendar/earnings",
                source_event_ref=identity or f"{symbol}:{item['date']}",
                event_type="earnings", stable_identity=identity or "",
                label=f"{symbol} earnings {item.get('year') or ''} Q{item.get('quarter') or ''}".strip(),
                reference_period=(f"FY{item['year']}Q{item['quarter']}"
                                  if item.get("year") and item.get("quarter") else ""),
                event_date=item["date"], time_precision="date", market_session=session,
                # Provider actuals are not a release-admission signal. The event
                # remains planned until the document platform has admitted a filing
                # or company release and confirm_release appends that evidence.
                status="planned",
                metadata={"session_source": "finnhub-hour" if session != "unknown" else "none",
                          "entity": symbol,
                          "fiscal_label": (f"FY{item['year']}Q{item['quarter']}"
                                           if item.get("year") and item.get("quarter") else "")}))
        for item in yahoo.get(symbol, []) if source_id == "yfinance_earnings" else ():
            match = next((row for row in primary if abs((row["date"] - item["date"]).days) <= 1), None)
            identity = earnings_identity(symbol, match.get("year"), match.get("quarter")) if match else None
            timestamp = item.get("at")
            session = _session_from_clock(timestamp)
            if timestamp and session != "unknown":
                local = timestamp.astimezone(EARNINGS_ET)
                precision, local_time, zone, utc_at = "minute", local.strftime("%H:%M"), "America/New_York", local.isoformat()
            else:
                precision, local_time, zone, utc_at = "date", "", "", ""
            output.append(ScheduleEventCandidate(
                source_id="yahoo_earnings_calendar", source_url="https://finance.yahoo.com/",
                source_event_ref=identity or f"{symbol}:{item['date']}", event_type="earnings",
                stable_identity=identity or "", label=f"{symbol} earnings calendar candidate",
                reference_period=(f"FY{match['year']}Q{match['quarter']}"
                                  if match and match.get("year") and match.get("quarter") else ""),
                event_date=item["date"], local_time=local_time, timezone=zone, utc_at=utc_at,
                time_precision=precision, market_session=_session(session),
                status="planned",
                metadata={"matched_finnhub": bool(match),
                          "entity": symbol,
                          "fiscal_label": (f"FY{match['year']}Q{match['quarter']}"
                                           if match and match.get("year") and match.get("quarter") else ""),
                          "session_source": "yfinance-clock" if session != "unknown" else "none"}))
    if not output and errors:
        raise RuntimeError("; ".join(errors[:5]))
    return output, errors


def _manual_overlay_candidates(config_dir: Path) -> list[ScheduleEventCandidate]:
    from ..schemas.events import CalendarEvent

    raw = yaml.safe_load((config_dir / "events.yaml").read_text(encoding="utf-8")) or {}
    output = []
    for raw_event in raw.get("events", []) or []:
        item = CalendarEvent.model_validate(raw_event)
        kind = str(item.kind.value if hasattr(item.kind, "value") else item.kind).lower()
        label = item.label or kind
        when = item.date
        if kind == "fomc":
            identity = fomc_identity(when.year, when.month, "statement")
        elif kind in {"cpi", "pce", "nfp"}:
            m = re.search(r"(\d{1,2})\s*月", label)
            reference_month = (when.month - 1) or 12
            reference_year = when.year if when.month > 1 else when.year - 1
            if m:
                reference_month = int(m.group(1))
                reference_year = when.year if reference_month < when.month else when.year - 1
            series = {"cpi": "CPI", "pce": "PCE", "nfp": "NFP"}[kind]
            identity = macro_identity(series, f"{reference_year}-{reference_month:02d}", "initial")
        elif kind == "gdp":
            q = re.search(r"Q([1-4])", label, re.I)
            stage = ("advance" if "先行" in label or "Advance" in label else
                     "second" if "第二次" in label or "Second" in label else
                     "third" if "第三次" in label or "Third" in label else "unknown")
            identity = macro_identity("GDP", f"{when.year}-Q{q.group(1)}" if q else "", stage)
        else:
            stable_key = item.stable_id or label or kind
            stable_key = re.sub(r"[^\w.-]+", "-", stable_key.strip().lower()).strip("-")
            identity = f"manual:{kind}:{stable_key or kind}"
        output.append(ScheduleEventCandidate(
            source_id="manual_events_yaml", source_url="config/events.yaml",
            source_event_ref=identity, event_type=kind, stable_identity=identity,
            label=label, event_date=when, time_precision="date", status="planned",
            metadata={"configured_triggers": list(item.triggers), "overlay": True}))
    return output


def reconcile_earnings_release_materials(*, store: ScheduleCalendarStore | None = None,
                                         now: datetime | None = None) -> list[dict[str, Any]]:
    """Promote scheduled earnings events only when admitted official documents exist."""
    from .products.unstructured import earnings_document_package
    from .stores.unstructured import get_platform_unstructured_repository

    repository = store or ScheduleCalendarStore()
    now = now or datetime.now(timezone.utc)
    documents = get_platform_unstructured_repository()
    ready: list[dict[str, Any]] = []
    try:
        for event in repository.latest_events(limit=10_000):
            if event["event_type"] != "earnings" or event["status"] == "cancelled":
                continue
            payload = event.get("payload", {})
            metadata = payload.get("metadata", {}) or {}
            entity = str(metadata.get("entity", "")).upper()
            period = str(metadata.get("fiscal_label") or event.get("reference_period") or "")
            if not entity or not period:
                continue
            package = earnings_document_package(documents, entity=entity, period=period)
            role_to_kind = {
                "regulatory_filing": "filing",
                "earnings_release": "company_release",
                "earnings_transcript": "transcript",
            }
            materials = [{
                "kind": role_to_kind[item.role], "status": "admitted",
                "ref": f"{item.document_id}@{item.version_id}",
                "document_id": item.document_id, "version_id": item.version_id,
                "source": item.source, "source_url": item.source_url,
            } for item in package.documents if item.role in role_to_kind]
            if not any(item["kind"] in {"filing", "company_release"} for item in materials):
                continue
            # A newly admitted transcript, call, or revised filing is a new
            # evidence vintage. Append a release-confirmation event version so the
            # follow-up run gets a distinct idempotency key and keeps the prior run.
            confirmed = {str(material["ref"]): material
                         for item in event.get("release_confirmations", [])
                         for material in item.get("materials", [])}
            admitted = {str(material["ref"]): material for material in materials}
            new_material_vintage = set(admitted) - set(confirmed)
            if (event["status"] == "planned" or not confirmed or new_material_vintage):
                merged = {**confirmed, **admitted}
                event = repository.confirm_release(
                    event["event_id"], expected_version=event["event_version"],
                    admitted_materials=list(merged.values()),
                    source_id="platform_admitted_documents", at=now)
            ready.append({"event": event, "materials": materials})
    finally:
        documents.close()
    return ready


def refresh_schedule_calendar(*, source_ids: Iterable[str] | None = None,
                              now: datetime | None = None,
                              config_dir: str | Path | None = None,
                              store: ScheduleCalendarStore | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    root = Path(config_dir or os.environ.get("ATS_CONFIG_DIR", REPO_ROOT / "config"))
    config = _load_source_config(root)
    source_cfg = config.get("sources", {})
    selected = set(source_ids or source_cfg)
    unknown = selected - set(source_cfg)
    if unknown:
        raise ValueError(f"unknown schedule calendar sources: {sorted(unknown)}")
    repository = store or ScheduleCalendarStore()
    results = []
    for source_id in sorted(selected):
        source = source_cfg[source_id]
        run_id = repository.record_source_run(source_id, at=now,
                                              provenance={"adapter": source.get("adapter"),
                                                          "url": source.get("url", "")})
        discovered = published = conflicts = quarantined = 0
        source_errors: list[str] = []
        try:
            adapter = source["adapter"]
            if adapter in {"finnhub_earnings", "yfinance_earnings"}:
                targets = [str(s).upper() for s in load_pead_global().get("targets", [])]
                cap = int(source.get("budget", {}).get("symbols_per_run", len(targets)))
                candidates, source_errors = _earnings_candidates(
                    adapter, now=now, symbols=targets[:cap])
            elif adapter == "federal_reserve_fomc_html":
                years = range(now.year, now.year + 2)
                candidates = parse_fomc_html(_fetch_text(source["url"]), years=years)
            elif adapter == "bls_ics":
                candidates = parse_bls_ics(_fetch_text(source["url"]))
            elif adapter == "bea_schedule_html":
                candidates = parse_bea_schedule_html(_fetch_text(source["url"]), year=now.year)
            else:
                raise ValueError(f"unregistered schedule calendar adapter {adapter!r}")
            discovered = len(candidates)
            for candidate in candidates:
                saved = repository.submit_candidate(candidate, at=now)
                if saved["duplicate"]:
                    continue
                if saved["review_status"] == "conflict":
                    conflicts += 1
                elif saved["review_status"] == "pending_identity":
                    quarantined += 1
                else:
                    repository.publish_candidate(saved["candidate_id"], at=now)
                    published += 1
            status = "complete" if not conflicts and not quarantined and not source_errors else "partial"
            repository.finish_source_run(run_id, status=status, discovered=discovered,
                                         published=published, conflicts=conflicts,
                                         quarantined=quarantined,
                                         error_code="partial_sources" if source_errors else "",
                                         error_detail="; ".join(source_errors), at=now)
            results.append({"source_id": source_id, "status": status,
                            "discovered": discovered, "published": published,
                            "conflicts": conflicts, "quarantined": quarantined})
        except Exception as exc:
            repository.finish_source_run(run_id, status="failed", discovered=discovered,
                                         published=published, conflicts=conflicts,
                                         quarantined=quarantined,
                                         error_code=type(exc).__name__, error_detail=str(exc), at=now)
            results.append({"source_id": source_id, "status": "failed",
                            "error_code": type(exc).__name__, "error": str(exc)[:300]})
    overlay_results = []
    if not source_ids or "manual_events_yaml" in selected:
        for candidate in _manual_overlay_candidates(root):
            saved = repository.submit_candidate(candidate, at=now)
            if saved["duplicate"]:
                continue
            if repository.latest_events(limit=10_000):
                existing = next((e for e in repository.latest_events(limit=10_000)
                                 if e["event_id"] == candidate.event_id), None)
            else:
                existing = None
            if existing:
                repository.override_event(candidate.event_id, patch={
                    "event_date": candidate.event_date.isoformat(), "local_time": "",
                    "timezone": "", "utc_at": "", "time_precision": "date",
                    "market_session": "unknown", "status": candidate.status,
                    "label": candidate.label, "metadata": candidate.metadata},
                    actor="config/events.yaml", reason="operator-maintained calendar overlay",
                    evidence={"path": "config/events.yaml", "candidate_id": saved["candidate_id"]},
                    at=now, source_candidate_id=saved["candidate_id"])
                overlay_results.append({"event_id": candidate.event_id, "status": "overridden"})
            elif saved["review_status"] != "pending_identity":
                repository.publish_candidate(saved["candidate_id"], actor="config/events.yaml",
                                             reason="operator-maintained calendar overlay", at=now)
                overlay_results.append({"event_id": candidate.event_id, "status": "published"})
    release_materials = []
    release_reconciliation = {"status": "not_requested", "error": ""}
    if source_ids is None:
        try:
            release_materials = reconcile_earnings_release_materials(store=repository, now=now)
            release_reconciliation["status"] = "complete"
        except Exception as exc:
            # Calendar source refresh remains useful even if the document platform
            # is unavailable; no release event is promoted on this failed pass.
            release_reconciliation = {"status": "failed",
                                      "error": f"{type(exc).__name__}: {exc}"[:300]}
    return {"sources": results, "manual_overlay": overlay_results,
            "release_reconciliation": release_reconciliation,
            "release_ready_events": [{"event_id": item["event"]["event_id"],
                                      "event_version": item["event"]["event_version"],
                                      "material_refs": [m["ref"] for m in item["materials"]]}
                                     for item in release_materials],
            "as_of": now.astimezone(timezone.utc).isoformat()}


__all__ = ["parse_bea_schedule_html", "parse_bls_ics", "parse_fomc_html",
           "reconcile_earnings_release_materials",
           "refresh_schedule_calendar"]
