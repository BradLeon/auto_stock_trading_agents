"""Frontier AI Labs revenue observation adapters.

Two governed public sources feed one dataset:

* ``sacra_public_company_profiles`` — periodic discovery of the two registered
  Sacra public company pages.  One probe touches each page exactly once and the
  raw HTML is persisted as a constrained snapshot artifact; parsing, ingestion
  and reporting then reuse that artifact offline.
* ``tickertrends_public_research`` — the same research object seen from a second
  angle: one public Substack research essay, re-checked on the same 7-day beat
  as the Sacra profiles.  The probe fingerprints the numeric claims it admits,
  not the page bytes, because Substack re-serialises ``body_html`` between
  fetches; the steady state is therefore ``no_change`` and markup churn can
  never re-baseline the main sequence.  The versioned JSON seed stays as the
  offline route used by tests and by governed replay.

Neither source contacts a paid API, an MCP connector or any authenticated
export.  Paywalled cells are server-side redacted upstream (``—``), so their
absence is recorded as a coverage gap, never as a zero revenue observation.

Every candidate keeps its own measurement identity.  Nothing here converts an
annualized run rate into reported ARR, and nothing turns a projection into a
historical actual.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import html
import json
import re
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from ..core.structured_models import (
    AdapterArtifact,
    AdapterBatch,
    AdapterFailure,
    DiscoveryResult,
    DiscoveryStatus,
    FetchRequest,
    IngestionStatus,
    NativeRecord,
    ReleaseCandidate,
)

DATASET_ID = "frontier_ai_labs_revenue"

SACRA_SOURCE_ID = "sacra_public_company_profiles"
TICKERTRENDS_SOURCE_ID = "tickertrends_public_research"

SACRA_PARSER_VERSION = "sacra_public_company_profiles/v1"
TICKERTRENDS_PARSER_VERSION = "tickertrends_public_research/v1"

# One measurement regime per (metric family, observation identity) combination.
# Both sources publish third-party annualized run-rate estimates, so they share
# a regime and may therefore be compared; a change in either estimator's own
# method bumps the regime and starts a new trend cell instead of silently
# extending the old one.
METHODOLOGY_REGIME_THIRD_PARTY_ESTIMATE = "third_party_annualized_run_rate/v1"
METHODOLOGY_REGIME_COMPANY_REPORTED = "company_reported_run_rate/v1"
METHODOLOGY_REGIME_PROJECTION = "forward_revenue_projection/v1"
METHODOLOGY_REGIME_TRAILING = "third_party_trailing_revenue/v1"

OBSERVATION_IDENTITY_COMPANY = "company_reported"
OBSERVATION_IDENTITY_MEDIA = "media_reported"
OBSERVATION_IDENTITY_ESTIMATE = "third_party_estimate"
OBSERVATION_IDENTITY_PROJECTION = "projection"
OBSERVATION_IDENTITIES = (
    OBSERVATION_IDENTITY_COMPANY,
    OBSERVATION_IDENTITY_MEDIA,
    OBSERVATION_IDENTITY_ESTIMATE,
    OBSERVATION_IDENTITY_PROJECTION,
)

METRIC_REPORTED_ARR = "ai.frontier_lab.reported_arr"
METRIC_ANNUALIZED_RUN_RATE = "ai.frontier_lab.annualized_revenue_run_rate"
METRIC_TRAILING_REVENUE = "ai.frontier_lab.trailing_revenue"
METRIC_FORWARD_PROJECTION = "ai.frontier_lab.forward_revenue_projection"

TICKERTRENDS_ACCEPTED_WINDOW = ("2026-01-01", "2026-06-30")
TICKERTRENDS_ARTICLE_SLUG = "anthropic-vs-openai-arr-tracking"
# The essay is hosted on Substack, whose public post API returns the same
# ``body_html`` the rendered page embeds.  It is unauthenticated, costs nothing
# and is the only route this source is allowed to use.
TICKERTRENDS_POST_API_TEMPLATE = "https://blog.tickertrends.io/api/v1/posts/{slug}"
TICKERTRENDS_POST_API_URL = TICKERTRENDS_POST_API_TEMPLATE.format(
    slug=TICKERTRENDS_ARTICLE_SLUG)
# Provenance label for where a probe's bytes came from.  It is recorded next to
# the parsed report so a live probe and an offline replay are distinguishable.
TICKERTRENDS_ORIGIN_LIVE = "live_post_api"
TICKERTRENDS_ORIGIN_SEED = "frozen_seed"
# Sacra writes its own estimate in prose; the citation chain still has to name it
# rather than leaving the paragraph unattributed.
SACRA_PUBLISHER = "Sacra"
TICKERTRENDS_DEFAULT_SEED_FILENAME = (
    "tickertrends_anthropic_vs_openai_arr_tracking.json")

SACRA_PAGES: tuple[dict[str, str], ...] = (
    {"slug": "openai", "entity_id": "OPENAI", "company": "OpenAI",
     "url": "https://sacra.com/c/openai/"},
    {"slug": "anthropic", "entity_id": "ANTHROPIC", "company": "Anthropic",
     "url": "https://sacra.com/c/anthropic/"},
)

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

COMPANY_ALIASES: dict[str, tuple[str, ...]] = {
    "OPENAI": ("OpenAI", "ChatGPT"),
    "ANTHROPIC": ("Anthropic", "Claude"),
}

# Sentences whose local context names a cost, financing, ratio or market
# structure item rather than a company revenue level are diagnosed, never
# published as observations.
_NON_REVENUE_CONTEXT = (
    "inference cost", "compute cost", "training cost", "capex", "capital expenditure",
    "cash burn", "cash flow", "burn", "gross margin", "operating margin", "margin",
    "net loss", "loss", "expense", "expenses", "valuation", "funding round",
    "secondary sale", "stake", "credit facility", "debt", "spend", "spending",
    "gap of", "times OpenAI", "multiple", "per month", "monthly", "weekly active",
)
_REVENUE_LABEL = ("revenue", "ARR", "run rate", "run-rate", "top-line", "top line", "sales")
# A run-rate estimate written as "ARR" is still a run rate, never contract ARR.
_RUN_RATE_CUES = ("annualized revenue", "run rate", "run-rate", "tracked ARR",
                  "tracking", "tracked", "private-market tracking", "tracks")
_PROJECTION_TOKENS = (
    "expects", "expected", "expect", "projects", "projected", "projection",
    "targets", "target", "targeting", "on pace", "forecast", "forecasts",
    "anticipates", "anticipated", "guidance", "aims", "would reach", "could reach",
)
_COMPANY_REPORTED_TOKENS = (
    "announced", "disclosed", "revealed", "stated", "told employees", "confirmed",
    "reported by the company", "according to the company", "chief financial officer",
    "CFO Sarah", "CFO ", "company said", "said it", "says it",
)
_MEDIA_TOKENS = (
    "according to The Information", "The Information", "Reuters", "Bloomberg",
    "TechCrunch", "Fortune", "Financial Times", "Wall Street Journal", "WSJ",
    "Axios", "CNBC", "Business Insider", "Forbes",
)
# Product-line revenue is real evidence but belongs to a different series
# identity.  It is recorded as a diagnostic and never summed into the
# company-level sequence.
_PRODUCT_SCOPE_TOKENS = (
    "Claude Code", "Codex", "ChatGPT", "ads business", "advertising business",
    "API business", "enterprise business", "Claude mobile app", "mobile app",
)
_MONTH_NAMES = {name: index for index, name in enumerate(calendar.month_name) if name}
_MONTH_ALT = {"Sept": 9}
_MONTH_PATTERN = "|".join(sorted(_MONTH_NAMES, key=len, reverse=True) + sorted(_MONTH_ALT))

_MULTIPLIERS = {
    "": 1.0, "thousand": 1e3, "k": 1e3, "million": 1e6, "m": 1e6, "mm": 1e6,
    "billion": 1e9, "b": 1e9, "bn": 1e9, "trillion": 1e12, "t": 1e12,
}

_MONEY_RE = re.compile(
    r"\$\s?([0-9][0-9,]*(?:\.[0-9]+)?)\s?"
    r"(trillion|billion|million|thousand|bn|[TBMK]\b)?")
_MONTH_YEAR_RE = re.compile(rf"\b({_MONTH_PATTERN})\.?(?:\s+(20\d\d))?\b")
_QUARTER_RE = re.compile(r"\bQ([1-4])\s+(20\d\d)\b")
_QUARTER_WORD_RE = re.compile(r"\b(First|Second|Third|Fourth)\s+quarter\s+(20\d\d)\b")
_END_OF_YEAR_RE = re.compile(r"\bend of\s+(20\d\d)\b")
_BARE_YEAR_RE = re.compile(r"\b(20\d\d)\b")

# A company revenue level this small could only be a per-customer, per-seat or
# per-month figure; treating it as a company total would be a unit error.
MINIMUM_COMPANY_REVENUE_USD = 100_000_000.0

CHART_READING_PROHIBITED = (
    "chart-derived values are prohibited; only amounts explicitly written in "
    "the body text are admitted")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(payload: str | bytes) -> str:
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


# --------------------------------------------------------------------------- #
# Candidate model
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class RevenueCandidate:
    """One explicitly written revenue figure plus the reasons it may be refused."""

    entity_id: str
    provider_field: str
    metric_id: str
    observation_identity: str
    methodology_regime: str
    raw_metric_label: str
    period: str
    period_start: str
    period_end: str
    period_basis: str
    value: float
    currency: str = "USD"
    raw_quote: str = ""
    publisher_url: str = ""
    origin_citation: dict[str, Any] = field(default_factory=dict)
    extraction_method: str = ""
    slice_key: str = ""
    product_scope: str = ""
    published_at: datetime | None = None
    diagnostics: tuple[str, ...] = ()
    period_year_source: str = ""

    @property
    def accepted(self) -> bool:
        return not self.diagnostics

    def as_native_record(self) -> NativeRecord:
        return NativeRecord(
            entity_id=self.entity_id,
            provider_field=self.provider_field,
            period=self.period,
            period_start=self.period_start,
            period_end=self.period_end,
            period_basis=self.period_basis,
            value=self.value,
            unit="USD",
            currency=self.currency,
            adjustment="",
            published_at=self.published_at,
            dimensions={
                "observation_identity": self.observation_identity,
                "raw_metric_label": self.raw_metric_label,
                "methodology_regime": self.methodology_regime,
            },
            raw={
                "raw_metric_label": self.raw_metric_label,
                "raw_quote": self.raw_quote,
                "publisher_url": self.publisher_url,
                "origin_citation": self.origin_citation,
                "extraction_method": self.extraction_method,
                "observation_identity": self.observation_identity,
                "methodology_regime": self.methodology_regime,
                "period_year_source": self.period_year_source,
                "reference_period": {
                    "period": self.period,
                    "period_start": self.period_start,
                    "period_end": self.period_end,
                    "period_basis": self.period_basis,
                },
            },
            slice_key=self.slice_key,
        )


# --------------------------------------------------------------------------- #
# HTML helpers
# --------------------------------------------------------------------------- #

_SCRIPT_RE = re.compile(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", re.S | re.I)
_FIGURE_RE = re.compile(r"<(figure|img|picture|canvas)[^>]*>.*?</\1>", re.S | re.I)
_INLINE_BREAK_RE = re.compile(r"<br\s*/?>", re.I)
_BLOCK_CLOSE_RE = re.compile(r"</(p|div|li|tr|td|h1|h2|h3|h4|h5|figcaption|blockquote)>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def strip_html(fragment: str, *, drop_figures: bool = True) -> str:
    """Flatten one HTML fragment to its written text, dropping tags and script."""
    body = _SCRIPT_RE.sub(" ", fragment)
    if drop_figures:
        # Charts and images are never read numerically; removing them here is
        # what makes "no visually estimated chart value" structurally true.
        body = _FIGURE_RE.sub(" ", body)
    body = _INLINE_BREAK_RE.sub(" ", body)
    body = _BLOCK_CLOSE_RE.sub("\n", body)
    text = html.unescape(_TAG_RE.sub(" ", body))
    text = text.replace("\xa0", " ").replace("\u2019", "'")
    return re.sub(r"\s+", " ", text).strip()


def extract_revenue_prose(page_html: str) -> str:
    """Return the written Revenue section text of one Sacra company page."""
    match = re.search(r'<h2[^>]*id="revenue"[^>]*>\s*Revenue\s*</h2>', page_html, re.I)
    if match is not None:
        tail = page_html[match.end():]
    else:
        fallback = re.search(r">\s*Revenue\s*</h2>", page_html, re.I)
        if fallback is None:
            return ""
        tail = page_html[fallback.end():]
    boundary = re.search(r"<h2\b", tail, re.I)
    section = tail[: boundary.start()] if boundary else tail
    paragraphs = re.findall(r"<p\b[^>]*>(.*?)</p>", section, re.S | re.I)
    return "\n".join(strip_html(item) for item in paragraphs).strip()


@dataclass(frozen=True)
class MetricRow:
    """One row of the Sacra "Revenue Metrics" signals table."""

    time_label: str
    metric_label: str
    measurement: str
    row_type: str
    redacted: bool
    citations: tuple[dict[str, str], ...]


def _collapse(cell: str) -> str:
    return re.sub(r"\s+", " ", cell).strip()


def _nested_block(fragment: str, tag: str, attribute_marker: str) -> str:
    """Return the inner HTML of one element, honoring nested tags of the same type."""
    start_match = None
    for candidate in re.finditer(rf"<{tag}\b[^>]*>", fragment, re.I):
        if attribute_marker in candidate.group(0):
            start_match = candidate
            break
    if start_match is None:
        return ""
    depth = 1
    offset = start_match.end()
    for found in re.finditer(rf"</?{tag}\b[^>]*>", fragment[offset:], re.I):
        depth += -1 if found.group(0).startswith(f"</{tag}") else 1
        if depth == 0:
            return fragment[offset: offset + found.start()]
    return fragment[offset:]


def _citation(item_html: str) -> dict[str, str]:
    """Read one publisher/title/quote/URL citation out of a popover list item."""
    link = re.search(r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', item_html, re.S | re.I)
    title_attr = re.search(r'<a\b[^>]*\stitle="([^"]*)"', item_html, re.I)
    title = _collapse(strip_html(link.group(2))) if link else ""
    if not title and title_attr:
        title = _collapse(html.unescape(title_attr.group(1)))
    publisher = ""
    quote = ""
    for classes, body in re.findall(r'<div class="([^"]*)"[^>]*>(.*?)</div>',
                                    item_html, re.S | re.I):
        text = _collapse(strip_html(body))
        if not text:
            continue
        if "italic" in classes.split():
            quote = quote or text
        elif "text-gray-600" in classes.split() and not publisher:
            publisher = text
    if not publisher and not quote and link is None:
        # Fallback for quote-only footnote rows: keep the whole text visible
        # rather than dropping the existence of a secondary source.
        quote = _collapse(strip_html(item_html))[:400]
    return {"title": title, "publisher": publisher, "quote": quote,
            "url": link.group(1) if link else ""}


def extract_metric_rows(page_html: str) -> list[MetricRow]:
    """Parse the signals-backed revenue table, keeping paywall rows as redacted."""
    anchor = page_html.find("Revenue Metrics")
    if anchor < 0:
        return []
    # The signals table sits immediately after its own heading; fall back to the
    # preceding table only if a layout change moves it above the heading.
    start = page_html.find("<table", anchor)
    if start < 0:
        start = page_html.rfind("<table", 0, anchor)
    end = page_html.find("</table>", start if start >= 0 else anchor)
    if start < 0 or end < 0 or end <= start:
        return []
    table = page_html[start:end]
    rows: list[MetricRow] = []
    for raw_row in re.findall(r"<tr\b(.*?)</tr>", table, re.S | re.I):
        header = raw_row.split(">", 1)[0]
        cells = re.findall(r"<td\b[^>]*>(.*?)</td>", raw_row, re.S | re.I)
        if len(cells) < 4:
            continue
        values = [_collapse(strip_html(cell)) for cell in cells[:4]]
        popover = _nested_block(cells[2], "ul", 'class="divide-y divide-gray-200"')
        citations: list[dict[str, str]] = []
        if popover:
            for item in re.findall(r"<li\b[^>]*>(.*?)</li>", popover, re.S | re.I):
                citation = _citation(item)
                if any(citation.values()):
                    citations.append(citation)
        measurement = values[2].split("Sources")[0].strip()
        rows.append(MetricRow(
            time_label=values[0], metric_label=values[1], measurement=measurement,
            row_type=values[3].lower(),
            redacted=('aria-hidden="true"' in header or "blur(" in header
                      or not re.search(r"\$", measurement)),
            citations=tuple(citations)))
    return rows


def semantic_fingerprint(page_html: str) -> str:
    """Hash the structural anchors the parser relies on.

    A DOM or wording change that keeps the same numbers is still observable: the
    probe then reports methodology drift instead of silently re-parsing a new
    layout through an old rule set.
    """
    anchors = {
        "revenue_heading": bool(re.search(r'id="revenue"', page_html, re.I)),
        "revenue_metrics_table": "Revenue Metrics" in page_html,
        "table_columns": re.findall(r"<th[^>]*>\s*([A-Za-z]+)\s*</th>", page_html, re.I)[:4],
        "citation_toggle": "citation-toggle" in page_html,
        "paywall_markers": page_html.count('aria-hidden="true"'),
    }
    return _sha256(_stable_json(anchors))


# --------------------------------------------------------------------------- #
# Amount / period / classification
# --------------------------------------------------------------------------- #

def parse_amount(literal: str) -> float | None:
    """Absolute USD value of one ``$40B`` style token."""
    match = _MONEY_RE.search(literal)
    if match is None:
        return None
    number = float(match.group(1).replace(",", ""))
    suffix = (match.group(2) or "").strip().lower()
    return number * _MULTIPLIERS.get(suffix, 1.0)


def _month_bounds(year: int, month: int) -> tuple[str, str, str]:
    last = calendar.monthrange(year, month)[1]
    return (f"{year:04d}-{month:02d}", f"{year:04d}-{month:02d}-01",
            f"{year:04d}-{month:02d}-{last:02d}")


_QUARTER_BOUNDS = {1: (1, 3), 2: (4, 6), 3: (7, 9), 4: (10, 12)}


def _quarter_bounds(year: int, quarter: int) -> tuple[str, str, str]:
    first, last_month = _QUARTER_BOUNDS[quarter]
    return (f"{year:04d}-Q{quarter}", f"{year:04d}-{first:02d}-01",
            _month_bounds(year, last_month)[2])


def _year_bounds(year: int) -> tuple[str, str, str]:
    return (f"{year:04d}", f"{year:04d}-01-01", f"{year:04d}-12-31")


def _period_in_text(text: str, *, default_year: int | None) -> tuple[tuple[str, str, str, str], str] | None:
    """First period named in one text fragment. Returns (bounds, basis, year_src)."""
    quarter = _QUARTER_RE.search(text)
    if quarter:
        period, start, end = _quarter_bounds(int(quarter.group(2)), int(quarter.group(1)))
        return (period, start, end, "quarter"), "explicit"
    word = _QUARTER_WORD_RE.search(text)
    if word:
        index = {"First": 1, "Second": 2, "Third": 3, "Fourth": 4}[word.group(1)]
        period, start, end = _quarter_bounds(int(word.group(2)), index)
        return (period, start, end, "quarter"), "explicit"
    month = _MONTH_YEAR_RE.search(text)
    if month:
        name = month.group(1)
        index = _MONTH_NAMES.get(name) or _MONTH_ALT.get(name, 0)
        year_text = month.group(2)
        if year_text:
            year, source = int(year_text), "explicit"
        elif default_year is not None:
            year, source = default_year, "publication_year"
        else:
            year, source = 0, ""
        if year:
            period, start, end = _month_bounds(year, index)
            return (period, start, end, "calendar_month"), source
    end_of_year = _END_OF_YEAR_RE.search(text)
    if end_of_year:
        period, start, end = _month_bounds(int(end_of_year.group(1)), 12)
        return (period, start, end, "calendar_month"), "explicit"
    bare = _BARE_YEAR_RE.search(text)
    if bare:
        period, start, end = _year_bounds(int(bare.group(1)))
        return (period, start, end, "annual"), "explicit"
    return None


def resolve_reference_period(texts: Sequence[str], *, default_year: int | None = None
                             ) -> tuple[tuple[str, str, str, str], str] | None:
    """Resolve (period, start, end, basis) preferring text nearest the amount.

    ``texts`` is ordered closest-first (the words directly after the figure,
    then the words before it).  A bare year is only used when no month or
    quarter is named anywhere in the figure's own clause.
    """
    for index, text in enumerate(texts):
        found = _period_in_text(text, default_year=default_year if index == 0 else None)
        if found is not None:
            return found
    if default_year is not None:
        for text in texts:
            found = _period_in_text(text, default_year=default_year)
            if found is not None:
                return found
    return None


def classify_metric(window: str, *, period_basis: str, row_type: str = "",
                    cue_window: str = "") -> str:
    """Pick the governing measurement identity for one quoted window.

    ``cue_window`` widens the annualization search over the surrounding clause:
    a series introduced as "tracked ARR rose from $X to $Y" is a run-rate
    series for every figure in that clause, even though each individual amount
    may only be written as "$Y in June".
    """
    if row_type == "projection":
        return METRIC_FORWARD_PROJECTION
    lowered = window.lower()
    cue = f"{cue_window} {window}".lower()
    annualizing = any(token.lower() in cue for token in _RUN_RATE_CUES)
    projecting = any(token.lower() in lowered for token in _PROJECTION_TOKENS)
    # A closed calendar quarter states revenue actually recorded in that period;
    # it is not an annualization even when the sentence also mentions one.
    if period_basis == "quarter" and "annualized" not in lowered:
        return METRIC_TRAILING_REVENUE
    if projecting and period_basis == "annual":
        return METRIC_FORWARD_PROJECTION
    if annualizing:
        return METRIC_FORWARD_PROJECTION if period_basis == "annual" \
            else METRIC_ANNUALIZED_RUN_RATE
    if projecting:
        return METRIC_FORWARD_PROJECTION
    if re.search(r"\bARR\b", window):
        return METRIC_REPORTED_ARR
    return METRIC_TRAILING_REVENUE


def classify_identity(window: str, *, row_type: str = "") -> str:
    """Decide how strongly the published figure is attributed."""
    if row_type == "projection":
        return OBSERVATION_IDENTITY_PROJECTION
    if any(token in window for token in _PROJECTION_TOKENS):
        return OBSERVATION_IDENTITY_PROJECTION
    if any(token in window for token in _COMPANY_REPORTED_TOKENS):
        return OBSERVATION_IDENTITY_COMPANY
    if any(token in window for token in _MEDIA_TOKENS):
        return OBSERVATION_IDENTITY_MEDIA
    return OBSERVATION_IDENTITY_ESTIMATE


def raw_metric_label(window: str) -> str:
    if re.search(r"\bARR\b", window):
        return "ARR"
    lowered = window.lower()
    if "run rate" in lowered or "run-rate" in lowered:
        return "run rate"
    if "annualized revenue" in lowered:
        return "annualized revenue"
    return "revenue"


def methodology_regime_for(metric_id: str, identity: str) -> str:
    if metric_id == METRIC_FORWARD_PROJECTION or identity == OBSERVATION_IDENTITY_PROJECTION:
        return METHODOLOGY_REGIME_PROJECTION
    if identity == OBSERVATION_IDENTITY_COMPANY:
        return METHODOLOGY_REGIME_COMPANY_REPORTED
    if metric_id == METRIC_TRAILING_REVENUE:
        return METHODOLOGY_REGIME_TRAILING
    return METHODOLOGY_REGIME_THIRD_PARTY_ESTIMATE


def provider_field_for(metric_id: str) -> str:
    return {
        METRIC_REPORTED_ARR: "reported_arr",
        METRIC_ANNUALIZED_RUN_RATE: "annualized_revenue_run_rate",
        METRIC_TRAILING_REVENUE: "trailing_revenue",
        METRIC_FORWARD_PROJECTION: "forward_revenue_projection",
    }[metric_id]


def nearest_subject(sentence: str, amount_index: int) -> tuple[str, str]:
    """Return ('product'|'company', name) for the noun governing the amount."""
    best: tuple[int, int, str, str] | None = None
    for kind, names in (("product", _PRODUCT_SCOPE_TOKENS),
                        ("company", [alias for aliases in COMPANY_ALIASES.values()
                                     for alias in aliases])):
        for name in names:
            for found in re.finditer(re.escape(name), sentence):
                if found.start() >= amount_index:
                    continue
                candidate = (found.start(), len(name), kind, name)
                if best is None or (candidate[0], candidate[1]) > (best[0], best[1]):
                    best = candidate
    return (best[2], best[3]) if best else ("", "")


def entity_for_alias(name: str) -> str | None:
    for entity, aliases in COMPANY_ALIASES.items():
        if name in aliases:
            return entity
    return None


# --------------------------------------------------------------------------- #
# Sacra prose / table parsing
# --------------------------------------------------------------------------- #

def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[\"A-Z0-9$])", text.replace("\n", " "))
    return [re.sub(r"\s+", " ", item).strip() for item in parts if item.strip()]


def build_candidate(*, match_literal: str, match_start: int, before: str, after: str,
                    sentence: str, quote: str, page_company_entity: str, url: str,
                    slice_key: str, extraction_method: str, origin: dict[str, Any] | None,
                    row_type: str = "", forced_period_label: str = "",
                    default_year: int | None = None,
                    accepted_window: Sequence[str] | None = None,
                    company_override: str = "") -> RevenueCandidate:
    """Turn one quoted figure into a candidate with explicit accept/reject reasons."""
    window = f"{before} {match_literal} {after}"
    reasons: list[str] = []
    value = parse_amount(match_literal)
    if value is None:
        reasons.append("amount_unresolved")
        value = 0.0

    lowered = window.lower()
    if any(token.lower() in lowered for token in _NON_REVENUE_CONTEXT):
        reasons.append("not_company_revenue_context")
    if not any(token.lower() in lowered for token in
               [label.lower() for label in _REVENUE_LABEL]):
        reasons.append("revenue_label_missing")

    subject_kind, subject_name = nearest_subject(sentence, match_start)
    product_scope = subject_name if subject_kind == "product" else ""
    if product_scope:
        reasons.append("product_scope_excluded")
    entity_id = company_override or page_company_entity
    trailing_owner = re.search(r"\bfor\s+(OpenAI|Anthropic)\b", after[:40])
    if trailing_owner:
        resolved_trailing = entity_for_alias(trailing_owner.group(1))
        if resolved_trailing:
            entity_id = resolved_trailing
    elif subject_kind == "company" and not company_override:
        resolved = entity_for_alias(subject_name)
        if resolved:
            entity_id = resolved
    if subject_kind == "" and not trailing_owner and not page_company_entity:
        reasons.append("company_unresolved")

    if forced_period_label and re.fullmatch(r"20\d\d", forced_period_label.strip()):
        period, period_start, period_end = _year_bounds(int(forced_period_label.strip()))
        period_basis, year_source = "annual", "explicit"
    else:
        resolved = resolve_reference_period(
            [after, before] if not forced_period_label else [forced_period_label, after],
            default_year=default_year)
        if resolved is None:
            reasons.append("reference_period_unresolved")
            period = period_start = period_end = ""
            period_basis = ""
            year_source = ""
        else:
            period, period_start, period_end, period_basis = resolved[0]
            year_source = resolved[1]

    identity = classify_identity(window, row_type=row_type)
    metric_id = classify_metric(
        window, period_basis=period_basis, row_type=row_type,
        cue_window=sentence[max(0, match_start - 160): match_start + len(match_literal)])
    if period_basis == "annual" and metric_id != METRIC_FORWARD_PROJECTION:
        # A whole calendar year without annualization language is not a
        # monthly run-rate point; keep the identity honest instead of guessing.
        metric_id = METRIC_TRAILING_REVENUE
    if value and not reasons and value < MINIMUM_COMPANY_REVENUE_USD:
        reasons.append("below_company_revenue_scale")
    if accepted_window and period_start and period_end:
        start, end = accepted_window
        if period_end > end or period_start < start:
            reasons.append("outside_accepted_reference_window")

    citation = dict(origin or {})
    citation.setdefault("origin_publisher", "")
    citation.setdefault("origin_title", "")
    citation.setdefault("origin_url", "")
    citation.setdefault("origin_quote", "")
    citation.setdefault("additional_citations", [])
    return RevenueCandidate(
        entity_id=entity_id,
        provider_field=provider_field_for(metric_id),
        metric_id=metric_id,
        observation_identity=identity,
        methodology_regime=methodology_regime_for(metric_id, identity),
        raw_metric_label=raw_metric_label(window),
        period=period, period_start=period_start, period_end=period_end,
        period_basis=period_basis, value=value, currency="USD",
        raw_quote=_collapse(quote)[:600],
        publisher_url=url, origin_citation=citation,
        extraction_method=extraction_method, slice_key=slice_key,
        product_scope=product_scope, diagnostics=tuple(reasons),
        period_year_source=year_source)


def parse_sacra_page(page_html: str, *, page: dict[str, str],
                     parser_version: str = SACRA_PARSER_VERSION
                     ) -> tuple[list[RevenueCandidate], dict[str, Any]]:
    """Extract company-level revenue observations from one Sacra profile page."""
    candidates: list[RevenueCandidate] = []
    diagnostics: list[dict[str, Any]] = []
    url = page["url"]
    slice_key = f"sacra:{page['slug']}"

    prose = extract_revenue_prose(page_html)
    if prose:
        for sentence in _sentences(prose):
            for match in _MONEY_RE.finditer(sentence):
                candidates.append(build_candidate(
                    match_literal=match.group(0), match_start=match.start(),
                    before=sentence[max(0, match.start() - 70): match.start()],
                    after=sentence[match.end(): match.end() + 50],
                    sentence=sentence, quote=sentence,
                    page_company_entity=page["entity_id"], url=url,
                    slice_key=slice_key,
                    extraction_method="sacra_revenue_prose",
                    origin={"origin_publisher": SACRA_PUBLISHER,
                            "origin_title": f"Sacra public profile: {page['company']}",
                            "origin_url": url,
                            "origin_quote": _collapse(sentence)[:600]}))
    else:
        diagnostics.append({"slice_key": slice_key, "code": "revenue_section_missing",
                            "detail": url})

    table_rows = extract_metric_rows(page_html)
    redacted_rows = 0
    for row in table_rows:
        if row.redacted:
            redacted_rows += 1
            diagnostics.append({
                "slice_key": slice_key, "code": "paywalled_measurement_redacted",
                "period_label": row.time_label, "metric_label": row.metric_label,
                "detail": "upstream redaction is a coverage gap, not a zero revenue value"})
            continue
        first = row.citations[0] if row.citations else {}
        candidates.append(build_candidate(
            match_literal=row.measurement, match_start=len(row.metric_label) + 1,
            before=f"{row.metric_label} ", after=row.row_type,
            sentence=row.metric_label,
            quote=first.get("quote") or row.measurement,
            page_company_entity=page["entity_id"], url=url, slice_key=slice_key,
            extraction_method="sacra_revenue_metrics_table",
            origin={"origin_publisher": first.get("publisher", ""),
                    "origin_title": first.get("title", ""),
                    "origin_url": first.get("url", ""),
                    "origin_quote": first.get("quote", ""),
                    "additional_citations": [dict(item) for item in row.citations[1:]]},
            row_type=row.row_type, forced_period_label=row.time_label))

    summary = {
        "slice_key": slice_key, "url": url, "entity_id": page["entity_id"],
        "content_sha256": _sha256(page_html),
        "semantic_fingerprint": semantic_fingerprint(page_html),
        "prose_characters": len(prose),
        "candidate_count": len(candidates),
        "accepted_count": sum(1 for item in candidates if item.accepted),
        "metric_row_count": len(table_rows),
        "paywalled_row_count": redacted_rows,
        "parser_version": parser_version,
        "chart_reading_note": CHART_READING_PROHIBITED,
    }
    return candidates, {"per_page": [summary], "diagnostics": diagnostics}


# --------------------------------------------------------------------------- #
# TickerTrends frozen seed parsing
# --------------------------------------------------------------------------- #

def parse_tickertrends_article(*, body_html: str, url: str, published_at: datetime,
                               parser_version: str = TICKERTRENDS_PARSER_VERSION,
                               accepted_window: Sequence[str] = TICKERTRENDS_ACCEPTED_WINDOW,
                               ) -> tuple[list[RevenueCandidate], dict[str, Any]]:
    """Extract explicitly stated monthly ARR points from the frozen 2026H1 essay."""
    text = strip_html(body_html, drop_figures=True)
    candidates: list[RevenueCandidate] = []
    slice_key = f"tickertrends:{TICKERTRENDS_ARTICLE_SLUG}"

    for sentence in _sentences(text):
        for match in _MONEY_RE.finditer(sentence):
            candidates.append(build_candidate(
                match_literal=match.group(0), match_start=match.start(),
                before=sentence[max(0, match.start() - 70): match.start()],
                after=sentence[match.end(): match.end() + 60],
                sentence=sentence, quote=sentence,
                page_company_entity="", url=url, slice_key=slice_key,
                extraction_method="tickertrends_frozen_article_body",
                origin={"origin_publisher": "TickerTrends",
                        "origin_title": TICKERTRENDS_ARTICLE_SLUG,
                        "origin_quote": _collapse(sentence)[:600]},
                default_year=published_at.year,
                accepted_window=accepted_window, row_type=""))

    summary = {
        "slice_key": slice_key, "url": url,
        "content_sha256": _sha256(body_html),
        "semantic_fingerprint": tickertrends_semantic_fingerprint(candidates),
        "accepted_window": list(accepted_window),
        "candidate_count": len(candidates),
        "accepted_count": sum(1 for item in candidates if item.accepted),
        "parser_version": parser_version,
        "chart_reading_note": CHART_READING_PROHIBITED,
    }
    return candidates, {"per_page": [summary], "diagnostics": []}


def tickertrends_semantic_fingerprint(candidates: Iterable[RevenueCandidate]) -> str:
    """Fingerprint the admitted numeric claims, never the surrounding markup.

    Substack re-serialises ``body_html`` between fetches — whitespace, image
    proxy hosts and embed wrappers all drift while the numbers stay identical.
    Hashing raw bytes would therefore report a new release on every probe and
    re-baseline the main sequence for no reason.  This covers exactly what the
    parser admits: entity, reference period, value, observation identity, metric
    family and the raw label the author used.
    """
    admitted = [
        {
            "entity_id": item.entity_id,
            "period": item.period,
            "value": repr(item.value),
            "observation_identity": item.observation_identity,
            "metric_id": item.metric_id,
            "raw_metric_label": item.raw_metric_label,
        }
        for item in candidates
        if item.accepted
    ]
    admitted.sort(key=lambda row: (row["entity_id"], row["period"],
                                   row["metric_id"], row["value"]))
    return _sha256(_stable_json(admitted))


def load_frozen_article(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _parse_iso(value: str) -> datetime:
    text = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def methodology_fingerprint(semantic: str, latest_period: str, parser_version: str) -> str:
    """Bind the semantic layout, latest period family and parser version together."""
    return _sha256(_stable_json({
        "semantic_fingerprint": semantic, "latest_period": latest_period,
        "parser_version": parser_version,
        "methodology_regime": METHODOLOGY_REGIME_THIRD_PARTY_ESTIMATE,
    }))


# --------------------------------------------------------------------------- #
# Adapters
# --------------------------------------------------------------------------- #

class SacraPublicCompanyProfilesAdapter:
    """Read-only periodic discovery over the two registered Sacra public pages."""

    source_id = SACRA_SOURCE_ID
    dataset_id = DATASET_ID
    parser_version = SACRA_PARSER_VERSION

    def __init__(self, *, client: Any | None = None, browser: Any | None = None,
                 fixture_dir: str | Path | None = None,
                 pages: Iterable[dict[str, str]] | None = None,
                 clock: Callable[[], datetime] | None = None,
                 request_timeout: float = 45.0):
        self.client = client
        self.browser = browser
        self.fixture_dir = Path(fixture_dir) if fixture_dir else None
        self.pages = tuple(pages or SACRA_PAGES)
        self.clock = clock or _now
        self.request_timeout = request_timeout
        # One probe touches each page exactly once.  Payloads stay in memory
        # only for the discovery -> ingest handoff; raw bytes are persisted by
        # the artifact store inside fetch(), never cached here.
        self.discovered_payloads: dict[str, str] = {}
        self.browser_snapshots_used = 0

    # -- transport ---------------------------------------------------------- #

    def _fixture_path(self, page: dict[str, str]) -> Path | None:
        if self.fixture_dir is None:
            return None
        candidate = self.fixture_dir / f"sacra_{page['slug']}_public_profile.html"
        return candidate if candidate.exists() else None

    def _static_get(self, url: str) -> str:
        if self.client is not None:
            response = self.client.get(url, timeout=self.request_timeout,
                                       follow_redirects=True)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            return response.text
        import httpx  # imported lazily: no network dependency at import time

        response = httpx.get(url, headers={"User-Agent": _UA},
                             timeout=self.request_timeout, follow_redirects=True)
        response.raise_for_status()
        return response.text

    def _payloads(self, request: FetchRequest) -> dict[str, str]:
        supplied = (request.query_scope or {}).get("payloads") or {}
        output: dict[str, str] = {}
        for page in self.pages:
            slug = page["slug"]
            if isinstance(supplied, dict) and supplied.get(slug):
                output[slug] = str(supplied[slug])
                continue
            if slug in self.discovered_payloads:
                output[slug] = self.discovered_payloads[slug]
                continue
            fixture = self._fixture_path(page)
            if fixture is not None:
                output[slug] = fixture.read_text(encoding="utf-8")
                self.discovered_payloads[slug] = output[slug]
                continue
            try:
                payload = self._static_get(page["url"])
            except (ConnectionError, TimeoutError):
                raise
            except Exception:
                raise ConnectionError(f"sacra_page_unavailable:{page['url']}") from None
            # Single controlled browser fallback: only when the static response
            # genuinely lacks the governed revenue section, and at most once per
            # page per run.  Nothing downstream ever opens the page again.
            if not extract_revenue_prose(payload) and self.browser is not None \
                    and self.browser_snapshots_used < len(self.pages):
                self.browser_snapshots_used += 1
                rendered = str(self.browser.snapshot(page["url"]))
                if extract_revenue_prose(rendered):
                    payload = rendered
            output[slug] = payload
            self.discovered_payloads[slug] = payload
        return output

    # -- discovery / fetch -------------------------------------------------- #

    def discover(self, request: FetchRequest) -> DiscoveryResult:
        checked_at = self.clock()
        payloads = self._payloads(request)
        candidates: list[ReleaseCandidate] = []
        failures: list[AdapterFailure] = []
        diagnostics: list[dict[str, Any]] = []
        latest_period = ""
        identity_parts: list[str] = []
        for page in self.pages:
            slug = page["slug"]
            payload = payloads.get(slug, "")
            if not payload:
                failures.append(AdapterFailure(status=IngestionStatus.UNREACHABLE,
                                               message=f"sacra_page_empty:{page['url']}",
                                               slice_key=f"sacra:{slug}"))
                continue
            page_candidates, report = parse_sacra_page(payload, page=page)
            per_page = next(iter(report.get("per_page") or []), {})
            periods = [item.period for item in page_candidates if item.period]
            page_period = max(periods, default="") if periods else ""
            latest_period = max(latest_period, page_period)
            digest = per_page.get("content_sha256", "")
            identity = f"sacra:{slug}:{page_period}:{digest}"
            identity_parts.append(identity)
            if not page_period:
                failures.append(AdapterFailure(status=IngestionStatus.PARSE_FAILED,
                                               message=f"revenue_observations_missing:{slug}",
                                               slice_key=f"sacra:{slug}"))
            candidates.append(ReleaseCandidate(
                identity=identity, period=page_period, urls=[page["url"]],
                methodology_fingerprint=methodology_fingerprint(
                    per_page.get("semantic_fingerprint", ""), page_period,
                    self.parser_version),
                metadata={"scope": slug, "entity_id": page["entity_id"],
                          "payload_sha256": digest,
                          "semantic_fingerprint": per_page.get("semantic_fingerprint", ""),
                          "latest_period": page_period,
                          "candidate_count": per_page.get("candidate_count", 0),
                          "accepted_count": per_page.get("accepted_count", 0),
                          "paywalled_row_count": per_page.get("paywalled_row_count", 0),
                          "parser_version": self.parser_version,
                          "methodology_regime": METHODOLOGY_REGIME_THIRD_PARTY_ESTIMATE}))
            diagnostics.extend(report.get("diagnostics") or [])
        status = DiscoveryStatus.PARTIAL if failures else DiscoveryStatus.NEW_RELEASE
        if not candidates:
            status = DiscoveryStatus.VALIDATION_FAILED
        return DiscoveryResult(
            source_id=request.source_id, dataset_id=request.dataset_id,
            checked_at=checked_at, status=status,
            latest_upstream_identity="SACRA:" + _sha256("|".join(sorted(identity_parts)))[:24],
            latest_available_period=latest_period, candidates=candidates,
            diagnostics={"pages": diagnostics,
                         "browser_snapshots_used": self.browser_snapshots_used,
                         "chart_reading_note": CHART_READING_PROHIBITED})

    def fetch(self, request: FetchRequest) -> AdapterBatch:
        fetched_at = self.clock()
        payloads = (request.query_scope or {}).get("payloads") or {}
        if not isinstance(payloads, dict) or not payloads:
            payloads = self._payloads(request)
        records: list[NativeRecord] = []
        artifacts: list[AdapterArtifact] = []
        failures: list[AdapterFailure] = []
        diagnostics: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for page in self.pages:
            slug = page["slug"]
            payload = payloads.get(slug, "")
            if not payload:
                failures.append(AdapterFailure(status=IngestionStatus.UNREACHABLE,
                                               message=f"sacra_page_empty:{page['url']}",
                                               slice_key=f"sacra:{slug}"))
                continue
            page_candidates, report = parse_sacra_page(payload, page=page)
            per_page = next(iter(report.get("per_page") or []), {})
            diagnostics.extend(report.get("diagnostics") or [])
            for candidate in page_candidates:
                if candidate.accepted:
                    records.append(candidate.as_native_record())
                else:
                    rejected.append({
                        "entity_id": candidate.entity_id, "period": candidate.period,
                        "value": candidate.value, "slice_key": candidate.slice_key,
                        "reason_codes": list(candidate.diagnostics),
                        "raw_quote": candidate.raw_quote[:200]})
            admitted, quarantined = partition_admissible_records(records)
            if quarantined:
                rejected.extend(quarantined)
                failures.append(AdapterFailure(
                    status=IngestionStatus.VALIDATION_FAILED,
                    message=f"quality_gate_quarantined:{slug}:"
                            f"{len(quarantined)}/{len(records)}",
                    slice_key=f"sacra:{slug}"))
            records = admitted
            if not records:
                failures.append(AdapterFailure(status=IngestionStatus.PARSE_FAILED,
                                               message=f"no_admissible_revenue:{slug}",
                                               slice_key=f"sacra:{slug}"))
            artifacts.append(AdapterArtifact(
                artifact_key=f"sacra:{slug}", payload=payload,
                query_scope={"slug": slug, "entity_id": page["entity_id"]},
                source_url=page["url"],
                source_version=per_page.get("content_sha256", "")[:16],
                media_type="text/html", retention="constrained_snapshot",
                metadata={**per_page, "excluded_candidates": [
                    {k: v for k, v in item.items() if k != "raw_quote"}
                    for item in rejected if item["slice_key"] == f"sacra:{slug}"]}))
        status = IngestionStatus.PARTIAL if failures else (
            IngestionStatus.SUCCEEDED if records else IngestionStatus.ZERO_MATCH)
        return AdapterBatch(
            source_id=request.source_id, dataset_id=request.dataset_id, status=status,
            fetched_at=fetched_at, records=records, artifacts=artifacts, failures=failures,
            provider_metadata={"parser_version": self.parser_version,
                               "pages": diagnostics,
                               "rejected_candidates": rejected,
                               "chart_reading_note": CHART_READING_PROHIBITED,
                               "browser_snapshots_used": self.browser_snapshots_used})


class TickerTrendsPublicResearchAdapter:
    """7-day re-check of one public Substack research essay.

    Two routes feed the same governed dataset:

    * the live public post API (production) — fetched once per probe, held in
      memory for the discovery -> ingest handoff, and fingerprinted semantically
      so an unchanged essay reports ``no_change``;
    * the versioned JSON seed — an explicitly pinned ``seed_path`` used by tests
      and by governed offline replay.

    The probe never guesses.  A paywalled or non-public article, a missing
    explicit seed and an unreachable host are all reported as explicit failures
    and the last accepted vintage is preserved untouched.
    """

    source_id = TICKERTRENDS_SOURCE_ID
    dataset_id = DATASET_ID
    parser_version = TICKERTRENDS_PARSER_VERSION

    def __init__(self, *, seed_path: str | Path | None = None,
                 client: Any | None = None,
                 clock: Callable[[], datetime] | None = None,
                 request_timeout: float = 45.0):
        # An explicit seed_path pins the offline route; leaving it unset means
        # the adapter probes the live public post API.
        self.seed_path = Path(seed_path) if seed_path else None
        self.client = client
        self.clock = clock or _now
        self.request_timeout = request_timeout
        self.article_url = TICKERTRENDS_POST_API_URL
        # Raw JSON text stays in memory only for the discovery -> ingest handoff;
        # the artifact store persists the constrained snapshot inside fetch().
        self.discovered_payloads: dict[str, str] = {}
        self.payload_origin = ""

    # -- transport ---------------------------------------------------------- #

    def _static_get(self, url: str) -> str:
        if self.client is not None:
            response = self.client.get(url, timeout=self.request_timeout,
                                       follow_redirects=True)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            return response.text
        import httpx  # imported lazily: no network dependency at import time

        response = httpx.get(url, headers={"User-Agent": _UA},
                             timeout=self.request_timeout, follow_redirects=True)
        response.raise_for_status()
        return response.text

    def _seed_article(self) -> dict[str, Any]:
        """The pinned offline artifact.  An explicit but missing path is fatal."""
        if self.seed_path is None:
            return {}
        if not Path(self.seed_path).exists():
            raise FileNotFoundError(
                f"tickertrends_seed_missing:{self.seed_path}; the history seed is a "
                "version-controlled frozen artifact, not a live fetch. Drop the "
                "explicit seed_path to use the live public post API, or point it at "
                f"default_seed_path() = {default_seed_path()}")
        return load_frozen_article(self.seed_path)

    def _live_article(self) -> dict[str, Any]:
        body = self._static_get(self.article_url)
        try:
            article = json.loads(body)
        except ValueError as exc:
            raise ConnectionError(
                f"tickertrends_post_api_not_json:{self.article_url}") from exc
        if not isinstance(article, dict):
            raise ConnectionError(
                f"tickertrends_post_api_unexpected:{self.article_url}")
        # Fail closed if the essay ever stops being publicly readable: a genuine
        # paywall must surface as unreachable, never as a plausible number set.
        if bool(article.get("free_unlock_required")):
            raise ConnectionError(
                f"tickertrends_article_paywalled:{self.article_url}")
        audience = str(article.get("audience") or "")
        if audience and audience != "everyone":
            raise ConnectionError(
                f"tickertrends_article_not_public:{audience}")
        self.payload_origin = TICKERTRENDS_ORIGIN_LIVE
        self.discovered_payloads[TICKERTRENDS_ARTICLE_SLUG] = body
        return article

    def _article(self, request: FetchRequest | None = None) -> dict[str, Any]:
        supplied = ((request.query_scope or {}).get("payloads") or {}) if request else {}
        if isinstance(supplied, dict) and supplied.get(TICKERTRENDS_ARTICLE_SLUG):
            self.payload_origin = self.payload_origin or TICKERTRENDS_ORIGIN_LIVE
            return json.loads(str(supplied[TICKERTRENDS_ARTICLE_SLUG]))
        if TICKERTRENDS_ARTICLE_SLUG in self.discovered_payloads:
            return json.loads(self.discovered_payloads[TICKERTRENDS_ARTICLE_SLUG])
        article = self._seed_article()
        if article:
            self.payload_origin = TICKERTRENDS_ORIGIN_SEED
            return article
        return self._live_article()

    # -- discovery ---------------------------------------------------------- #

    def discover(self, request: FetchRequest) -> DiscoveryResult:
        """One probe, fingerprinted by admitted claims so churn is not a release."""
        checked_at = self.clock()
        article = self._article(request)
        published_at = _parse_iso(article.get("post_date", ""))
        url = str(article.get("canonical_url") or self.article_url)
        candidates, report = parse_tickertrends_article(
            body_html=str(article.get("body_html") or ""), url=url,
            published_at=published_at)
        summary = next(iter(report.get("per_page") or []), {})
        periods = [item.period for item in candidates if item.accepted and item.period]
        latest_period = max(periods, default="")
        fingerprint = str(summary.get("semantic_fingerprint", ""))
        identity = (f"tickertrends:{TICKERTRENDS_ARTICLE_SLUG}:"
                    f"{latest_period}:{fingerprint}")
        failures: list[AdapterFailure] = []
        if not candidates:
            failures.append(AdapterFailure(
                status=IngestionStatus.PARSE_FAILED,
                message="revenue_observations_missing:tickertrends",
                slice_key=f"tickertrends:{TICKERTRENDS_ARTICLE_SLUG}"))
        return DiscoveryResult(
            source_id=request.source_id, dataset_id=request.dataset_id,
            checked_at=checked_at,
            status=(DiscoveryStatus.PARTIAL if failures
                    else DiscoveryStatus.NEW_RELEASE),
            latest_upstream_identity=(f"TICKERTRENDS:"
                                      f"{_sha256(identity)[:24]}"),
            latest_available_period=latest_period,
            candidates=[ReleaseCandidate(
                identity=identity, period=latest_period, urls=[url],
                methodology_fingerprint=methodology_fingerprint(
                    fingerprint, latest_period, self.parser_version),
                metadata={"scope": TICKERTRENDS_ARTICLE_SLUG,
                          "payload_sha256": summary.get("content_sha256", ""),
                          "semantic_fingerprint": fingerprint,
                          "latest_period": latest_period,
                          "candidate_count": summary.get("candidate_count", 0),
                          "accepted_count": summary.get("accepted_count", 0),
                          "payload_origin": self.payload_origin,
                          "parser_version": self.parser_version,
                          "methodology_regime": METHODOLOGY_REGIME_THIRD_PARTY_ESTIMATE})],
            diagnostics={"per_page": [summary],
                         "payload_origin": self.payload_origin,
                         "scheduled_discovery": True,
                         "chart_reading_note": CHART_READING_PROHIBITED})

    def fetch(self, request: FetchRequest) -> AdapterBatch:
        fetched_at = self.clock()
        article = self._article(request)
        published_at = _parse_iso(article.get("post_date", ""))
        body_html = str(article.get("body_html") or "")
        url = str(article.get("canonical_url") or self.article_url)
        candidates, report = parse_tickertrends_article(
            body_html=body_html, url=url, published_at=published_at)
        summary = next(iter(report.get("per_page") or []), {})
        records: list[NativeRecord] = []
        rejected: list[dict[str, Any]] = []
        for candidate in candidates:
            record_candidate = RevenueCandidate(
                **{**candidate.__dict__, "published_at": published_at})
            if candidate.accepted:
                records.append(record_candidate.as_native_record())
            else:
                rejected.append({"entity_id": candidate.entity_id,
                                 "period": candidate.period, "value": candidate.value,
                                 "slice_key": candidate.slice_key,
                                 "reason_codes": list(candidate.diagnostics),
                                 "raw_quote": candidate.raw_quote[:200]})
        admitted, quarantined = partition_admissible_records(records)
        failures: list[AdapterFailure] = []
        total = len(records)
        records = admitted
        if quarantined:
            rejected.extend(quarantined)
            failures.append(AdapterFailure(
                status=IngestionStatus.VALIDATION_FAILED,
                message=f"quality_gate_quarantined:{len(quarantined)}/{total}",
                slice_key=f"tickertrends:{TICKERTRENDS_ARTICLE_SLUG}"))
        status = IngestionStatus.PARTIAL if failures and records else (
            IngestionStatus.SUCCEEDED if records else IngestionStatus.ZERO_MATCH)
        return AdapterBatch(
            source_id=request.source_id, dataset_id=request.dataset_id, status=status,
            fetched_at=fetched_at, records=records, failures=failures,
            artifacts=[AdapterArtifact(
                artifact_key=f"tickertrends:{TICKERTRENDS_ARTICLE_SLUG}",
                payload=article,
                query_scope={"slug": TICKERTRENDS_ARTICLE_SLUG,
                             "accepted_reference_period": list(TICKERTRENDS_ACCEPTED_WINDOW)},
                source_url=url, source_version=str(summary.get("content_sha256", ""))[:16],
                media_type="application/json", retention="constrained_snapshot",
                metadata={**summary, "published_at": published_at.isoformat(),
                          "payload_origin": self.payload_origin,
                          "excluded_candidates": rejected})],
            provider_metadata={
                "parser_version": self.parser_version,
                "published_at": published_at.isoformat(),
                "accepted_reference_period": list(TICKERTRENDS_ACCEPTED_WINDOW),
                "rejected_candidates": rejected,
                "chart_reading_note": CHART_READING_PROHIBITED,
                "payload_origin": self.payload_origin,
                "scheduled_discovery": True})


def default_seed_path() -> Path:
    """Location of the version-controlled TickerTrends offline artifact.

    The live route is the production path; this seed exists so tests and
    governed offline replays can pin the 2026H1 history without a network read.
    """
    from ...config import REPO_ROOT

    return (REPO_ROOT / "tests" / "fixtures" / "frontier_ai_labs_revenue"
            / TICKERTRENDS_DEFAULT_SEED_FILENAME)


def revenue_record_issues(record: NativeRecord, *, seen: set[tuple[str, ...]] | None = None
                          ) -> list[str]:
    """One record's quality-gate failures; an empty list means admissible."""
    issues: list[str] = []
    key = (record.entity_id, record.provider_field, record.period,
           str(record.dimensions.get("observation_identity", "")))
    if seen is not None:
        if key in seen:
            issues.append(f"duplicate_cell:{key}")
        seen.add(key)
    try:
        numeric = float(record.value)
    except (TypeError, ValueError):
        return issues + ["value_not_numeric"]
    if not numeric > 0:
        issues.append(f"non_positive_revenue:{key}")
    if numeric < MINIMUM_COMPANY_REVENUE_USD:
        issues.append(f"below_company_revenue_scale:{key}")
    if record.currency != "USD":
        issues.append(f"currency_unsupported:{record.currency}")
    if not record.period or not record.period_start or not record.period_end:
        issues.append(f"reference_period_incomplete:{key}")
    for dimension in ("observation_identity", "methodology_regime", "raw_metric_label"):
        if not record.dimensions.get(dimension):
            issues.append(f"{dimension}_missing:{key}")
    citation = record.raw.get("origin_citation") or {}
    if not record.raw.get("raw_quote"):
        issues.append(f"raw_quote_missing:{key}")
    if not record.raw.get("publisher_url"):
        issues.append(f"publisher_url_missing:{key}")
    if not citation.get("origin_publisher") and not citation.get("origin_title"):
        issues.append(f"citation_chain_incomplete:{key}")
    return issues


def validate_frontier_labs_revenue_records(records: Sequence[NativeRecord]) -> list[str]:
    """Deterministic quality-gate diagnostics; rows are never silently dropped."""
    issues: list[str] = []
    seen: set[tuple[str, ...]] = set()
    for record in records:
        issues.extend(revenue_record_issues(record, seen=seen))
    return sorted(set(issues))


def partition_admissible_records(records: Sequence[NativeRecord]
                                 ) -> tuple[list[NativeRecord], list[dict[str, Any]]]:
    """Split records into platform observations and quarantined diagnostics.

    A record failing the gate keeps its artifact and its reason codes but never
    becomes a platform observation, so a bad parse cannot overwrite the last
    known-good value.
    """
    admitted: list[NativeRecord] = []
    quarantined: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for record in records:
        issues = revenue_record_issues(record, seen=seen)
        if issues:
            quarantined.append({
                "entity_id": record.entity_id, "period": record.period,
                "value": record.value, "slice_key": record.slice_key,
                "reason_codes": issues,
                "raw_quote": str(record.raw.get("raw_quote") or "")[:200]})
        else:
            admitted.append(record)
    return admitted, quarantined
