import pytest

from ats.data import admission, documents


def _validate_release(symbol, company, body, period="Q2 FY2026"):
    detected = __import__("ats.data.fiscal", fromlist=["detect_period"]).detect_period(body)
    claimed_period = f"Q{detected[1]} FY{detected[0]}" if detected else ""
    candidate = admission.CandidateDocument(
        expected_entity=symbol,
        claimed_entity=(symbol if admission.mentions_entity(body, symbol, company) else ""),
        target_period=period,
        claimed_period=claimed_period,
        expected_semantic="company_release",
        claimed_semantic="release",
        text=body,
        source="sec",
        source_url="https://www.sec.gov/Archives/edgar/data/fixture/ex99.htm",
        completeness="full",
        min_chars=1,
    )
    return admission.validate_candidate(
        candidate, extensions=(documents.official_document_issues,))


def test_goog_xbrl_cover_is_not_an_earnings_release():
    raw = ("ALPHABET INC. Q2 2026 FORM 8-K. IDEA: XBRL DOCUMENT. "
           "X - Definition Namespace Prefix: dei_ Data Type: xbrli:stringItemType " * 50)
    body = documents.strip_xbrl_boilerplate(raw)

    result = _validate_release("GOOG", "Alphabet", body)

    assert "release_semantics_missing" in result.reason_codes


@pytest.mark.parametrize(
    ("symbol", "company", "body"),
    [
        ("INTC", "Intel", "Intel Q2 2026 announces pricing of a public stock offering. " * 40),
        ("MRVL", "Marvell Technology", "Marvell Q2 2026 issues warrants and notes. " * 40),
    ],
)
def test_non_earnings_corporate_actions_are_not_releases(symbol, company, body):
    result = _validate_release(symbol, company, body)

    assert "release_semantics_missing" in result.reason_codes


@pytest.mark.parametrize(
    ("symbol", "company", "wrong_body"),
    [
        ("LITE", "Lumentum Holdings", "Teck Resources Q2 2026 financial results. Revenue and net income. "),
        ("CRDO", "Credo Technology", "Criteo Q2 2026 financial results. Revenue and net income. "),
    ],
)
def test_search_results_for_similarly_named_companies_fail_identity(symbol, company, wrong_body):
    result = _validate_release(symbol, company, wrong_body * 40)

    assert "identity_unresolved" in result.reason_codes
