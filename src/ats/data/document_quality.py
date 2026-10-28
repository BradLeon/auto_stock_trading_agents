"""Neutral article parsing and analysis views, without modifying source bytes."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser

POLICY_VERSION = "article-quality-v2"
PAGE_FIELDS_VERSION = "article-page-fields-v2"
_PADDING = frozenset("\u034f\u00ad\u200b\u200c\u200d\u2060\ufeff")
_NAVIGATION = re.compile(
    r"^(?:view in browser|unsubscribe|manage subscription|forwarded this email\??|"
    r"subscribe now|share this post|leave a comment)$", re.I)


@dataclass(frozen=True)
class TextView:
    text: str
    offsets: tuple[int, ...]
    original_hash: str
    version: str = POLICY_VERSION

    def locator(self, start: int, end: int) -> dict:
        if not 0 <= start < end <= len(self.text):
            raise ValueError("invalid analysis-view span")
        return {"field": "body", "start": self.offsets[start],
                "end": self.offsets[end - 1] + 1, "cleaner_version": self.version}


def analysis_view(body: str, *, title: str = "") -> TextView:
    """Remove only padding and standalone navigation, keeping a character map.

    Quoted calls to subscribe and inline subscription boundaries remain evidence.
    Only repeated copies of an exact supplied title are removed.
    """
    chars, offsets = [], []
    title_seen = False
    position = 0
    for line in body.splitlines(keepends=True):
        visible = "".join(c for c in line if c not in _PADDING).strip()
        repeated_title = bool(title and visible == title and title_seen)
        if title and visible == title:
            title_seen = True
        if not _NAVIGATION.fullmatch(visible) and not repeated_title:
            for index, char in enumerate(line):
                if char not in _PADDING:
                    chars.append(char)
                    offsets.append(position + index)
        position += len(line)
    left = 0
    right = len(chars)
    while left < right and chars[left].isspace():
        left += 1
    while right > left and chars[right-1].isspace():
        right -= 1
    return TextView("".join(chars[left:right]), tuple(offsets[left:right]),
                    hashlib.sha256(body.encode()).hexdigest())


class _PageFields(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.fields = {}
        self.tag = ""
        self.parts = []
        self.field_key = ""
        self.depth = 0

    def handle_starttag(self, tag, attrs):
        if self.tag and tag == self.tag:
            self.depth += 1
        attrs = dict(attrs)
        if tag == "meta":
            key = attrs.get("property") or attrs.get("name")
            if key in {"og:title", "description", "og:description", "article:published_time"}:
                self.fields.setdefault(key, attrs.get("content", "").strip())
        labels = set((attrs.get("class", "") + " " + attrs.get("id", "")).lower().split())
        subtitle = bool(labels & {"subtitle", "post-subtitle", "article-subtitle"})
        if (tag in {"h1", "title"} or subtitle) and not self.tag:
            self.tag, self.parts = tag, []
            self.field_key = "subtitle" if subtitle else tag
            self.depth = 1

    def handle_data(self, data):
        if self.tag:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == self.tag:
            self.depth -= 1
            if self.depth:
                return
            self.fields.setdefault(self.field_key, " ".join("".join(self.parts).split()))
            self.tag = ""


def page_fields(html: str) -> dict[str, str]:
    parser = _PageFields()
    parser.feed(html)
    fields = {"title_source": "url_slug", "title_quality": "fallback",
        "parser_version": PAGE_FIELDS_VERSION, "lede": parser.fields.get(
            "og:description", parser.fields.get("description", "")),
        "subtitle": parser.fields.get("subtitle", ""),
        "published_at": parser.fields.get("article:published_time", "")}
    if fields["lede"]:
        fields["lede_source"] = "html:og:description" if parser.fields.get("og:description") else "html:description"
    if fields["subtitle"]:
        fields["subtitle_source"] = "html:explicit_subtitle"
    if fields["published_at"]:
        fields["published_at_source"] = "html:article:published_time"
    for key in ("h1", "og:title", "title"):
        if parser.fields.get(key):
            return {**fields, "title": unescape(parser.fields[key]), "title_source": f"html:{key}",
                    "title_quality": "original"}
    return fields


def association_evidence(fields: dict[str, str], entities: list[str]) -> list[dict]:
    from .entity_association import entity_mentions

    evidence = []
    for field, text in fields.items():
        for entity in entities:
            evidence.extend({**row, "field": field, "policy_version": POLICY_VERSION}
                            for row in entity_mentions(text, entity))
    return evidence


_BOUNDARIES = (
    (re.compile(r"subscribe to [^.\n]{0,80}?\s+to unlock the rest", re.I), "subscribe to unlock the rest"),
    (re.compile(r"subscribe to unlock(?: the rest)?", re.I), "subscribe to unlock"),
    (re.compile(r"continue reading by subscribing", re.I), "continue reading by subscribing"),
    (re.compile(r"this post is for paid subscribers", re.I), "this post is for paid subscribers"),
)
_WEAK_BOUNDARY = re.compile(r"read the full post", re.I)
COMPLETENESS_VERSION = "visible-content-v3"
VISIBLE_VIEW_VERSION = "article-visible-prefix-v1"


def visible_analysis_view(body: str, *, title: str = "", source: str = "") -> TextView:
    """Exclude an own-post paid boundary and its footer from research inputs.

    Immutable source bytes and their hash remain intact. Quoted boundaries are
    retained; only the source with an explicit partial-content policy is cut.
    """
    view = analysis_view(body, title=title)
    if source != "semianalysis":
        return view
    visibility = completeness_assessment(body)
    strong = [row for row in visibility["markers"] if row["strength"] == "strong"]
    if not strong:
        return view
    boundary = min(row["start"] for row in strong)
    end = next((i for i, offset in enumerate(view.offsets) if offset >= boundary), len(view.text))
    text = view.text[:end].rstrip()
    return TextView(text, view.offsets[:len(text)], view.original_hash, VISIBLE_VIEW_VERSION)


def substantive_preview(text: str, *, minimum_chars: int = 400) -> bool:
    """A conservative, neutral short-preview gate, not a relevance judgement.

    Padding/marketing volume alone never satisfies it. Require multiple substantive
    sentences and either a registered issuer name or a measured financial quantity.
    The source policy still decides whether partial material is permitted.
    """
    from ..config import load_entities

    visible = analysis_view(text).text
    if len(visible) < minimum_chars or len(re.findall(r"[.!?](?:\s|$)", visible)) < 2:
        return False
    marketing = re.compile(r"\b(?:subscribe|subscription|unlock|upgrade|newsletter|sign up)\b", re.I)
    sentences = re.split(r"(?<=[.!?])\s+", visible)
    content = " ".join(sentence for sentence in sentences if not marketing.search(sentence))
    if len(content) < minimum_chars:
        return False
    return bool(re.search(r"(?:\$\s*\d|\d(?:\.\d+)?\s*%|\d+\s+(?:billion|million))", content, re.I)
                or association_evidence({"body": content}, list(load_entities())))


def completeness_assessment(body: str, *, stored: str = "full") -> dict:
    """Locate access-boundary evidence; explicitly quoted/other-post text is ignored.

    Weak calls to open a page do not prove a paywall. They yield an unresolved state
    rather than laundering a stored full label. No absence-of-marker claim upgrades
    a previously partial or unknown body to full.
    """
    view = analysis_view(body)
    markers, ignored = [], []
    spans = set()
    patterns = [(*row, "strong") for row in _BOUNDARIES] + [(_WEAK_BOUNDARY, "read the full post", "weak")]
    for pattern, reason, strength in patterns:
        for match in pattern.finditer(view.text):
            if (match.start(), match.end()) in spans:
                continue
            spans.add((match.start(), match.end()))
            start = view.text.rfind("\n", 0, match.start()) + 1
            before = view.text[max(start, match.start()-160):match.start()]
            after = view.text[match.end():match.end()+160]
            quoted = view.text[start:match.start()].lstrip().startswith(">")
            for opening, closing in [('"', '"'), ('“', '”')]:
                opened = (before.count(opening) % 2 == 1 if opening == closing else
                          before.rfind(opening) > before.rfind(closing))
                quoted = quoted or (opened and closing in after)
            other = bool(re.search(r"\b(?:related|other|another|recommended)\s+(?:post|article)\b", before, re.I))
            row = {**view.locator(match.start(), match.end()), "reason": reason, "strength": strength}
            if quoted or other:
                ignored.append({**row, "ignored_basis": "explicit_quote_or_other_article"})
            else:
                markers.append(row)
    strong = [row for row in markers if row["strength"] == "strong"]
    if strong:
        boundary = min(row["start"] for row in strong)
        visible_prefix = analysis_view(body[:boundary]).text
        state = "partial" if substantive_preview(visible_prefix) else "teaser"
        reason = strong[0]["reason"]
    elif markers:
        state, reason = "unknown", "access_boundary_unresolved"
    else:
        state, reason = stored, ""
        if stored == "partial" and not substantive_preview(view.text):
            state, reason = "teaser", "insufficient_visible_preview"
    return {"completeness": state, "reason": reason, "stored": stored,
            "conflict": stored == "full" and state != "full",
            "markers": markers, "ignored_markers": ignored,
            "policy_version": COMPLETENESS_VERSION, "effective_chars": len(view.text)}


def interpretation_fingerprint(*, version_id: str, title: str, published_at: str,
                               completeness: str, quality: dict, extractor: dict | None = None) -> str:
    """Reuse requires identical body, parsing, visibility and extraction policy."""
    import json

    provenance = quality.get("provenance") or {}
    semantic_quality = {key: quality.get(key) for key in (
        "policy_version", "cleaner_version", "original_hash", "entity_evidence", "field_evidence")}
    semantic_quality["source_fields"] = {key: provenance.get(key) for key in (
        "title_source", "title_quality", "published_at_source", "lede", "subtitle", "parser_version")}
    semantic_quality["visibility"] = {key: (quality.get("visibility") or {}).get(key)
                                      for key in ("policy_version", "markers", "ignored_markers")}
    return hashlib.sha256(json.dumps({"version_id": version_id, "title": title,
        "published_at": published_at, "completeness": completeness, "quality": semantic_quality,
        "extractor": extractor or {}, "parser": POLICY_VERSION,
        "visible_view": VISIBLE_VIEW_VERSION,
        "completeness_policy": COMPLETENESS_VERSION}, sort_keys=True,
        ensure_ascii=False).encode()).hexdigest()
