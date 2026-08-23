from datetime import datetime, timezone

import duckdb

from ats.data.admission import CandidateDocument, validate_candidate
from ats.data.defeatbeta import (
    DefeatBetaConfig,
    Paragraph,
    Transcript,
    available,
    coverage_smoke,
    dataset_snapshot,
    fetch,
    save_structure,
    structured_transcript_issues,
)
from ats.data.document_assets import ingest
from ats.memory import get_store


def _fixture(tmp_path):
    parquet = tmp_path / "transcripts.parquet"
    spec = tmp_path / "spec.json"
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE calls (symbol VARCHAR, fiscal_year INTEGER, fiscal_quarter INTEGER, "
        "report_date DATE, transcripts STRUCT(speaker VARCHAR, content VARCHAR)[])"
    )
    con.execute(
        "INSERT INTO calls VALUES "
        "('TSM', 2026, 1, '2026-04-17', ["
        "{'speaker':'Operator','content':'Welcome to the first quarter call.'},"
        "{'speaker':'C. C. Wei','content':'Revenue increased.'}]),"
        "('TSM', 2026, 2, '2026-07-16', ["
        "{'speaker':'Operator','content':'Welcome to the second quarter call.'},"
        "{'speaker':'C. C. Wei','content':'AI demand remains robust.'},"
        "{'speaker':'Operator','content':'We will now begin Q&A.'}])"
    )
    con.execute(f"COPY calls TO '{parquet}' (FORMAT PARQUET)")
    spec.write_text('{"dataset": {"updated_at": "2026-08-22T00:00:00Z"}}',
                    encoding="utf-8")
    return DefeatBetaConfig(
        transcripts_uri=str(parquet), filings_uri=str(tmp_path / "filings.parquet"),
        spec_uri=str(spec),
    )


def test_local_parquet_queries_exact_symbol_and_fiscal_period(tmp_path):
    config = _fixture(tmp_path)

    transcript = fetch(
        "TSMC", fiscal_year=2026, fiscal_quarter=2, config=config,
        now=datetime(2026, 8, 23, tzinfo=timezone.utc),
    )

    assert transcript is not None
    assert transcript.symbol == "TSM"
    assert transcript.label == "Q2 FY2026"
    assert transcript.report_date == "2026-07-16"
    assert [p.ordinal for p in transcript.paragraphs] == [0, 1, 2]
    assert "**C. C. Wei:** AI demand remains robust." in transcript.text
    assert "## Questions and Answers" in transcript.text
    assert transcript.snapshot.updated_at == "2026-08-22T00:00:00+00:00"
    assert transcript.snapshot.lag_hours == 24


def test_latest_and_coverage_queries_use_the_configured_local_mirror(tmp_path):
    config = _fixture(tmp_path)

    latest = fetch("TSM", config=config)

    assert latest is not None and latest.fiscal_quarter == 2
    assert available(["TSM", "005930.KS"], config=config) == {"TSM": "2026-07-16"}
    smoke = coverage_smoke(["TSMC", "005930.KS"], config=config)
    assert [(row["symbol"], row["status"]) for row in smoke] == [
        ("TSM", "quarantined"), ("005930.KS", "missing")
    ]
    assert smoke[0]["reason_codes"] == ["completeness_too_short",
                                        "transcript_ending_missing"]


def test_missing_spec_is_visible_without_breaking_transcript_fetch(tmp_path):
    config = _fixture(tmp_path)
    config = DefeatBetaConfig(
        transcripts_uri=config.transcripts_uri,
        filings_uri=config.filings_uri,
        spec_uri=str(tmp_path / "missing-spec.json"),
    )

    snapshot = dataset_snapshot(config, now=datetime(2026, 8, 23, tzinfo=timezone.utc))
    transcript = fetch("TSM", fiscal_year=2026, fiscal_quarter=2, config=config)

    assert snapshot.updated_at == "" and snapshot.lag_hours is None
    assert transcript is not None and transcript.snapshot.updated_at == ""


def _structured_candidate(paragraphs):
    text = "\n\n".join(p.content for p in paragraphs)
    return CandidateDocument(
        expected_entity="TSM", claimed_entity="TSM", target_period="2026Q2",
        claimed_period="Q2 FY2026", expected_semantic="earnings_transcript",
        claimed_semantic="transcript", text=text, source="defeatbeta",
        external_id="defeatbeta:TSM:2026-07-16", completeness="full", min_chars=1,
        metadata={"paragraphs": paragraphs},
    )


def test_structured_transcript_checks_opening_qa_end_speakers_and_noise():
    good = tuple([
        Paragraph(0, "Operator", "Welcome to TSMC's earnings call."),
        Paragraph(1, "C. C. Wei", "These are our prepared remarks."),
        Paragraph(2, "Operator", "We will now begin the question-and-answer session."),
        Paragraph(3, "Analyst", "Could you discuss AI demand?"),
        Paragraph(4, "C. C. Wei", "AI demand remains robust."),
        Paragraph(5, "Operator", "Thank you. This concludes the call."),
    ])
    broken = tuple([
        Paragraph(0, "", "Navigation menu cookie preferences advertisement"),
        Paragraph(2, "Operator", "document.querySelector function() {display:none}"),
        Paragraph(3, "", "unrelated shell"),
    ])

    assert validate_candidate(
        _structured_candidate(good), extensions=(structured_transcript_issues,)
    ).accepted
    result = validate_candidate(
        _structured_candidate(broken), extensions=(structured_transcript_issues,)
    )
    assert {"transcript_paragraph_order_gap", "transcript_speaker_coverage_low",
            "transcript_management_speaker_missing", "transcript_opening_missing",
            "transcript_qa_missing", "transcript_ending_missing",
            "transcript_frontend_noise"} <= set(result.reason_codes)


def test_realistic_opening_and_analyst_questions_count_as_structure():
    paragraphs = (
        Paragraph(0, "Operator", "Today's program is being recorded."),
        Paragraph(1, "Investor Relations", "Good afternoon to everyone joining us today."),
        Paragraph(2, "Chief Executive Officer", "Revenue and margins improved."),
        Paragraph(3, "Named Analyst", "Could you discuss capacity?"),
        Paragraph(4, "Chief Executive Officer", "Capacity will expand."),
        Paragraph(5, "Another Analyst", "What is the expected timeline?"),
        Paragraph(6, "Operator", "Thank you. This concludes today's call."),
    )
    result = validate_candidate(
        _structured_candidate(paragraphs), extensions=(structured_transcript_issues,)
    )

    assert result.accepted


def test_raw_speaker_structure_is_saved_beside_the_immutable_markdown(tmp_path):
    config = _fixture(tmp_path)
    transcript = fetch("TSM", fiscal_year=2026, fiscal_quarter=2, config=config)
    document = ingest(
        entity="TSM", key=transcript.label, doc_type="earnings_transcript",
        text=transcript.text, source="defeatbeta", min_chars=1, store=get_store(),
    )

    path = save_structure(transcript, document)

    assert path and path.is_file()
    payload = __import__("json").loads(path.read_text(encoding="utf-8"))
    assert payload["paragraphs"][1] == {
        "ordinal": 1, "speaker": "C. C. Wei", "content": "AI demand remains robust."
    }
