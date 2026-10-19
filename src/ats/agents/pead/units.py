"""Currency and unit reading for the event comparison and the actuals extraction (2026-10-19).

A bare number is not a comparison; see `fundamental/event.compute_tri_diffs`.
"""
from __future__ import annotations

import re

# A bare number is not a comparison: "5" (billion euro) against "5" (a count), or 46.7 (USD billions) against
# 1.45 (TWD trillions), produced differences of +32000% on TSM and ASML in the first run with a baseline
# (2026-10-19). Two quantities are compared only when both state a unit and the units are the same.
_CURRENCIES = (("TWD", r"NT\$|TWD|新台币|台币"), ("USD", r"US\$|USD|美元|(?<![A-Za-z])\$"), ("EUR", r"EUR|€|欧元"),
               ("KRW", r"KRW|₩|韩元"), ("JPY", r"JPY|日元"), ("CNY", r"CNY|RMB|人民币"))
_SCALES = ((1e12, r"万亿|trillion|tn\b|T\b"), (1e9, r"十亿|billion|bn\b|B\b"), (1e8, r"亿"),
           (1e6, r"百万|million|mn\b|M\b"), (1e4, r"万"))
# the local currency a company's consensus revenue is quoted in; everything else defaults to USD
LOCAL_CURRENCY = {"ASML": "EUR", "TSM": "TWD", "SKHY": "KRW", "000660.KS": "KRW"}


def _currency_in(text: str) -> str:
    for code, pattern in _CURRENCIES:
        if re.search(pattern, text, re.I):
            return code
    return ""


def mixes_currencies(text: object) -> bool:
    """True when one reading names more than one currency ("NT$1.27万亿（约 US$46.7B）"): the second figure is a
    conversion the extractor made at a rate of its own choosing (TSM Q3, 2026-10-19: US$46.7B turned into NT$1.27T
    at 27.2 when the quarter's rate was near 32). A converted figure is not a reading of the company's results."""
    return len({code for code, pattern in _CURRENCIES if re.search(pattern, str(text or ""), re.I)}) > 1


def quantity(text: object) -> tuple[float, str] | None:
    """(value in base units, unit) for the first number that carries a currency or a percent sign; else None."""
    raw = str(text or "").replace(",", "")
    for match in re.finditer(r"(?<![\w.])(-?\d+(?:\.\d+)?)\s*(%|万亿|十亿|百万|亿|万|trillion|billion|million|tn|bn|mn|bp|T|B|M)?",
                             raw):
        number = float(match.group(1))
        if match.group(2) is None and 1900 <= abs(number) <= 2100:
            continue                                  # a year, not a reading
        before, after = raw[max(0, match.start() - 6):match.start()], raw[match.end():match.end() + 8]
        if match.group(2) == "%":
            return number, "pct"
        currency = _currency_in(before + " " + after)
        if not currency:
            continue
        scale = 1.0
        if match.group(2):
            for factor, pattern in _SCALES:
                if re.fullmatch(pattern, match.group(2), re.I):
                    scale = factor
                    break
        return number * scale, currency
    return None


