"""Explicit acceptance runs store semantic consumer evidence, not table hashes."""

from __future__ import annotations

from datetime import date

from ats.runtime import consumer_acceptance
from ats.schemas.chain import SeriesPoint, SourceDef


def _source(identifier: str, adapter: str) -> SourceDef:
    return SourceDef(
        id=identifier, label=identifier, adapter=adapter,
        entity="TW_IC_EXPORT" if adapter == "tw_mof" else "KR_SEMI_EXPORT",
        stance="regulator", observation_type="regulatory", cadence="monthly",
        concepts=["supply_tightness"], direction_from=["yoy", "mom"],
    )


def test_chain_regional_acceptance_records_aggregate_equivalent_output(monkeypatch, tmp_path) -> None:
    from ats.chain import sources

    definitions = [_source("tw", "tw_mof"), _source("kr", "kr_ecos")]
    points = [SeriesPoint(period="2026-07", value=100, unit="USD M", yoy=0.1,
                          mom=0.02, published_at=date(2026, 8, 1))]
    monkeypatch.setattr(sources, "load_sources", lambda: definitions)
    monkeypatch.setattr(sources, "_platform_fetch", lambda *_args, **_kwargs: points)
    monkeypatch.setattr(sources, "_legacy_fetch", lambda *_args, **_kwargs: points)
    monkeypatch.setattr(consumer_acceptance, "_regional_platform_lineage", lambda *_args, **_kwargs: [
        {"observation_id": "obs-1", "artifact_id": "artifact-1", "period": "2026-07"}])

    result = consumer_acceptance.accept_chain_regional(
        data_db=tmp_path / "data.sqlite", record=True)

    assert result["status"] == "reconciled"
    assert result["category"] == "equivalent_regional_consumer_output"
    assert result["comparison"]["details"]["platform"]["tw"][0]["period"] == "2026-07"
    assert result["comparison"]["details"]["output"]["kr"]["platform_observations"]


def test_monitor_acceptance_records_governed_policy_upgrade(monkeypatch, tmp_path) -> None:
    platform = {
        "update": {"event_summary": "1 new events", "materiality": 0.0},
        "events": [{"id": "doc-1", "source": "platform:ibkr_news",
                    "published_at": "2026-08-30T00:00:00+00:00", "headline": "Nvidia news",
                    "url": "ibkr-news://DJ-N/1", "triage_score": None, "triage_category": None}],
        "dossier": {"fiscal_label": "Q2 FY2027", "phase": "prep", "narrative": "seed"},
    }
    legacy = {**platform, "events": []}
    monkeypatch.setattr(consumer_acceptance, "_monitor_run", lambda *, mode, **_kwargs:
                        platform if mode == "platform" else legacy)
    monkeypatch.setattr(consumer_acceptance, "_platform_news_lineage", lambda _events: [{
        "document_id": "doc-1", "source": "ibkr_news", "version_id": "version-1",
        "source_url": "ibkr-news://DJ-N/1", "chars": 900,
    }])

    result = consumer_acceptance.accept_pead_monitor(
        symbol="NVDA", data_db=tmp_path / "data.sqlite", record=True)

    assert result["status"] == "reconciled"
    assert result["category"] == "governed_news_source_policy_upgrade"
    assert result["verification"]["details"]["lineage"][0]["version_id"] == "version-1"


def test_research_acceptance_records_platform_only_governed_upgrade(monkeypatch, tmp_path) -> None:
    smoke = {
        "candidates": [{
            "article_id": "imap:semi-preview", "source": "newsletter:SemiAnalysis",
            "published_at": "2026-08-30T00:00:00+00:00", "completeness": "partial",
            "truncation_reason": "subscription_preview",
        }],
        "no_llm_output_count": 0,
        "processing_runs": [{"version_id": "SEMI:1:v1", "status": "succeeded"}],
        "existing_insight_outputs": [{"article_id": "imap:semi-preview", "ticker": "NVDA"}],
    }
    lineage = [{
        "article_id": "imap:semi-preview", "document_id": "SEMI:1",
        "version_id": "SEMI:1:v1", "source": "newsletter:SemiAnalysis",
        "published_at": "2026-08-30T00:00:00+00:00", "completeness": "partial",
        "truncation_reason": "subscription_preview", "chars": 800,
    }]
    monkeypatch.setattr(consumer_acceptance, "_research_platform_smoke", lambda **_kwargs: smoke)
    monkeypatch.setattr(consumer_acceptance, "_platform_research_lineage", lambda _ids: lineage)

    result = consumer_acceptance.accept_pead_research(
        data_db=tmp_path / "data.sqlite", record=True)

    assert result["status"] == "reconciled"
    assert result["category"] == "governed_platform_research_processing_upgrade"
    assert result["verification"]["details"]["lineage"][0]["completeness"] == "partial"


def test_evidence_chain_acceptance_uses_actual_report_contract_not_structured_rows(monkeypatch, tmp_path) -> None:
    smoke = {
        "sector": "ai_hardware", "as_of": "2026-09-01T00:00:00+00:00", "entities": ["NVDA"],
        "report": {"chars": 1200, "sha256": "report-hash", "no_llm_marker": True,
                   "heading": "# 🤖 产业链证据 — AI硬件"},
        "assessments": [{"claim_id": "hbm", "layer": "memory", "verdict": "unknown",
                         "evidence_clusters": 1, "stance_classes": 1,
                         "observation_ids": ["obs-1"]}],
        "observation_ids": ["obs-1"],
    }
    lineage = {"observations": [{
        "observation_id": "obs-1", "document_id": "NVDA:Q2:release",
        "version_id": "NVDA:Q2:release@v1", "document_chars": 900,
        "lineage_kind": "document_version",
        "observed_at": "2026-08-28T00:00:00+00:00",
    }], "failures": []}
    monkeypatch.setattr(consumer_acceptance, "_evidence_chain_platform_smoke", lambda **_kwargs: smoke)
    monkeypatch.setattr(consumer_acceptance, "_platform_evidence_chain_lineage", lambda _ids: lineage)

    result = consumer_acceptance.accept_evidence_chain(data_db=tmp_path / "data.sqlite", record=True)

    assert result["status"] == "reconciled"
    assert result["category"] == "governed_platform_evidence_report_upgrade"
    assert result["comparison"]["details"]["platform"]["report"]["sha256"] == "report-hash"


def test_evidence_chain_acceptance_keeps_legacy_evidence_snapshots_explicit(monkeypatch, tmp_path) -> None:
    smoke = {
        "sector": "ai_hardware", "as_of": "2026-09-01T00:00:00+00:00", "entities": ["NVDA"],
        "report": {"chars": 1200, "sha256": "report-hash", "no_llm_marker": True,
                   "heading": "# 🤖 产业链证据 — AI硬件"},
        "assessments": [{"claim_id": "hbm", "layer": "memory", "verdict": "unknown",
                         "evidence_clusters": 1, "stance_classes": 1,
                         "observation_ids": ["obs-1"]}],
        "observation_ids": ["obs-1"],
    }
    lineage = {"observations": [{
        "observation_id": "obs-1", "document_id": "NVDA:Q2:legacy",
        "lineage_kind": "evidence_snapshot", "source_url": "https://example.test/nvda",
        "entity": "NVDA", "source_entity": "NVDA", "observed_at": "2026-08-28T00:00:00+00:00",
        "evidence_span": "Nvidia reported a result.",
    }], "failures": []}
    monkeypatch.setattr(consumer_acceptance, "_evidence_chain_platform_smoke", lambda **_kwargs: smoke)
    monkeypatch.setattr(consumer_acceptance, "_platform_evidence_chain_lineage", lambda _ids: lineage)

    result = consumer_acceptance.accept_evidence_chain(data_db=tmp_path / "data.sqlite", record=True)

    assert result["status"] == "reconciled"
    assert result["details"]["lineage_summary"] == {
        "cited_observations": 1, "document_version": 0, "evidence_snapshot": 1, "unreplayable": 0,
    }


def test_evidence_chain_acceptance_rejects_unidentified_evidence_snapshot(monkeypatch, tmp_path) -> None:
    smoke = {
        "sector": "ai_hardware", "as_of": "2026-09-01T00:00:00+00:00", "entities": ["NVDA"],
        "report": {"chars": 1200, "sha256": "report-hash", "no_llm_marker": True,
                   "heading": "# 🤖 产业链证据 — AI硬件"},
        "assessments": [{"claim_id": "hbm", "layer": "memory", "verdict": "unknown",
                         "evidence_clusters": 1, "stance_classes": 1,
                         "observation_ids": ["obs-1"]}],
        "observation_ids": ["obs-1"],
    }
    lineage = {"observations": [{
        "observation_id": "obs-1", "document_id": "NVDA:Q2:legacy",
        "lineage_kind": "evidence_snapshot", "source_url": "",
        "entity": "NVDA", "source_entity": "NVDA", "observed_at": "2026-08-28T00:00:00+00:00",
        "evidence_span": "Nvidia reported a result.",
    }], "failures": []}
    monkeypatch.setattr(consumer_acceptance, "_evidence_chain_platform_smoke", lambda **_kwargs: smoke)
    monkeypatch.setattr(consumer_acceptance, "_platform_evidence_chain_lineage", lambda _ids: lineage)

    result = consumer_acceptance.accept_evidence_chain(data_db=tmp_path / "data.sqlite", record=True)

    assert result["status"] == "mismatch"
