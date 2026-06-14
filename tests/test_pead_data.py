"""PEAD data sources — pure logic + graceful degradation (no network)."""

from datetime import date

from ats.data import consensus, earnings_calendar, options, runup, transcript


def test_transcript_reads_manual_file(tmp_path):
    p = tmp_path / "t.txt"
    p.write_text("hello call", encoding="utf-8")
    text, src = transcript.fetch("COHR", "Q3 FY2026", source=str(p))
    assert text == "hello call" and src.startswith("file:")


def test_transcript_none_when_missing(monkeypatch, tmp_path):
    # No manual file, no FMP, no search, no news -> empty + "none".
    monkeypatch.setattr(transcript, "manual_path", lambda *a: tmp_path / "missing.txt")
    monkeypatch.setattr(transcript, "_fmp", lambda s: ("", ""))
    monkeypatch.setattr(transcript, "_from_search", lambda s: ("", ""))
    monkeypatch.setattr(transcript, "_from_news", lambda s, **k: ("", ""))
    text, src = transcript.fetch("COHR", "Q3 FY2026")
    assert text == "" and src == "none"


def test_transcript_search_used_before_news(monkeypatch, tmp_path):
    monkeypatch.setattr(transcript, "_fmp", lambda s: ("", ""))
    monkeypatch.setattr(transcript, "_from_search", lambda s: ("SEARCH TEXT", "tavily:fool.com/x"))
    monkeypatch.setattr(transcript, "_from_news", lambda s, **k: ("NEWS TEXT", "news:..."))
    text, src = transcript.fetch("COHR", "Q3 FY2026")
    assert text == "SEARCH TEXT" and src.startswith("tavily:")


def test_transcript_from_news_finds_and_scrapes(monkeypatch):
    from datetime import datetime, timezone
    from ats.data import news
    from ats.schemas.news import NewsItem

    now = datetime.now(timezone.utc)
    items = [
        NewsItem(id="1", source="finnhub", headline="COHR stock pops on AI optics",
                 url="https://example.com/a", published_at=now),
        NewsItem(id="2", source="finnhub", headline="Coherent (COHR) Q3 2026 Earnings Call Transcript",
                 url="https://www.fool.com/earnings/call-transcripts/2026/05/06/coherent-cohr-q3/",
                 published_at=now),
    ]
    monkeypatch.setattr(news, "fetch_news", lambda sym, since, until=None: items)
    monkeypatch.setattr(transcript, "_fetch_url", lambda url: "TRANSCRIPT BODY " * 300)  # long

    text, src = transcript._from_news("COHR")
    assert text.startswith("TRANSCRIPT BODY")
    assert "fool.com" in src and src.startswith("news:")


def test_transcript_from_news_skips_short_pages(monkeypatch):
    from datetime import datetime, timezone
    from ats.data import news
    from ats.schemas.news import NewsItem

    items = [NewsItem(id="2", source="finnhub", headline="COHR earnings call transcript",
                      url="https://www.fool.com/x", published_at=datetime.now(timezone.utc))]
    monkeypatch.setattr(news, "fetch_news", lambda sym, since, until=None: items)
    monkeypatch.setattr(transcript, "_fetch_url", lambda url: "paywall stub")  # too short
    assert transcript._from_news("COHR") == ("", "")


def test_transcript_fmp_used_when_no_override(monkeypatch, tmp_path):
    monkeypatch.setattr(transcript, "manual_path", lambda *a: tmp_path / "missing.txt")
    monkeypatch.setattr(transcript, "_fmp", lambda s: ("fmp body", "fmp:Q1-2026"))
    text, src = transcript.fetch("COHR", "Q3 FY2026")
    assert text == "fmp body" and src == "fmp:Q1-2026"


def test_transcript_explicit_source_beats_fmp(monkeypatch, tmp_path):
    p = tmp_path / "t.txt"
    p.write_text("explicit", encoding="utf-8")
    monkeypatch.setattr(transcript, "_fmp", lambda s: ("fmp body", "fmp:Q1-2026"))
    text, src = transcript.fetch("COHR", "Q3 FY2026", source=str(p))
    assert text == "explicit"          # explicit override wins


def test_pick_expiration_after_earnings():
    exps = ("2026-05-01", "2026-05-08", "2026-05-15")
    assert options._pick_expiration(exps, date(2026, 5, 6)) == "2026-05-08"
    assert options._pick_expiration(exps, None) == "2026-05-01"


def test_options_degrades_to_empty(monkeypatch):
    monkeypatch.setattr(options, "_thetadata", lambda *a: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(options, "_yfinance", lambda *a: (_ for _ in ()).throw(RuntimeError()))
    out = options.fetch("COHR")
    assert out["expected_move_pct"] is None and out["source"] is None


def test_consensus_num_filters_nan():
    assert consensus._num(float("nan")) is None
    assert consensus._num("1.38") == 1.38
    assert consensus._num(None) is None


def test_earnings_calendar_degrades(monkeypatch):
    monkeypatch.setattr(earnings_calendar, "_yf_next",
                        lambda s: (_ for _ in ()).throw(RuntimeError()))
    assert earnings_calendar.next_earnings_date("COHR") is None


def test_runup_ret_20d():
    assert runup._ret_20d([100.0] * 10) is None          # too short
    closes = [100.0] * 20 + [110.0]                       # +10% over 20d
    assert round(runup._ret_20d(closes), 2) == 10.0
