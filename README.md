# ats — Multi-agent automated stock trading

A LangGraph-orchestrated, human-in-the-loop stock trading system. Analyst team
(macro / industry / fundamental / technical) → risk guardrails → Manager
decisions → **Boss approval (HITL)** → Trader (IBKR paper) → memory/performance.

See [`docs/DESIGN.md`](docs/DESIGN.md) for the full design.

## Status

- ✅ **Phase 1** — project skeleton, Pydantic contracts, config, LLM gateway
- ✅ **Phase 2** — LangGraph topology; dry-run runs end-to-end through the HITL interrupt
- ✅ **Phase 3 (data)** — live market data (yfinance) + indicators wired into ingest
- ✅ **Phase 4 (analysts)** — macro/industry/fundamental/technical agents on Claude
  Opus 4.8 via **OpenRouter**, each with a SKILL.md and structured (tool-calling) output
- ⬜ **Next** — SEC/financials + FRED macro + news/social sources, LLM Manager,
  IBKR paper portfolio/execution, Context Memory + performance, scheduling,
  Feishu/Discord Boss channel

The LLM goes through OpenRouter (OpenAI-compatible) so the provider/model is a
one-line config swap (`config/settings.yaml` → `llm.default_model`). Set
`OPENAI_API_KEY` (OpenRouter key) + `OPENAI_BASE_URL=https://openrouter.ai/api/v1`
in `.env`. Behind a SOCKS proxy? `pip install socksio`.

## Quickstart

```bash
uv venv --python 3.11 .venv
uv pip install --python .venv -e .            # or: .[dev]
cp .env.example .env                          # fill in keys as phases land

# Run one trading cycle (stub data), interactive Boss approval:
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli run

# Unattended smoke test (auto-approve):
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli run --yes

# Tests:
PYTHONPATH=src .venv/bin/python -m pytest -q
```

## Layout

```
config/            settings.yaml (non-secret) + watchlist.yaml
src/ats/
  schemas/         Pydantic data contracts (agent I/O)
  llm/gateway.py   role -> chat model (Claude Opus; OpenAI-compatible swap)
  channel/         BossChannel port + CLI adapter (Feishu/Discord = Phase 2)
  graph/           StateGraph: state, nodes (stub), build, checkpoint
  agents/          real agent logic (Phase 3+)
  skills/          SKILL.md per role (Phase 4+)
  data/ memory/ broker/ runtime/
```
