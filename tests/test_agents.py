"""Phase 4: analyst output robustness (no network)."""

from datetime import datetime, timezone

from ats.agents import analysts
from ats.agents.outputs import TechnicalView, _as_list
from ats.schemas.market import MarketSnapshot, Ticker


def test_view_coerces_scalar_to_list():
    # LLMs sometimes return a string where the schema wants a list.
    v = TechnicalView(signal="neutral", conviction=0.5, thesis="x",
                      key_risks="one big risk", sources="one source")
    assert v.key_risks == ["one big risk"]
    assert v.sources == ["one source"]


def test_as_list_handles_none_and_empty():
    assert _as_list(None) == []
    assert _as_list("") == []
    assert _as_list(["a"]) == ["a"]


def test_clean_clamps_conviction():
    v = TechnicalView(signal="bullish", conviction=1.7, thesis="x")
    assert analysts._clean(v)["conviction"] == 1.0
    v2 = TechnicalView(signal="bearish", conviction=-0.3, thesis="x")
    assert analysts._clean(v2)["conviction"] == 0.0


def test_analysts_stub_without_llm():
    now = datetime.now(timezone.utc)
    snap = MarketSnapshot(ticker=Ticker(symbol="NVDA"), as_of=now)
    assert "stub" in analysts.technical("NVDA", snap, now, use_llm=False).thesis
    assert analysts.fundamental("NVDA", snap, None, now, use_llm=False).signal == "bullish"
    assert "stub" in analysts.macro(None, now, use_llm=False).thesis
