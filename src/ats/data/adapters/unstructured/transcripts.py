"""Earnings-call transcript provider access under the unified namespace."""

from ats.data.transcript import (
    extract_body,
    fetch,
    looks_like_transcript,
    manual_path,
)

__all__ = ["extract_body", "fetch", "looks_like_transcript", "manual_path"]
