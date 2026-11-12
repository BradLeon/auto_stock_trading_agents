"""Close-history downloads: serial, one reused session, retry only transport failures.

Background (2026-11-12): new TLS handshakes to Yahoo through the local proxy are
reset at random, so a threaded batch lost about half its symbols every run.
"""

from __future__ import annotations

import pandas as pd
import pytest

from ats.data.runtime import market_data


def _frame(columns: dict[str, list[float | None]]) -> pd.DataFrame:
    return pd.DataFrame(columns, index=pd.to_datetime(["2026-11-10", "2026-11-11"]))


@pytest.fixture(autouse=True)
def _no_wait(monkeypatch):
    monkeypatch.setattr(market_data, "_RETRY_DELAYS", (0.0, 0.0))


def _scripted(monkeypatch, responses):
    calls: list[list[str]] = []

    def fake(symbols, period):
        calls.append(list(symbols))
        return responses[len(calls) - 1]

    monkeypatch.setattr(market_data, "_download_close_frame", fake)
    return calls


def test_transport_failure_is_retried_for_that_symbol_only(monkeypatch):
    calls = _scripted(monkeypatch, [
        (_frame({"AAPL": [1.0, 2.0], "NVDA": [None, None]}), ["AAPL", "NVDA"], {"NVDA"}),
        (_frame({"NVDA": [3.0, 4.0]}), ["NVDA"], set()),
    ])

    rows = market_data.fetch_close_history_many(["AAPL", "NVDA"])

    assert calls == [["AAPL", "NVDA"], ["NVDA"]]
    assert rows["AAPL"].status == "succeeded" and rows["AAPL"].closes == (1.0, 2.0)
    assert rows["NVDA"].status == "succeeded" and rows["NVDA"].closes == (3.0, 4.0)
    assert rows["NVDA"].bar_as_of.isoformat() == "2026-11-11"


def test_persistent_transport_failure_is_unavailable_not_no_data(monkeypatch):
    failing = (_frame({"TSM": [None, None]}), ["TSM"], {"TSM"})
    calls = _scripted(monkeypatch, [
        (_frame({"MU": [5.0, 6.0], "TSM": [None, None]}), ["MU", "TSM"], {"TSM"}),
        failing, failing,
    ])

    rows = market_data.fetch_close_history_many(["MU", "TSM"])

    assert calls == [["MU", "TSM"], ["TSM"], ["TSM"]]
    assert rows["MU"].status == "succeeded"
    assert (rows["TSM"].status, rows["TSM"].reason) == ("unavailable", "transport_error")


def test_symbol_yahoo_answered_without_data_is_not_retried(monkeypatch):
    calls = _scripted(monkeypatch, [
        (_frame({"MSFT": [1.0, 2.0], "GONE": [None, None]}), ["MSFT", "GONE"], set()),
    ])

    rows = market_data.fetch_close_history_many(["MSFT", "GONE"])

    assert calls == [["MSFT", "GONE"]]
    assert (rows["GONE"].status, rows["GONE"].reason) == ("no_data", "empty_symbol_series")


def test_two_value_boundary_still_supported(monkeypatch):
    calls = _scripted(monkeypatch, [(_frame({"MSFT": [1.0, 2.0]}), ["MSFT", "TSLA"])])

    rows = market_data.fetch_close_history_many(["MSFT", "TSLA"])

    assert calls == [["MSFT", "TSLA"]]
    assert rows["MSFT"].status == "succeeded"
    assert rows["TSLA"].status == "no_data"


def test_download_is_serial_on_the_shared_session_and_maps_transport_failures(monkeypatch):
    import yfinance as yf
    from curl_cffi import requests as curl_requests

    monkeypatch.setattr(market_data, "_SESSION", None)

    def base_request(self, method, url, *args, **kwargs):
        raise curl_requests.exceptions.SSLError("SSL_connect: Connection closed abruptly")

    monkeypatch.setattr(curl_requests.Session, "request", base_request)
    seen: dict = {}

    def fake_download(tickers, **kwargs):
        seen.update(kwargs)
        session = kwargs["session"]
        for ticker in ("HY9H.F", "%5EVIX"):
            with pytest.raises(curl_requests.exceptions.SSLError):
                session.request("GET", f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}?range=1y")
        with pytest.raises(curl_requests.exceptions.SSLError):
            session.request("GET", "https://query1.finance.yahoo.com/v1/test/getcrumb")
        return pd.DataFrame({"Close": [1.0]})

    monkeypatch.setattr(yf, "download", fake_download)
    monkeypatch.setattr("ats.data.base.yf_symbol",
                        lambda symbol: {"HY9H": "HY9H.F"}.get(symbol, symbol))

    _frame_out, normalized, failed = market_data._download_close_frame(["HY9H", "^VIX", "AAPL"], "1y")

    assert seen["threads"] is False
    assert seen["session"] is market_data._tracking_session()
    assert normalized == ["HY9H.F", "^VIX", "AAPL"]
    assert failed == {"HY9H", "^VIX"}

    # The next call starts with a clean record on the same session.
    monkeypatch.setattr(yf, "download", lambda tickers, **kwargs: pd.DataFrame({"Close": [1.0]}))
    assert market_data._download_close_frame(["AAPL"], "1y")[2] == set()
