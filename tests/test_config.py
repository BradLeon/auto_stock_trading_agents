"""config.py: PEAD config merging, in particular the signal_chain fallback.

Regression: MSFT.yaml originally had no `signal_chain`, so the PEAD score report
always rendered "跨标的信号链（暂无数据）" — the LLM call was never even reached
(agents/pead/prep.py::signal_chain short-circuits on an empty list). The fallback
derives peers from the ticker's sector layer so a new PEAD target never silently
ships without a cross-symbol view; a curated YAML entry still wins.
"""

from ats.config import load_pead_config


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
