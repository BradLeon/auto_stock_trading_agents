"""config.py: PEAD config merging, in particular the signal_chain fallback.

Regression: MSFT.yaml originally had no `signal_chain`, so the PEAD score report
always rendered "跨标的信号链（暂无数据）" — the LLM call was never even reached
(agents/pead/prep.py::signal_chain short-circuits on an empty list). The fallback
derives peers from the ticker's sector layer so a new PEAD target never silently
ships without a cross-symbol view; a curated YAML entry still wins.
"""

from ats.config import load_pead_config


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
