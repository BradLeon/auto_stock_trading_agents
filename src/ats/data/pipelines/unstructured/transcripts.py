"""DefeatBeta earnings-call identity and ordered-speaker validation."""
from typing import Any

from ...defeatbeta import Paragraph, _render


def parse_transcript(row: dict[str, Any]) -> tuple[str, str, str]:
    symbol = str(row.get("symbol") or "").upper()
    year = int(row.get("fiscal_year") or 0)
    quarter = int(row.get("fiscal_quarter") or 0)
    segments = row.get("transcripts") or []
    if not symbol or not 2000 <= year <= 2200 or quarter not in {1, 2, 3, 4}:
        raise ValueError("transcript_period_invalid")
    if not isinstance(segments, list) or not segments:
        raise ValueError("transcript_segments_missing")
    rendered = []
    seen: set[int] = set()
    for part in segments:
        if not isinstance(part, dict):
            raise TypeError("transcript_segment_invalid")
        # Historical and current DefeatBeta exports use different ordinal keys.
        value = next((part[key] for key in ("paragraph_number", "paragraph_order", "ordinal")
                      if key in part and part[key] is not None), None)
        if value is None:
            raise ValueError("transcript_segment_order_missing")
        ordinal = int(value)
        speaker = str(part.get("speaker") or "").strip()
        content = str(part.get("content") or "").strip()
        if ordinal < 0 or ordinal in seen or not speaker or not content:
            raise ValueError("transcript_segment_invalid")
        seen.add(ordinal)
        rendered.append((ordinal, Paragraph(ordinal, speaker, content)))
    rendered.sort()
    if [ordinal for ordinal, _ in rendered] != list(
            range(rendered[0][0], rendered[0][0] + len(rendered))):
        raise ValueError("transcript_segment_order_gap")
    return symbol, f"Q{quarter} FY{year}", _render(part for _, part in rendered)
