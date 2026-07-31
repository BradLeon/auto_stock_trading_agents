"""Context Memory — SQLite structured store.

Persists each cycle's reports, decisions, trades, and performance so the Manager
can be fed prior outcomes and the Boss can pull a name's history on demand. The
semantic/vector layer (Chroma) is a later add; this is the structured backbone.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..schemas.memory import PerformanceRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (
    cycle_id TEXT PRIMARY KEY, as_of TEXT, approval_status TEXT, manager_summary TEXT
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

CREATE TABLE IF NOT EXISTS technical_reviews (
    name TEXT, as_of TEXT, summary TEXT, payload TEXT,
    PRIMARY KEY (name, as_of)
);
CREATE TABLE IF NOT EXISTS fills (
    exec_id TEXT PRIMARY KEY, symbol TEXT, side TEXT, shares REAL, price REAL,
    time TEXT, realized_pnl REAL, commission REAL, order_id TEXT
);
CREATE TABLE IF NOT EXISTS risk_reviews (
    as_of TEXT PRIMARY KEY, risk_state TEXT, summary TEXT, payload TEXT
);
CREATE TABLE IF NOT EXISTS score_consumption (
    symbol TEXT, fiscal_label TEXT, consumed_at TEXT, cycle_id TEXT,
    PRIMARY KEY (symbol, fiscal_label)
);
-- One row per detected earnings print. Caches the resolved fiscal_label so the
-- quarter key cannot drift between the score write and a later Chief read (the
-- upstream calendars do revise quarter/year), and records which source decided
-- the session, for audit.
CREATE TABLE IF NOT EXISTS pead_periods (
    symbol TEXT, earnings_date TEXT, fiscal_label TEXT, quarter INTEGER, year INTEGER,
    session TEXT, session_source TEXT, at TEXT, eps_actual REAL, rev_actual REAL,
    label_source TEXT, detected_at TEXT,
    PRIMARY KEY (symbol, earnings_date)
);
-- Versioned score ledger. Two jobs: (1) the idempotency guard, so a symbol whose
-- session is unknown can be attempted in BOTH daily windows without double-scoring;
-- (2) the v1(no transcript) -> v2(with transcript) upgrade record. Note this is the
-- only reliable "when was it scored" signal — pead_dossier.updated_at is bumped by
-- the daily monitor and so never goes stale.
-- `final` = this score is the one the Chief may act on. A transcript-backed score is
-- final immediately; a transcript-less v1 is NOT (it is deliberately withheld from the
-- Chief while we retry for the transcript), and gets promoted at the upgrade deadline.
CREATE TABLE IF NOT EXISTS pead_score_runs (
    symbol TEXT, fiscal_label TEXT, version INTEGER, scored_at TEXT, earnings_date TEXT,
    has_transcript INTEGER DEFAULT 0, transcript_source TEXT, window TEXT,
    latency_hours REAL, total REAL, band TEXT, decision_summary TEXT,
    final INTEGER DEFAULT 0,
    PRIMARY KEY (symbol, fiscal_label, version)
);
-- ---------------------------------------------------------------------------
-- Trade journal
-- ---------------------------------------------------------------------------
-- One row per INTENT, not per fill. This matters: of the first 52 order rows,
-- 24 errored and 16 were cancelled — orders evaporating (IBKR down, approval
-- declined) is this system's most common real outcome, and a journal of fills
-- only would hide it completely.
CREATE TABLE IF NOT EXISTS journal_entries (
    entry_id TEXT PRIMARY KEY,            -- = trades.client_order_id
    cycle_id TEXT, as_of TEXT, symbol TEXT, action TEXT, source TEXT, setup TEXT,
    -- plan, pre-registered at decision time and never rewritten
    intended_notional REAL, intended_qty REAL, conviction REAL,
    order_type TEXT, limit_price REAL,
    stop_price REAL, target_price REAL, planned_horizon_days INTEGER,
    invalidation TEXT, planned_risk_usd REAL, risk_unit_source TEXT, rationale TEXT,
    -- regime SNAPSHOT (denormalised on purpose: a later review rewrite must not
    -- rewrite what we believed at the time)
    regime_risk_state TEXT,
    -- evidence quality — the agent-native "did I follow my plan"
    ev_score_total REAL, ev_score_band TEXT, ev_has_transcript INTEGER,
    ev_score_latency_h REAL, ev_expected_move_pct REAL,
    -- gates
    risk_notes_json TEXT,
    approval_status TEXT, approval_reviewer TEXT, approval_comment TEXT,
    approval_divergence TEXT,
    -- execution rollup
    submit_attempts INTEGER DEFAULT 0, terminal_status TEXT,
    filled_qty REAL, avg_fill_price REAL, slippage_bps REAL
);
-- Falsifiable claims, recorded when made. entry_id NULL = we predicted but did not
-- trade, which is exactly the sample that keeps calibration unbiased.
-- ref_date is the first session we could ACT on the claim (when the score was made),
-- NOT the print date. The earnings gap is not capturable — you cannot buy at the
-- pre-announcement close after seeing the result — and PEAD is by definition the
-- POST-announcement drift. print_date is kept alongside so the gap itself, a separate
-- and non-tradeable quantity, can still be measured.
CREATE TABLE IF NOT EXISTS predictions (
    prediction_id TEXT PRIMARY KEY, made_at TEXT, symbol TEXT,
    source TEXT, ref_key TEXT, kind TEXT,
    predicted_value REAL, predicted_band TEXT,
    ref_price REAL, ref_date TEXT, print_date TEXT,
    sector_etf TEXT, benchmark TEXT, entry_id TEXT
);
-- One row per horizon. All horizons are KEPT: short/long disagreement (right at
-- T+1, wrong at T+20 = right entry, wrong holding period) is the signal, not noise
-- to be collapsed into a single verdict.
CREATE TABLE IF NOT EXISTS prediction_outcomes (
    prediction_id TEXT, horizon_days INTEGER, as_of TEXT,
    realized_pct REAL, excess_vs_sector_pct REAL, excess_vs_bench_pct REAL,
    PRIMARY KEY (prediction_id, horizon_days)
);
-- One round trip: net position 0 -> nonzero -> back to 0. Adds/trims are LEGS of the
-- same episode, not new episodes — this is what fixes the old analytics counting a
-- scaled-out position as N trades. No separate episode_legs table: a leg IS a fill
-- plus which episode/entry it belongs to, so `fills.episode_id`/`fills.entry_id`
-- (below) carry that, and a second table would just be redundant (Occam).
CREATE TABLE IF NOT EXISTS trade_episodes (
    episode_id TEXT PRIMARY KEY, symbol TEXT, direction TEXT, origin TEXT,
    status TEXT,
    opened_at TEXT, closed_at TEXT, avg_entry REAL, avg_exit REAL,
    realized_pnl REAL, unrealized_pnl REAL, commission REAL, basis_source TEXT,
    holding_days INTEGER, r_multiple REAL, r_multiple_mtm REAL, risk_unit_source TEXT,
    mae_pct REAL, mfe_pct REAL, mae_source TEXT, excess_vs_sector_pct REAL,
    setup TEXT, primary_entry_id TEXT, exit_reason TEXT, exit_as_planned INTEGER,
    invalidation_triggered INTEGER, horizon_overdue_days INTEGER
);
-- Single-row bookkeeping, e.g. tracking_start_date: before it we did not observe,
-- which is NOT the same as "no trades happened".
CREATE TABLE IF NOT EXISTS journal_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS idx_journal_symbol ON journal_entries(symbol);
CREATE INDEX IF NOT EXISTS idx_pred_symbol ON predictions(symbol);
CREATE INDEX IF NOT EXISTS idx_episode_symbol ON trade_episodes(symbol);
CREATE INDEX IF NOT EXISTS idx_insights_ticker ON research_insights(ticker);
CREATE INDEX IF NOT EXISTS idx_events_symbol ON pead_events(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol);
"""


class TradingMemory:
    def __init__(self, db_path: str | Path):
        self.path = str(db_path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: the store singleton is created on one thread but
        # read/written from uvicorn worker threads in `ats serve` (approval callback
        # → resume → persist). Without this, serve crashes mid-execution with
        # "SQLite objects created in a thread can only be used in that same thread"
        # and records bogus errors. timeout lets concurrent writers wait out a lock.
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Additive column migrations (kept out of _SCHEMA so old DBs get them too)."""
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(pead_events)")}
        for ddl in ("triage_score REAL", "triage_category TEXT"):
            if ddl.split()[0] not in cols:
                self.conn.execute(f"ALTER TABLE pead_events ADD COLUMN {ddl}")
        tcols = {r["name"] for r in self.conn.execute("PRAGMA table_info(trades)")}
        for ddl in ("limit_price REAL", "filled_at TEXT", "error TEXT",
                    "realized_pnl REAL", "source TEXT", "context TEXT"):
            if ddl.split()[0] not in tcols:
                self.conn.execute(f"ALTER TABLE trades ADD COLUMN {ddl}")
        scols = {r["name"] for r in self.conn.execute("PRAGMA table_info(pead_score_runs)")}
        if scols and "final" not in scols:
            # Pre-existing rows were all transcript-backed scores, so they are final.
            self.conn.execute("ALTER TABLE pead_score_runs ADD COLUMN final INTEGER DEFAULT 0")
            self.conn.execute("UPDATE pead_score_runs SET final = has_transcript")
        # Journal linkage. `client_order_id` is the idempotency key; `perm_id` /
        # `order_ref` are the durable broker identities (orderId is a per-client
        # sequence that TWS resets, so it cannot be joined on across days).
        # NOTE: `trades.entry_id` below is dead weight — client_order_id IS the
        # JournalEntry id by construction (see JournalEntry docstring), so nothing
        # ever needed a second column for it. Left in place rather than dropped:
        # SQLite column drops require a full table rebuild for zero benefit on an
        # already-deployed table. Use client_order_id, not this column.
        for ddl in ("client_order_id TEXT", "perm_id TEXT", "order_ref TEXT",
                    "attempt INTEGER DEFAULT 1", "entry_id TEXT", "first_submitted_at TEXT"):
            if ddl.split()[0] not in tcols:
                self.conn.execute(f"ALTER TABLE trades ADD COLUMN {ddl}")
        fcols = {r["name"] for r in self.conn.execute("PRAGMA table_info(fills)")}
        for ddl in ("perm_id TEXT", "order_ref TEXT", "origin TEXT",
                    "link_confidence TEXT", "captured_at TEXT",
                    "entry_id TEXT", "episode_id TEXT"):
            if ddl.split()[0] not in fcols:
                self.conn.execute(f"ALTER TABLE fills ADD COLUMN {ddl}")
        # Any column added to a table that already exists in the wild needs an ALTER —
        # CREATE TABLE IF NOT EXISTS in _SCHEMA is a no-op on an existing table.
        pcols = {r["name"] for r in self.conn.execute("PRAGMA table_info(predictions)")}
        if pcols and "print_date" not in pcols:
            self.conn.execute("ALTER TABLE predictions ADD COLUMN print_date TEXT")
        # NULLs are distinct in a SQLite unique index, so the 52 legacy rows (which
        # have no client_order_id) coexist with the constraint.
        self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_coid "
                          "ON trades(client_order_id)")
        self.conn.commit()

    # --- writes ---------------------------------------------------------- #
    _TRADE_COLS = ("order_id", "cycle_id", "symbol", "action", "qty", "order_type", "status",
                   "avg_fill_price", "submitted_at", "rationale", "limit_price", "filled_at",
                   "error", "realized_pnl", "source", "context", "perm_id", "order_ref")

    @staticmethod
    def client_order_id(cycle_id: str, symbol: str, action: str) -> str:
        """Deterministic idempotency key: one INTENT, one row.

        A cycle proposes at most one order per (symbol, action); repeated rows for the
        same triple are retries of the same intent, not new intents. Without this key
        the 2026-07-23 IBKR outage wrote the same GOOG/ASML/KLAC trim five times each.
        """
        return f"{cycle_id}:{symbol.upper()}:{action}"

    def _insert_trades(self, entries, *, cycle_id: str, source: str, context: str = "") -> None:
        """Upsert one row per intent, counting attempts instead of duplicating rows.

        On a repeat we keep whatever execution facts we already learned — a retry that
        errors must never erase a fill the first attempt achieved — and keep the
        original submit time, while advancing status/error and bumping `attempt`.
        """
        for t in entries:
            coid = self.client_order_id(cycle_id, t.symbol, t.action)
            submitted = t.submitted_at.isoformat() if t.submitted_at else None
            filled = t.filled_at.isoformat() if t.filled_at else None
            prior = self.conn.execute(
                "SELECT attempt, status, avg_fill_price, filled_at, order_id, first_submitted_at "
                "FROM trades WHERE client_order_id = ?", (coid,)).fetchone()
            if prior is None:
                self.conn.execute(
                    f"INSERT INTO trades ({','.join(self._TRADE_COLS)}, client_order_id, "
                    f"attempt, first_submitted_at) VALUES "
                    f"({','.join('?' * len(self._TRADE_COLS))},?,?,?)",
                    (t.order_id, cycle_id, t.symbol, t.action, t.qty, t.order_type, t.status,
                     t.avg_fill_price, submitted, t.rationale, t.limit_price, filled,
                     t.error, None, source, context,
                     getattr(t, "perm_id", "") or None, getattr(t, "order_ref", "") or None,
                     coid, 1, submitted))
                continue
            had_fill = prior["avg_fill_price"] is not None
            self.conn.execute(
                "UPDATE trades SET attempt = attempt + 1, status = ?, error = ?, "
                "order_id = ?, avg_fill_price = COALESCE(avg_fill_price, ?), "
                "filled_at = COALESCE(filled_at, ?), qty = ?, limit_price = ?, "
                "context = ?, submitted_at = ?, "
                "perm_id = COALESCE(?, perm_id), order_ref = COALESCE(?, order_ref) "
                "WHERE client_order_id = ?",
                (prior["status"] if had_fill else t.status, t.error,
                 t.order_id or prior["order_id"], t.avg_fill_price, filled,
                 t.qty, t.limit_price, context, submitted,
                 getattr(t, "perm_id", "") or None, getattr(t, "order_ref", "") or None,
                 coid))

    def save_trades(self, entries, *, cycle_id: str, source: str, context: str = "") -> None:
        """Persist trade-log entries with their full context (trader path, standalone)."""
        if not entries:
            return
        self._insert_trades(entries, cycle_id=cycle_id, source=source, context=context)
        self.conn.commit()

    def upsert_fills(self, fills: list[dict]) -> int:
        """Insert IBKR fills, deduped by exec_id (accumulates per-trade realized P&L)."""
        if not fills:
            return 0
        before = self.conn.execute("SELECT COUNT(*) c FROM fills").fetchone()["c"]
        # Column-explicit: `fills` gains journal columns over time, and a positional
        # INSERT silently breaks the moment one is added.
        self.conn.executemany(
            "INSERT OR IGNORE INTO fills (exec_id, symbol, side, shares, price, time, "
            "realized_pnl, commission, order_id, perm_id, order_ref, captured_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(f["exec_id"], f["symbol"], f.get("side", ""), f.get("shares", 0), f.get("price", 0),
              f.get("time", ""), f.get("realized_pnl"), f.get("commission", 0),
              f.get("order_id", ""), f.get("perm_id"), f.get("order_ref"),
              datetime.now(timezone.utc).isoformat())
             for f in fills])
        self.conn.commit()
        return self.conn.execute("SELECT COUNT(*) c FROM fills").fetchone()["c"] - before

    def set_cycle_approval(self, cycle_id: str, status: str) -> None:
        """Record the Boss's verdict once it is known.

        `save_chief_run` writes the cycle row BEFORE the approval interrupt (so a card
        the Boss never answers still leaves an audit trail), which is why
        approval_status was NULL on every cycle ever recorded — nothing came back to
        fill it in.
        """
        self.conn.execute("UPDATE cycles SET approval_status = ? WHERE cycle_id = ?",
                          (status, cycle_id))
        self.conn.commit()

    # --- journal entries (one row per INTENT) ------------------------------ #
    # The typed columns; `risk_notes` and `approval` are objects in Python and JSON
    # on disk, so they are mapped explicitly rather than dumped.
    _JOURNAL_COLS = (
        "entry_id", "cycle_id", "as_of", "symbol", "action", "source", "setup",
        "intended_notional", "intended_qty", "conviction", "order_type", "limit_price",
        "stop_price", "target_price", "planned_horizon_days", "invalidation",
        "planned_risk_usd", "risk_unit_source", "rationale", "regime_risk_state",
        "ev_score_total", "ev_score_band", "ev_has_transcript", "ev_score_latency_h",
        "ev_expected_move_pct", "risk_notes_json", "approval_status", "approval_reviewer",
        "approval_comment", "approval_divergence", "submit_attempts", "terminal_status",
        "filled_qty", "avg_fill_price", "slippage_bps")

    @staticmethod
    def _journal_to_row(e) -> dict:
        import json as _json

        d = e.model_dump(mode="json")
        ap = e.approval
        d["risk_notes_json"] = _json.dumps(e.risk_notes, ensure_ascii=False)
        d["approval_status"] = ap.status if ap else None
        d["approval_reviewer"] = ap.reviewer if ap else None
        d["approval_comment"] = ap.comment if ap else None
        d["approval_divergence"] = (_json.dumps(ap.model_dump(mode="json"),
                                                ensure_ascii=False) if ap else None)
        return {k: d.get(k) for k in TradingMemory._JOURNAL_COLS}

    @staticmethod
    def _row_to_journal(row):
        import json as _json

        from ..schemas.journal import ApprovalDivergence, JournalEntry

        d = dict(row)
        d["risk_notes"] = _json.loads(d.pop("risk_notes_json", None) or "[]")
        raw = d.pop("approval_divergence", None)
        d["approval"] = ApprovalDivergence.model_validate(_json.loads(raw)) if raw else None
        for k in ("approval_status", "approval_reviewer", "approval_comment"):
            d.pop(k, None)
        # A NULL column on a field that has a non-null default means "never set" —
        # let the default speak rather than failing validation (e.g. submit_attempts
        # is NULL until an order row exists).
        fields = JournalEntry.model_fields
        return JournalEntry.model_validate({
            k: v for k, v in d.items()
            if k in fields and not (v is None and fields[k].default is not None)})

    def save_journal_entry(self, entry) -> None:
        """Insert the pre-registered plan. Idempotent on entry_id: a retried cycle
        re-states the same plan, it does not create a second intent."""
        row = self._journal_to_row(entry)
        self.conn.execute(
            f"INSERT OR REPLACE INTO journal_entries ({','.join(row)}) "
            f"VALUES ({','.join('?' * len(row))})", tuple(row.values()))
        self.conn.commit()

    def update_journal_outcome(self, entry_id: str, row: dict) -> None:
        """Attach the execution result. The PLAN columns are never touched here —
        a journal whose plan can be edited after the outcome proves nothing."""
        sets = ", ".join(f"{k} = COALESCE(?, {k})" for k in row)
        self.conn.execute(
            f"UPDATE journal_entries SET {sets}, submit_attempts = "
            "(SELECT attempt FROM trades WHERE client_order_id = ?) WHERE entry_id = ?",
            (*row.values(), entry_id, entry_id))
        self.conn.commit()

    def journal_entries(self, *, since: str = "", symbol: str = "",
                        limit: int = 500) -> list:
        """-> list[JournalEntry]."""
        sql = "SELECT * FROM journal_entries WHERE 1=1"
        args: list = []
        if since:
            sql += " AND as_of >= ?"
            args.append(since)
        if symbol:
            sql += " AND symbol = ?"
            args.append(symbol.upper())
        sql += " ORDER BY as_of DESC, symbol LIMIT ?"
        args.append(limit)
        return [self._row_to_journal(r) for r in self.conn.execute(sql, args).fetchall()]

    def get_journal_entry(self, entry_id: str):
        """-> JournalEntry | None. Used to attach the opening plan onto an episode."""
        row = self.conn.execute(
            "SELECT * FROM journal_entries WHERE entry_id = ?", (entry_id,)).fetchone()
        return self._row_to_journal(row) if row else None

    # --- trade episodes (one round trip: flat -> position -> flat) --------- #
    _EPISODE_COLS = (
        "episode_id", "symbol", "direction", "origin", "status", "opened_at",
        "closed_at", "avg_entry", "avg_exit", "realized_pnl", "unrealized_pnl",
        "commission", "basis_source", "holding_days", "r_multiple", "r_multiple_mtm",
        "risk_unit_source", "mae_pct", "mfe_pct", "mae_source", "excess_vs_sector_pct",
        "setup", "primary_entry_id", "exit_reason", "exit_as_planned",
        "invalidation_triggered", "horizon_overdue_days")

    def save_episode(self, episode) -> None:
        """Upsert by episode_id — the reducer re-derives episodes from fills each run,
        so re-saving the same episode_id must replace, not duplicate."""
        row = episode.model_dump(mode="json")
        row = {k: row.get(k) for k in self._EPISODE_COLS}
        self.conn.execute(
            f"INSERT OR REPLACE INTO trade_episodes ({','.join(row)}) "
            f"VALUES ({','.join('?' * len(row))})", tuple(row.values()))
        self.conn.commit()

    def get_episode(self, episode_id: str):
        """-> TradeEpisode | None."""
        from ..schemas.journal import TradeEpisode

        row = self.conn.execute(
            "SELECT * FROM trade_episodes WHERE episode_id = ?", (episode_id,)).fetchone()
        return TradeEpisode.model_validate(dict(row)) if row else None

    def list_episodes(self, *, symbol: str = "", status: str = "",
                      decision_gradeable_only: bool = False, limit: int = 500) -> list:
        """-> list[TradeEpisode]."""
        from ..schemas.journal import TradeEpisode

        sql = "SELECT * FROM trade_episodes WHERE 1=1"
        args: list = []
        if symbol:
            sql += " AND symbol = ?"
            args.append(symbol.upper())
        if status:
            sql += " AND status = ?"
            args.append(status)
        if decision_gradeable_only:
            # Mirrors TradeEpisode.decision_gradeable: a real linked plan, not origin —
            # manual-origin episodes also have no plan (manual orders bypass
            # persist_decision) and must not slip into decision-quality stats either.
            sql += " AND primary_entry_id != ''"
        sql += " ORDER BY opened_at DESC LIMIT ?"
        args.append(limit)
        return [TradeEpisode.model_validate(dict(r))
                for r in self.conn.execute(sql, args).fetchall()]

    def fills_for_symbol(self, symbol: str) -> list[dict]:
        """Raw fill rows for the reducer, oldest first."""
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM fills WHERE symbol = ? ORDER BY time ASC", (symbol.upper(),))]

    def symbols_with_fills(self) -> list[str]:
        return [r[0] for r in self.conn.execute(
            "SELECT DISTINCT symbol FROM fills ORDER BY symbol")]

    def legs_for_episode(self, episode_id: str) -> list[dict]:
        """Raw fill rows belonging to one episode, oldest first. The last row is the
        closing leg for a closed episode, or the most recent trim for an open one."""
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM fills WHERE episode_id = ? ORDER BY time ASC", (episode_id,))]

    # --- predictions + outcomes -------------------------------------------- #
    def save_prediction(self, prediction) -> None:
        row = prediction.model_dump(mode="json")
        self.conn.execute(
            f"INSERT OR REPLACE INTO predictions ({','.join(row)}) "
            f"VALUES ({','.join('?' * len(row))})", tuple(row.values()))
        self.conn.commit()

    def save_prediction_outcome(self, outcome) -> None:
        row = outcome.model_dump(mode="json")
        self.conn.execute(
            f"INSERT OR REPLACE INTO prediction_outcomes ({','.join(row)}) "
            f"VALUES ({','.join('?' * len(row))})", tuple(row.values()))
        self.conn.commit()

    def has_outcome(self, prediction_id: str, horizon_days: int) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM prediction_outcomes WHERE prediction_id = ? AND horizon_days = ?",
            (prediction_id, horizon_days)).fetchone() is not None

    def open_predictions(self, horizons: list[int]) -> list:
        """Predictions still missing at least one horizon. -> list[Prediction]."""
        from ..schemas.journal import Prediction

        rows = self.conn.execute(
            "SELECT p.* FROM predictions p WHERE (SELECT COUNT(*) FROM prediction_outcomes o "
            "WHERE o.prediction_id = p.prediction_id) < ? ORDER BY p.made_at",
            (len(horizons),)).fetchall()
        return [Prediction.model_validate(dict(r)) for r in rows]

    def all_predictions(self, source: str = "") -> list:
        """-> list[Prediction], every one ever made (optionally filtered by source).
        Calibration accrues per SCORE regardless of whether a trade followed, so this
        is the base population for Stage D's calibration report."""
        from ..schemas.journal import Prediction

        sql = "SELECT * FROM predictions"
        args: list = []
        if source:
            sql += " WHERE source = ?"
            args.append(source)
        sql += " ORDER BY made_at"
        return [Prediction.model_validate(dict(r))
                for r in self.conn.execute(sql, args).fetchall()]

    def get_prediction(self, prediction_id: str):
        """-> Prediction | None. Single-row lookup, mirrors get_episode/
        get_journal_entry — used to resolve a bare counterexample ID back to its
        full record (e.g. when rendering, or when Stage E's critic picks cases)."""
        from ..schemas.journal import Prediction

        row = self.conn.execute(
            "SELECT * FROM predictions WHERE prediction_id = ?", (prediction_id,)).fetchone()
        return Prediction.model_validate(dict(row)) if row else None

    def predictions_for_entries(self, entry_ids: list[str]) -> list:
        """-> list[Prediction] whose entry_id is one of the given ids — the predictions
        that actually led to a trade in a given episode (an episode has no fiscal_label
        of its own; this is how its review card finds the right quarter's predictions)."""
        from ..schemas.journal import Prediction

        ids = [e for e in entry_ids if e]
        if not ids:
            return []
        rows = self.conn.execute(
            f"SELECT * FROM predictions WHERE entry_id IN ({','.join('?' * len(ids))}) "
            "ORDER BY made_at", ids).fetchall()
        return [Prediction.model_validate(dict(r)) for r in rows]

    def prediction_outcomes(self, prediction_id: str) -> list:
        """-> list[PredictionOutcome]."""
        from ..schemas.journal import PredictionOutcome

        return [PredictionOutcome.model_validate(dict(r)) for r in self.conn.execute(
            "SELECT * FROM prediction_outcomes WHERE prediction_id = ? ORDER BY horizon_days",
            (prediction_id,)).fetchall()]

    # --- journal bookkeeping ---------------------------------------------- #
    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO journal_meta VALUES (?,?)", (key, value))
        self.conn.commit()

    def get_meta(self, key: str, default: str = "") -> str:
        row = self.conn.execute(
            "SELECT value FROM journal_meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def recent_fills(self, symbol: str | None = None, limit: int = 20) -> list[dict]:
        if symbol:
            rows = self.conn.execute(
                "SELECT * FROM fills WHERE symbol = ? ORDER BY time DESC LIMIT ?",
                (symbol, limit)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM fills ORDER BY time DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    # --- chief runs (reuse cycles + decisions tables) ---------------------- #
    def save_chief_run(self, *, cycle_id: str, as_of, summary: str, decisions) -> None:
        self.conn.execute("INSERT OR REPLACE INTO cycles VALUES (?,?,?,?)",
                          (cycle_id, as_of.isoformat(), None, summary))
        self.conn.executemany(
            "INSERT INTO decisions VALUES (?,?,?,?,?,?,?)",
            [(cycle_id, d.symbol, d.action, d.notional_usd, d.limit_price, d.conviction,
              d.rationale) for d in decisions])
        self.conn.commit()

    def last_chief_run(self) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM cycles WHERE cycle_id LIKE 'chief-%' ORDER BY as_of DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["decisions"] = [dict(r) for r in self.conn.execute(
            "SELECT * FROM decisions WHERE cycle_id = ?", (row["cycle_id"],)).fetchall()]
        return out

    def save_risk_review(self, review) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO risk_reviews VALUES (?,?,?,?)",
            (review.as_of.isoformat(), review.risk_state, review.notes,
             review.model_dump_json()))
        self.conn.commit()

    def latest_risk_review(self):
        from ..schemas.risk import RiskReview

        row = self.conn.execute(
            "SELECT payload FROM risk_reviews ORDER BY as_of DESC LIMIT 1").fetchone()
        return RiskReview.model_validate_json(row["payload"]) if row else None

    def recent_risk_reviews(self, limit: int = 10) -> list[dict]:
        rows = self.conn.execute(
            "SELECT as_of, risk_state, summary FROM risk_reviews ORDER BY as_of DESC LIMIT ?",
            (limit,)).fetchall()
        return [dict(r) for r in rows]

    # --- PEAD score consumption (a score is actionable to the Chief ONCE) ---- #
    def mark_score_consumed(self, symbol: str, fiscal_label: str, cycle_id: str = "") -> None:
        """Record that the Chief has acted on (responded to) this earnings' score, so
        it becomes background thereafter — keyed per earnings (symbol, fiscal_label)."""
        from datetime import datetime, timezone

        self.conn.execute(
            "INSERT OR REPLACE INTO score_consumption VALUES (?,?,?,?)",
            (symbol.upper(), fiscal_label, datetime.now(timezone.utc).isoformat(), cycle_id))
        self.conn.commit()

    def is_score_consumed(self, symbol: str, fiscal_label: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM score_consumption WHERE symbol = ? AND fiscal_label = ?",
            (symbol.upper(), fiscal_label)).fetchone()
        return row is not None

    # --- Detected earnings prints (quarter key + session, cached per print) ---- #
    def upsert_period(self, print_, fiscal_label: str, label_source: str) -> None:
        """Record/refresh a detected print keyed (symbol, earnings_date).

        Caching the label matters: the upstream calendars revise quarter/year, and a
        label that shifts between the score write and a later read would orphan the
        dossier (its PK is (symbol, fiscal_label)).
        """
        from datetime import datetime, timezone

        p = print_
        self.conn.execute(
            "INSERT OR REPLACE INTO pead_periods VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (p.symbol.upper(), p.date.isoformat(), fiscal_label, p.quarter, p.year,
             p.session, p.session_source, p.at.isoformat() if p.at else None,
             p.eps_actual, p.rev_actual, label_source,
             datetime.now(timezone.utc).isoformat()))
        self.conn.commit()

    def get_period(self, symbol: str, earnings_date) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM pead_periods WHERE symbol = ? AND earnings_date = ?",
            (symbol.upper(), earnings_date.isoformat() if hasattr(earnings_date, "isoformat")
             else earnings_date)).fetchone()
        return dict(row) if row else None

    # --- Versioned score runs (idempotency + v1→v2 upgrade record) ------------ #
    def record_score_run(self, *, symbol: str, fiscal_label: str, version: int,
                         earnings_date, has_transcript: bool, transcript_source: str = "",
                         window: str = "", latency_hours: float | None = None,
                         total: float | None = None, band: str = "",
                         decision_summary: str = "", final: bool | None = None) -> None:
        """`final` defaults to has_transcript: a transcript-backed score is actionable
        at once, a transcript-less v1 is withheld until promoted."""
        from datetime import datetime, timezone

        self.conn.execute(
            "INSERT OR REPLACE INTO pead_score_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (symbol.upper(), fiscal_label, version, datetime.now(timezone.utc).isoformat(),
             earnings_date.isoformat() if hasattr(earnings_date, "isoformat") else earnings_date,
             1 if has_transcript else 0, transcript_source, window, latency_hours,
             total, band, decision_summary,
             1 if (has_transcript if final is None else final) else 0))
        self.conn.commit()

    def stamp_score_run(self, symbol: str, fiscal_label: str, *, window: str,
                        latency_hours: float | None) -> None:
        """Attach which window produced the latest run and how long after the print.

        Fixed windows necessarily leave a gap (a 06:00 ET print is only scored at
        11:00), so record the real print→score latency: that turns "is twice a day
        often enough" from an argument into a measurement.
        """
        last = self.latest_score_run(symbol, fiscal_label)
        if not last:
            return
        self.conn.execute(
            "UPDATE pead_score_runs SET window = ?, latency_hours = ? "
            "WHERE symbol = ? AND fiscal_label = ? AND version = ?",
            (window, latency_hours, symbol.upper(), fiscal_label, last["version"]))
        self.conn.commit()

    def promote_score_run(self, symbol: str, fiscal_label: str) -> bool:
        """Mark the latest run final without re-scoring — used when the transcript
        never arrived and the v1 becomes the best available answer."""
        last = self.latest_score_run(symbol, fiscal_label)
        if not last or last["final"]:
            return False
        self.conn.execute(
            "UPDATE pead_score_runs SET final = 1 WHERE symbol = ? AND fiscal_label = ? "
            "AND version = ?", (symbol.upper(), fiscal_label, last["version"]))
        self.conn.commit()
        return True

    def latest_score_run(self, symbol: str, fiscal_label: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM pead_score_runs WHERE symbol = ? AND fiscal_label = ? "
            "ORDER BY version DESC LIMIT 1", (symbol.upper(), fiscal_label)).fetchone()
        return dict(row) if row else None

    def next_score_version(self, symbol: str, fiscal_label: str) -> int:
        last = self.latest_score_run(symbol, fiscal_label)
        return (last["version"] + 1) if last else 1

    def score_state(self, symbol: str, fiscal_label: str) -> str:
        """'unscored' | 'v1_no_transcript' | 'final' — drives the window state machine."""
        last = self.latest_score_run(symbol, fiscal_label)
        if not last:
            return "unscored"
        return "final" if (last["has_transcript"] or last["final"]) else "v1_no_transcript"

    def is_score_final(self, symbol: str, fiscal_label: str) -> bool:
        """Whether the Chief may act on this quarter's score yet."""
        return self.score_state(symbol, fiscal_label) == "final"

    def save_performance(self, record: PerformanceRecord) -> None:
        """Standalone performance snapshot (trader daily snapshot, no full cycle)."""
        p = record
        self.conn.execute("INSERT INTO performance VALUES (?,?,?,?,?,?,?,?,?)",
                          (p.cycle_id, p.as_of.isoformat(), p.net_liquidation, p.daily_pnl,
                           p.cumulative_pnl, p.realized_pnl, p.unrealized_pnl, p.num_positions,
                           p.model_dump_json()))
        self.conn.commit()

    # --- reads ----------------------------------------------------------- #
    def last_performance(self) -> PerformanceRecord | None:
        row = self.conn.execute(
            "SELECT payload FROM performance ORDER BY rowid DESC LIMIT 1").fetchone()
        return PerformanceRecord.model_validate_json(row["payload"]) if row else None

    def performance_history(self, limit: int = 30) -> list[PerformanceRecord]:
        rows = self.conn.execute(
            "SELECT payload FROM performance ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
        return list(reversed(
            [PerformanceRecord.model_validate_json(r["payload"]) for r in rows]))

    def recent_decisions(self, symbol: str | None = None, limit: int = 20) -> list[dict]:
        if symbol:
            rows = self.conn.execute(
                "SELECT * FROM decisions WHERE symbol = ? ORDER BY rowid DESC LIMIT ?",
                (symbol, limit)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM decisions ORDER BY rowid DESC LIMIT ?", (limit,)).fetchall()
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

    # --- technical reviews ------------------------------------------------ #
    # `as_of` is stored as a DATE, not a timestamp: the analyst runs once per
    # session, so a same-day rerun must overwrite rather than accumulate rows.
    def save_technical_review(self, review) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO technical_reviews VALUES (?,?,?,?)",
            (review.name, review.as_of.date().isoformat(), review.summary_line(),
             review.model_dump_json()))
        self.conn.commit()

    def latest_technical_review(self, name: str = "technical"):
        from ..schemas.technical import TechnicalReview

        row = self.conn.execute(
            "SELECT payload FROM technical_reviews WHERE name = ? "
            "ORDER BY as_of DESC LIMIT 1", (name,)).fetchone()
        return TechnicalReview.model_validate_json(row["payload"]) if row else None

    def previous_technical_review(self, name: str = "technical", *, before: str = ""):
        """The most recent review STRICTLY before `before` (an ISO date).

        Change detection ("NVDA dropped from 100% to 50%") needs yesterday's
        reading; without the strict bound a same-day rerun would compare against
        the row it is about to overwrite and always report "no change".
        """
        from ..schemas.technical import TechnicalReview

        row = self.conn.execute(
            "SELECT payload FROM technical_reviews WHERE name = ? AND as_of < ? "
            "ORDER BY as_of DESC LIMIT 1", (name, before)).fetchone()
        return TechnicalReview.model_validate_json(row["payload"]) if row else None

    def recent_technical_reviews(self, name: str = "technical", limit: int = 8) -> list[dict]:
        rows = self.conn.execute(
            "SELECT name, as_of, summary FROM technical_reviews "
            "WHERE name = ? ORDER BY as_of DESC LIMIT ?", (name, limit)).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        self.conn.close()
