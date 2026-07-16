"""LLM-facing views for PEAD agents (no min/max; numerics clamped in code)."""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field, field_validator, model_validator


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [v] if v.strip() else []
    return v


def _as_objlist(v):
    """Coerce a list-of-objects field. Gemini-flash occasionally serializes the
    whole list as a JSON string ('[{...}, {...}]') instead of an actual array,
    which fails list validation and silently drops every row. Parse it back."""
    if v is None:
        return []
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        try:
            parsed = json.loads(s)
        except Exception:
            return []
        return parsed if isinstance(parsed, list) else [parsed]
    return v


def _split_jsonish_array(s: str) -> list[str] | None:
    """Best-effort parse of a '[...]'-looking string into a list of strings. Tries
    strict JSON first; on failure (models emit pseudo-JSON with UNESCAPED inner ASCII
    quotes, e.g. an item containing \"demand visibility\"), falls back to stripping the
    outer brackets and splitting on the '","' item boundary. Returns None if it doesn't
    look like an array at all."""
    s = s.strip()
    if not (s.startswith("[") and s.endswith("]")):
        return None
    try:
        parsed = json.loads(s)
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
    except Exception:
        pass
    inner = s[1:-1].strip()
    if not inner:
        return []
    parts = re.split(r'"\s*,\s*"', inner)          # item boundary: closing-quote , opening-quote
    cleaned = [p.strip().strip('"').strip() for p in parts]
    return [p for p in cleaned if p] or None


def _as_strlist(v):
    """Coerce a list-of-strings field. Models sometimes serialize the whole list as
    a JSON-array string ('[\"a\", \"b\"]') instead of an actual array — pydantic then
    keeps it as a single-element list holding the raw blob, so the report renders one
    giant item instead of an ordered list. Parse the array back into real strings."""
    if v is None:
        return []
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        arr = _split_jsonish_array(s)
        return arr if arr is not None else [s]
    # a one-element list whose sole item is itself a JSON-array string
    if isinstance(v, list) and len(v) == 1 and isinstance(v[0], str):
        arr = _split_jsonish_array(v[0])
        if arr is not None:
            return arr
    return v


_UNIT_MULT = {"trillion": 1e12, "tn": 1e12, "t": 1e12,
              "billion": 1e9, "bn": 1e9, "b": 1e9,
              "million": 1e6, "mn": 1e6, "m": 1e6,
              "thousand": 1e3, "k": 1e3}


def _coerce_num(v):
    """Coerce an LLM-formatted number to float. Handles currency symbols, commas,
    and scale words: '€7.35' -> 7.35, '€9.7 billion' -> 9.7e9, '$1,234M' -> 1.234e9.
    Returns None when there's no parseable number (keeps the field optional)."""
    if v is None or isinstance(v, (int, float)):
        return v
    s = str(v).strip()
    if not s:
        return None
    s = re.sub(r"[€$£¥,]", "", s).strip()
    m = re.match(r"^-?\d+(?:\.\d+)?", s)
    if not m:
        return None
    num = float(m.group())
    unit = s[m.end():].strip().lower()
    return num * _UNIT_MULT.get(unit, 1.0)


def _strip_xml_params(text: str) -> tuple[str, dict]:
    """Extract <parameter name="...">...</parameter> blocks from text.
    Returns (clean_text, {name: content})."""
    pattern = re.compile(r'</parameter>\s*<parameter name="([^"]+)">(.*?)(?=</parameter>|$)',
                         re.DOTALL)
    extracted = {}
    # Find the first </parameter> — everything before it is clean narrative
    first_close = text.find("</parameter>")
    if first_close == -1:
        return text, {}
    clean = text[:first_close].strip()
    tail = text[first_close:]
    for m in pattern.finditer(tail):
        extracted[m.group(1)] = m.group(2).strip()
    return clean, extracted


class NarrativeView(BaseModel):
    narrative: str = Field(description="core thesis: business drivers, key bottleneck, margin story")
    focus_ranking: list[str] = Field(default_factory=list, description="what matters most this quarter, ordered")
    valuation: str = Field(default="", description="PE / forward PE / ceiling-floor read")

    @field_validator("focus_ranking", mode="before")
    @classmethod
    def _cfr(cls, v):
        return _as_strlist(v)

    @model_validator(mode="before")
    @classmethod
    def _extract_embedded_params(cls, data):
        """If the LLM embeds focus_ranking/valuation as XML inside narrative, recover them."""
        if not isinstance(data, dict):
            return data
        narrative = data.get("narrative", "") or ""
        if "</parameter>" not in narrative:
            return data
        clean, extras = _strip_xml_params(narrative)
        data = dict(data)
        data["narrative"] = clean
        if not data.get("focus_ranking") and "focus_ranking" in extras:
            try:
                data["focus_ranking"] = json.loads(extras["focus_ranking"])
            except Exception:
                raw = extras["focus_ranking"].strip()
                if raw:
                    data["focus_ranking"] = [raw]
        if not data.get("valuation") and "valuation" in extras:
            data["valuation"] = extras["valuation"]
        return data

    @field_validator("focus_ranking", mode="before")
    @classmethod
    def _c(cls, v):
        return _as_list(v)


class FundamentalAnalysisView(BaseModel):
    background: str = Field(default="",
                            description="3-5 numbered bullets: moat, revenue driver, margin swing, structural risk, secular tailwind")
    peer_comparison: str = Field(default="",
                                 description="markdown table vs 1-2 key peers on this quarter's decisive dimensions")
    watch_metrics: str = Field(default="",
                               description="grouped markdown watch-list of this quarter's quantitative metrics")
    catalysts: list[str] = Field(default_factory=list, description="dated upcoming catalysts")
    key_risks: list[str] = Field(default_factory=list,
                                 description="thesis-invalidating, company-specific risks, ordered by severity")
    valuation: str = Field(default="",
                           description="trailing/forward PE + ceiling/floor multiples with implied prices")

    @field_validator("catalysts", "key_risks", mode="before")
    @classmethod
    def _c(cls, v):
        return _as_list(v)


class ExpectationRowView(BaseModel):
    dim_key: str
    metric: str = ""
    conservative: str = ""
    neutral: str = Field(default="", description="the base-case expectation")
    optimistic: str = ""
    source: str = ""


class ExpectationsView(BaseModel):
    rows: list[ExpectationRowView] = Field(default_factory=list)

    @field_validator("rows", mode="before")
    @classmethod
    def _c(cls, v):
        return _as_objlist(v)


class SignalItemView(BaseModel):
    symbol: str
    signal: str = Field(default="", description="one-line implication for the target")


class SignalChainView(BaseModel):
    items: list[SignalItemView] = Field(default_factory=list)
    summary: str = ""

    @field_validator("items", mode="before")
    @classmethod
    def _c(cls, v):
        return _as_objlist(v)


class ActualMetricView(BaseModel):
    dim_key: str
    metric: str = ""
    actual: str = ""
    vs_expected: str = Field(default="", description="远超/中性/低于 + 方向标记")
    note: str = ""


class ActualsView(BaseModel):
    reported_eps: float | None = None
    reported_revenue: float | None = None
    metrics: list[ActualMetricView] = Field(default_factory=list)
    guidance: str = Field(default="", description="forward guidance extracted from the text")
    transcript_signals: list[str] = Field(default_factory=list,
                                           description="key qualitative call signals")

    @field_validator("metrics", mode="before")
    @classmethod
    def _cm(cls, v):
        return _as_objlist(v)

    @field_validator("transcript_signals", mode="before")
    @classmethod
    def _c(cls, v):
        return _as_list(v)

    # LLMs often return currency-formatted strings ('€7.35', '€9.7 billion');
    # coerce rather than hard-fail the whole extraction (which zeroed the scorecard).
    @field_validator("reported_eps", "reported_revenue", mode="before")
    @classmethod
    def _num(cls, v):
        return _coerce_num(v)


class ScoreItemView(BaseModel):
    dim_key: str
    score: float = Field(description="-2 (far below) .. 0 (in line) .. +2 (far above expectations)")
    note: str = ""


class ScoresView(BaseModel):
    items: list[ScoreItemView] = Field(default_factory=list)

    @field_validator("items", mode="before")
    @classmethod
    def _c(cls, v):
        return _as_objlist(v)


class ExpectationChangeView(BaseModel):
    dim_key: str = ""
    change: str = ""


class ContextUpdateView(BaseModel):
    materiality: float = Field(description="0=noise .. 1=thesis-changing")
    event_summary: str = Field(default="", description="what's genuinely new since last update")
    narrative_delta: str = Field(default="", description="how the thesis changes; empty if nothing")
    expectation_changes: list[ExpectationChangeView] = Field(default_factory=list)


class TriageItemView(BaseModel):
    idx: int = Field(description="echo the input item's idx exactly")
    materiality: float = Field(default=0.0, description="0=noise .. 1=thesis-critical")
    category: str = ""
    reason: str = ""


class TriageBatchView(BaseModel):
    items: list[TriageItemView] = Field(default_factory=list)


class InsightItemView(BaseModel):
    ticker: str
    direction: str = Field(default="neutral", description="bullish | bearish | neutral")
    impact_path: str = Field(default="direct",
                             description="direct | supply_chain | competitive | demand | macro")
    summary: str = Field(default="", description="implication for THIS ticker, 1-2 sentences")
    evidence_quote: str = Field(default="", description="short verbatim quote from the article")
    confidence: float = Field(default=0.0, description="0-1")


class InsightBatchView(BaseModel):
    insights: list[InsightItemView] = Field(default_factory=list)
    article_gist: str = Field(default="", description="one sentence: what the article is about")
