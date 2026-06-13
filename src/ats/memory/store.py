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

    def close(self) -> None:
        self.conn.close()
