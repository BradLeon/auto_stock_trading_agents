from ats.data import defeatbeta, sec


def _filing(symbol="AMD", cik="2488", accession="000000248826000001",
            filed="2026-08-04"):
    return defeatbeta.Filing(
        symbol=symbol, cik=cik, accession=accession, form_type="8-K",
        filing_date=filed, url="https://www.sec.gov/filing",
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
    monkeypatch.setattr(sec, "exhibit_text", lambda _cik, accession: bodies.get(
        accession, ("", "")))

    record = sec.earnings_release_record(
        "AMD", near="2026-08-04", period="Q2 FY2026")

    assert record is not None
    assert record["accession"] == "correct"
    assert record["source_url"] == "https://sec/correct"


def test_filing_without_ex99_is_a_visible_gap_not_largest_html_fallback(monkeypatch):
    monkeypatch.setattr(defeatbeta, "filings", lambda *a, **k: [_filing()])
    monkeypatch.setattr(sec, "exhibit_text", lambda *_: ("", ""))

    record = sec.earnings_release_record(
        "AMD", near="2026-08-04", period="Q2 FY2026")

    assert record is None
