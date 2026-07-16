# ats — Multi-agent automated stock trading

A LangGraph-orchestrated, human-in-the-loop stock trading system. Analyst team
(macro / sector / PEAD) updates the knowledge base → **Chief single decision
maker** → 6-layer risk gate → **Boss approval (HITL)** → Trader (IBKR) →
memory/performance. Every order path funnels through one decision graph with a
single approval interrupt.

See [`docs/DESIGN.md`](docs/DESIGN.md) for the full design,
[`docs/WORKFLOWS.md`](docs/WORKFLOWS.md) for the workflow/trigger matrix and
the chief decision graph, [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md) for
the data-source status (tested vs pending) + how to test each, and
[`docs/GO_LIVE.md`](docs/GO_LIVE.md) for the step-by-step go-live checklist.

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
- ✅ **PEAD MVP** — earnings-event workflow (separate from the daily cycle): a
  per-ticker, earnings-anchored state machine. See below.
- ⬜ **Optional next** — Feishu-async PEAD resume, Day1/2 drift tracking, Discord
  channel, latency optimization

## PEAD earnings workflow (MVP)

A Post-Earnings-Announcement-Drift workflow that automates a structured,
earnings-anchored process per ticker, reusing the same agents/HITL/risk/memory/
IBKR building blocks as the daily cycle (`graph/pead.py`).

```
pead prep   T-N..T-1  核心叙事 + 保守/中性/乐观预期表 + consensus(yfinance)
                      + 期权(ThetaData主, yfinance兜底: Expected Move/IV/skew)
                      + 抢跑(vs SMH/QQQ) + 信号链(上游 hyperscaler / 同业)  → dossier
pead score  T(盘后)    实际(财报数字 + earnings-call transcript) → 预期偏差
                      → 加权 Surprise Scorecard → 确定性情景决策树(总分×抢跑+个股门槛)
                      + portfolio → 风控硬裁剪 → HITL 审批 → 执行 → dossier
```

LLM judges semantics (narrative, expectations, per-dimension surprise); **the
weighting, threshold bands, and decision tree are deterministic code** (auditable).

```bash
ats pead prep COHR                                  # build the pre-earnings dossier
ats pead score COHR --transcript path/to/call.txt   # score + decide (HITL); --live to trade
ats pead score COHR --channel feishu                # async: card → phone approval → webhook
ats pead show COHR                                   # print the dossier
ats pead monitor COHR                               # one continuous-context pass (news → dossier)
ats pead watch                                       # monitor all targets (config/pead.yaml)
ats thetadata COHR                                   # probe the local ThetaData terminal
```

**v2 — continuous & autonomous.** The dossier is a *living* document: a daily
`monitor` ingests target + supply-chain news (Finnhub + curated RSS; X stub) and
folds material developments into the narrative/expectations in memory
(`store.pead_events`, deduped). The scheduler (`ats schedule`) routes each PEAD
target daily — always `monitor`, `prep` when earnings is near, `score` the
session after earnings (transcript auto-fetched via FMP, `FMP_API_KEY`). Trades
go through deterministic risk then **Feishu async approval** (`--channel feishu`;
the `ats serve` webhook routes `pead:` threads back to the PEAD graph). Boundary:
context/analysis is autonomous; only trades require approval. Config: global
`config/pead.yaml` (targets, monitor switches, schedule windows) + per-ticker
`config/pead/<SYM>.yaml`.

- Per-ticker config in `config/pead/<SYM>.yaml` (scorecard dims/weights, special
  long threshold, signal chain). COHR is seeded from a real worked example
  (special +1.5 long bar). `_defaults.yaml` covers the rest.
- Transcript source: drop the call text at `var/transcripts/<SYM>_<fiscal>.txt`
  (or pass `--transcript <path|url>`) — matches grabbing transcripts from
  fool.com/investing.com manually. Consensus is free via yfinance.
- MVP approval is the CLI channel; Feishu-async PEAD resume is a follow-up.
- **Options data (ThetaData)**: for precise Expected Move / IV / skew, run the
  local ThetaData Terminal: put creds in `var/thetadata/creds.txt`, then
  `./scripts/start_thetadata.sh` (REST on 127.0.0.1:25503). Without it, options
  fall back to the yfinance chain automatically.

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
3. Set `channel.kind: feishu` in `config/settings.yaml` (or `ats chief run --channel feishu`).
4. Run the webhook: `ats serve --port 8000`. Then `ats chief run --channel feishu`
   sends a card and exits; tapping Approve resumes execution via the webhook.

The graph is transport-agnostic — the same interrupt/checkpoint mechanism backs
CLI and Feishu; Discord is a drop-in adapter behind the same `BossChannel` port.

**Group custom-bot mode** (simpler — no app needed). A Feishu group bot is
push-only, so approval uses URL buttons that hit a signed GET endpoint:
1. `FEISHU_BOT_WEBHOOK` = the group bot's incoming webhook.
2. `FEISHU_APPROVE_BASE` = public URL of `ats serve` (tunnel); `FEISHU_APPROVE_SECRET`
   = any random string (HMAC-protects the approve links).
3. `ats serve` exposes `GET /feishu/approve`; tapping Approve/Reject in the card
   resumes the run. Use `--channel feishu_bot` (e.g. `ats pead score COHR --channel feishu_bot`).

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

# Chief decision run (dry-run by default), interactive Boss approval:
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli chief run

# Wiring smoke test without LLM/broker (stub decision, ends with zero trades):
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli chief run --no-llm --offline

# Manual order through the same risk gate + approval funnel (dry-run):
PYTHONPATH=src .venv/bin/python -m ats.runtime.cli trader buy NVDA 1 --limit 100 --dry-run

# Tests:
PYTHONPATH=src .venv/bin/python -m pytest -q
```

## Layout

```
config/            settings.yaml (non-secret) + watchlist.yaml
src/ats/
  schemas/         Pydantic data contracts (agent I/O)
  llm/gateway.py   role -> chat model (Claude Opus; OpenAI-compatible swap)
  channel/         BossChannel port: CLI / Feishu adapters
  graph/           chief (decision funnel) + pead (earnings graph) + checkpoint
  agents/          agent logic per role (macro/ sector/ pead/ chief/)
  skills/          SKILL.md per role
  data/ memory/ broker/ runtime/
```
