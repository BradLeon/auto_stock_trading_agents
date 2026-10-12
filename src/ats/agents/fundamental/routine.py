"""例行模式：更新预期基线，不产出交易动作（Phase D task 5.2）。

例行模式在信息简报或预期数据发生有效变化时运行：
1. 读取上一有效基线（本标的 dossier 中的 expectation_set，属基本面自有记录）；
2. 把简报里的每条事实变化分为 确认 / 否定 / 新增 / 待验证 四类之一；
3. 对有数值读数的变化产出 `FundamentalExpectationUpdate` 投影（每条变化一份，
   归类结果随投影 driver 留痕，可事后复核）。

例行模式 SHALL NOT 产出可执行订单、仓位或数量建议——投影 payload 层面本就
没有这些字段，发布路径再拦截一次动作词表作为边界（与信息简报共用词表）。
"""

from __future__ import annotations

from ats.workflow.evaluation_clock import now as evaluation_now

import re
from datetime import datetime, timezone

from ...agent.task_projection import ProjectionScope, build_envelope
from ..information.briefs import assert_no_advice

log = __import__("logging").getLogger("ats.agents.fundamental.routine")

# 四类归类（5.2）。中文标签进入 driver 留痕。
CONFIRM, REFUTE, NEW, PENDING = "确认", "否定", "新增", "待验证"
CLASSIFICATIONS: tuple[str, ...] = (CONFIRM, REFUTE, NEW, PENDING)

# 数值偏离判定阈值（相对基线中性值的百分比）。落在 10% 内视为与基线一致，
# 偏离超过 25% 视为否定，中间地带归入待验证——宁可让人复核，不强行二值化。
CONFIRM_TOLERANCE = 0.10
REFUTE_TOLERANCE = 0.25

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _now() -> str:
    return evaluation_now(timezone.utc).isoformat(timespec="seconds")


def load_baseline(store, symbol: str) -> dict:
    """上一有效基线：本标的最新 dossier 的 expectation_set（自有记录，非跨角色）。"""
    from ...schemas.pead import ExpectationSet

    symbol = symbol.upper()
    for meta in store.recent_dossiers(symbol, limit=10):
        dossier = store.get_dossier(symbol, meta.get("fiscal_label", ""))
        es = getattr(dossier, "expectation_set", None) if dossier else None
        if not isinstance(es, ExpectationSet):
            continue
        import hashlib
        import json
        label = meta.get("fiscal_label", "")
        baseline_digest = hashlib.sha256(es.model_dump_json().encode()).hexdigest()
        baseline_refs = [f"pead-baseline:{symbol}:{meta.get('fiscal_label', '')}@{baseline_digest}"]
        if not label and getattr(dossier, "earnings_date", None):
            from ...data.fiscal import parse_label
            period = store.get_period(symbol, dossier.earnings_date)
            if period and parse_label(period.get("fiscal_label", "")) == (period.get("year"), period.get("quarter")) and all(
                    period.get(key) for key in ("year", "quarter", "fiscal_label", "detected_at")):
                known = datetime.fromisoformat(period["detected_at"].replace("Z", "+00:00"))
                if known <= evaluation_now(timezone.utc):
                    label = period["fiscal_label"]
                    digest = hashlib.sha256(json.dumps(period, sort_keys=True, default=str).encode()).hexdigest()
                    baseline_refs.append(f"pead-period:{symbol}:{period['earnings_date']}@{digest}")
        metrics: dict[str, float] = {}
        for e in es.expectations:
            neutral_value = _first_number(e.neutral)
            if neutral_value is not None:
                metrics[e.dim_key] = neutral_value
        return {
            "symbol": symbol, "fiscal_label": label, "baseline_refs": baseline_refs,
            "narrative": es.narrative, "metrics": metrics,
            "dim_keys": [e.dim_key for e in es.expectations],
            "kpi_words": _kpi_words(es),
        }
    return {"symbol": symbol, "fiscal_label": "", "narrative": "",
            "metrics": {}, "dim_keys": [], "kpi_words": set()}


def _kpi_words(es) -> set[str]:
    """基线里出现的可匹配词元：dim_key 本身 + 其分词 + metric 词。"""
    words: set[str] = set()
    for e in es.expectations:
        words.add(e.dim_key.lower())
        words.update(tok for tok in e.dim_key.lower().split("_") if len(tok) >= 3)
        if e.metric:
            words.add(str(e.metric).lower())
    return words


def _first_number(text: str) -> float | None:
    if not text:
        return None
    hit = _NUM_RE.search(str(text).replace(",", ""))
    return float(hit.group(0)) if hit else None


def _change_metric_number(change: str) -> float | None:
    """事实变化文本里携带的数值读数（第一个数字）。"""
    # Information prefixes publish dates; the year isn't a business reading.
    return _first_number(re.sub(r"^\s*\[\d{4}-\d{2}-\d{2}\]\s*", "", change))


def _metric_number_for(change: str, metric: str) -> float | None:
    """Accept only a local reading of a named KPI, never a headline's first number.

    Without a unit contract, currency/scaled quantities cannot be compared with
    a bare baseline float. Preserve those changes as qualitative evidence.
    """
    if metric == "narrative":
        return None
    text = re.sub(r"^\s*\[\d{4}-\d{2}-\d{2}\]\s*", "", change)
    tokens = [metric, *(t for t in metric.split("_") if len(t) >= 3)]
    match = next((m for token in tokens if (m := re.search(
        r"(?<![A-Za-z0-9_])"+re.escape(token)+r"(?![A-Za-z0-9_])", text, re.I))), None)
    if match is None:
        return None
    reading = _NUM_RE.search(text, match.end())
    if reading is None or reading.start()-match.end() > 40:
        return None
    bridge = text[match.end():reading.start()]
    suffix = text[reading.end():]
    if (re.search(r"[$€£]|\b(?:USD|EUR|GBP)\b", bridge, re.I)
            or re.match(r"\s*(?:million|billion|trillion|万亿|百万|十亿|亿|万|[BMT]\b)", suffix, re.I)):
        return None
    number = float(reading.group())
    if 1900 <= abs(number) <= 2100 or re.match(r"[-/]\d", suffix):
        return None  # dates/years are not readings
    if re.match(r"\s*%", suffix) and not any(
            token in metric.lower() for token in ("margin", "growth", "rate", "pct", "percent")):
        return None
    return number


def _mentions_baseline(change: str, baseline: dict) -> bool:
    text = change.lower()
    words = baseline.get("kpi_words") or set()
    return any(w in text for w in words)


def classify_change(change: str, baseline: dict) -> str:
    """把一条事实变化归入四类之一（确定性规则；LLM 路径见 classify_changes）。

    规则（按序命中）：
    1. 不涉及基线任何 KPI/维度词 → 新增；
    2. 涉及基线且带数值读数：与基线中性值偏离 ≤10% → 确认；
       偏离 ≥25% → 否定；中间地带 → 待验证；
    3. 涉及基线但无数值 → 待验证。
    """
    if not _mentions_baseline(change, baseline):
        return NEW
    metrics: dict[str, float] = baseline.get("metrics") or {}
    number = _metric_number_for(change, _metric_key_for(change, metrics))
    if number is None or not metrics:
        return PENDING
    reference = _reference_value(change, metrics)
    if reference is None or reference == 0:
        return PENDING
    deviation = abs(number - reference) / abs(reference)
    if deviation <= CONFIRM_TOLERANCE:
        return CONFIRM
    if deviation >= REFUTE_TOLERANCE:
        return REFUTE
    return PENDING


def _reference_value(change: str, metrics: dict[str, float]) -> float | None:
    """变化文本提到的维度里，取第一个有中性值的作为参照。"""
    text = change.lower()
    for key, value in metrics.items():
        if key.lower() in text or any(
                tok in text for tok in key.lower().split("_") if len(tok) >= 3):
            return value
    return None


def classify_changes(changes: list[str], baseline: dict, *, use_llm: bool = True) -> list[str]:
    """整批归类。use_llm 时先走 LLM 判断，失败或不可用时回退确定性规则。"""
    if not changes:
        return []
    if use_llm:
        try:
            labels = _classify_with_llm(changes, baseline)
            if labels is not None:
                return labels
        except Exception as exc:  # noqa: BLE001 - LLM 失败回退规则，不中断
            log.warning("routine classify LLM failed, falling back to rules: %s", exc)
    return [classify_change(c, baseline) for c in changes]


def _classify_with_llm(changes: list[str], baseline: dict) -> list[str] | None:
    from ..base import run_structured
    from .outputs import ClassificationView

    metrics = baseline.get("metrics") or {}
    metric_lines = "\n".join(f"  - {k}: neutral={v}" for k, v in metrics.items()) or "  (none)"
    lines = "\n".join(f"  [{i}] {c}" for i, c in enumerate(changes))
    ctx = (
        "Classify each fact change against the current expectation baseline.\n"
        f"Baseline metrics:\n{metric_lines}\nBaseline narrative:\n{baseline.get('narrative', '')}\n\n"
        "Fact changes:\n" + lines +
        "\n\nFor each item output classification exactly one of: 确认 | 否定 | 新增 | 待验证. "
        "确认 = consistent with the baseline expectation; 否定 = contradicts it; "
        "新增 = a subject the baseline does not track; 待验证 = related but not yet decidable."
    )
    view: ClassificationView = run_structured(
        "fundamental_analyst", ClassificationView, ctx, skill_slug="fundamental-routine")
    out: list[str] = []
    for item in view.items:
        label = (item.classification or "").strip()
        out.append(label if label in CLASSIFICATIONS else PENDING)
    return out if len(out) == len(changes) else None


def build_expectation_updates(changes: list[str], classifications: list[str],
                              baseline: dict, *, period: str = "") -> list[dict]:
    """每条归类变化一份 FundamentalExpectationUpdate payload 草稿。

    已绑定 KPI 且单位可比较的数值读数才更新数值；纯定性/未绑定/单位不明
    的变化保留完整分类与 driver，metric=narrative、两个数值均为 null。
    """
    metrics: dict[str, float] = baseline.get("metrics") or {}
    period = period or baseline.get("fiscal_label", "") or "current"
    drafts: list[dict] = []
    for change, label in zip(changes, classifications):
        metric_key = _metric_key_for(change, metrics)
        number = _metric_number_for(change, metric_key)
        reference = _reference_value(change, metrics)
        if number is None:
            # A classified narrative is useful evidence but no measurable update.
            metric_key, previous, new_value = "narrative", None, None
        else:
            previous = reference
            new_value = number if label in (CONFIRM, REFUTE, NEW) else reference
            if new_value is None:
                metric_key, previous = "narrative", None
        drafts.append({
            "entity": baseline.get("symbol", ""),
            "metric": metric_key,
            "period": period,
            "previous_value": previous,
            "new_value": new_value,
            "driver": f"[{label}] {change}",
        })
    return drafts


def _metric_key_for(change: str, metrics: dict[str, float]) -> str:
    text = change.lower()
    for key in metrics:
        if key.lower() in text or any(
                tok in text for tok in key.lower().split("_") if len(tok) >= 3):
            return key
    return "narrative"


def publish_expectation_update(store, payload: dict, *, as_of: str = "",
                               input_refs=None, use_llm: bool = True) -> str:
    """校验并落一份 FundamentalExpectationUpdate 投影。动作词表先拦截。"""
    assert_no_advice([payload.get("driver", "")])
    from ...workflow.decision_requirements import validate_routine_value_contract
    from ...workflow.cutover_routing import RouteUnavailable
    from ...agent.task_projection import EnvelopeValidationError
    try:
        validate_routine_value_contract(payload)
    except RouteUnavailable as exc:
        raise EnvelopeValidationError(str(exc)) from exc
    from ..base import execution_versions
    versions = execution_versions("fundamental_analyst", "fundamental-routine", use_llm=use_llm,
                                  template_source=__file__)
    envelope = build_envelope(
        role="fundamental_expectation_update", payload=payload,
        scope=ProjectionScope(kind="entity", id=str(payload.get("entity", "")).upper()),
        as_of=as_of or _now(), input_refs=input_refs, **versions)
    store.save_task_projection_envelope(envelope)
    return envelope.projection_id


from ...workflow.runtime_reads import scoped_read  # noqa: E402


@scoped_read("fundamental")
def run_routine_pass(request) -> dict:
    """例行模式一轮：归类 → 发布预期更新投影。独立终结。"""
    from ...memory import get_store

    store = get_store()
    baseline = load_baseline(store, request.symbol)
    from ...workflow.consumer_reads import read_projection

    brief = read_projection(store, consumer="fundamental", role="information_brief",
                            scope=ProjectionScope(kind="entity", id=request.symbol))
    briefs = [brief] if brief else []
    if not briefs:
        return {"mode": "routine", "symbol": request.symbol,
                "classified": [], "published": [],
                "note": "no information brief available; routine pass is a no-op"}
    if not baseline.get("fiscal_label") or not (baseline.get("narrative") or baseline.get("dim_keys")):
        raise RuntimeError("Routine requires a valid labelled previous expectation baseline")
    payload = briefs[0].get("payload") or {}
    changes = list(payload.get("fact_changes") or [])
    labels = classify_changes(changes, baseline, use_llm=request.use_llm)
    drafts = build_expectation_updates(changes, labels, baseline)
    published: list[str] = []
    for draft in drafts:
        published.append(publish_expectation_update(
            store, draft, input_refs=[briefs[0].get("projection_id", ""), *baseline.get("baseline_refs", [])],
            use_llm=request.use_llm))
    summary = {label: labels.count(label) for label in CLASSIFICATIONS if labels.count(label)}
    return {"mode": "routine", "symbol": request.symbol,
            "classified": list(zip(changes, labels)), "published": published,
            "summary": summary}
