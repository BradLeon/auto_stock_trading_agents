"""SEC and official disclosure provider access under the unified namespace."""

from ats.data.sec import (
    SecFetchFailure,
    SecFetchResult,
    SecIndexDocument,
    SecRecordResult,
    earnings_release,
    earnings_release_record,
    earnings_release_result,
    exhibit_result,
    exhibit_text,
    foreign_regulatory_result,
    issuer_filing_regime,
    periodic_filing_result,
    primary_filing_result,
)

__all__ = [
    "SecFetchFailure",
    "SecFetchResult",
    "SecIndexDocument",
    "SecRecordResult",
    "earnings_release",
    "earnings_release_record",
    "earnings_release_result",
    "exhibit_result",
    "exhibit_text",
    "foreign_regulatory_result",
    "issuer_filing_regime",
    "periodic_filing_result",
    "primary_filing_result",
]
