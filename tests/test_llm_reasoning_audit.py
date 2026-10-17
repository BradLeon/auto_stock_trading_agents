"""max_tokens / reasoning audit (2026-10-17): hidden reasoning must not be able to eat a role's whole budget.

Measured on layer_rotation: the answer is ~1.7K tokens, but deepseek-v4-pro behind OpenRouter spent 8.5-9.3K
tokens reasoning, hit max_tokens=8192 and returned no tool call; run_structured retried 4 times with the same result.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from ats.config import LLMConfig, LLMRoleConfig, get_config
from ats.llm import gateway

# Roles whose role config is part of a cached execution identity (agents/base.execution_versions): changing
# them retires cached Information/Fundamental work, so they are left alone on purpose and listed here.
IDENTITY_BOUND = {"research_extract", "context_monitor", "fundamental_analyst"}
ANSWER_HEADROOM = 2048
GENEROUS_CEILING = 12288


def _openrouter_deepseek_roles():
    llm = get_config().app.llm
    for role in llm.routing:
        rc = llm.for_role(role)
        if rc.provider == "openai" and (rc.model or "").startswith("deepseek/"):
            yield role, rc


def test_reasoning_field_is_excluded_from_the_dump_when_unset():
    # Unchanged roles keep their execution identity byte for byte.
    cfg = LLMConfig(routing={"x": LLMRoleConfig(model="m", max_tokens=100)})
    assert "reasoning" not in cfg.for_role("x").model_dump(mode="json")
    assert LLMConfig(routing={"x": LLMRoleConfig(reasoning="off")}).for_role("x").model_dump(mode="json")["reasoning"] == "off"


@pytest.mark.parametrize("role", sorted(IDENTITY_BOUND))
def test_identity_bound_roles_are_not_touched(role):
    assert get_config().app.llm.for_role(role).reasoning is None


@pytest.mark.parametrize("bad", [True, 0, 100, 255])
def test_reasoning_budget_must_be_off_or_a_real_budget(bad):
    with pytest.raises(ValueError):
        LLMRoleConfig(reasoning=bad)


def test_reasoning_request_body():
    assert gateway.reasoning_body(None) is None
    assert gateway.reasoning_body("off") == {"reasoning": {"enabled": False}}
    assert gateway.reasoning_body(4000) == {"reasoning": {"max_tokens": 4000}}


def test_every_openrouter_deepseek_role_has_a_reasoning_policy_or_a_generous_ceiling():
    """A role with neither a reasoning setting nor a large ceiling can silently return nothing."""
    unsafe = []
    for role, rc in _openrouter_deepseek_roles():
        if role in IDENTITY_BOUND or rc.reasoning is not None:
            continue
        if (rc.max_tokens or 0) < GENEROUS_CEILING:
            unsafe.append((role, rc.max_tokens))
    assert unsafe == []


def test_a_capped_reasoning_budget_leaves_room_for_the_answer():
    for role, rc in _openrouter_deepseek_roles():
        if isinstance(rc.reasoning, int):
            assert rc.max_tokens >= rc.reasoning + ANSWER_HEADROOM, role


def test_the_structured_extraction_roles_that_truncated_in_production_think_no_more():
    llm = get_config().app.llm
    for role in ("layer_rotation", "structure_analyst", "fundamental_classifier"):
        assert llm.for_role(role).reasoning == "off", role


def test_gateway_sends_the_reasoning_field_only_through_openrouter(monkeypatch):
    seen = []

    class Recorder:
        def __init__(self, **kwargs):
            seen.append(kwargs)

    import langchain_openai

    monkeypatch.setattr(langchain_openai, "ChatOpenAI", Recorder)
    real = get_config()

    def fake_config(base_url):
        secrets = SimpleNamespace(openai_api_key="k", openai_base_url=base_url)
        return SimpleNamespace(app=real.app, secrets=secrets)

    for base_url, expected in (("https://openrouter.ai/api/v1", {"reasoning": {"enabled": False}}),
                               ("https://api.openai.com/v1", None)):
        gateway.get_model.cache_clear()
        monkeypatch.setattr(gateway, "get_config", lambda b=base_url: fake_config(b))
        gateway.get_model("layer_rotation")
        assert seen[-1].get("extra_body") == expected
    gateway.get_model.cache_clear()
