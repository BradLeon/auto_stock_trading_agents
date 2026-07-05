"""Correlation clustering — the AI-theme crowding blind spot.

Trailing pairwise correlation from price series, greedy union-find clustering on
corr > threshold, then each cluster's total portfolio weight. Pure functions.
"""

from __future__ import annotations

import logging

log = logging.getLogger("ats.risk.correlation")


def _returns(closes: list[float]) -> list[float]:
    return [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))
            if closes[i - 1]]


def _pearson(a: list[float], b: list[float]) -> float | None:
    n = min(len(a), len(b))
    if n < 20:
        return None
    a, b = a[-n:], b[-n:]
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return None
    return cov / (va ** 0.5 * vb ** 0.5)


def corr_matrix(prices: dict[str, list[float]]) -> dict[tuple[str, str], float]:
    """Pairwise return correlation for symbols with enough overlapping data."""
    rets = {s: _returns(c) for s, c in prices.items() if len(c) > 20}
    syms = sorted(rets)
    out: dict[tuple[str, str], float] = {}
    for i, s1 in enumerate(syms):
        for s2 in syms[i + 1:]:
            c = _pearson(rets[s1], rets[s2])
            if c is not None:
                out[(s1, s2)] = round(c, 3)
    return out


def clusters(weights: dict[str, float], prices: dict[str, list[float]],
             threshold: float = 0.7) -> list[dict]:
    """Union-find on corr>threshold; return clusters sorted by total weight desc.
    Each: {members, weight, avg_corr}. Singletons included (so every holding maps)."""
    cm = corr_matrix(prices)
    syms = list(weights)
    parent = {s: s for s in syms}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (s1, s2), c in cm.items():
        if s1 in parent and s2 in parent and c >= threshold:
            parent[find(s1)] = find(s2)

    groups: dict[str, list[str]] = {}
    for s in syms:
        groups.setdefault(find(s), []).append(s)

    out = []
    for members in groups.values():
        w = sum(weights.get(m, 0.0) for m in members)
        pairs = [cm.get((a, b)) or cm.get((b, a))
                 for i, a in enumerate(members) for b in members[i + 1:]]
        pairs = [p for p in pairs if p is not None]
        out.append({"members": sorted(members), "weight": round(w, 4),
                    "avg_corr": round(sum(pairs) / len(pairs), 3) if pairs else 0.0})
    return sorted(out, key=lambda c: c["weight"], reverse=True)
