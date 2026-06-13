# ats — Multi-agent automated stock trading

A LangGraph-orchestrated, human-in-the-loop stock trading system. Analyst team
(macro / industry / fundamental / technical) → risk guardrails → Manager
decisions → **Boss approval (HITL)** → Trader (IBKR paper) → memory/performance.

See [`docs/DESIGN.md`](docs/DESIGN.md) for the full design, and
[`docs/GO_LIVE.md`](docs/GO_LIVE.md) for the step-by-step paper-trading go-live
checklist (env, IBKR/TWS, Feishu app, tunnel, webhook, scheduling).

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
- ✅ **Phase 8 (Context Memory)** — SQLite store (`memory/`) persists reports,
  decisions, trades, and per-cycle performance. Prior PnL + recent fills are fed
  back to the Manager; the Boss `report <SYM>` pulls a name's history. DB at
  `var/ats.sqlite` (gitignored), overridable via `ATS_DB_PATH`.
- ✅ **Phase 9 (Feishu approval)** — async Boss approval: the run checkpoints at
  the interrupt (persistent SqliteSaver) and sends a Feishu card; the Boss taps
  Approve/Reject; an `ats serve` webhook resumes the cycle by thread_id. Graph
  stays decoupled from the transport.
- ✅ **Phase 10 (scheduling)** — `ats schedule` runs a daily cron, gated to NYSE
  sessions (skips weekends/holidays via `pandas_market_calendars`). Pairs with
  Feishu: scheduled analysis → card → phone approval → webhook executes.
- ⬜ **Optional next** — Discord channel (drop-in adapter), earnings-call
  transcripts + news/social sources, latency optimization

### Daily automation

```bash
# Configure config/settings.yaml: schedule.run_at / timezone, channel.kind: feishu
ats schedule            # cron daemon (mon-fri, NYSE sessions only)
ats schedule --now      # run one cycle immediately (skips if not a session)
ats serve               # in a second process: handle Feishu approval callbacks
```

### Feishu approval setup

1. Create a Feishu/Lark custom app → get **App ID / App Secret**; grant
   `im:message` (send) and enable bot. Put a target **chat_id** in `.env`
   (`FEISHU_CHAT_ID`).
2. Enable **Event/Card callback** → set the request URL to your public
   `https://<host>/feishu/callback` (in dev, tunnel with ngrok/cloudflared to the
   `ats serve` port). Copy the **Verification Token** to `FEISHU_VERIFICATION_TOKEN`.
3. Set `channel.kind: feishu` in `config/settings.yaml` (or `ats run --channel feishu`).
4. Run the webhook: `ats serve --port 8000`. Then `ats run --live --channel feishu`
   sends a card and exits; tapping Approve resumes execution via the webhook.

The graph is transport-agnostic — the same interrupt/checkpoint mechanism backs
CLI and Feishu; Discord is a drop-in adapter behind the same `BossChannel` port.

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
