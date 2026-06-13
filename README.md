# ats — Multi-agent automated stock trading

A LangGraph-orchestrated, human-in-the-loop stock trading system. Analyst team
(macro / industry / fundamental / technical) → risk guardrails → Manager
decisions → **Boss approval (HITL)** → Trader (IBKR paper) → memory/performance.

See [`docs/DESIGN.md`](docs/DESIGN.md) for the full design.

## Status

- ✅ **Phase 1** — project skeleton, Pydantic contracts, config, LLM gateway
- ✅ **Phase 2** — LangGraph topology with stub nodes; dry-run runs end-to-end
  through the HITL interrupt
- ⬜ **Phase 3+** — real data sources, LLM-backed analysts + SKILLs, IBKR paper,
  Context Memory, scheduling, Feishu/Discord Boss channel (Phase 2 of roadmap)

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
