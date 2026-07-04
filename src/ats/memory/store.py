"""Context Memory — SQLite structured store.

Persists each cycle's reports, decisions, trades, and performance so the Manager
can be fed prior outcomes and the Boss can pull a name's history on demand. The
semantic/vector layer (Chroma) is a later add; this is the structured backbone.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from ..schemas.memory import PerformanceRecord

if TYPE_CHECKING:
    from ..graph.state import TradingState

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (
    cycle_id TEXT PRIMARY KEY, as_of TEXT, approval_status TEXT, manager_summary TEXT
);
CREATE TABLE IF NOT EXISTS reports (
    cycle_id TEXT, role TEXT, symbol TEXT, signal TEXT, conviction REAL, thesis TEXT, as_of TEXT
);
CREATE TABLE IF NOT EXISTS decisions (
    cycle_id TEXT, symbol TEXT, action TEXT, notional_usd REAL, limit_price REAL,
    conviction REAL, rationale TEXT
);
CREATE TABLE IF NOT EXISTS trades (
    order_id TEXT, cycle_id TEXT, symbol TEXT, action TEXT, qty REAL, order_type TEXT,
    status TEXT, avg_fill_price REAL, submitted_at TEXT, rationale TEXT
);
CREATE TABLE IF NOT EXISTS performance (
    cycle_id TEXT, as_of TEXT, net_liquidation REAL, daily_pnl REAL, cumulative_pnl REAL,
    realized_pnl REAL, unrealized_pnl REAL, num_positions INTEGER, payload TEXT
);
CREATE TABLE IF NOT EXISTS pead_dossier (
    symbol TEXT, fiscal_label TEXT, phase TEXT, payload TEXT, updated_at TEXT,
    PRIMARY KEY (symbol, fiscal_label)
);
CREATE TABLE IF NOT EXISTS pead_events (
    id TEXT PRIMARY KEY, symbol TEXT, published_at TEXT, source TEXT,
    headline TEXT, url TEXT, processed INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS research_articles (
    id TEXT PRIMARY KEY, source TEXT, title TEXT, url TEXT,
    published_at TEXT, fetched_at TEXT, chars INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS research_insights (
    article_id TEXT, ticker TEXT, direction TEXT, impact_path TEXT,
    summary TEXT, evidence_quote TEXT, confidence REAL, created_at TEXT
);
CREATE TABLE IF NOT EXISTS sector_reviews (
    sector TEXT, as_of TEXT, regime TEXT, summary TEXT, payload TEXT,
    PRIMARY KEY (sector, as_of)
);
CREATE TABLE IF NOT EXISTS macro_reviews (
    name TEXT, as_of TEXT, regime TEXT, summary TEXT, payload TEXT,
    PRIMARY KEY (name, as_of)
);
CREATE INDEX IF NOT EXISTS idx_insights_ticker ON research_insights(ticker);
CREATE INDEX IF NOT EXISTS idx_events_symbol ON pead_events(symbol);
CREATE INDEX IF NOT EXISTS idx_reports_symbol ON reports(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
"""


class TradingMemory:
    def __init__(self, db_path: str | Path):
        self.path = str(db_path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Additive column migrations (kept out of _SCHEMA so old DBs get them too)."""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(pead_events)")}
        for ddl in ("triage_score REAL", "triage_category TEXT"):
            if ddl.split()[0] not in cols:
                self.conn.execute(f"ALTER TABLE pead_events ADD COLUMN {ddl}")
        self.conn.commit()

    # --- writes ---------------------------------------------------------- #
    def save_cycle(self, state: "TradingState", performance: PerformanceRecord) -> None:
        c = self.conn
        c.execute("INSERT OR REPLACE INTO cycles VALUES (?,?,?,?)",
                  (state.cycle_id, state.as_of.isoformat(),
                   getattr(state.approval, "status", None), state.manager_summary))

        reports = []
        if state.macro_report:
            reports.append(("macro_analyst", None, state.macro_report))
        for r in state.industry_reports:
            reports.append(("industry_analyst", r.sector, r))
        for r in state.fundamental_reports:
            reports.append(("fundamental_analyst", r.symbol, r))
        for r in state.technical_reports:
            reports.append(("technical_analyst", r.symbol, r))
        c.executemany(
            "INSERT INTO reports VALUES (?,?,?,?,?,?,?)",
            [(state.cycle_id, role, sym, r.signal, r.conviction, r.thesis, r.as_of.isoformat())
             for role, sym, r in reports])

        c.executemany(
            "INSERT INTO decisions VALUES (?,?,?,?,?,?,?)",
            [(state.cycle_id, d.symbol, d.action, d.notional_usd, d.limit_price, d.conviction,
              d.rationale) for d in state.decisions])

        c.executemany(
            "INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(t.order_id, state.cycle_id, t.symbol, t.action, t.qty, t.order_type, t.status,
              t.avg_fill_price, t.submitted_at.isoformat() if t.submitted_at else None,
              t.rationale) for t in state.order_results])

        p = performance
        c.execute("INSERT INTO performance VALUES (?,?,?,?,?,?,?,?,?)",
                  (p.cycle_id, p.as_of.isoformat(), p.net_liquidation, p.daily_pnl,
                   p.cumulative_pnl, p.realized_pnl, p.unrealized_pnl, p.num_positions,
                   p.model_dump_json()))
        c.commit()

    # --- reads ----------------------------------------------------------- #
    def last_performance(self) -> PerformanceRecord | None:
        row = self.conn.execute(
            "SELECT payload FROM performance ORDER BY rowid DESC LIMIT 1").fetchone()
        return PerformanceRecord.model_validate_json(row["payload"]) if row else None

    def performance_history(self, limit: int = 30) -> list[PerformanceRecord]:
        rows = self.conn.execute(
            "SELECT payload FROM performance ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        return [PerformanceRecord.model_validate_json(r["payload"]) for r in rows]

    def recent_reports(self, symbol: str, limit: int = 5) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM reports WHERE symbol = ? ORDER BY rowid DESC LIMIT ?",
            (symbol, limit)).fetchall()
        return [dict(r) for r in rows]

    def recent_trades(self, symbol: str | None = None, limit: int = 10) -> list[dict]:
        if symbol:
            rows = self.conn.execute(
                "SELECT * FROM trades WHERE symbol = ? ORDER BY rowid DESC LIMIT ?",
                (symbol, limit)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM trades ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # --- PEAD dossier ---------------------------------------------------- #
    def save_dossier(self, dossier) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO pead_dossier VALUES (?,?,?,?,?)",
            (dossier.symbol, dossier.fiscal_label, dossier.phase,
             dossier.model_dump_json(), dossier.updated_at.isoformat()))
        self.conn.commit()

    def get_dossier(self, symbol: str, fiscal_label: str):
        from ..schemas.pead import PeadDossier

        row = self.conn.execute(
            "SELECT payload FROM pead_dossier WHERE symbol = ? AND fiscal_label = ?",
            (symbol, fiscal_label)).fetchone()
        return PeadDossier.model_validate_json(row["payload"]) if row else None

    def recent_dossiers(self, symbol: str | None = None, limit: int = 10) -> list[dict]:
        if symbol:
            rows = self.conn.execute(
                "SELECT symbol, fiscal_label, phase, updated_at FROM pead_dossier "
                "WHERE symbol = ? ORDER BY updated_at DESC LIMIT ?", (symbol, limit)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT symbol, fiscal_label, phase, updated_at FROM pead_dossier "
                "ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # --- PEAD event log -------------------------------------------------- #
    def append_events(self, symbol: str, items) -> list:
        """Insert news items not already stored; return the genuinely-new ones."""
        if not items:
            return []
        existing = {r["id"] for r in self.conn.execute(
            "SELECT id FROM pead_events WHERE id IN (%s)" % ",".join("?" * len(items)),
            [i.id for i in items]).fetchall()}
        fresh = []
        for i in items:                     # also dedup within the batch itself
            if i.id not in existing:
                existing.add(i.id)
                fresh.append(i)
        self.conn.executemany(
            "INSERT OR IGNORE INTO pead_events (id,symbol,published_at,source,headline,url) "
            "VALUES (?,?,?,?,?,?)",
            [(i.id, symbol, i.published_at.isoformat(), i.source, i.headline, i.url) for i in fresh])
        self.conn.commit()
        return fresh

    def recent_events(self, symbol: str, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM pead_events WHERE symbol = ? ORDER BY published_at DESC LIMIT ?",
            (symbol, limit)).fetchall()
        return [dict(r) for r in rows]

    def count_events(self, symbol: str) -> int:
        return self.conn.execute("SELECT COUNT(*) c FROM pead_events WHERE symbol = ?",
                                 (symbol,)).fetchone()["c"]

    def set_triage(self, scores: dict[str, tuple[float, str]]) -> None:
        """Persist triage results: {event_id: (materiality, category)}."""
        if not scores:
            return
        self.conn.executemany(
            "UPDATE pead_events SET triage_score = ?, triage_category = ? WHERE id = ?",
            [(score, cat, eid) for eid, (score, cat) in scores.items()])
        self.conn.commit()

    # --- research (newsletters) ------------------------------------------ #
    def article_seen(self, article_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM research_articles WHERE id = ?",
                                 (article_id,)).fetchone() is not None

    def save_article(self, art) -> None:
        """Store article metadata only (bodies are not persisted)."""
        from datetime import datetime, timezone

        self.conn.execute(
            "INSERT OR IGNORE INTO research_articles VALUES (?,?,?,?,?,?,?)",
            (art.id, art.source, art.title, art.url, art.published_at.isoformat(),
             datetime.now(timezone.utc).isoformat(), len(art.body)))
        self.conn.commit()

    def save_insights(self, article_id: str, insights) -> None:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        self.conn.executemany(
            "INSERT INTO research_insights VALUES (?,?,?,?,?,?,?,?)",
            [(article_id, i.ticker, i.direction, i.impact_path, i.summary,
              i.evidence_quote, i.confidence, now) for i in insights])
        self.conn.commit()

    def recent_insights(self, ticker: str | None = None, limit: int = 20) -> list[dict]:
        if ticker:
            rows = self.conn.execute(
                "SELECT * FROM research_insights WHERE ticker = ? ORDER BY rowid DESC LIMIT ?",
                (ticker, limit)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM research_insights ORDER BY rowid DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(r) for r in rows]

    # --- sector reviews --------------------------------------------------- #
    def save_sector_review(self, review) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO sector_reviews VALUES (?,?,?,?,?)",
            (review.sector, review.as_of.isoformat(), review.regime, review.summary,
             review.model_dump_json()))
        self.conn.commit()

    def latest_sector_review(self, sector: str):
        from ..schemas.sector import SectorReview

        row = self.conn.execute(
            "SELECT payload FROM sector_reviews WHERE sector = ? ORDER BY as_of DESC LIMIT 1",
            (sector,)).fetchone()
        return SectorReview.model_validate_json(row["payload"]) if row else None

    def recent_sector_reviews(self, sector: str, limit: int = 8) -> list[dict]:
        rows = self.conn.execute(
            "SELECT sector, as_of, regime, summary FROM sector_reviews "
            "WHERE sector = ? ORDER BY as_of DESC LIMIT ?", (sector, limit)).fetchall()
        return [dict(r) for r in rows]

    # --- macro reviews ---------------------------------------------------- #
    def save_macro_review(self, review) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO macro_reviews VALUES (?,?,?,?,?)",
            (review.name, review.as_of.isoformat(), review.regime, review.summary,
             review.model_dump_json()))
        self.conn.commit()

    def latest_macro_review(self, name: str = "macro"):
        from ..schemas.macro_strategy import MacroReview

        row = self.conn.execute(
            "SELECT payload FROM macro_reviews WHERE name = ? ORDER BY as_of DESC LIMIT 1",
            (name,)).fetchone()
        return MacroReview.model_validate_json(row["payload"]) if row else None

    def recent_macro_reviews(self, name: str = "macro", limit: int = 8) -> list[dict]:
        rows = self.conn.execute(
            "SELECT name, as_of, regime, summary FROM macro_reviews "
            "WHERE name = ? ORDER BY as_of DESC LIMIT ?", (name, limit)).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self.conn.close()
