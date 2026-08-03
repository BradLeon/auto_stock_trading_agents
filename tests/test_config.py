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
    # KLAC has no signal_chain in config/pead/KLAC.yaml — falls back to its
    # ai_hardware.yaml L6_equipment layer peers.
    cfg = load_pead_config("KLAC")
    peers = {s.symbol for s in cfg.signal_chain}
    assert peers, "expected a non-empty derived peer list"
    assert "KLAC" not in peers          # never include itself
    assert "ASML" in peers              # same L6_equipment layer
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
