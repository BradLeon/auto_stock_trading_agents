"""Code-ownership registry for the shared SQLite transition period."""

DATA_LAYER_TABLES = frozenset({
    "source_documents", "document_candidates", "document_versions",
    "document_entities", "document_source_aliases", "document_chunks",
    "data_document_artifacts", "data_document_pages",
    "document_processing_runs", "data_sources", "ingestion_runs",
    "measurement_series", "measurement_points", "evidence_observations",
    "evidence_failures", "evidence_facts", "evidence_fact_projections",
    "earnings_events",
    "newsletter_cursors", "data_migrations",
})

WORKFLOW_MEMORY_TABLES = frozenset({
    "cycles", "decisions", "trades", "performance", "pead_dossier", "pead_events",
    "research_articles", "research_insights", "task_projections", "claim_proposals",
    # The unified agent-output envelope is Workflow memory, not a data product:
    # it carries opinions (role, model and prompt versions, scope, as_of) rather than
    # neutral evidence, so it sits here next to `task_projections` — which it will
    # eventually replace.
    "task_projection_envelopes",
    # Decision audit (§12.3, Phase B). Decisions carry opinions (who proposed
    # what, why, who approved), so they are Workflow memory, never the data
    # layer's neutral facts.
    "decision_cycles", "decision_revisions", "decision_risk_reviews",
    "boss_approvals", "cycle_events",
    # Clerk / trade ledger (§11, Phase C). Exceptions, run trails and derived
    # read models all carry trade consequences and opinions about them, so they
    # are Workflow memory too.
    "ledger_exceptions", "clerk_runs", "ledger_read_models",
    "claim_assessments", "sector_reviews", "macro_reviews",
    "technical_reviews", "fills", "risk_reviews", "score_consumption", "pead_periods",
    "pead_score_runs", "journal_entries", "journal_meta", "predictions",
    "prediction_outcomes", "trade_episodes",
})

__all__ = ["DATA_LAYER_TABLES", "WORKFLOW_MEMORY_TABLES"]
