BEGIN TRANSACTION;
CREATE TABLE shadow_intent_provenance (
    intent_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL
);
INSERT INTO "shadow_intent_provenance" VALUES('a90ae1e9e313f4cf0b64f3366dfeeb8d67e4d5368c14714032cfa2de2b8e1998','{"authorization": {"approval_at": "2026-10-08T08:23:36.168590+00:00", "approval_id": "phase-f-shadow-ledger-validation:r1:approval:r1", "cycle_id": "phase-f-shadow-ledger-validation", "decision_hash": "fb68177153ad9efcb2789d9adc24df38", "market_as_of": "2026-10-08T08:23:35.739931+00:00", "portfolio_snapshot_id": "pf:2026-10-08T08:23:35.255221+00:00", "review_at": "2026-10-08T08:23:35.484718+00:00", "review_id": "phase-f-shadow-ledger-validation:r1:review:r1", "revision_no": 1, "route_generation": 1, "route_id": "shadow-candidate", "ruleset_version": "risk-af175377e1d1"}, "chain": {"approval_id": "phase-f-shadow-ledger-validation:r1:approval:r1", "decision_hash": "fb68177153ad9efcb2789d9adc24df38"}, "order": {"action": "buy", "conviction": 0.0, "execution_basis": {"currency": "USD", "defer_to_regular_session": false, "max_deviation": 0.01, "max_notional": 905.4, "policy_version": "phase-f-execution-price-v1", "quote": {"adjusted": false, "ask": 100.1, "bid": 100.0, "close": null, "currency": "USD", "market_data_mode": "live", "min_size": 1.0, "min_tick": 0.01, "price_kind": "bid_ask", "queried_at": "2026-10-08T08:23:35.739931Z", "schema_version": "runtime-execution-price-v1", "session": "regular", "size_increment": 1.0, "source": "synthetic:shadow-business", "source_as_of": "2026-10-08T08:23:35.739931Z", "source_precision": "tick", "symbol": "AAPL"}, "quote_ref": "execution-quote:cf29748bf1fc336d08b357d29d7bc2fd7af6d465ccb720c715ba89b2b358daf8", "reference_price": 100.1, "requested_order": {"action": "buy", "conviction": 0.0, "execution_basis": {}, "invalidation": "", "limit_price": null, "notional_usd": 1000.0, "order_type": "limit", "planned_horizon_days": null, "qty": null, "rationale": "", "references": [], "setup": "unknown", "stop_price": null, "symbol": "AAPL", "target_price": null, "target_weight": null, "time_in_force": "DAY"}}, "invalidation": "", "limit_price": 100.6, "notional_usd": 905.4, "order_type": "limit", "planned_horizon_days": null, "qty": 9.0, "rationale": "", "references": [], "setup": "unknown", "stop_price": null, "symbol": "AAPL", "target_price": null, "target_weight": null, "time_in_force": "DAY"}, "quantity": 9.0, "route": {"account": "DU1", "environment": "paper", "generation": 1, "reason": "isolated candidate only", "route_id": "shadow-candidate", "updated_at": "2026-10-08T08:23:35.482746+00:00", "updated_by": "fixture"}}');
CREATE TABLE shadow_order_intents (
    intent_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    cycle_id TEXT NOT NULL,
    revision_no INTEGER NOT NULL DEFAULT 0,
    sequence INTEGER NOT NULL DEFAULT 0,
    symbol TEXT NOT NULL,
    action TEXT NOT NULL,
    quantity REAL NOT NULL,
    decision_hash TEXT NOT NULL DEFAULT '',
    approval_id TEXT NOT NULL DEFAULT '',
    route_id TEXT NOT NULL DEFAULT '',
    route_generation INTEGER NOT NULL DEFAULT 0,
    submitted INTEGER NOT NULL DEFAULT 0,
    submit_refusal_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
INSERT INTO "shadow_order_intents" VALUES('a90ae1e9e313f4cf0b64f3366dfeeb8d67e4d5368c14714032cfa2de2b8e1998','validation-shadow','phase-f-shadow-ledger-validation',1,0,'AAPL','buy',9.0,'fb68177153ad9efcb2789d9adc24df38','phase-f-shadow-ledger-validation:r1:approval:r1','shadow-candidate',1,0,'','2026-10-08T08:23:36.608932+00:00');
CREATE TABLE shadow_submit_attempts (
    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    intent_id TEXT NOT NULL,
    attempted_at TEXT NOT NULL,
    accepted INTEGER NOT NULL,
    refusal_code TEXT NOT NULL DEFAULT '',
    refusal_id TEXT NOT NULL DEFAULT '',
    detail_json TEXT NOT NULL DEFAULT '{}'
);
INSERT INTO "shadow_submit_attempts" VALUES(1,'a90ae1e9e313f4cf0b64f3366dfeeb8d67e4d5368c14714032cfa2de2b8e1998','2026-10-08T08:23:36.610588+00:00',0,'isolated_run_prohibited','broker-write-refusal-000001','{"execution_price_audit": [{"approval_quote_ref": "execution-quote:cf29748bf1fc336d08b357d29d7bc2fd7af6d465ccb720c715ba89b2b358daf8", "execution_quote_ref": "execution-quote:3fc57e4e4f6d816db57e3d45efe4b85b220e5671051369d37d8071369898e5e6", "order_seq": 0, "policy_version": "phase-f-execution-price-v1", "quote": {"adjusted": false, "ask": 100.1, "bid": 100.0, "close": null, "currency": "USD", "market_data_mode": "live", "min_size": 1.0, "min_tick": 0.01, "price_kind": "bid_ask", "queried_at": "2026-10-08T08:23:36.600426Z", "schema_version": "runtime-execution-price-v1", "session": "regular", "size_increment": 1.0, "source": "synthetic:shadow-business", "source_as_of": "2026-10-08T08:23:36.600426Z", "source_precision": "tick", "symbol": "AAPL"}}], "guard": {"at": "2026-10-08T08:23:36.609900+00:00", "caller": "IBKRBroker.place_orders", "detail": "cycle_id=phase-f-shadow-ledger-validation revision_no=1 orders=1", "operation": "place_orders", "pid": 4752, "quantity": null, "reason_code": "isolated_run_prohibited", "refusal_id": "broker-write-refusal-000001", "symbol": ""}, "pid": 4752}');
CREATE TRIGGER provenance_no_update BEFORE UPDATE ON shadow_intent_provenance
BEGIN SELECT RAISE(ABORT, 'shadow provenance is append-only'); END;
CREATE TRIGGER provenance_no_delete BEFORE DELETE ON shadow_intent_provenance
BEGIN SELECT RAISE(ABORT, 'shadow provenance is append-only'); END;
CREATE INDEX idx_shadow_intents_run
    ON shadow_order_intents(run_id, cycle_id);
CREATE TRIGGER shadow_attempts_no_update
BEFORE UPDATE ON shadow_submit_attempts
BEGIN SELECT RAISE(ABORT, 'shadow submit attempts are append-only'); END;
CREATE TRIGGER shadow_attempts_no_delete
BEFORE DELETE ON shadow_submit_attempts
BEGIN SELECT RAISE(ABORT, 'shadow submit attempts are append-only'); END;
CREATE TRIGGER shadow_intents_no_update
BEFORE UPDATE ON shadow_order_intents
BEGIN SELECT RAISE(ABORT, 'shadow order intents are append-only'); END;
CREATE TRIGGER shadow_intents_no_delete
BEFORE DELETE ON shadow_order_intents
BEGIN SELECT RAISE(ABORT, 'shadow order intents are append-only'); END;
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('shadow_submit_attempts',1);
COMMIT;
