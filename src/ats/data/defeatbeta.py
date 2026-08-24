"""Earnings-call transcripts from the defeatbeta Yahoo Finance mirror (HuggingFace).

Primary transcript source, ahead of every search-based path. The point is not accuracy
in the usual sense — it is that **two whole classes of failure become impossible**:

  * Wrong company. Rows are keyed by ticker. Nothing here can hand back Sherwin-Williams
    for SKHY, Teradyne or Newmont for Samsung, ASM International for ASML, or Apple for
    AMD — all five of which a web search did on 2026-08-06.
  * Wrong period. `fiscal_year` / `fiscal_quarter` / `report_date` are structured
    columns, so the period check is an equality test rather than a regex sniffing prose.
    A September-2025 deck cannot be served as Q2 FY2027 the way one was here.

It also carries speaker names per paragraph, which the scraped pages do not: the
observer currently receives an undifferentiated wall of text and has to infer who is
talking before it can attribute a quote.

Coverage, measured 2026-08-06: 236k transcripts, dataset refreshed daily (spec.json
said 2026-08-05). **US-listed only** — zero symbols carry an exchange suffix, so
005930.KS (Samsung) and 2408.TW (Nanya) are absent and must be hand-dropped into
`信息源/<SYM>/`. SK hynix IS present under its US ADR ticker SKHY, which is also our
canonical id, so the most important non-US witness is covered.

Licence: ODC-BY. A 2.2GB parquet read over HTTPS with a symbol predicate takes ~40s —
irrelevant at eight witnesses reporting four times a year.

Risk worth stating: this is one person's mirror. If it stalls we fall back to the
search path, with every guard still in place, and `信息源/` keeps what was already
pulled. That is a degradation, not a data loss.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("ats.data.defeatbeta")

REPO = "defeatbeta/yahoo-finance-data"
TRANSCRIPTS = (f"https://huggingface.co/datasets/{REPO}/resolve/main/data/"
               "stock_earning_call_transcripts.parquet")
NEWS = (f"https://huggingface.co/datasets/{REPO}/resolve/main/data/stock_news.parquet")
SPEC = f"https://huggingface.co/datasets/{REPO}/resolve/main/spec.json"


@dataclass(frozen=True)
class DefeatBetaConfig:
    transcripts_uri: str = TRANSCRIPTS
    news_uri: str = NEWS
    filings_uri: str = ""
    spec_uri: str = SPEC


def load_config() -> DefeatBetaConfig:
    """Configuration boundary for production HTTPS or a pinned local mirror."""
    return DefeatBetaConfig(
        transcripts_uri=os.environ.get("ATS_DEFEATBETA_TRANSCRIPTS_URI", TRANSCRIPTS),
        news_uri=os.environ.get("ATS_DEFEATBETA_NEWS_URI", NEWS),
        filings_uri=os.environ.get("ATS_DEFEATBETA_FILINGS_URI", FILINGS),
        spec_uri=os.environ.get("ATS_DEFEATBETA_SPEC_URI", SPEC),
    )


@dataclass(frozen=True)
class DatasetSnapshot:
    updated_at: str = ""
    checked_at: str = ""
    lag_hours: float | None = None
    spec_uri: str = ""


@dataclass(frozen=True)
class Paragraph:
    ordinal: int
    speaker: str
    content: str


@dataclass
class Transcript:
    symbol: str
    fiscal_year: int
    fiscal_quarter: int
    report_date: str
    text: str = ""
    paragraphs: tuple[Paragraph, ...] = ()
    snapshot: DatasetSnapshot | None = None

    @property
    def label(self) -> str:
        """Fiscal label in this repo's usual shape, e.g. `Q2 FY2026`."""
        return f"Q{self.fiscal_quarter} FY{self.fiscal_year}"

    def __post_init__(self) -> None:
        if not self.text and self.paragraphs:
            self.text = _render(self.paragraphs)


def _connect(uri: str = ""):
    import duckdb

    con = duckdb.connect()
    if uri.startswith(("http://", "https://")):
        try:
            con.execute("LOAD httpfs")
        except Exception:
            con.execute("INSTALL httpfs; LOAD httpfs;")
    return con


def _render(rows) -> str:
    """Stable Markdown retaining paragraph order and speaker attribution."""
    out = ["## Prepared Remarks"]
    in_questions = False
    for row in rows:
        if isinstance(row, Paragraph):
            speaker, content = row.speaker, row.content
        else:
            speaker, content = row
        body = (content or "").strip()
        if not body:
            continue
        who = (speaker or "").strip()
        low = f"{who} {body}".lower()
        if not in_questions and any(marker in low for marker in (
            "question-and-answer", "questions and answers", "q&a", "begin q and a",
            "begin the question",
        )):
            out.append("## Questions and Answers")
            in_questions = True
        out.append(f"**{who}:** {body}" if who else body)
    return "\n\n".join(out)


def _parse_timestamp(value: object) -> datetime | None:
    raw = str(value or "").strip().replace("Z", "+00:00")
    if not raw:
        return None
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return stamp.replace(tzinfo=stamp.tzinfo or timezone.utc).astimezone(timezone.utc)


def _find_snapshot_timestamp(value: object) -> datetime | None:
    keys = {"updated_at", "last_updated", "last_modified", "snapshot_at", "updatedat",
            "update_time"}
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in keys:
                parsed = _parse_timestamp(child)
                if parsed:
                    return parsed
        for child in value.values():
            parsed = _find_snapshot_timestamp(child)
            if parsed:
                return parsed
    elif isinstance(value, list):
        for child in value:
            parsed = _find_snapshot_timestamp(child)
            if parsed:
                return parsed
    return None


def dataset_snapshot(config: DefeatBetaConfig | None = None, *,
                     now: datetime | None = None,
                     dataset_file: str = "stock_earning_call_transcripts.parquet",
                     ) -> DatasetSnapshot:
    """Read ``spec.json`` and expose the snapshot time/lag used by quality gates."""
    config = config or load_config()
    checked = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        if config.spec_uri.startswith(("http://", "https://")):
            import httpx

            response = httpx.get(config.spec_uri, timeout=20, follow_redirects=True)
            response.raise_for_status()
            payload = response.json()
        else:
            payload = json.loads(Path(config.spec_uri).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - health remains explicit but non-fatal
        log.info("defeatbeta: spec snapshot unavailable (%s)", exc)
        return DatasetSnapshot(checked_at=checked.isoformat(timespec="seconds"),
                               spec_uri=config.spec_uri)
    file_stamp = ((payload.get("files") or {}).get(dataset_file)
                  if isinstance(payload, dict) else None)
    updated = _parse_timestamp(file_stamp) or _find_snapshot_timestamp(payload)
    lag = max(0.0, (checked - updated).total_seconds() / 3600) if updated else None
    return DatasetSnapshot(
        updated_at=updated.isoformat(timespec="seconds") if updated else "",
        checked_at=checked.isoformat(timespec="seconds"),
        lag_hours=lag,
        spec_uri=config.spec_uri,
    )


def _uri(value: str) -> str:
    return value.replace("'", "''")


def fetch(symbol: str, *, fiscal_year: int | None = None,
          fiscal_quarter: int | None = None,
          config: DefeatBetaConfig | None = None,
          now: datetime | None = None) -> Transcript | None:
    """Latest transcript for `symbol`, or the exact quarter when one is named.

    Returns None when the symbol is absent (every non-US listing) or the dataset is
    unreachable — callers fall through to the next source. Never raises: an outage in a
    third-party mirror must not take down a scheduled window.
    """
    from ..config import canonical_entity

    config = config or load_config()
    sym = canonical_entity(symbol).upper()
    where = ["symbol = ?"]
    args: list = [sym]
    if fiscal_year:
        where.append("fiscal_year = ?")
        args.append(int(fiscal_year))
    if fiscal_quarter:
        where.append("fiscal_quarter = ?")
        args.append(int(fiscal_quarter))
    sql = (f"SELECT symbol, fiscal_year, fiscal_quarter, report_date, transcripts "
           f"FROM read_parquet('{_uri(config.transcripts_uri)}') WHERE {' AND '.join(where)} "
           f"ORDER BY report_date DESC LIMIT 1")
    try:
        con = _connect(config.transcripts_uri)
        row = con.execute(sql, args).fetchone()
    except Exception as exc:  # noqa: BLE001 - third-party mirror; degrade, never break
        log.info("defeatbeta: query failed for %s (%s)", sym, exc)
        return None
    if not row:
        log.info("defeatbeta: no transcript for %s%s", sym,
                 f" {fiscal_year}Q{fiscal_quarter}" if fiscal_year else "")
        return None

    paragraphs = tuple(
        Paragraph(int(p.get("paragraph_order", p.get("ordinal", index))),
                  str(p.get("speaker") or "").strip(),
                  str(p.get("content") or "").strip())
        for index, p in enumerate(row[4] or [])
        if str(p.get("content") or "").strip()
    )
    text = _render(paragraphs)
    if not text.strip():
        return None
    return Transcript(symbol=row[0], fiscal_year=int(row[1]), fiscal_quarter=int(row[2]),
                      report_date=str(row[3]), text=text, paragraphs=paragraphs,
                      snapshot=dataset_snapshot(config, now=now))


def structured_transcript_issues(candidate) -> list:
    """Source-specific strong checks for paragraph/speaker transcript structure."""
    from .admission import ValidationIssue

    raw = candidate.metadata.get("paragraphs", ())
    paragraphs = tuple(
        item if isinstance(item, Paragraph) else Paragraph(
            int(item.get("ordinal", index)), str(item.get("speaker") or ""),
            str(item.get("content") or ""),
        )
        for index, item in enumerate(raw)
    )
    issues = []
    if not paragraphs:
        return [ValidationIssue("structure", "transcript_paragraphs_missing")]

    ordinals = [paragraph.ordinal for paragraph in paragraphs]
    start = ordinals[0]
    if ordinals != list(range(start, start + len(ordinals))):
        issues.append(ValidationIssue(
            "structure", "transcript_paragraph_order_gap", str(ordinals[:20])))

    speakers = [paragraph.speaker.strip() for paragraph in paragraphs]
    coverage = sum(bool(speaker) for speaker in speakers) / len(speakers)
    if coverage < 0.7:
        issues.append(ValidationIssue(
            "structure", "transcript_speaker_coverage_low", f"{coverage:.1%}"))
    generic = {"", "operator", "analyst", "unidentified analyst", "unknown"}
    management = {speaker.lower() for speaker in speakers if speaker.lower() not in generic}
    if not management:
        issues.append(ValidationIssue("identity", "transcript_management_speaker_missing"))

    opening = " ".join(p.content for p in paragraphs[:10]).lower()
    if not any(marker in opening for marker in (
        "welcome", "prepared remarks", "earnings call", "joining us", "today's program",
        "good afternoon", "good morning",
    )):
        issues.append(ValidationIssue("structure", "transcript_opening_missing"))
    all_text = " ".join(p.content for p in paragraphs).lower()
    explicit_qa = any(marker in all_text for marker in (
        "question-and-answer", "questions and answers", "q&a", "begin q and a",
        "begin the question",
    ))
    question_paragraphs = sum("?" in paragraph.content for paragraph in paragraphs)
    if not explicit_qa and question_paragraphs < 2:
        issues.append(ValidationIssue("structure", "transcript_qa_missing"))
    ending = " ".join(p.content for p in paragraphs[-10:]).lower()
    if not any(marker in ending for marker in (
        "concludes", "conclusion", "thank you", "have a good", "end of",
    )):
        issues.append(ValidationIssue("completeness", "transcript_ending_missing"))

    noise_markers = (
        "<script", "javascript:", "function(", "document.queryselector", "{display:",
        "cookie preferences", "navigation menu",
    )
    noise_chars = sum(
        len(p.content) for p in paragraphs
        if any(marker in p.content.lower() for marker in noise_markers)
    )
    total_chars = sum(len(p.content) for p in paragraphs) or 1
    if noise_chars / total_chars > 0.02:
        issues.append(ValidationIssue(
            "completeness", "transcript_frontend_noise", f"{noise_chars / total_chars:.1%}"))
    return issues


def structure_metadata(transcript: Transcript) -> dict:
    return {
        "symbol": transcript.symbol,
        "fiscal_year": transcript.fiscal_year,
        "fiscal_quarter": transcript.fiscal_quarter,
        "report_date": transcript.report_date,
        "snapshot": transcript.snapshot.__dict__ if transcript.snapshot else {},
        "paragraphs": [paragraph.__dict__ for paragraph in transcript.paragraphs],
    }


def save_structure(transcript: Transcript, document) -> Path | None:
    """Persist the source structure beside immutable prose, never only the rendering."""
    version_path = getattr(document, "version_path", None)
    if version_path is None:
        return None
    folder = Path(version_path).parent / ".structured"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{Path(version_path).stem}.json"
    if not path.exists():
        path.write_text(json.dumps(structure_metadata(transcript), ensure_ascii=False,
                                   indent=2, sort_keys=True), encoding="utf-8")
    return path


def available(symbols: list[str], *, config: DefeatBetaConfig | None = None) -> dict[str, str]:
    """{symbol: latest report_date} for the ones the dataset carries. For coverage
    reporting — tells you which witnesses will never arrive on their own."""
    from ..config import canonical_entity

    config = config or load_config()
    syms = [canonical_entity(s).upper() for s in symbols]
    if not syms:
        return {}
    placeholders = ",".join("?" * len(syms))
    try:
        con = _connect(config.transcripts_uri)
        rows = con.execute(
            f"SELECT symbol, max(report_date) "
            f"FROM read_parquet('{_uri(config.transcripts_uri)}') "
            f"WHERE symbol IN ({placeholders}) GROUP BY symbol", syms).fetchall()
    except Exception as exc:  # noqa: BLE001
        log.info("defeatbeta: availability query failed (%s)", exc)
        return {}
    return {r[0]: str(r[1]) for r in rows}


def coverage_smoke(symbols: list[str], *, config: DefeatBetaConfig | None = None,
                   now: datetime | None = None) -> list[dict]:
    """One read-only query for latest-period coverage and transcript purity."""
    from ..config import canonical_entity
    from .admission import CandidateDocument, validate_candidate

    config = config or load_config()
    requested = list(dict.fromkeys(canonical_entity(symbol).upper() for symbol in symbols))
    if not requested:
        return []
    placeholders = ",".join("?" * len(requested))
    sql = (
        "SELECT symbol,fiscal_year,fiscal_quarter,report_date,transcripts FROM ("
        "SELECT symbol,fiscal_year,fiscal_quarter,report_date,transcripts,"
        "row_number() OVER (PARTITION BY symbol ORDER BY report_date DESC) AS rn "
        f"FROM read_parquet('{_uri(config.transcripts_uri)}') "
        f"WHERE symbol IN ({placeholders})) WHERE rn=1"
    )
    try:
        rows = _connect(config.transcripts_uri).execute(sql, requested).fetchall()
    except Exception as exc:  # noqa: BLE001
        return [{"symbol": symbol, "status": "unreachable", "error": str(exc)}
                for symbol in requested]
    snapshot = dataset_snapshot(config, now=now)
    by_symbol = {row[0]: row for row in rows}
    output = []
    for symbol in requested:
        row = by_symbol.get(symbol)
        if row is None:
            output.append({"symbol": symbol, "status": "missing"})
            continue
        paragraphs = tuple(
            Paragraph(int(p.get("paragraph_order", p.get("ordinal", index))),
                      str(p.get("speaker") or "").strip(),
                      str(p.get("content") or "").strip())
            for index, p in enumerate(row[4] or [])
            if str(p.get("content") or "").strip()
        )
        transcript = Transcript(
            symbol=row[0], fiscal_year=int(row[1]), fiscal_quarter=int(row[2]),
            report_date=str(row[3]), paragraphs=paragraphs, snapshot=snapshot,
        )
        candidate = CandidateDocument(
            expected_entity=symbol, claimed_entity=row[0], target_period=transcript.label,
            claimed_period=transcript.label, expected_semantic="earnings_transcript",
            claimed_semantic="transcript", text=transcript.text, source="defeatbeta",
            completeness="full", min_chars=2000,
            metadata={"paragraphs": paragraphs},
        )
        validation = validate_candidate(
            candidate, extensions=(structured_transcript_issues,))
        output.append({
            "symbol": symbol, "status": "accepted" if validation.accepted else "quarantined",
            "report_date": transcript.report_date, "fiscal_label": transcript.label,
            "paragraphs": len(paragraphs), "chars": len(transcript.text),
            "reason_codes": list(validation.reason_codes),
            "snapshot_updated_at": snapshot.updated_at, "snapshot_lag_hours": snapshot.lag_hours,
        })
    return output


FILINGS = (f"https://huggingface.co/datasets/{REPO}/resolve/main/data/"
           "stock_sec_filing.parquet")
# US domestic issuers report on 8-K; foreign private issuers (ASML, TSM, SK hynix) use
# 6-K. Both carry the earnings release as an exhibit, so both are in scope — restricting
# to 8-K would silently exclude exactly the non-US witnesses we work hardest to cover.
EARNINGS_FORMS = ("8-K", "6-K")


@dataclass
class Filing:
    symbol: str
    cik: str
    accession: str
    form_type: str
    filing_date: str
    url: str
    report_date: str = ""
    items: str = ""
    primary_document: str = ""


class FilingResults(list[Filing]):
    """List-compatible metadata result that keeps mirror failures observable."""

    def __init__(self, values=(), *, status: str = "succeeded", error: str = "",
                 source_uri: str = ""):
        super().__init__(values)
        self.status = status
        self.error = error
        self.source_uri = source_uri


def filings(symbol: str, *, forms: tuple[str, ...] = EARNINGS_FORMS,
            near: str = "", window_days: int = 4,
            config: DefeatBetaConfig | None = None) -> FilingResults:
    """Filing METADATA for a symbol — the table carries no document text.

    That is the useful shape rather than a limitation: it hands us the CIK and the
    accession number, which is all EDGAR needs to serve the exhibit deterministically.
    No search, no guessing which company or which quarter.

    `near` (YYYY-MM-DD) keeps only filings within `window_days` of that date, which is
    how the earnings release gets told apart from the other 400 8-Ks a company files:
    the release lands on the print date. Without it, "latest 8-K" picks up director
    changes and shelf registrations.
    """
    from datetime import date, timedelta

    from ..config import canonical_entity

    config = config or load_config()
    sym = canonical_entity(symbol).upper()
    where = ["symbol = ?", f"form_type IN ({','.join('?' * len(forms))})"]
    args: list = [sym, *forms]
    if near:
        try:
            pivot = date.fromisoformat(near)
        except ValueError:
            pivot = None
        if pivot:
            where.append("filing_date BETWEEN ? AND ?")
            args += [str(pivot - timedelta(days=window_days)),
                     str(pivot + timedelta(days=window_days))]
    sql = (f"SELECT symbol, cik, accession_number, form_type, filing_date, filing_url, "
           f"report_date "
           f"FROM read_parquet('{_uri(config.filings_uri)}') WHERE {' AND '.join(where)} "
           f"ORDER BY filing_date DESC LIMIT 20")
    try:
        rows = _connect(config.filings_uri).execute(sql, args).fetchall()
    except Exception as exc:  # noqa: BLE001 - a mirror outage must not break the window
        log.info("defeatbeta: filing query failed for %s (%s)", sym, exc)
        return FilingResults(status="unreachable", error=str(exc),
                             source_uri=config.filings_uri)
    values = [Filing(symbol=r[0], cik=str(r[1]), accession=str(r[2]), form_type=str(r[3]),
                     filing_date=str(r[4]), url=str(r[5] or ""),
                     report_date=str(r[6] or "")) for r in rows]
    return FilingResults(values, status="succeeded" if values else "missing",
                         source_uri=config.filings_uri)
