"""Industry-notes reader + prep injection (hermetic, no network/LLM)."""

from ats.data import industry


def _cfg(root, files=None, cap=12000):
    return {"industry_notes": {"root": str(root), "files": files or [],
                               "max_chars_per_file": cap}}


def test_fetch_notes_reads_whitelist_and_truncates(monkeypatch, tmp_path):
    (tmp_path / "a.md").write_text("A" * 20000, encoding="utf-8")
    (tmp_path / "b.md").write_text("chain overview", encoding="utf-8")
    (tmp_path / "skip.md").write_text("not whitelisted", encoding="utf-8")
    monkeypatch.setattr("ats.config.load_pead_global",
                        lambda: _cfg(tmp_path, files=["a.md", "b.md"], cap=5000))

    notes = industry.fetch_notes()
    assert [n for n, _ in notes] == ["a.md", "b.md"]      # only whitelist, in order
    assert len(notes[0][1]) == 5000                        # truncated to cap
    assert "skip.md" not in dict(notes)


def test_fetch_notes_all_md_when_no_whitelist(monkeypatch, tmp_path):
    (tmp_path / "x.md").write_text("xx", encoding="utf-8")
    (tmp_path / "y.md").write_text("yy", encoding="utf-8")
    monkeypatch.setattr("ats.config.load_pead_global", lambda: _cfg(tmp_path))
    assert {n for n, _ in industry.fetch_notes()} == {"x.md", "y.md"}


def test_fetch_notes_degrades_when_root_missing(monkeypatch):
    monkeypatch.setattr("ats.config.load_pead_global", lambda: _cfg("/no/such/dir"))
    assert industry.fetch_notes() == []
    monkeypatch.setattr("ats.config.load_pead_global", lambda: _cfg(""))
    assert industry.fetch_notes() == []


def test_narrative_injects_industry_context(monkeypatch):
    from ats.agents.pead import prep
    from ats.agents.pead.outputs import NarrativeView
    from ats.schemas.pead import PeadConfig

    captured = {}

    def fake_llm(role, schema, ctx, **k):
        captured["ctx"] = ctx
        return NarrativeView(narrative="t", focus_ranking=[], valuation="")

    monkeypatch.setattr(prep, "run_structured", fake_llm)
    prep.narrative(PeadConfig(symbol="COHR"), "fundamentals", {"eps": 1.6},
                   industry_context="### chain.md\nCOHR sits mid-chain in AI optics")
    assert "COHR sits mid-chain in AI optics" in captured["ctx"]
    assert "STABLE reference" in captured["ctx"]           # framing present
