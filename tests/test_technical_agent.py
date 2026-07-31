"""Technical analyst: universe resolution, readings, Chief injection.

Fully hermetic — no network, no broker. The strategy math itself is covered by
tests/test_research_replay.py (36 tests over the SAME module, since the backtest
imports the production strategy).
"""

from datetime import datetime, timedelta, timezone

import pytest

from ats.agents.technical import review as tech
from ats.agents.technical import strategy as st
from ats.schemas.technical import TechnicalConfig, TechnicalReading, TechnicalReview

NOW = datetime(2026, 7, 31, tzinfo=timezone.utc)


def _cfg(**kw):
    base = dict(name="technical", label="技术面", strategy="jia",
                universe={"include_holdings": False, "include_pead_targets": False,
                          "exclude": ["SGOV"], "min_bars": 200},
                params={}, review={"history_days": 420, "chief_block_chars": 1200})
    base.update(kw)
    return TechnicalConfig(**base)


def rising(n=300, start=100.0, step=1.0):
    return [start + i * step for i in range(n)]


def falling(n=300, start=400.0, step=1.0):
    return [start - i * step for i in range(n)]


# ── the analyst must never be able to emit an order ──────────────────────────
def test_technical_package_never_touches_trade_decision():
    """Structural guarantee, same as the one added for PEAD after it was caught
    constructing TradeDecision with real share counts. `target_exposure` sits one
    step from a position instruction, so the boundary is enforced, not trusted."""
    import pathlib

    pkg = pathlib.Path("src/ats/agents/technical")
    for path in pkg.glob("*.py"):
        src = path.read_text(encoding="utf-8")
        assert "import TradeDecision" not in src, path
        assert "TradeDecision(" not in src, path


def test_reading_model_has_no_order_fields():
    fields = set(TechnicalReading.model_fields)
    assert not fields & {"qty", "notional_usd", "notional", "action", "order_type"}


# ── universe resolution ──────────────────────────────────────────────────────
def test_universe_merges_targets_and_holdings(monkeypatch):
    monkeypatch.setattr("ats.config.load_pead_global", lambda: {"targets": ["NVDA", "TSM"]})

    class _Pos:
        def __init__(self, symbol, sec_type="STK"):
            self.symbol, self.sec_type = symbol, sec_type

    class _PF:
        positions = [_Pos("MSFT"), _Pos("NVDA"), _Pos("SGOV"), _Pos("COHR", "OPT")]

    monkeypatch.setattr("ats.trader.portfolio.snapshot", lambda: _PF())
    syms, notes = tech.resolve_universe(
        _cfg(universe={"include_holdings": True, "include_pead_targets": True,
                       "exclude": ["SGOV"], "min_bars": 200}))
    assert syms == ["MSFT", "NVDA", "TSM"]          # union, deduped, SGOV dropped
    assert any("期权持仓本身不评估" in n for n in notes)   # the OPT leg, not the name
    assert any("现金等价物" in n for n in notes)


def test_universe_degrades_to_targets_when_broker_unavailable(monkeypatch):
    monkeypatch.setattr("ats.config.load_pead_global", lambda: {"targets": ["NVDA"]})
    monkeypatch.setattr("ats.trader.portfolio.snapshot", lambda: None)
    syms, notes = tech.resolve_universe(
        _cfg(universe={"include_holdings": True, "include_pead_targets": True,
                       "exclude": [], "min_bars": 200}))
    assert syms == ["NVDA"]
    assert any("IBKR 不可用" in n for n in notes)


def test_universe_collapses_aliases_of_the_same_instrument(monkeypatch):
    """HY9H (Frankfurt ADR) and SKHY are one company; fetch_prices' reverse map
    keeps only one, so the other would silently report 'no price data'."""
    monkeypatch.setattr("ats.config.load_pead_global",
                        lambda: {"targets": ["SKHY", "HY9H", "NVDA"]})
    syms, notes = tech.resolve_universe(
        _cfg(universe={"include_holdings": False, "include_pead_targets": True,
                       "exclude": [], "min_bars": 200}))
    assert syms == ["NVDA", "SKHY"]
    assert any("别名已合并" in n for n in notes)


# ── readings ─────────────────────────────────────────────────────────────────
def test_readings_match_the_strategy_module_exactly():
    """The agent must not re-derive anything — it only calls strategy.py."""
    closes = rising()
    got = tech.compute_readings({"X": closes}, vix=16.9, vix3m=20.0, cfg=_cfg())[0]
    want_score = st.momentum_score_7(closes, len(closes) - 1)
    want_base = st.exposure_from(want_score, 16.9, "jia")
    assert got.score == want_score
    assert got.target_exposure == pytest.approx(round(want_base, 4))


def test_short_history_is_marked_stale_not_guessed():
    got = tech.compute_readings({"X": rising(50)}, vix=15.0, vix3m=18.0, cfg=_cfg())[0]
    assert got.stale and got.target_exposure == 0.0 and "历史不足" in got.note


def test_tier2_bear_caps_exposure_below_sma200():
    got = tech.compute_readings({"X": falling()}, vix=15.0, vix3m=18.0, cfg=_cfg())[0]
    assert got.bear_fired and got.target_exposure <= st.BEAR_PRICE_CAP


def test_tier1_panic_needs_vix3m_and_is_skipped_without_it():
    """A stale VIX3M divided into a spiking VIX fabricates an inversion on
    exactly the days that matter, so absence must disable Tier 1."""
    panicked = tech.compute_readings({"X": rising()}, vix=23.0, vix3m=19.0, cfg=_cfg())[0]
    assert panicked.panic_fired and panicked.target_exposure == 0.0
    unknown = tech.compute_readings({"X": rising()}, vix=23.0, vix3m=None, cfg=_cfg())[0]
    assert not unknown.panic_fired and unknown.target_exposure > 0.0


def test_config_params_override_strategy_defaults():
    hot = _cfg(params={"bear_cap": 0.1})
    got = tech.compute_readings({"X": falling()}, vix=15.0, vix3m=18.0, cfg=hot)[0]
    assert got.target_exposure <= 0.1


def test_prior_exposure_drives_change_detection():
    r = tech.compute_readings({"X": rising()}, vix=15.0, vix3m=18.0, cfg=_cfg(),
                              prior={"X": 0.5})[0]
    assert r.prev_exposure == 0.5 and r.changed


# ── Chief injection ──────────────────────────────────────────────────────────
def _review(**kw):
    base = dict(as_of=NOW, vix=18.0, vix3m=19.5, readings=[
        TechnicalReading(symbol="NVDA", score=7, target_exposure=1.0, prev_exposure=1.0),
        TechnicalReading(symbol="COHR", score=2, target_exposure=0.45,
                         prev_exposure=1.0, bear_fired=True),
        TechnicalReading(symbol="ZZZ", stale=True, note="历史不足"),
    ])
    base.update(kw)
    return TechnicalReview(**base)


def test_chief_block_only_surfaces_what_needs_attention():
    """20 fully-invested names would crowd out everything else; 'all normal' is
    precisely what the Chief does not need to spend attention on."""
    block = _review().chief_block()
    assert "COHR" in block                       # below full exposure -> shown
    assert "NVDA" not in block                   # full and unchanged -> omitted
    assert "非方向判断、非交易指令" in block       # the boundary travels with the data
    assert "ZZZ" in block                        # stale is disclosed, not hidden


def test_chief_block_respects_its_character_budget():
    many = [TechnicalReading(symbol=f"S{i}", score=2, target_exposure=0.4)
            for i in range(60)]
    assert len(_review(readings=many).chief_block(1200)) <= 1200


def test_chief_block_is_empty_when_there_are_no_readings():
    assert _review(readings=[]).chief_block() == ""


def test_chief_context_helper_never_raises(monkeypatch):
    from ats.agents.technical import context as tech_ctx

    def boom():
        raise RuntimeError("store down")

    monkeypatch.setattr("ats.memory.get_store", boom)
    assert tech_ctx.chief_block() == ""


# ── persistence ──────────────────────────────────────────────────────────────
def test_same_day_rerun_overwrites_and_previous_finds_yesterday():
    from ats.memory import get_store

    store = get_store()
    y = _review(as_of=NOW - timedelta(days=1),
                readings=[TechnicalReading(symbol="NVDA", target_exposure=1.0)])
    t = _review(readings=[TechnicalReading(symbol="NVDA", target_exposure=0.5)])
    store.save_technical_review(y)
    store.save_technical_review(t)
    store.save_technical_review(t)                       # rerun same day

    assert len(store.recent_technical_reviews()) == 2    # not 3
    assert store.latest_technical_review().readings[0].target_exposure == 0.5
    prev = store.previous_technical_review(before=NOW.date().isoformat())
    assert prev.readings[0].target_exposure == 1.0       # strictly before today
