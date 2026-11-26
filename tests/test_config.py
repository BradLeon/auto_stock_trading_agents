"""config.py: PEAD config merging, in particular the signal_chain fallback.

Regression: MSFT.yaml originally had no `signal_chain`, so the PEAD score report
always rendered "跨标的信号链（暂无数据）" — the LLM call was never even reached
(agents/pead/prep.py::signal_chain short-circuits on an empty list). The fallback
derives peers from the ticker's sector layer so a new PEAD target never silently
ships without a cross-symbol view; a curated YAML entry still wins.
"""

from ats.config import load_pead_config, load_sector_config


def test_risk_yaml_is_single_source_for_all_adjustable_risk_config(monkeypatch, tmp_path):
    from ats.config import (
        get_config,
        load_instrument_risk_registry,
        load_risk_policy,
        load_sector_config,
        reset_config_cache,
    )

    (tmp_path / "sectors").mkdir()
    (tmp_path / "settings.yaml").write_text(
        "environment: paper\nrisk:\n  max_position_pct: 0.99\n", encoding="utf-8")
    (tmp_path / "risk.yaml").write_text(
        """limits:
  max_position_pct: 0.12
  beta_cap: 1.7
sector_layer_caps:
  demo:
    L1: {weight_cap: 0.22, weight_cap_hard: 0.30}
option_survival:
  max_peak_expiry_nav_pct: 0.44
directive:
  limited_headroom_pct: 0.08
instruments:
  TEST2X:
    economic_entity: TEST
    risk_symbol: TEST
    exposure_multiplier: 2.0
""", encoding="utf-8")
    (tmp_path / "sectors" / "demo.yaml").write_text(
        """name: demo
layers:
  - key: L1
    label: Demo layer
    weight_cap: 0.99
    tickers: [{symbol: TEST}]
""", encoding="utf-8")
    monkeypatch.setenv("ATS_CONFIG_DIR", str(tmp_path))
    reset_config_cache()
    try:
        assert get_config().app.risk.max_position_pct == 0.12
        assert get_config().app.risk.beta_cap == 1.7
        layer = load_sector_config("demo").layers[0]
        assert layer.weight_cap == 0.22
        assert layer.weight_cap_hard == 0.30
        assert load_risk_policy().option_survival.max_peak_expiry_nav_pct == 0.44
        assert load_risk_policy().directive.limited_headroom_pct == 0.08
        assert load_instrument_risk_registry().resolve("TEST2X").exposure_multiplier == 2.0
    finally:
        reset_config_cache()


def test_signal_chain_falls_back_to_sector_layer_when_yaml_declares_none():
    # META has no config/pead/META.yaml at all — falls back to its ai_hardware.yaml
    # L2_cloud layer peers. (chain/kb_review.py reports exactly this gap: the
    # fallback keeps the report populated but carries no role and no comment, so
    # `relation_hint` cannot tell 「上游 HBM 主供」 from 「上游 EUV」.)
    #
    # This used to be AMD, which was chainless for the same reason. It stopped being
    # the right fixture on 2026-08-17 when AMD got a curated config/pead/AMD.yaml —
    # it joined `observe` as the fourth name in the L4 XPU cross-section. The test was
    # asserting on a gap, not on AMD, so the fixture moved rather than the assertion.
    cfg = load_pead_config("META")
    peers = {s.symbol for s in cfg.signal_chain}
    assert peers, "expected a non-empty derived peer list"
    assert "META" not in peers          # never include itself
    assert "MSFT" in peers              # same L2_cloud layer
    assert all(s.role == "peer" for s in cfg.signal_chain)  # no guessed direction


def test_explicit_yaml_signal_chain_overrides_the_fallback():
    # MSFT.yaml explicitly curates signal_chain with upstream/peer roles —
    # the sector-layer fallback must not touch it.
    cfg = load_pead_config("MSFT")
    by_symbol = {s.symbol: s.role for s in cfg.signal_chain}
    assert by_symbol.get("NVDA") == "upstream"
    assert by_symbol.get("AMZN") == "peer"
    assert "CRWV" not in by_symbol      # would appear if the fallback ran instead


def test_signal_chain_fallback_empty_when_symbol_in_no_sector_layer():
    cfg = load_pead_config("NOT_A_REAL_TICKER")
    assert cfg.signal_chain == []


def test_third_party_prose_names_resolve_to_the_listed_entity():
    """Journalists name companies their way, not the filing's way. Measured after the
    news + newsletter sources were wired up (2026-08-18): `NVIDIA (NVDA)`, `MICRON (MU)`,
    `MICROSOFT`, `GOOGLE CLOUD` all arrived as fresh unresolvable entities.

    An entity that resolves to nothing is not untidy, it is UNREACHABLE:
    `corroborate.assess_layer` gathers rows by entity, so those readings can never enter
    any claim. Enumerating one alias per phrasing loses that race, so the parenthesised
    ticker — the publisher telling us the answer — is parsed as a fallback.
    """
    from ats.config import canonical_entity

    assert canonical_entity("NVIDIA (NVDA)") == "NVDA"
    assert canonical_entity("MICRON (MU)") == "MU"
    assert canonical_entity("MICROSOFT") == "MSFT"        # MSFT was never registered
    assert canonical_entity("GOOGLE CLOUD") == "GOOG"     # business line, not the issuer
    assert canonical_entity("HY9H") == "SKHY"             # the original folding still holds
    # Unregistered but ticker-shaped: fold to the ticker so at least it is STABLE across
    # phrasings, instead of splitting into one identity per way of writing it.
    assert canonical_entity("QUMULUSAI (QMLS)") == "QMLS"
    # Not ticker-shaped: left alone rather than guessed at.
    assert canonical_entity("SOME PRIVATE LAB") == "SOME PRIVATE LAB"


def test_ai_hardware_uses_one_sector_ticker_per_company_and_tracks_l2_operators():
    """Sector analysis is company-level even when the execution book holds several
    listings. Listed L2 operators should not participate in only one of cross-section
    or proposition tracking while disappearing from the other."""
    cfg = load_sector_config("ai_hardware")
    memory = cfg.layer_by_key("L6_memory")
    assert memory is not None
    memory_symbols = [t.symbol for t in memory.tickers]
    assert "SKHY" in memory_symbols
    assert "HY9H" not in memory_symbols and "000660.KS" not in memory_symbols
    from ats.config import load_instrument_risk_registry
    registry = load_instrument_risk_registry()
    for execution_symbol in ("7709", "HY9H", "SKHY", "000660.KS"):
        assert cfg.layer_of(registry.resolve(execution_symbol).layer_symbol) == "L6_memory"

    cloud = cfg.layer_by_key("L2_cloud")
    assert cloud is not None
    assert {"CRWV", "SPCX"} <= {t.symbol for t in cloud.tickers}
    demand = next(c for c in cloud.claims if c.id == "cloud_demand_still_accelerating")
    assert "CRWV" in demand.expected_witnesses()
    assert demand.stance_of("CRWV") == "incumbent"


def test_anthropic_names_fold_onto_the_listed_ant_identity():
    """ANT listed 2026-11-19: every way of writing the company is one witness, not two."""
    from ats.config import canonical_entity, entity_meta

    for name in ("ANT", "ANTHROPIC", "Anthropic", "Anthropic PBC", "Claude"):
        assert canonical_entity(name) == "ANT", name
    assert entity_meta("ANTHROPIC")["filings"] == "sec"
    assert canonical_entity("OPENAI") == "OPENAI"          # the other private lab is untouched


def test_the_evidence_ledger_records_ant_under_the_company_name():
    """ANT is the security code; evidence, claims and witnesses say ANTHROPIC."""
    from ats.config import canonical_entity, ledger_entity, load_sector_config

    for name in ("ANT", "Anthropic", "Claude", "ANTHROPIC"):
        assert ledger_entity(name) == "ANTHROPIC", name
    assert canonical_entity("ANT") == "ANT"                      # the security side is unchanged
    assert ledger_entity("Ant") == "Ant" and ledger_entity("NVDA") == "NVDA"
    assert ledger_entity("HY9H") == "HY9H"                       # only declared companies are renamed
    layer = next(l for l in load_sector_config("ai_hardware").layers if l.key == "L1_app")
    claim = next(c for c in layer.claims if c.id == "ai_frontier_labs_commercialization")
    witnesses = claim.expected_witnesses() | {w.entity.upper() for w in claim.witnesses}
    assert "ANTHROPIC" in witnesses and "ANT" not in witnesses


def test_a_document_fetched_as_ant_is_offered_anthropics_claim_dimensions():
    from ats.agents.evidence.observer import concept_menu

    menu, keys = concept_menu("ANT")
    assert "frontier_revenue_scale_trend" in keys and menu


def test_ant_code_is_security_only_and_never_folds_ordinary_text():
    """ANT is also the English word and Ant Group: only the company's names fold onto it."""
    from ats.config import canonical_entity, entity_meta

    assert canonical_entity("Ant") == "Ant" and canonical_entity("ANT GROUP") == "ANT GROUP"
    assert canonical_entity("ANT") == "ANT"                       # the security code still resolves to itself
    assert entity_meta("ANT")["name"] == "Anthropic"
    assert canonical_entity("Anthropic") == canonical_entity("Claude") == "ANT"


def test_bare_ant_in_text_needs_stock_context_but_the_company_name_does_not():
    from ats.data.entity_association import entity_mentions

    assert entity_mentions("Ant Group files for new listing", "ANT") == []
    assert entity_mentions("ANT GROUP SEES PROFIT JUMP", "ANT") == []
    assert entity_mentions("ANT unveils new model", "ANT") == []              # no stock context
    assert [m["kind"] for m in entity_mentions("Shares of ANT rose 4% on Monday", "ANT")] == ["ticker"]
    assert [m["kind"] for m in entity_mentions("Buy $ANT today", "ANT")] == ["ticker"]
    named = entity_mentions("Anthropic ships Claude 5.5", "ANT")
    assert {m["alias"].casefold() for m in named} == {"anthropic", "claude"}
    assert {m["kind"] for m in named} == {"company_alias"}
