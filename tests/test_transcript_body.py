"""Chrome stripping + head/tail clipping, measured against a REAL saved transcript.

The regression these lock down: a manually-saved investing.com transcript is 52%
page furniture (nav/summary before the body, ad pixels after). With head-only
truncation at 40k chars, only 7% of the actual transcript body reached the
extractor — the entire Q&A and guidance sections were silently discarded.
"""

from __future__ import annotations

import pytest

from ats.agents.pead.score import _clip
from ats.config import REPO_ROOT
from ats.data.transcript import extract_body, looks_like_transcript

GOOG_FILE = REPO_ROOT / "var" / "transcripts" / "GOOG_Q22026.txt"
needs_file = pytest.mark.skipif(not GOOG_FILE.exists(),
                                reason="real transcript sample not on disk")


@pytest.fixture
def raw() -> str:
    return GOOG_FILE.read_text(encoding="utf-8")


@needs_file
def test_strips_chrome_from_both_ends(raw):
    body, note = extract_body(raw, "file:GOOG_Q22026.txt")

    assert "Full transcript" in body[:200]          # starts at the section heading
    assert "Conference Call Operator" in body[:300]  # ...immediately followed by the call
    assert body.rstrip().endswith("You may now disconnect.")
    # The ad-pixel tail and the nav head are both gone.
    assert "doubleclick.net" not in body
    assert "Popular Searches" not in body
    assert len(body) < len(raw) * 0.6
    assert "chrome-strip" in note


@needs_file
def test_stripped_body_still_passes_the_score_guard(raw):
    body, _ = extract_body(raw, "")
    ok, why = looks_like_transcript(body)
    assert ok, why
    # Q&A must survive — it carries the analyst pushback the scorecard reads.
    assert any(m in body.lower() for m in ("next question", "question-and-answer"))


@needs_file
def test_full_body_fits_the_default_cap(raw):
    """The point of the fix: at the configured cap the transcript is NOT clipped."""
    body, _ = extract_body(raw, "")
    assert _clip(body, 120_000) == body


@needs_file
def test_clip_keeps_both_ends(raw):
    """Under a cap tight enough to bite, prepared remarks AND Q&A both survive."""
    body, _ = extract_body(raw, "")
    clipped = _clip(body, 20_000)

    assert len(clipped) < len(body)
    assert "省略" in clipped                                  # gap is marked, not silent
    assert "Conference Call Operator" in clipped[:2000]       # head kept
    tail = clipped[-8000:]
    assert any(m in tail.lower() for m in ("next question", "question-and-answer",
                                           "disconnect")), "tail (Q&A) was dropped"


def test_extract_body_never_makes_input_worse():
    """A body with no recognizable markers is returned untouched, not mangled."""
    plain = "Some earnings summary prose. " * 200
    body, note = extract_body(plain, "")
    assert body == plain.strip()
    assert note == ""


def test_extract_body_refuses_an_implausible_trim():
    """If the markers match but leave a stub, keep the original and say why."""
    text = ("nav chrome " * 400) + "Prepared Remarks:\nDuration: 42 minutes\n" + ("footer " * 400)
    body, note = extract_body(text, "")
    assert body == text.strip()          # kept whole
    assert note.startswith("⚠️")         # and flagged
