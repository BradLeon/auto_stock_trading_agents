"""Keep the documented consumer topology aligned with actual code paths."""

from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _consumers() -> dict:
    return yaml.safe_load(_read("config/data/consumer_release.yaml"))["consumers"]


def test_direct_consumer_contracts_keep_persistent_inputs_explicit() -> None:
    consumers = _consumers()
    assert consumers["chain_regional"]["inputs"] == [
        "regional_tw_exports", "regional_kr_exports"]
    assert consumers["macro_agent"]["inputs"] == [
        "regional_tw_exports", "regional_kr_exports"]
    assert consumers["pead_fundamentals"]["inputs"] == ["company_financials"]
    assert consumers["sector_fundamentals"]["inputs"] == ["company_financials"]
    assert consumers["pead_consensus"]["inputs"] == ["market_consensus"]
    assert consumers["sector_consensus"]["inputs"] == ["market_consensus"]


def test_regional_and_company_products_are_reached_through_consumer_scoped_facades() -> None:
    chain_sources = _read("src/ats/chain/sources.py")
    macro = _read("src/ats/agents/macro/assemble.py")
    pead = _read("src/ats/graph/pead.py")
    sector = _read("src/ats/agents/sector/assemble.py")

    assert 'read_mode("chain_regional", source_id=source.id)' in chain_sources
    assert 'consumer="chain_regional"' in chain_sources
    assert 'regional.fetch(consumer="macro_agent")' in macro
    assert "fund_src.fetch(state.symbol)" in pead
    assert "consensus_src.fetch(state.symbol)" in pead
    assert 'regional.fetch(consumer="sector_agent")' in sector
    assert 'fundamentals.fetch_light(sym, consumer="sector_fundamentals")' in sector
    assert 'consensus_src.fetch(sym, consumer="sector_consensus")' in sector


def test_dram_reaches_sector_only_through_chain_evidence() -> None:
    consumers = _consumers()
    inventory = yaml.safe_load(_read("config/data/legacy_inventory.yaml"))
    sector = _read("src/ats/agents/sector/assemble.py")
    source_config = _read("config/sources.yaml")

    assert "industry_dram_contract_price" not in consumers["sector_agent"]["inputs"]
    legacy_sector = next(row for row in inventory["consumers"] if row["id"] == "sector-agent")
    assert "industry_dram_contract_price" in legacy_sector["sources"]
    assert "def _chain_evidence(" in sector
    assert "from ...chain.corroborate import assess_layer" in sector
    assert "dram_contract_price:" in source_config
    assert "adapter: trendforce" in source_config
    assert "concepts: [hbm_pricing, supply_tightness]" in source_config


def test_unstructured_and_orchestration_boundaries_are_explicit() -> None:
    research = _read("src/ats/data/research.py")
    chain_report = _read("src/ats/chain/report.py")
    chief = _read("src/ats/graph/chief.py")
    scheduler = _read("src/ats/runtime/scheduler.py")

    assert 'get_unstructured_read_router(consumer=consumer, legacy_repository=store)' in research
    assert 'get_unstructured_read_router(\n        consumer="evidence_chain"' in chain_report
    assert 'workflow_data_boundary("chief_graph")' in chief
    assert 'workflow_data_boundary("runtime_scheduler")' in scheduler
