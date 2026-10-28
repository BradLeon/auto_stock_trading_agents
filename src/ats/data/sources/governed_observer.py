"""Governed Evidence observers as third-party evidence sources for the Chain engine.

The three L1 observers (AI production adoption, frontier-lab commercialisation, raw capability) already read governed
datasets through DataProducts and already apply their own, versioned trend rules.  This adapter does not recompute any
of that.  It reads each observer's packet and turns every governed reading into one `SeriesPoint` that arrives already
interpreted -- speaker (entity, stance), concept, direction and a deterministic, re-checkable evidence line -- so the
claim engine (corroboration, stance classes, adjudication, assessments) treats it exactly like a customs series.

    direction   from the observer's own status:  expanding -> up, contracting -> down, stable/mixed -> flat
    no reading  `insufficient_history` / `unavailable` produce NO point: a gap is not "flat"

Nothing here calls a model, and nothing here invents a period or a value: every figure and period comes from the
packet's governed fields.
"""

from __future__ import annotations

from typing import Any, Callable

from ...schemas.chain import SeriesPoint

PRODUCTION = "ai_core_production_workflow_penetration"
COMMERCIALIZATION = "ai_frontier_labs_commercialization"
RAW_CAPABILITY = "ai_frontier_raw_capability"

_DIRECTION = {"expanding": "up", "confirmed_expansion": "up", "provisional_expansion": "up",
              "crossed": "up", "contracting": "down", "stable": "flat", "mixed": "flat",
              "not_crossed": "flat"}

# axis id -> (data provider entity, concept, stance, observation type)
PRODUCTION_AXES = {
    "enterprise_breadth": ("US_CENSUS_BTOS", "enterprise_ai_adoption_breadth", "regulator", "regulatory"),
    "worker_persistence": ("US_RPS_GENAI", "worker_ai_usage_persistence", "regulator", "regulatory"),
    # Ramp and Anthropic report facts about their own networks (reported_actual, a PRIMARY type): the stance is the
    # position of the speaker, customer-side payments vs provider-side usage telemetry.
    "paid_procurement": ("RAMP_AI_INDEX", "paid_ai_procurement", "customer", "reported_actual"),
    "task_production": ("ANTHROPIC_ECON_INDEX", "task_production_depth", "supplier", "reported_actual"),
}
# revenue identity -> (stance, observation type): who is speaking about the lab's revenue
REVENUE_IDENTITY = {
    "company_reported": ("incumbent", "reported_actual"),
    "media_reported": ("regulator", "media"),
    "third_party_estimate": ("regulator", "research"),
}
REVENUE_SOURCES = {"tickertrends_public_research": "TICKERTRENDS", "sacra_public_company_profiles": "SACRA"}


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _manifest(packet: dict[str, Any]) -> str:
    manifest = packet.get("snapshot_manifest") or packet.get("manifest") or {}
    return str(manifest.get("snapshot_id") or manifest.get("manifest_id") or manifest.get("id") or "")[:12]


def production_points(packet: dict[str, Any]) -> list[SeriesPoint]:
    points: list[SeriesPoint] = []
    manifest = _manifest(packet)
    for axis in packet.get("axis_overview") or []:
        spec = PRODUCTION_AXES.get(str(axis.get("axis_id") or ""))
        direction = _DIRECTION.get(str(axis.get("trend_status") or ""))
        value = _num(axis.get("headline_value"))
        if spec is None or direction is None or value is None:
            continue                                      # no governed trend yet: a gap, not a flat reading
        entity, concept, stance, kind = spec
        unit = str(axis.get("headline_unit") or "")
        period = str(axis.get("period") or "")
        label = f"W{period}" if str(axis.get("axis_id")) == "enterprise_breadth" and period.isdigit() else period
        net = _num(axis.get("trend_net_change_pp"))
        change = f"，期内净变化 {net:+.2f}pp" if net is not None else ""
        span = (f"{label} {axis.get('axis_label')}（{axis.get('source_id')}）{value:g}{unit and ' ' + unit}，"
                f"趋势 {axis.get('trend_status')}{change}；统计主体：{axis.get('statistical_unit')}；"
                f"分母：{axis.get('denominator')}"
                + (f"（manifest {manifest}）" if manifest else ""))
        points.append(SeriesPoint(period=label, series=str(axis["axis_id"]), value=value, unit=unit,
                                  entity=entity, stance=stance, observation_type=kind, concept=concept,
                                  direction=direction, evidence_span=span))
    return points


def commercialization_points(packet: dict[str, Any]) -> list[SeriesPoint]:
    points: list[SeriesPoint] = []
    manifest = _manifest(packet)
    for company in packet.get("companies") or []:
        trend = company.get("trend") or {}
        direction = _DIRECTION.get(str(trend.get("status") or ""))
        # The trend belongs to the comparison cell (one identity, one methodology), so the figure quoted next to it is
        # that cell's newest point -- not the company headline, which may be a different identity (a media quote).
        history = [row for row in company.get("history_rows") or [] if _num(row.get("value_usd_bn")) is not None]
        record = max(history, key=lambda row: (str(row.get("period_start") or ""), str(row.get("period") or "")),
                     default={})
        value = _num(record.get("value_usd_bn"))
        if direction is None or value is None:
            continue
        identity = str(record.get("observation_identity") or "")
        stance, kind = REVENUE_IDENTITY.get(identity, ("regulator", "research"))
        speaker = REVENUE_SOURCES.get(str(record.get("source_id") or ""), "")
        entity = str(company.get("entity_id") or "").upper()
        qualifier = f"（{record.get('value_qualifier')}）" if record.get("value_qualifier") else ""
        span = (f"{record.get('period')} {company.get('company')} 年化收入运行率 {value:g} USD bn{qualifier}，"
                f"口径：{record.get('observation_identity_label') or identity}（{record.get('source_label')}）；"
                f"可比序列 {trend.get('comparable_point_count')} 个披露点（{trend.get('first_period')}→"
                f"{trend.get('last_period')}），趋势 {trend.get('status')}；缺失月份不插值"
                + (f"（manifest {manifest}）" if manifest else ""))
        points.append(SeriesPoint(period=str(record.get("period") or ""), series=f"revenue_{entity.lower()}",
                                  value=value, unit="USD bn", entity=entity, source_entity=speaker,
                                  stance=stance, observation_type=kind, concept="frontier_revenue_scale_trend",
                                  direction=direction, evidence_span=span))
    routed = packet.get("openrouter") or {}
    facts = routed.get("facts") or {}
    trend = facts.get("trend") or {}
    tokens = _num(facts.get("latest_total_tokens"))
    if tokens is not None and trend.get("status") and facts.get("latest_complete_week"):
        change = _num(trend.get("change_rate"))
        move = f"，最近 4 周均值较前 4 周 {change * 100:+.1f}%" if change is not None else ""
        span = (f"{facts['latest_complete_week']} 周 OpenRouter 公共路由 token {tokens / 1e12:.1f} 万亿{move}，"
                f"趋势 {trend.get('status')}；只表示路由用量与竞争格局，不是全市场用量或收入"
                + (f"（manifest {manifest}）" if manifest else ""))
        points.append(SeriesPoint(
            period=str(facts["latest_complete_week"]), series="routed_tokens", value=tokens, unit="tokens",
            entity="OPENROUTER", stance="customer", observation_type="reported_actual", concept="routed_token_usage",
            direction=_DIRECTION.get(str(trend.get("status")), "flat"), evidence_span=span))
    return points


def raw_capability_points(packet: dict[str, Any]) -> list[SeriesPoint]:
    manifest = _manifest(packet)
    a, b = packet.get("a") or {}, packet.get("b") or {}
    dates = [str((item.get("current") or {}).get("release_date") or "")
             for item in list(a.get("benchmarks") or []) + list(b.get("benchmarks") or [])]
    period = max((d for d in dates if d), default="")[:7]
    if not period:
        return []
    points: list[SeriesPoint] = []
    a_expanded = sum(1 for item in a.get("benchmarks") or []
                     if item.get("status") in {"confirmed_expansion", "provisional_expansion"})
    b_confirmed = sum(1 for item in b.get("benchmarks") or [] if item.get("status") == "confirmed_crossing")
    b_provisional = sum(1 for item in b.get("benchmarks") or [] if item.get("status") == "provisional_crossing")
    for status, concept, series, value, text in (
            (a.get("status"), "capability_frontier_extension", "frontier_extension", float(a_expanded),
             f"A：{a_expanded} 项 benchmark 出现可比前沿外扩"),
            (b.get("status"), "capability_threshold_crossing", "threshold_crossing",
             float(b_confirmed + b_provisional),
             f"B：{b_confirmed} 项确认跨越、{b_provisional} 项仅点估计暂定跨越")):
        direction = _DIRECTION.get(str(status or ""))
        if direction is None:
            continue
        span = (f"{period} 前沿能力 benchmark（{packet.get('claim_id')}）{text}，规则状态 {status}；"
                "benchmark 是能力的量化 proxy，不是综合智能排名；缺失不是零"
                + (f"（manifest {manifest}）" if manifest else ""))
        points.append(SeriesPoint(period=period, series=series, value=value, unit="benchmarks",
                                  entity="FRONTIER_BENCHMARKS", stance="regulator", observation_type="research",
                                  concept=concept, direction=direction, evidence_span=span))
    return points


CONVERTERS: dict[str, Callable[[dict[str, Any]], list[SeriesPoint]]] = {
    PRODUCTION: production_points,
    COMMERCIALIZATION: commercialization_points,
    RAW_CAPABILITY: raw_capability_points,
}


def _observe(observer: str) -> dict[str, Any]:
    """Run the observer exactly as the layer configures it (sections, primary sources, definition version)."""
    from ...agents.evidence import layer_runner
    from ...config import load_sector_config

    layer = next(item for item in load_sector_config("ai_hardware").layers if item.key == "L1_app")
    runners, error = layer_runner._configured_observers(list(layer.evidence_observers))
    if error or observer not in runners:
        return {"status": "unavailable", "reason": (error or {}).get("reason") or f"{observer} 未在 L1 配置启用"}
    return runners[observer](periods=None, as_of=None, chart_dir="", products=None,
                             workflow_scope={"sector": "ai_hardware", "layer": "L1_app"})


def fetch(*, lookback_months: int = 6, observer: str, **_ignored: Any) -> list[SeriesPoint]:
    """Run one governed observer and return its readings as interpreted points ([] when it cannot be read)."""
    if observer not in CONVERTERS:
        raise ValueError(f"unknown governed observer {observer!r}")
    packet = _observe(observer)
    if packet.get("status") != "ok":
        return []
    return CONVERTERS[observer](packet)
