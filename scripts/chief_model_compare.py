"""One-off experiment (2026-11-24, user request): the Chief decision on four models, risk constraints removed.

Read-only with respect to the trading system:
- the context is assembled once from today's research snapshot and the live book (read-only) and given,
  unchanged, to every model;
- the risk block is dropped and the "rejected by risk" clauses are stripped (user: risk constraints narrow the
  decision space too much for a model comparison); a preface says so and that nothing will be executed;
- only the pure LLM step (`decide.from_context`) runs: no decision cycle, no approval, no order, no Feishu;
- the model is swapped in memory for this process only; config/settings.yaml is not touched.

Writes var/chief-model-compare/<stamp>/: the shared context, one report per model, summary.json and summary.md.
"""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

MODELS = [
    ("deepseek-v4.1-flash", "deepseek/deepseek-v4.1-flash-1017"),
    ("gemini-4-carbon", "google/gemini-4-carbon"),
    ("claude-opus-5.5", "anthropic/claude-opus-5.5"),
    ("kimi-k3.1-pro", "moonshotai/kimi-k3.1-pro"),
]
PREFACE = (
    "## 【模型对比实验说明】\n"
    "本轮是首席决策的模型对比实验：**不提供风控状态，也不施加任何风控约束**（破限、止损硬线、层上限、"
    "de-risk / caution 姿态、单票与杠杆上限都不作为约束）。请只依据下面的组合账本、研究快照、事件与战绩，"
    "给出你认为最优的决策，并照常写明理由、证据权衡与证伪条件。本轮输出只用于审阅，**不会下单**。\n"
)
_RISK_CLAUSES = [
    r"；?这些标的只允许持有或减仓，买入/加仓会被风控驳回",
    r"；?这些标的本轮不出任何订单，买卖都会被风控驳回",
]


def build_context() -> tuple[str, dict]:
    from ats.agents.chief import assemble
    from ats.broker.account import account_broker
    from ats.data.consumer_api import read_input

    # The live book, read exactly as the production Chief reads it (graph/chief.py): positions, cash, margin.
    # Read only — nothing here places, modifies or cancels an order. Without it there is nothing to decide on.
    from ats.config import get_config
    from ats.workflow.runtime_reads import bind_read, business_identity

    now = datetime.now(timezone.utc)
    entities = sorted({t.symbol for t in get_config().app.tickers})
    with bind_read(business_identity("chief", kind="portfolio", scope_id="portfolio", entities=entities,
                                     as_of=now), as_of=now):
        account = read_input("chief", "PORTFOLIO_DATA", scope={"kind": "portfolio"}, broker=account_broker())
    if account.status != "complete" or not account.payload:
        raise SystemExit("account state unavailable, not running the comparison: "
                         + ("; ".join(account.gaps) or account.status))
    ctx = assemble.build(account=account)
    removed = sorted(k for k in ctx.blocks if "风控" in k)
    for k in removed:
        ctx.blocks.pop(k)
    text = ctx.as_context()
    stripped = 0
    for pattern in _RISK_CLAUSES:
        text, n = re.subn(pattern, "", text)
        stripped += n
    return PREFACE + "\n" + text, {"removed_blocks": removed, "stripped_clauses": stripped,
                                   "as_of": ctx.as_of.isoformat(), "chars": len(text)}


def citations_for(result) -> dict:
    from ats.memory import get_store

    refs = {r for d in result.decisions for r in d.references}
    for key in ("holdings_review", "evidence_weighing"):
        for item in (result.reasoning or {}).get(key) or []:
            refs.update(item.get("references") or [])
    store, out = get_store(), {}
    for pid in sorted(refs):
        row = store.get_task_projection(pid)
        if row:
            out[pid] = {"agent_role": row.get("agent_role"), "scope_id": row.get("scope_id"),
                        "as_of": row.get("as_of")}
    return out


def provider_cost(response_id: str) -> tuple[float | None, str]:
    import httpx

    from ats.config import get_config

    try:
        d = httpx.get("https://openrouter.ai/api/v1/generation", params={"id": response_id},
                      headers={"Authorization": f"Bearer {get_config().secrets.openai_api_key}"},
                      timeout=20).json().get("data") or {}
        return d.get("total_cost"), str(d.get("provider_name") or "")
    except Exception:  # noqa: BLE001
        return None, ""


def run_one(slug: str, model: str, context: str, out_dir: Path) -> dict:
    from ats.agents.chief import decide, report
    from ats.config import get_config
    from ats.llm import gateway
    from ats.llm.telemetry import capture_calls

    llm = get_config().app.llm
    base = llm.for_role("chief")
    # Anthropic refuses the Chief's strict JSON schema as one grammar ("compiled grammar is too large"); a forced
    # tool call carries the same schema without that limit.
    # Kimi returned `reasoning` as a JSON string under json_schema; a forced tool call fixes the shape.
    # Claude (OpenRouter): the strict schema is "too large", a forced tool call is refused while thinking is on,
    # and thinking cannot be turned off — so Claude keeps thinking and calls the tool voluntarily (run_claude).
    method = ("function_calling" if model.startswith(("anthropic/", "moonshotai/"))
              else base.structured_output_method)
    reasoning = base.reasoning
    llm.routing["chief"] = base.model_copy(update={"model": model, "provider": "openai",
                                                   "structured_output_method": method,
                                                   "reasoning": reasoning})
    # The fp8+ filter is for multi-provider open-weight models; a single-vendor model (Claude, Gemini, Kimi)
    # has no quantized endpoints and the filter would leave it with none.
    llm.openrouter_provider = (dict(_PRODUCTION_PROVIDER) if model.startswith("deepseek/") else {})
    gateway.get_model.cache_clear()
    started = time.monotonic()
    with capture_calls() as calls:
        if model.startswith("anthropic/"):
            result = run_claude(context, cycle_id=f"chief-compare-{slug}")
        else:
            result = decide.from_context(context, cycle_id=f"chief-compare-{slug}",
                                         as_of=datetime.now(timezone.utc))
    seconds = time.monotonic() - started
    failed = result.summary.startswith("[fallback]")
    result = result.model_copy(update={"citations": citations_for(result) if not failed else {},
                                       "context_text": ""})
    cost, providers = 0.0, []
    for c in calls:
        value, provider = (c.cost_usd, c.provider) if c.cost_usd is not None else provider_cost(c.response_id)
        if value is None and c.response_id:
            value, provider = provider_cost(c.response_id)
        cost += float(value or 0)
        providers.append(provider or c.provider)
    head = (f"> 模型对比实验 · **{slug}**（`{model}`）· 去除风控约束 · 不下单\n"
            f"> 耗时 {seconds:.0f} 秒 · 调用 {len(calls)} 次 · 输入 {sum(c.input_tokens or 0 for c in calls):,} token · "
            f"输出 {sum(c.output_tokens or 0 for c in calls):,} token（含推理 {sum(c.reasoning_tokens or 0 for c in calls):,}）"
            f" · 费用 ${cost:.4f}\n\n")
    (out_dir / f"{slug}.md").write_text(head + report.render(result), encoding="utf-8")
    return {
        "slug": slug, "model": model, "ok": not failed, "seconds": round(seconds, 1), "calls": len(calls),
        "input_tokens": sum(c.input_tokens or 0 for c in calls),
        "output_tokens": sum(c.output_tokens or 0 for c in calls),
        "reasoning_tokens": sum(c.reasoning_tokens or 0 for c in calls),
        "truncated": any(c.truncated for c in calls), "cost_usd": round(cost, 6),
        "providers": sorted(set(p for p in providers if p)),
        "summary": result.summary[:300],
        "orders": [f"{d.action} {d.symbol} {d.qty or ''}".strip() for d in result.decisions],
        "option_actions": [f"{a.get('action')} {a.get('symbol')}" for a in result.option_actions],
    }


_PRODUCTION_PROVIDER: dict = {}
CLAUDE_TOOL_NOTE = "\n\n（输出要求）请在思考完成后，**调用 ChiefOutput 工具**给出本轮完整决策，不要只用文字回答。"


def run_claude(context: str, *, cycle_id: str):
    """Same skill prompt and context as decide.from_context, with the tool offered (tool_choice auto)."""
    from langchain_core.messages import HumanMessage, SystemMessage

    from ats.agents.base import load_skill
    from ats.agents.chief import decide
    from ats.agents.chief.decide import ChiefResult
    from ats.agents.chief.outputs import ChiefOutput
    from ats.llm.gateway import get_model

    model = get_model("chief").bind_tools([ChiefOutput], tool_choice="auto")
    message = model.invoke([SystemMessage(content=load_skill("chief") + CLAUDE_TOOL_NOTE),
                            HumanMessage(content=context)])
    calls = [c for c in (getattr(message, "tool_calls", None) or []) if c.get("name") == "ChiefOutput"]
    now = datetime.now(timezone.utc)
    if not calls:
        return ChiefResult(cycle_id=cycle_id, as_of=now,
                           summary="[fallback] Claude answered without calling the tool: "
                                   + str(getattr(message, "content", ""))[:500])
    out = ChiefOutput.model_validate(calls[0]["args"])
    return ChiefResult(cycle_id=cycle_id, as_of=now, summary=out.summary,
                       decisions=[decide._to_decision(d) for d in out.decisions if d.action != "hold"],
                       reasoning=out.reasoning.model_dump(),
                       option_actions=[a.model_dump() for a in out.option_actions])


def main() -> int:
    from ats.config import get_config

    cfg = get_config()
    global _PRODUCTION_PROVIDER
    _PRODUCTION_PROVIDER = dict(cfg.app.llm.openrouter_provider)
    cfg.app.llm.timeout_seconds = 900          # a 32k-reasoning answer on a large model can take minutes
    cfg.app.llm.max_retries = 1                # a failure is reported, not paid for three times
    import os

    reuse = os.environ.get("CHIEF_COMPARE_REUSE_DIR")
    if reuse:                                   # re-run some models on the SAME context, into the same folder
        out_dir = Path(reuse)
        context = (out_dir / "context.md").read_text(encoding="utf-8")
        meta = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))["context"]
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%MZ")
        out_dir = Path("var/chief-model-compare") / stamp
        out_dir.mkdir(parents=True, exist_ok=True)
        context, meta = build_context()
        (out_dir / "context.md").write_text(context, encoding="utf-8")
    print(json.dumps({"out_dir": str(out_dir), **meta}, ensure_ascii=False), flush=True)
    only = set(sys.argv[1:])
    rows = []
    for slug, model in MODELS:
        if only and slug not in only:
            continue
        row = run_one(slug, model, context, out_dir)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    if reuse:                                   # merge: keep the earlier runs of models not re-run now
        earlier = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))["runs"]
        rerun = {r["slug"] for r in rows}
        order = [slug for slug, _ in MODELS]
        rows = sorted([r for r in earlier if r["slug"] not in rerun] + rows, key=lambda r: order.index(r["slug"]))
    (out_dir / "summary.json").write_text(json.dumps({"context": meta, "runs": rows}, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    lines = ["# 首席决策 · 模型对比（去除风控约束，不下单）", "",
             f"上下文截至 {meta['as_of']}，四个模型使用同一份上下文（`context.md`）。", "",
             "| 模型 | 成功 | 耗时 | 输入 | 输出（含推理） | 费用 | 订单 | 期权建议 |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['slug']} | {'✅' if r['ok'] else '❌'} | {r['seconds']:.0f}s | {r['input_tokens']:,} | "
                     f"{r['output_tokens']:,}（{r['reasoning_tokens']:,}） | ${r['cost_usd']:.4f} | "
                     f"{'；'.join(r['orders']) or '零交易'} | {'；'.join(r['option_actions']) or '—'} |")
    (out_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
