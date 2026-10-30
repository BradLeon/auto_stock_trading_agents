"""Beta for the risk review is reused across processes for a day, and approving warms it before the snapshot."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from ats.data import fundamentals
from ats.risk import assess
from ats.schemas.portfolio import PortfolioSnapshot, Position


@pytest.fixture
def fetched(monkeypatch, tmp_path):
    """Count network pulls; the cache file lives beside the (test) memory database."""
    monkeypatch.setenv("ATS_DB_PATH", str(tmp_path / "ats.sqlite"))
    calls = []

    def light(symbol, **kw):
        calls.append(symbol)
        return {"beta": 1.5}

    monkeypatch.setattr(fundamentals, "fetch_light", light)
    return calls


def test_a_second_call_reuses_the_beta_without_a_network_pull(fetched):
    assert fundamentals.cached_beta("NVDA") == (1.5, True)
    assert fundamentals.cached_beta("NVDA") == (1.5, False)
    assert fetched == ["NVDA"]


def test_a_day_old_beta_is_pulled_again(fetched, monkeypatch, tmp_path):
    fundamentals.cached_beta("NVDA")
    path = tmp_path / "risk_beta_cache.json"
    cache = json.loads(path.read_text())
    cache["NVDA"]["at"] -= fundamentals._BETA_TTL + 1
    path.write_text(json.dumps(cache))
    assert fundamentals.cached_beta("NVDA") == (1.5, True)
    assert fetched == ["NVDA", "NVDA"]


def test_a_missing_beta_is_not_remembered(monkeypatch, tmp_path):
    monkeypatch.setenv("ATS_DB_PATH", str(tmp_path / "ats.sqlite"))
    monkeypatch.setattr(fundamentals, "fetch_light", lambda symbol, **kw: {"beta": None})
    assert fundamentals.cached_beta("XYZ") == (None, True)
    assert not (tmp_path / "risk_beta_cache.json").exists()


def test_a_corrupt_cache_file_is_ignored(fetched, tmp_path):
    (tmp_path / "risk_beta_cache.json").write_text("{not json")
    assert fundamentals.cached_beta("NVDA") == (1.5, True)


def _portfolio():
    stock = Position(symbol="NVDA", sector="ai", qty=10, avg_cost=100, market_price=200, market_value=2000,
                     unrealized_pnl=0, weight=.2)
    return PortfolioSnapshot(as_of=datetime(2026, 10, 30, tzinfo=timezone.utc), net_liquidation=10000, cash=8000, positions=[stock])


def test_warming_fetches_each_holdings_beta_once_and_only_those_missing(fetched, monkeypatch):
    monkeypatch.setattr(assess.time, "sleep", lambda s: None)
    pf = _portfolio()
    assert assess.warm_betas(pf) == 1
    assert assess.warm_betas(pf) == 0
    assert fetched == ["NVDA"]


def test_the_review_after_warming_pays_for_no_network_and_no_pacing(fetched, monkeypatch):
    slept = []
    monkeypatch.setattr(assess.time, "sleep", lambda s: slept.append(s))
    assess.warm_betas(_portfolio())
    slept.clear()
    pf = _portfolio()
    assess.enrich_beta(pf)
    assert pf.positions[0].beta == 1.5 and fetched == ["NVDA"] and slept == []
