"""事件模式：cutoff 冻结基线、三类差异、事件评审投影（Phase D tasks 5.3–5.5, 5.9）。

事件模式以财报或明确公司事件为触发器：
- 在 cutoff 冻结基线与其引用；后续到达的信息不改写冻结基线；
- 只使用报告期间正确且通过准入的 actuals、财报稿、指引与电话会（期间守卫在
  `graph/pead.score_fetch` 内执行，这里负责冻结记录与排除留痕）；
- 分别计算对冻结基线、Consensus 与市场隐含预期的三类差异，分列呈现、不取平均，
  方向不一致时保留分歧；
- 产出 `FundamentalEventReview` 投影：方向（-1|0|1）、幅度、信心、理由与可证伪
  条件——没有 action、没有数量、没有风控结论；电话会迟到时产出新版本，旧版本
  保留可查回。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from ...agent.task_projection import ProjectionScope, build_envelope

log = __import__("logging").getLogger("ats.agents.fundamental.event")

_DIRECTION_WORDS = {1: "预期差为正", 0: "预期差中性", -1: "预期差为负"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _meta_key(symbol: str, fiscal_label: str) -> str:
    return f"frozen_baseline:{symbol.upper()}:{fiscal_label}"


# --------------------------------------------------------------------------- #
# 基线冻结（5.3）
# --------------------------------------------------------------------------- #
def freeze_baseline(store, *, symbol: str, fiscal_label: str, cutoff: str = "") -> dict:
    """在 cutoff 冻结本标的本报告期的预期基线与其引用，写入 Workflow memory。

    冻结记录是一次性快照：事件评审的差异计算永远以它为参照，后续到达的
    简报、文档或重跑 prep 都不改写它（重跑只会重算 prep 的 dossier，冻结
    记录独立存在）。
    """
    from ...schemas.pead import ExpectationSet

    symbol = symbol.upper()
    cutoff = cutoff or _now()
    dossier = store.get_dossier(symbol, fiscal_label)
    es = getattr(dossier, "expectation_set", None) if dossier else None
    frozen = {
        "symbol": symbol, "fiscal_label": fiscal_label, "frozen_at": cutoff,
        "narrative": es.narrative if isinstance(es, ExpectationSet) else "",
        "expectations": [
            {"dim_key": e.dim_key, "metric": e.metric, "neutral": e.neutral,
             "conservative": e.conservative, "optimistic": e.optimistic, "source": e.source}
            for e in (es.expectations if isinstance(es, ExpectationSet) else [])],
        "consensus": _consensus_view(es) if isinstance(es, ExpectationSet) else {},
        "refs": {
            "dossier_symbol": symbol, "dossier_fiscal_label": fiscal_label,
            "expectation_set_as_of": es.as_of.isoformat() if isinstance(es, ExpectationSet) else "",
        },
    }
    store.set_meta(_meta_key(symbol, fiscal_label), json.dumps(frozen, ensure_ascii=False))
    return frozen


def _consensus_view(es) -> dict:
    out: dict = {}
    for field in ("consensus_eps", "consensus_revenue", "consensus_target_price",
                  "consensus_eps_low", "consensus_eps_high",
                  "consensus_revenue_low", "consensus_revenue_high"):
        value = getattr(es, field, None)
        if value is not None:
            out[field] = value
    return out


def load_frozen_baseline(store, *, symbol: str, fiscal_label: str) -> dict | None:
    raw = store.get_meta(_meta_key(symbol, fiscal_label), "")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def record_exclusion(store, *, symbol: str, fiscal_label: str, item: dict) -> None:
    """期间不符或未过准入的材料被排除时留痕（SHALL NOT 静默丢弃，5.3）。"""
    key = f"event_exclusions:{symbol.upper()}:{fiscal_label}"
    existing = store.get_meta(key, "")
    try:
        entries = json.loads(existing) if existing else []
    except json.JSONDecodeError:
        entries = []
    entries.append({"excluded_at": _now(), **item})
    store.set_meta(key, json.dumps(entries, ensure_ascii=False))


def read_exclusions(store, *, symbol: str, fiscal_label: str) -> list[dict]:
    key = f"event_exclusions:{symbol.upper()}:{fiscal_label}"
    raw = store.get_meta(key, "")
    try:
        return json.loads(raw) if raw else []
    except json.JSONDecodeError:
        return []


# --------------------------------------------------------------------------- #
# 三类差异（5.4）
# --------------------------------------------------------------------------- #
def _number(text: object) -> float | None:
    import re

    if text is None:
        return None
    hit = re.search(r"-?\d+(?:\.\d+)?", str(text).replace(",", ""))
    return float(hit.group(0)) if hit else None


def compute_tri_diffs(*, frozen_baseline: dict | None, expectation_set,
                      actuals, market_setup) -> dict:
    """分别对 冻结基线 / Consensus / 市场隐含预期 计算实际值差异。

    三个口径分列、各自带方向符号；方向不一致时保留分歧并说明，绝不取平均。
    """
    vs_baseline: list[dict] = []
    baseline_sign = 0
    if frozen_baseline:
        actual_by_dim = {}
        if actuals is not None:
            for m in actuals.metrics:
                actual_by_dim[m.dim_key] = _number(m.actual)
        for e in frozen_baseline.get("expectations", []):
            neutral = _number(e.get("neutral"))
            actual = actual_by_dim.get(e.get("dim_key", ""))
            if neutral is None or actual is None:
                continue
            delta = actual - neutral
            rel = delta / abs(neutral) if neutral else 0.0
            vs_baseline.append({"dim_key": e.get("dim_key"), "neutral": neutral,
                                "actual": actual, "delta": round(delta, 4),
                                "delta_pct": round(rel, 4)})
            baseline_sign += 1 if rel > 0.01 else (-1 if rel < -0.01 else 0)
        baseline_sign = _sign(baseline_sign)

    consensus_sign = 0
    vs_consensus: dict = {}
    if actuals is not None and expectation_set is not None:
        eps = getattr(actuals, "reported_eps", None)
        cons_eps = getattr(expectation_set, "consensus_eps", None)
        if eps is not None and cons_eps:
            cons_eps_num = _number(cons_eps)
            if cons_eps_num:
                rel = (eps - cons_eps_num) / abs(cons_eps_num)
                vs_consensus["eps"] = {"consensus": cons_eps_num, "reported": eps,
                                       "delta_pct": round(rel, 4)}
                consensus_sign = _sign(rel)
        rev = getattr(actuals, "reported_revenue", None)
        cons_rev = getattr(expectation_set, "consensus_revenue", None)
        if rev is not None and cons_rev:
            cons_rev_num = _number(cons_rev)
            if cons_rev_num:
                rel = (rev - cons_rev_num) / abs(cons_rev_num)
                vs_consensus["revenue"] = {"consensus": cons_rev_num, "reported": rev,
                                           "delta_pct": round(rel, 4)}

    market_sign = 0
    vs_market: dict = {}
    em = getattr(market_setup, "expected_move_pct", None) if market_setup else None
    if em is not None and vs_consensus.get("eps"):
        # 市场隐含预期：期权 Expected Move。实际 EPS 惊喜幅度超出 EM，说明市场
        # 没有充分定价；反之已在价内。
        surprise = abs(vs_consensus["eps"]["delta_pct"]) * 100
        vs_market = {"expected_move_pct": em, "eps_surprise_pct":
                     vs_consensus["eps"]["delta_pct"] * 100,
                     "priced_in": surprise <= em}
        market_sign = _sign(vs_consensus["eps"]["delta_pct"]) if surprise > em else 0

    signs = {"vs_baseline": baseline_sign, "vs_consensus": consensus_sign,
             "vs_market": market_sign}
    disagree = len({v for v in signs.values() if v != 0}) > 1
    return {
        "vs_baseline": vs_baseline, "vs_consensus": vs_consensus, "vs_market": vs_market,
        "directions": signs, "directions_agree": not disagree,
        "note": ("三类方向不一致，分歧保留、未取平均" if disagree else ""),
    }


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


# --------------------------------------------------------------------------- #
# 事件评审投影（5.4/5.5/5.9）
# --------------------------------------------------------------------------- #
def _falsifiable_conditions(expectation_set, limit: int = 3) -> list[str]:
    """从基线预期生成可证伪条件：下一期实际值击穿中性情形即评审作废。"""
    if expectation_set is None:
        return []
    out = []
    for e in list(expectation_set.expectations)[:limit]:
        if (e.neutral or "").strip():
            out.append(f"若 {e.dim_key}（{e.metric}）实际值明显低于中性情形 {e.neutral}，"
                       f"本事件评审作废")
    return out


def build_event_review_payload(*, symbol: str, period: str, event: str,
                               scorecard, event_view: dict, tri_diffs: dict,
                               actuals, narrative: str = "") -> dict:
    lines = [line.model_dump(mode="json") for line in scorecard.lines] if scorecard else []
    payload = {
        "entity": symbol.upper(),
        "event": event,
        "period": period,
        "direction": int(event_view.get("direction", 0)),
        "magnitude": float(event_view.get("magnitude", 0.0)),
        "notes": event_view.get("rationale", ""),
        "scorecard": [
            {"section": "scorecard_lines", "lines": lines},
            {"section": "tri_diff", **(tri_diffs or {})},
        ],
        "guidance": (getattr(actuals, "guidance", "") or "") if actuals else "",
        "narrative": narrative,
        "confidence": event_view.get("confidence"),
        "falsifiable_conditions": list(event_view.get("falsifiable_conditions") or []),
    }
    return payload


def publish_event_review(store, payload: dict, *, as_of: str = "",
                         input_refs=None, supersedes_projection_id: str = "") -> str:
    envelope = build_envelope(
        role="fundamental_event_review", payload=payload,
        scope=ProjectionScope(kind="entity", id=str(payload["entity"]).upper()),
        as_of=as_of or _now(), input_refs=input_refs,
        supersedes_projection_id=supersedes_projection_id)
    store.save_task_projection_envelope(envelope)
    return envelope.projection_id


def event_reviews_for(store, *, symbol: str, period: str,
                      own_symbol: str | None = None) -> list[dict]:
    """同一报告期的全部事件评审版本（含早期版本，按时间倒序）。

    `own_symbol` 是本次运行正在评审的标的：读别人的事件评审（symbol ≠ own_symbol）
    就是跨标的观点通道，守卫直接拒绝（5.11）。
    """
    from ...workflow.architecture_guards import assert_fundamental_scope

    assert_fundamental_scope("fundamental_event_review", symbol,
                             own_symbol=own_symbol or symbol)
    rows = store.task_projection_envelopes(
        agent_role="fundamental_event_review", scope_kind="entity",
        scope_id=symbol.upper(), limit=200) or []
    return [row for row in rows if (row.get("payload") or {}).get("period") == period]


def direction_word(direction: int) -> str:
    return _DIRECTION_WORDS.get(int(direction), "预期差中性")


# --------------------------------------------------------------------------- #
# 显式入口（5.1 的事件分支）
# --------------------------------------------------------------------------- #
def run_event_pass(request) -> dict:
    """事件模式一轮：冻结基线 → 跑 PEAD score 图 → 发布事件评审投影。

    图内的 score_fetch 已有期间守卫与证据守卫；本函数补上冻结记录与投影发布
    （发布在 score_persist 内完成，这里只做冻结与汇总）。
    """
    from ...memory import get_store

    store = get_store()
    frozen = freeze_baseline(store, symbol=request.symbol,
                             fiscal_label=request.fiscal_label, cutoff=request.cutoff)
    from ...graph.pead import build_pead_graph
    from ...graph.pead_state import PeadState

    now = datetime.now(timezone.utc)
    state = PeadState(symbol=request.symbol, phase="score", as_of=now,
                      use_llm=request.use_llm,
                      use_broker=False, live_data=bool(request.extra.get("live_data", False)),
                      dry_run=True, transcript_source=request.extra.get("transcript_source"))
    app = build_pead_graph(checkpointer=None)
    result = app.invoke(state, config={"configurable": {
        "thread_id": f"fundamental-event-{request.symbol}-{now:%Y%m%d%H%M%S}"}})
    return {"mode": "event", "symbol": request.symbol,
            "fiscal_label": request.fiscal_label, "frozen_at": frozen.get("frozen_at", ""),
            "event_review_summary": result.get("event_summary", {}),
            "fiscal_label_resolved": result.get("fiscal_label", "")}
