"""Local price store for research replays.

Two reasons this exists rather than hitting yfinance each run:
  · reproducibility — a backtest whose inputs change between runs cannot be
    compared across iterations, and silently-revised vendor history would make
    yesterday's conclusion unreproducible today.
  · iteration speed, and not hammering the vendor while tuning a report.

Deliberately a separate DB from var/ats.sqlite: this is disposable research
cache, not system-of-record. Deleting it must never affect the trading system.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date
from pathlib import Path

log = logging.getLogger("ats.research.prices")

DB_PATH = Path("var/research_prices.sqlite")
_DDL = """
CREATE TABLE IF NOT EXISTS prices (
    symbol TEXT NOT NULL,
    d      TEXT NOT NULL,
    close  REAL NOT NULL,
    PRIMARY KEY (symbol, d)
);
"""


def _conn(path: Path | None = None) -> sqlite3.Connection:
    p = Path(path or DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(p))
    db.execute(_DDL)
    return db


def store(rows: dict[str, dict[date, float]], *, path: Path | None = None) -> int:
    """Upsert {symbol: {date: close}}. Returns rows written."""
    db = _conn(path)
    n = 0
    with db:
        for sym, series in rows.items():
            db.executemany(
                "INSERT INTO prices(symbol,d,close) VALUES (?,?,?) "
                "ON CONFLICT(symbol,d) DO UPDATE SET close=excluded.close",
                [(sym, d.isoformat(), float(c)) for d, c in series.items()])
            n += len(series)
    db.close()
    return n


def load(symbols: list[str], start: str, end: str, *, path: Path | None = None
         ) -> tuple[list[date], dict[str, list[float]]]:
    """Aligned closes for `symbols` over [start, end].

    Only dates where EVERY requested symbol has a close are returned — a replay
    over a ragged panel would silently change basket composition mid-run.
    """
    db = _conn(path)
    per: dict[str, dict[date, float]] = {}
    for sym in symbols:
        rows = db.execute(
            "SELECT d, close FROM prices WHERE symbol=? AND d BETWEEN ? AND ? ORDER BY d",
            (sym, start, end)).fetchall()
        per[sym] = {date.fromisoformat(d): c for d, c in rows}
    db.close()
    if not per or any(not v for v in per.values()):
        return [], {}
    common = sorted(set.intersection(*(set(v) for v in per.values())))
    return common, {s: [per[s][d] for d in common] for s in symbols}


def coverage(*, path: Path | None = None) -> list[tuple[str, int, str, str]]:
    db = _conn(path)
    rows = db.execute(
        "SELECT symbol, COUNT(*), MIN(d), MAX(d) FROM prices GROUP BY symbol ORDER BY symbol"
    ).fetchall()
    db.close()
    return rows


def fetch_and_store(symbols: list[str], start: str, end: str,
                    *, path: Path | None = None) -> int:
    """Download from yfinance and persist. Network call — the only one here."""
    import warnings

    import yfinance as yf

    warnings.filterwarnings("ignore")
    df = yf.download(symbols, start=start, end=end, interval="1d",
                     auto_adjust=True, progress=False)["Close"]
    if df is None or df.empty:
        log.warning("research prices: empty download for %s", symbols)
        return 0
    rows: dict[str, dict[date, float]] = {}
    for sym in symbols:
        col = df[sym].dropna() if sym in df else None
        if col is None or col.empty:
            log.warning("research prices: no data for %s", sym)
            continue
        rows[sym] = {i.date(): float(v) for i, v in col.items()}
    return store(rows, path=path)


def ensure(symbols: list[str], start: str, end: str, *, path: Path | None = None
           ) -> tuple[list[date], dict[str, list[float]]]:
    """Load from cache; download only the symbols that are missing or short."""
    have = {r[0]: r for r in coverage(path=path)}
    missing = [s for s in symbols
               if s not in have or have[s][2] > start or have[s][3] < end[:10]]
    if missing:
        log.info("research prices: fetching %s", missing)
        fetch_and_store(missing, start, end, path=path)
    return load(symbols, start, end, path=path)
