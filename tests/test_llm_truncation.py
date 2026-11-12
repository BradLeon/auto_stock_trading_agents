"""Truncated structured output is detected and labelled, not passed off as a complete answer (hermetic, no LLM).

2026-11-12: every incomplete macro review hit max_tokens (OpenRouter native_finish_reason=length) while the response
said finish_reason=tool_calls and carried closed JSON holding only the finished parameters; pydantic defaults filled
the rest. These tests replay that wire shape offline.
"""

import json
from datetime import UTC, datetime

import httpx
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_openai import ChatOpenAI

from ats.agents.macro import assemble, report
from ats.agents.macro import review as macro_review
from ats.agents.macro.outputs import MacroReviewLLMView, SectorTiltView
from ats.llm import telemetry
from ats.llm.telemetry import LLMCallRecord, UsageCallback, capture_calls
from ats.memory import get_store
from ats.schemas.macro_strategy import MacroConfig

NOW = datetime.now(UTC)
CFG = MacroConfig(
    name="macro", label="宏观", output_dir="",
    themes=[{"key": "fed_policy", "label": "货币政策", "kind": "quant", "quant": ["fed_funds"], "queries": []}],
    search={"max_results_per_query": 2, "recency_days": 14, "max_chars_per_result": 500},
    review={"max_context_chars": 48000})


def _model(arguments: str, *, finish: str, completion_tokens: int, reasoning_tokens: int = 0, max_tokens=24000):
    body = {
        "id": "gen-offline-1", "object": "chat.completion", "created": 0, "model": "offline",
        "choices": [{"index": 0, "finish_reason": finish, "message": {
            "role": "assistant", "content": None, "tool_calls": [{
                "id": "call-1", "type": "function",
                "function": {"name": "MacroReviewLLMView", "arguments": arguments}}]}}],
        "usage": {"prompt_tokens": 23447, "completion_tokens": completion_tokens,
                  "total_tokens": 23447 + completion_tokens,
                  "completion_tokens_details": {"reasoning_tokens": reasoning_tokens}}}
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)))
    chat = ChatOpenAI(model="offline", api_key="offline", base_url="http://offline/v1", http_client=client,
                      max_retries=0, max_tokens=max_tokens,
                      callbacks=[UsageCallback("macro_strategist", max_tokens)])
    return chat.with_structured_output(MacroReviewLLMView, method="function_calling")


HEAD = {"regime": "neutral", "summary": "总评", "conclusion_delta": "变化", "rate_path": "持有"}


def test_relay_rewritten_finish_reason_is_still_caught_by_the_token_ceiling():
    # The 11-12 shape: the relay says tool_calls, the JSON is closed, the tail is missing, output == max_tokens.
    with capture_calls() as calls:
        view = _model(json.dumps(HEAD, ensure_ascii=False), finish="tool_calls", completion_tokens=24000).invoke("x")
    assert view.rate_path == "持有" and view.sector_tilts == [] and view.falsifier == ""   # parses, tail defaulted
    assert len(calls) == 1
    call = calls[0]
    assert call.truncated and call.finish_reason == "tool_calls"
    assert (call.response_id, call.output_tokens, call.input_tokens) == ("gen-offline-1", 24000, 23447)


def test_a_complete_answer_below_the_ceiling_is_not_flagged():
    with capture_calls() as calls:
        _model(json.dumps(HEAD, ensure_ascii=False), finish="tool_calls", completion_tokens=10681,
               reasoning_tokens=3983).invoke("x")
    assert not calls[0].truncated and calls[0].reasoning_tokens == 3983


def test_unparseable_truncated_arguments_still_return_none_and_are_recorded():
    # Unchanged behaviour: half a JSON object parses to None (run_structured then retries); the record says why.
    with capture_calls() as calls:
        view = _model('{"regime": "neutral", "sector_tilts": [{"sector": "半', finish="length",
                      completion_tokens=24000).invoke("x")
    assert view is None
    assert calls[0].truncated and calls[0].finish_reason == "length"


def test_records_outside_a_capture_block_are_only_logged(caplog):
    with caplog.at_level("WARNING", logger="ats.llm.telemetry"):
        telemetry.record(LLMCallRecord("r", "gen-x", "tool_calls", 1, 100, 0, 100))
    assert "max_tokens ceiling" in caplog.text and "gen-x" in caplog.text


def test_gateway_attaches_the_usage_callback_to_every_model(monkeypatch):
    import langchain_openai

    from ats.config import get_config
    from ats.llm import gateway

    seen = []

    class Recorder:
        def __init__(self, **kwargs):
            seen.append(kwargs)

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", Recorder)
    gateway.get_model.cache_clear()
    try:
        gateway.get_model("macro_strategist")
    finally:
        gateway.get_model.cache_clear()
    (callback,) = seen[-1]["callbacks"]
    rc = get_config().app.llm.for_role("macro_strategist")
    assert isinstance(callback, UsageCallback)
    assert (callback.role, callback.max_tokens) == ("macro_strategist", rc.max_tokens)


def test_short_fields_precede_the_two_long_ones_in_the_tool_schema():
    order = list(convert_to_openai_tool(MacroReviewLLMView)["function"]["parameters"]["properties"])
    assert order[-2:] == ["themes", "factset_earnings_assessment"]
    assert order.index("falsifier") < order.index("themes")
    assert order.index("sector_tilts") < order.index("top_risks") < order.index("scenarios")


def _run_with(monkeypatch, view, *, truncated: bool):
    def fake_run_structured(*_a, **_k):
        telemetry.record(LLMCallRecord("macro_strategist", "gen-x", "tool_calls", 23447,
                                       24000 if truncated else 9000, 0, 24000))
        return view

    monkeypatch.setattr("ats.config.load_macro_config", lambda name="macro": CFG)
    monkeypatch.setattr(macro_review, "run_structured", fake_run_structured)
    monkeypatch.setattr(assemble, "build", lambda cfg, live_data=True: assemble.MacroContext(cfg=cfg))
    return macro_review.run("macro", use_llm=True, live_data=False)


def test_truncated_macro_review_is_kept_labelled_and_reported(monkeypatch):
    review = _run_with(monkeypatch, MacroReviewLLMView(**HEAD), truncated=True)
    assert review.llm_truncated
    # No FactSet material in this context, so the assessment is not counted as missing.
    assert review.llm_missing_fields == ["sector_tilts", "asset_implications", "top_risks", "scenarios",
                                         "horizon", "falsifier", "themes"]
    saved = get_store().latest_macro_review("macro")
    assert saved.llm_truncated and saved.llm_missing_fields == review.llm_missing_fields
    assert "本期不完整" in report.render(review, CFG) and "falsifier" in report.render(review, CFG)


def test_complete_macro_review_has_no_notice(monkeypatch):
    view = MacroReviewLLMView(
        **HEAD, sector_tilts=[SectorTiltView(sector="半导体", stance="超配")], asset_implications="a",
        top_risks=["r"], scenarios=[{"name": "基准"}], horizon="4-8 周", falsifier="f",
        themes=[{"key": "fed_policy", "direction": "持稳"}])
    review = _run_with(monkeypatch, view, truncated=False)
    assert not review.llm_truncated and review.llm_missing_fields == []
    assert "⚠️" not in report.render(review, CFG)


def test_omitted_field_without_truncation_is_noted_softly(monkeypatch):
    view = MacroReviewLLMView(
        **HEAD, sector_tilts=[SectorTiltView(sector="半导体", stance="超配")], asset_implications="a",
        scenarios=[{"name": "基准"}], horizon="4-8 周", falsifier="f",
        themes=[{"key": "fed_policy", "direction": "持稳"}])          # top_risks omitted, as on 11-07
    review = _run_with(monkeypatch, view, truncated=False)
    assert not review.llm_truncated and review.llm_missing_fields == ["top_risks"]
    text = report.render(review, CFG)
    assert "模型本期未填写：top_risks" in text and "本期不完整" not in text
