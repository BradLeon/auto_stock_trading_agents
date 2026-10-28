"""A network fault is `unreachable`, not `parse_failed` (G0, 2026-10-21).

Clash reset TLS connections to Yahoo and the Taiwan MOF site; the real clients raise `ssl.SSLError`, `URLError`,
httpx and curl_cffi errors, none of which is a stdlib `ConnectionError`, so the runs were recorded as
`parse_failed` and read as parser bugs.
"""
from __future__ import annotations

import ssl
from datetime import datetime, timezone
from urllib.error import URLError

import pytest

from ats.data.pipelines.structured.ingestion import _is_transport_error
from ats.data.structured import (FetchRequest, IngestionPipeline, SQLiteStructuredRepository,
                                 StructuredCatalog)


class _Adapter:
    def __init__(self, error):
        self.error = error

    def fetch(self, request):
        raise self.error


def _transport_errors():
    errors = [ssl.SSLError("[SSL: UNEXPECTED_EOF_WHILE_READING] EOF"), URLError("tls reset"),
              ConnectionResetError("reset"), TimeoutError("slow")]
    try:
        from curl_cffi.requests.exceptions import ConnectionError as CurlConnectionError
        errors.append(CurlConnectionError("curl: (35) Connection closed abruptly"))
    except ImportError:
        pass
    try:
        import httpx
        errors.append(httpx.ConnectError("refused"))
    except ImportError:
        pass
    return errors


@pytest.mark.parametrize("error", _transport_errors(), ids=lambda e: type(e).__name__)
def test_network_failures_are_transport_errors(error):
    assert _is_transport_error(error)


@pytest.mark.parametrize("error", [ValueError("unexpected column"), KeyError("avg"), RuntimeError("bad shape")],
                         ids=lambda e: type(e).__name__)
def test_parser_failures_are_not_transport_errors(error):
    assert not _is_transport_error(error)


def _run(tmp_path, error):
    repo = SQLiteStructuredRepository(tmp_path / "s.sqlite", artifact_root=tmp_path / "a")
    repo.bootstrap_catalog(StructuredCatalog.load())
    request = FetchRequest(source_id="sec_companyfacts", dataset_id="company_financials", entities=["MSFT"],
                           periods=["FY2026Q4"], query_scope={"entity": "MSFT"})
    return IngestionPipeline(repo).run(_Adapter(error), request)


def test_a_tls_reset_run_is_recorded_unreachable(tmp_path):
    assert _run(tmp_path, ssl.SSLError("EOF in violation of protocol"))["status"] == "unreachable"


def test_an_unparsable_response_is_still_parse_failed(tmp_path):
    assert _run(tmp_path, ValueError("unexpected column"))["status"] == "parse_failed"
