"""LLM gateway: role -> chat model, with a provider-swap seam.

Default path is Anthropic Claude Opus via `langchain_anthropic.ChatAnthropic`,
which integrates natively with LangGraph and supports tool-calling + prompt
caching. The OpenAI-compatible swap path lets us point at any OpenAI-format
endpoint (incl. a LiteLLM proxy) by setting `provider: openai` in settings.yaml
and OPENAI_BASE_URL in .env — agent code never changes.

Imports of provider SDKs are lazy so Phase 1/2 can run (and tests can stub the
model) without the heavy LLM deps installed.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from ..config import get_config


@lru_cache(maxsize=None)
def get_model(role: str) -> Any:
    """Return a configured LangChain chat model for the given agent role.

    Roles: macro_analyst, industry_analyst, fundamental_analyst,
    technical_analyst, risk_manager, manager.
    """
    cfg = get_config()
    rc = cfg.app.llm.for_role(role)
    common = dict(
        temperature=rc.temperature,
        max_tokens=rc.max_tokens,
        timeout=cfg.app.llm.timeout_seconds,
        max_retries=cfg.app.llm.max_retries,
    )

    if rc.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=rc.model,
            api_key=cfg.secrets.anthropic_api_key or None,
            **common,
        )

    if rc.provider == "deepseek":
        # OpenAI-SDK compatible (api-docs.deepseek.com): same wire format, only base_url
        # and key differ.
        #
        # Thinking mode is DISABLED, and it is not optional. v4-flash reasons by default,
        # and the API rejects a forced tool_choice while reasoning:
        #   400 "Thinking mode does not support this tool_choice"
        # `with_structured_output(method="function_calling")` forces the tool, so every
        # structured call fails outright — measured 5/5. The two alternatives were worse:
        # `json_schema` is unavailable on this endpoint, and `json_mode` returns JSON that
        # ignores the schema (it invented an `observation` field where the model expects
        # `metric`), which is a silent failure rather than a loud one.
        #
        # It must go in `extra_body`: langchain_openai forwards `model_kwargs` as
        # top-level SDK arguments, and the SDK rejects an unknown `thinking` kwarg.
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=rc.model,
            api_key=cfg.secrets.deepseek_api_key or None,
            base_url=cfg.secrets.deepseek_base_url or "https://api.deepseek.com",
            extra_body={"thinking": {"type": "disabled"}},
            **common,
        )

    if rc.provider == "openai":
        from langchain_openai import ChatOpenAI

        base_url = cfg.secrets.openai_base_url or None
        # OpenRouter (optional) attribution headers — harmless against vanilla OpenAI.
        headers = {"HTTP-Referer": "https://github.com/ats", "X-Title": "ats"} \
            if base_url and "openrouter" in base_url else None
        return ChatOpenAI(
            model=rc.model,
            api_key=cfg.secrets.openai_api_key or None,
            base_url=base_url,
            default_headers=headers,
            **common,
        )

    raise ValueError(f"Unknown LLM provider {rc.provider!r} for role {role!r}")


def reset_model_cache() -> None:
    get_model.cache_clear()
