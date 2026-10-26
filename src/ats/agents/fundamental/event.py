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

from ats.workflow.evaluation_clock import now as evaluation_now

import json
from datetime import datetime, timezone

from ...agent.task_projection import ProjectionScope, build_envelope

log = __import__("logging").getLogger("ats.agents.fundamental.event")

_DIRECTION_WORDS = {1: "预期差为正", 0: "预期差中性", -1: "预期差为负"}


def _now() -> str:
    return evaluation_now(timezone.utc).isoformat(timespec="seconds")


def _meta_key(symbol: str, fiscal_label: str) -> str:
    return f"frozen_baseline:{symbol.upper()}:{fiscal_label}"


# --------------------------------------------------------------------------- #
# 基线冻结（5.3）
# --------------------------------------------------------------------------- #
def _period_key(label: str) -> tuple[str, str] | None:
    """(fiscal year, quarter) from either spelling in use: `FY2027Q1` (calendar) or `Q1 FY2027` (baseline)."""
    import re

    text = str(label or "").upper().replace(" ", "")
    hit = re.search(r"FY(\d{4})Q([1-4])", text) or re.search(r"Q([1-4])FY(\d{4})", text)
    if not hit:
        return None
    first, second = hit.groups()
    return (first, second) if len(first) == 4 else (second, first)


def published_baseline(store, *, symbol: str, fiscal_label: str, cutoff: str = "") -> dict | None:
    """The pre-print baseline as it was published: newest `[基线 vN]` projection of the period not after `cutoff`.

    The dossier's expectation set is overwritten by the scoring phase after the print, so it cannot be the source of
    a baseline frozen at review time. The published projection is append-only and carries the same expectation table,
    narrative and consensus, and its timestamp proves it predates the print.
    """
    from datetime import datetime

    wanted = _period_key(fiscal_label)
    limit = None
    if cutoff:
        try:
            limit = datetime.fromisoformat(str(cutoff).replace("Z", "+00:00"))
        except ValueError:
            limit = None
    for row in store.task_projection_envelopes(
            agent_role="fundamental_expectation_update", scope_kind="entity", scope_id=symbol.upper(), limit=500) or []:
        payload = row.get("payload") or {}
        detail = payload.get("baseline") or {}
        if (not str(payload.get("driver", "")).startswith("[基线") or not detail
                or _period_key(payload.get("period", "")) != wanted):
            continue
        if limit is not None:
            made = datetime.fromisoformat(str(row.get("created_at") or "").replace("Z", "+00:00"))
            if made.tzinfo is None:
                made = made.replace(tzinfo=timezone.utc)
            if made > limit:
                continue
        consensus = detail.get("consensus") or {}
        return {
            "narrative": str(detail.get("synthesis") or ""),
            "expectations": [
                {"dim_key": e.get("dim_key", ""), "metric": e.get("metric", ""), "neutral": e.get("neutral", ""),
                 "conservative": e.get("conservative", ""), "optimistic": e.get("optimistic", ""),
                 "source": e.get("source", "")} for e in detail.get("expectation_table") or []],
            "consensus": {f"consensus_{k}": v for k, v in consensus.items()
                          if k in {"eps", "revenue", "target_price", "eps_low", "eps_high", "revenue_low",
                                   "revenue_high"} and v is not None},
            "refs": {"baseline_projection_id": row.get("projection_id", ""),
                     "baseline_built_at": detail.get("built_at", ""), "baseline_published_at": row.get("created_at", "")}}
    return None


def _not_after(moment, cutoff: str) -> bool:
    try:
        limit = datetime.fromisoformat(str(cutoff).replace("Z", "+00:00"))
    except ValueError:
        return True
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment <= limit


def freeze_baseline(store, *, symbol: str, fiscal_label: str, cutoff: str = "") -> dict:
    """在 cutoff 冻结本标的本报告期的预期基线与其引用，写入 Workflow memory。

    冻结记录是一次性快照：事件评审的差异计算永远以它为参照，后续到达的
    简报、文档或重跑 prep 都不改写它。基线取财报前已发布的 `[基线 vN]`
    （评分阶段会覆盖档案里的预期集合，档案不能再当来源）；已冻结且有内容的记录
    原样沿用，读不到基线时不写空记录覆盖。
    """
    from ...schemas.pead import ExpectationSet

    symbol = symbol.upper()
    cutoff = cutoff or _now()
    existing = load_frozen_baseline(store, symbol=symbol, fiscal_label=fiscal_label)
    if existing and (existing.get("expectations") or existing.get("narrative")):
        return existing
    frozen = {"symbol": symbol, "fiscal_label": fiscal_label, "frozen_at": cutoff,
              "narrative": "", "expectations": [], "consensus": {}, "refs": {}}
    published = published_baseline(store, symbol=symbol, fiscal_label=fiscal_label, cutoff=cutoff)
    if published:
        frozen.update(published)
    else:
        dossier = store.get_dossier(symbol, fiscal_label)
        es = getattr(dossier, "expectation_set", None) if dossier else None
        # An expectation set built after the cutoff is not a baseline, whatever the dossier calls it.
        if (isinstance(es, ExpectationSet) and es.expectations
                and _not_after(es.as_of, cutoff)):
            frozen.update({
                "narrative": es.narrative,
                "expectations": [
                    {"dim_key": e.dim_key, "metric": e.metric, "neutral": e.neutral,
                     "conservative": e.conservative, "optimistic": e.optimistic, "source": e.source}
                    for e in es.expectations],
                "consensus": _consensus_view(es),
                "refs": {"dossier_symbol": symbol, "dossier_fiscal_label": fiscal_label,
                         "expectation_set_as_of": es.as_of.isoformat()}})
    if frozen["expectations"] or frozen["narrative"]:
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


from ..pead.units import (LOCAL_CURRENCY, mixes_currencies, quantity,  # noqa: E402,F401
                          _CURRENCIES, _SCALES, _currency_in)


# A reading more than 4x, or less than a quarter of, its expectation is, for a large company's revenue, margin or
# EPS, far more likely a currency, per-share-basis or scale mismatch than a real surprise (TSM EPS +561% on
# 2026-10-19: NT$ per share against a US$ per ADR consensus; revenue -97% from a NT$ against a US$ figure). It is
# reported as not comparable, not as a beat or a miss.
IMPLAUSIBLE_RATIO = (0.25, 4.0)


def _implausible(actual: float, expected: float) -> bool:
    if not expected:
        return False
    if actual > 0 and expected > 0:
        return not IMPLAUSIBLE_RATIO[0] <= actual / expected <= IMPLAUSIBLE_RATIO[1]
    return abs((actual - expected) / expected) > 1.0


def _comparable(a: tuple[float, str] | None, b: tuple[float, str] | None) -> str:
    """"" when the two quantities can be compared, else why not."""
    if a is None or b is None:
        return "一方没有写明币种或单位"
    if a[1] != b[1]:
        return f"币种或单位不同（{a[1]} vs {b[1]}）"
    return ""


def compute_tri_diffs(*, frozen_baseline: dict | None, expectation_set,
                      actuals, market_setup, symbol: str = "") -> dict:
    """分别对 冻结基线 / Consensus / 市场隐含预期 计算实际值差异。

    三个口径分列、各自带方向符号；方向不一致时保留分歧并说明，绝不取平均。
    只比较币种或单位一致的两个数；不一致的不算差异，列入 `not_comparable` 并写明原因。
    """
    vs_baseline: list[dict] = []
    not_comparable: list[dict] = []
    baseline_sign = 0
    # After the print the dossier's expectation set is gone; the consensus frozen with the baseline is the pre-print one.
    frozen_consensus = (frozen_baseline or {}).get("consensus") or {}
    if frozen_consensus and not (getattr(expectation_set, "consensus_eps", None)
                                 or getattr(expectation_set, "consensus_revenue", None)):
        from types import SimpleNamespace

        expectation_set = SimpleNamespace(symbol=symbol, **frozen_consensus)
    if frozen_baseline:
        actual_by_dim = {}
        if actuals is not None:
            for m in actuals.metrics:
                actual_by_dim[m.dim_key] = m.actual
        for e in frozen_baseline.get("expectations", []):
            dim = e.get("dim_key", "")
            if dim not in actual_by_dim or not str(e.get("neutral") or "").strip():
                continue
            if mixes_currencies(actual_by_dim[dim]):
                not_comparable.append({"scope": "baseline", "dim_key": dim,
                                       "reason": "实际值同时写了多个币种，其中一个是换算出来的，不据此比较"})
                continue
            neutral_q, actual_q = quantity(e.get("neutral")), quantity(actual_by_dim[dim])
            if neutral_q is None and actual_q is None:
                continue                                   # a qualitative dimension: the scorecard line carries it
            why = _comparable(neutral_q, actual_q)
            if why:
                not_comparable.append({"scope": "baseline", "dim_key": dim, "reason": why})
                continue
            delta = actual_q[0] - neutral_q[0]
            rel = delta / abs(neutral_q[0]) if neutral_q[0] else 0.0
            if _implausible(actual_q[0], neutral_q[0]):
                not_comparable.append({"scope": "baseline", "dim_key": dim, "reason": (
                    f"差异 {rel:+.0%} 过大（超出 -75%~+300%），更可能是币种、每股口径或量级不一致，不当作真实预期差")})
                continue
            vs_baseline.append({"dim_key": dim, "unit": neutral_q[1], "neutral": neutral_q[0],
                                "actual": actual_q[0], "delta": round(delta, 4), "delta_pct": round(rel, 4)})
            baseline_sign += 1 if rel > 0.01 else (-1 if rel < -0.01 else 0)
        baseline_sign = _sign(baseline_sign)

    consensus_sign = 0
    vs_consensus: dict = {}
    if actuals is not None and expectation_set is not None:
        reporting = str(getattr(actuals, "reporting_currency", "") or "").upper()
        local = LOCAL_CURRENCY.get((symbol or getattr(expectation_set, "symbol", "")).upper(), "USD")
        eps = getattr(actuals, "reported_eps", None)
        cons_eps_num = _number(getattr(expectation_set, "consensus_eps", None))
        if eps is not None and cons_eps_num:
            # analyst EPS consensus is quoted per US-listed share, in USD
            rel = (eps - cons_eps_num) / abs(cons_eps_num)
            if reporting == "USD" and not _implausible(eps, cons_eps_num):
                vs_consensus["eps"] = {"consensus": cons_eps_num, "reported": eps, "unit": "USD",
                                       "delta_pct": round(rel, 4)}
                consensus_sign = _sign(rel)
            elif reporting == "USD":
                not_comparable.append({"scope": "consensus_eps", "reason": (
                    f"实际 EPS 与共识相差 {rel:+.0%}，过大，更可能是每股口径（普通股 vs ADR）或币种不一致"),
                    "consensus": cons_eps_num, "reported": eps})
            else:
                not_comparable.append({"scope": "consensus_eps", "reason": (
                    f"共识 EPS 以美元计，实际 EPS 币种为 {reporting or '未写明'}，不换算、不比较"),
                    "consensus": cons_eps_num, "reported": eps})
        rev = getattr(actuals, "reported_revenue", None)
        cons_rev_num = _number(getattr(expectation_set, "consensus_revenue", None))
        revenue_text = next((m.actual for m in actuals.metrics if "revenue" in str(m.dim_key).lower()), "")
        if rev is not None and cons_rev_num and mixes_currencies(revenue_text):
            not_comparable.append({"scope": "consensus_revenue", "reason": (
                "实际营收的文字同时写了多个币种，其中一个是换算出来的，不据此与共识比较"),
                "consensus": cons_rev_num, "reported": rev})
        elif rev is not None and cons_rev_num:
            rel = (rev - cons_rev_num) / abs(cons_rev_num)
            if reporting == local and rev > 1e6 and not _implausible(rev, cons_rev_num):
                vs_consensus["revenue"] = {"consensus": cons_rev_num, "reported": rev, "unit": local,
                                           "delta_pct": round(rel, 4)}
            elif reporting == local and rev > 1e6:
                not_comparable.append({"scope": "consensus_revenue", "reason": (
                    f"实际营收与共识相差 {rel:+.0%}，过大，更可能是量级或币种不一致"),
                    "consensus": cons_rev_num, "reported": rev})
            else:
                not_comparable.append({"scope": "consensus_revenue", "reason": (
                    f"共识营收以本币 {local} 计，实际营收币种为 {reporting or '未写明'}"
                    + ("" if rev > 1e6 else "，且数值不是绝对金额")), "consensus": cons_rev_num, "reported": rev})

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
    elif em is not None:
        vs_market = {"expected_move_pct": em, "note": "无可比的 EPS 惊喜，期权隐含波幅仅作参照，未判断是否已在价内"}

    signs = {"vs_baseline": baseline_sign, "vs_consensus": consensus_sign,
             "vs_market": market_sign}
    disagree = len({v for v in signs.values() if v != 0}) > 1
    return {
        "vs_baseline": vs_baseline, "vs_consensus": vs_consensus, "vs_market": vs_market,
        "not_comparable": not_comparable[:12],
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


NO_COMPARISON_BASIS = ("【无比较依据】本期没有财报前冻结基线，也没有可用的共识：未做预期差判断，"
                       "direction=0 表示「无依据」而不是「符合预期」；以下仅为纪要与指引的提取。")
NO_BASIS_CONFIDENCE = 0.1


def actuals_view(actuals) -> dict | None:
    """What was reported, as the legacy report's section 3 showed it (headline numbers, per-dimension readings,
    call signals). The scenario tree and execution checklist of sections 5-6 are deliberately not carried: they name
    trade actions, which an event review may not."""
    if actuals is None:
        return None
    metrics = list(getattr(actuals, "metrics", None) or [])
    view = {
        "reported_eps": getattr(actuals, "reported_eps", None),
        "reported_revenue": getattr(actuals, "reported_revenue", None),
        "reporting_currency": str(getattr(actuals, "reporting_currency", "") or ""),
        "metrics": [{"dim_key": getattr(m, "dim_key", ""), "metric": str(getattr(m, "metric", ""))[:200],
                     "actual": str(getattr(m, "actual", ""))[:400],
                     "vs_expected": str(getattr(m, "vs_expected", ""))[:60],
                     "note": str(getattr(m, "note", ""))[:400]} for m in metrics[:16]],
        "transcript_signals": [str(x)[:300] for x in list(getattr(actuals, "transcript_signals", None) or [])[:8]],
        "transcript_source": str(getattr(actuals, "transcript_source", "") or "")[:200]}
    return view if (view["reported_eps"] is not None or view["reported_revenue"] is not None or view["metrics"]
                    or view["transcript_signals"]) else None


def baseline_reference(store, symbol: str, period: str) -> str:
    """The newest published `[基线 vN]` projection for the period: what the gap below was measured against."""
    rows = [row for row in (store.task_projection_envelopes(
        agent_role="fundamental_expectation_update", scope_kind="entity", scope_id=symbol.upper(), limit=500) or [])
        if (row.get("payload") or {}).get("period") == period
        and str((row.get("payload") or {}).get("driver", "")).startswith("[基线")]
    return rows[0]["projection_id"] if rows else ""


def build_event_review_payload(*, symbol: str, period: str, event: str,
                               scorecard, event_view: dict, tri_diffs: dict,
                               actuals, narrative: str = "", baseline_ref: str = "",
                               baseline_as_of: str = "") -> dict:
    lines = [line.model_dump(mode="json") for line in scorecard.lines] if scorecard else []
    notes = event_view.get("rationale", "")
    confidence = event_view.get("confidence")
    # Nothing to compare the actuals with (no frozen pre-print baseline and no consensus): the scorecard then reads
    # "no information, assume neutral" on every line. That is a gap, not a considered neutral view, and the Chief must
    # not read direction 0 as "in line with expectations" (found 2026-10-17 on TSM/ASML Q3, whose prints came before
    # the baseline mechanism existed). The extracted guidance and call signals are still published.
    no_basis = not (tri_diffs or {}).get("vs_baseline") and not (tri_diffs or {}).get("vs_consensus")
    if no_basis:
        notes = (NO_COMPARISON_BASIS + notes).strip()
        confidence = min(float(confidence if confidence is not None else NO_BASIS_CONFIDENCE), NO_BASIS_CONFIDENCE)
    payload = {
        "entity": symbol.upper(),
        "event": event,
        "period": period,
        "direction": int(event_view.get("direction", 0)),
        "magnitude": float(event_view.get("magnitude", 0.0)),
        "notes": notes,
        "scorecard": [
            {"section": "scorecard_lines", "lines": lines},
            {"section": "tri_diff", **(tri_diffs or {})},
        ],
        "guidance": (getattr(actuals, "guidance", "") or "") if actuals else "",
        "narrative": narrative,
        "confidence": confidence,
        "falsifiable_conditions": list(event_view.get("falsifiable_conditions") or []),
        "actuals": actuals_view(actuals),
        # `frozen_baseline` means the review was scored against a baseline built before the print, whether or not
        # any of its lines could be put into numbers (the unit-aware comparison leaves many as not_comparable).
        "comparison_basis": ("frozen_baseline" if ((tri_diffs or {}).get("vs_baseline") or baseline_ref)
                             else "consensus_only" if (tri_diffs or {}).get("vs_consensus") else "none"),
    }
    if baseline_ref:
        payload["baseline_ref"] = baseline_ref
    if baseline_as_of:
        payload["baseline_as_of"] = baseline_as_of
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
from ...workflow.runtime_reads import scoped_read  # noqa: E402


@scoped_read("fundamental")
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

    now = evaluation_now(timezone.utc)
    state = PeadState(symbol=request.symbol, fiscal_label=request.fiscal_label, phase="score", as_of=now,
                      use_llm=request.use_llm,
                      use_broker=False,
                      # A model-backed event review must read the quarter's documents; with live_data False the graph
                      # is offline, gets no transcript and no release, and every scorecard line comes back as
                      # "data missing" (found 2026-10-17 on TSM/ASML: the Dispatcher run was `complete` but empty).
                      # Offline stays the default only for runs without a model.
                      live_data=bool(request.extra.get("live_data", request.use_llm)),
                      dry_run=True, transcript_source=request.extra.get("transcript_source"))
    app = build_pead_graph(checkpointer=None)
    result = app.invoke(state, config={"configurable": {
        "thread_id": f"fundamental-event-{request.symbol}-{now:%Y%m%d%H%M%S}"}})
    return {"mode": "event", "symbol": request.symbol,
            "fiscal_label": request.fiscal_label, "frozen_at": frozen.get("frozen_at", ""),
            "event_review_summary": result.get("event_summary", {}),
            "fiscal_label_resolved": result.get("fiscal_label", "")}
