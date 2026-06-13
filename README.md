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
- ✅ **Phase 5 (manager)** — LLM Manager synthesizes all reports + guardrails into
  trades; a deterministic validator hard-clips them (only tightens). Trader skips
  holds and sizes notional→shares.
- ✅ **Phase 6 (IBKR paper)** — `broker/ibkr.py` (ib_async): risk manager reads the
  live portfolio (tightens guardrails on over-cap names / hot sectors); Trader
  places real paper orders on `--live`. All paths degrade gracefully if TWS is
  down. Probe with `ats ibkr`.
- ✅ **Phase 7 (macro + fundamentals data)** — `data/macro.py` (FRED rates/CPI/jobs +
  yfinance VIX/SPX/NDX + CNN fear&greed) and `data/fundamentals.py` (yfinance
  metrics + SEC EDGAR filings) feed the macro & fundamental analysts real numbers.
  Each feed degrades to a note. FRED needs `FRED_API_KEY`; the rest need no key.
- ⬜ **Next** — Context Memory + performance tracking, scheduling, Feishu/Discord
  Boss channel

### IBKR setup (paper)

Start TWS or IB Gateway, log into the **paper** account, then enable
File ▸ Global Config ▸ API ▸ Settings → "ActiveX and Socket Clients", port 7497,
trust 127.0.0.1. Verify: `PYTHONPATH=src .venv/bin/python -m ats.runtime.cli ibkr`.
TWS auto-logs-out daily, so re-check before a `--live` run.

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
