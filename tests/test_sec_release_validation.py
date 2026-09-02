import pytest

from ats.data import defeatbeta, fiscal, sec


def _filing(symbol="AMD", cik="2488", accession="000000248826000001",
            filed="2026-08-04"):
    return defeatbeta.Filing(
        symbol=symbol, cik=cik, accession=accession, form_type="8-K",
        filing_date=filed, url="https://www.sec.gov/filing", items="2.02,9.01",
        primary_document="filing.htm",
    )


def _release(company="AMD", period="Q2 2026"):
    return (f"{company} reports {period} financial results. Revenue increased and "
            "net income improved. Earnings per share and guidance were updated. " * 50)


def test_unanchored_latest_filing_lookup_is_refused(monkeypatch):
    monkeypatch.setattr(defeatbeta, "filings", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("must not query latest without an earnings event")))

    assert sec.earnings_release_record("AMD") is None


def test_release_requires_matching_symbol_valid_cik_identity_period_and_semantics(monkeypatch):
    filings = [
        _filing(symbol="INTC", accession="wrong-symbol"),
        _filing(cik="", accession="missing-cik"),
        _filing(accession="wrong-company"),
        _filing(accession="wrong-period"),
        _filing(accession="correct"),
    ]
    bodies = {
        "wrong-company": (_release("Intel", "Q2 2026"), "https://sec/wrong-company"),
        "wrong-period": (_release("AMD", "Q1 2026"), "https://sec/wrong-period"),
        "correct": (_release(), "https://sec/correct"),
    }
    monkeypatch.setattr(defeatbeta, "filings", lambda *a, **k: filings)
    monkeypatch.setattr(sec, "_ticker_cik", lambda symbol: ("", "missing", ()))
    monkeypatch.setattr(sec, "exhibit_text", lambda _cik, accession: bodies.get(
        accession, ("", "")))

    record = sec.earnings_release_record(
        "AMD", near="2026-08-04", period="Q2 FY2026")

    assert record is not None
    assert record["accession"] == "correct"
    assert record["source_url"] == "https://sec/correct"


def test_filing_without_ex99_is_a_visible_gap_not_largest_html_fallback(monkeypatch):
    monkeypatch.setattr(defeatbeta, "filings", lambda *a, **k: [_filing()])
    monkeypatch.setattr(sec, "_ticker_cik", lambda symbol: ("", "missing", ()))
    monkeypatch.setattr(sec, "exhibit_text", lambda *_: ("", ""))

    record = sec.earnings_release_record(
        "AMD", near="2026-08-04", period="Q2 FY2026")

    assert record is None


@pytest.mark.parametrize(
    ("label", "body"),
    [
        (
            "Q2 FY2026",
            "AMAZON.COM ANNOUNCES SECOND QUARTER RESULTS. Amazon.com today announced "
            "financial results for its second quarter ended June 30, 2026. Net sales "
            "increased compared with the second quarter 2025. Revenue and net income "
            "increased and management provided guidance. " * 20,
        ),
        (
            "Q4 FY2026",
            "Microsoft Cloud and AI Strength Fuels Fourth Quarter Results. July 29, "
            "2026. Microsoft announced results for the quarter ended June 30, 2026. "
            "Revenue, operating income, net income and diluted earnings per share "
            "increased compared with the corresponding period of last fiscal year. " * 20,
        ),
        (
            "Q4 FY2026",
            "KLA CORPORATION REPORTS FISCAL 2026 FOURTH QUARTER AND FULL YEAR RESULTS. "
            "KLA announced financial and operating results for its fourth quarter and "
            "fiscal year ended June 30, 2026. Revenue, net income and guidance were "
            "reported. " * 20,
        ),
    ],
)
def test_release_period_binding_prefers_primary_period_over_comparisons(label, body):
    ok, reason = fiscal.verify_release_period(label, body)

    assert ok, reason


def test_release_period_binding_rejects_comparison_or_guidance_only_target():
    body = (
        "Company reports first quarter 2026 financial results. Revenue and net income "
        "increased compared with first quarter 2025. Guidance for second quarter 2026 "
        "was raised. Earnings per share improved. " * 20
    )

    ok, reason = fiscal.verify_release_period("Q2 FY2026", body)

    assert not ok
    assert "主报告期" in reason


def test_release_period_binding_accepts_results_with_same_sentence_outlook():
    body = (
        "ASML reports Q2 2026 financial results and provides Q3 outlook and guidance. "
        "Revenue and net income increased. " * 30
    )

    ok, reason = fiscal.verify_release_period("Q2 FY2026", body)

    assert ok, reason


def test_release_period_binding_prefers_6k_current_period_table_column():
    body = (
        "Preliminary Results of Operations. Basis: Consolidated. Current Period "
        "(Second quarter of 2026) Previous Period (First quarter of 2026) Changes. "
        "Year to Year Comparison (Second quarter of 2025). Revenue and operating profit."
    )

    ok, reason = fiscal.verify_release_period("Q2 FY2026", body)

    assert ok, reason


def test_release_period_binding_accepts_end_date_with_resolved_event_anchor():
    body = "Lam Research reports financial results for the quarter ended June 28, 2026."

    ok, reason = fiscal.verify_release_period(
        "Q4 FY2026", body, event_date="2026-07-29")

    assert ok
    assert "期末日与事件绑定" in reason


def test_release_period_binding_rejects_stale_end_date_even_in_same_fiscal_year():
    body = "Lam Research reports financial results for the quarter ended March 29, 2026."

    ok, reason = fiscal.verify_release_period(
        "Q4 FY2026", body, event_date="2026-07-29")

    assert not ok
    assert "period unresolved" in reason
