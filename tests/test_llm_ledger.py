"""ops-observability-dashboard: every model call lands in the llm_calls ledger; the ledger never fails a call."""

from __future__ import annotations

import os
import sqlite3
from types import SimpleNamespace

from ats.llm import telemetry


def _rows():
    conn = sqlite3.connect(os.environ["ATS_DB_PATH"])
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM llm_calls ORDER BY id")]
    finally:
        conn.close()


def _call(**kw):
    base = dict(role="chief", response_id="gen-1", finish_reason="stop", input_tokens=1000,
                output_tokens=200, reasoning_tokens=50, max_tokens=4096,
                model="deepseek/deepseek-v4.1-flash-1017", provider="openrouter")
    base.update(kw)
    return telemetry.LLMCallRecord(**base)


def test_provider_cost_is_recorded_with_run_ids():
    from ats.agent.task_projection import projection_context

    with projection_context(workflow_run_id="run-1", agent_run_id="agent-1"):
        telemetry.record(_call(cost_usd=0.0123))
    row = _rows()[-1]
    assert (row["role"], row["model"], row["provider"]) == ("chief", "deepseek/deepseek-v4.1-flash-1017",
                                                          "openrouter")
    assert (row["input_tokens"], row["output_tokens"], row["reasoning_tokens"]) == (1000, 200, 50)
    assert (row["cost_usd"], row["cost_source"]) == (0.0123, "provider")
    assert (row["workflow_run_id"], row["agent_run_id"]) == ("run-1", "agent-1")


def test_estimate_from_price_then_unknown(monkeypatch):
    prices = {"deepseek-v4-flash": {"input": 0.5, "output": 2.0}}
    monkeypatch.setattr("ats.config.get_config",
                        lambda: SimpleNamespace(app=SimpleNamespace(llm=SimpleNamespace(prices=prices))))
    telemetry.record(_call(model="deepseek-v4-flash", provider="deepseek"))
    telemetry.record(_call(model="no-price-model"))
    estimated, unknown = _rows()[-2:]
    assert estimated["cost_source"] == "estimate"
    assert abs(estimated["cost_usd"] - (1000 * 0.5 + 200 * 2.0) / 1e6) < 1e-12
    assert unknown["cost_usd"] is None and unknown["cost_source"] == ""
    assert unknown["workflow_run_id"] == ""


def test_ledger_failure_never_raises(monkeypatch, caplog):
    monkeypatch.setattr(telemetry, "_ledger_path", lambda: "/nonexistent/dir/ats.sqlite")
    telemetry.record(_call())          # must not raise
    assert "ledger write skipped" in caplog.text


def test_callback_reads_provider_cost_from_token_usage():
    message = SimpleNamespace(
        response_metadata={"token_usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0002},
                           "model_name": "deepseek/deepseek-v4.1-flash-1017", "finish_reason": "stop",
                           "id": "gen-9"},
        usage_metadata={"input_tokens": 10, "output_tokens": 5}, id="x")
    response = SimpleNamespace(generations=[[SimpleNamespace(message=message, generation_info={})]],
                               llm_output={})
    rec = telemetry.UsageCallback("chief", 4096, model="m", provider="openrouter")._record(response)
    assert rec.cost_usd == 0.0002 and rec.model == "deepseek/deepseek-v4.1-flash-1017"
    assert rec.response_id == "gen-9" and rec.provider == "openrouter"
