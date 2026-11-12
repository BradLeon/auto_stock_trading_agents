"""Another company's earnings call must never be admitted, selected or scored as ours.

Regression for 2026-11-05. Four web transcripts were admitted under the wrong ticker:
Seagate (11-03) and Estee Lauder (11-04) as MSFT Q1 FY2027, Ambarella (09-04) and
Marvell (09-22) as NVDA Q2 FY2027. The chain behind it:

  1. the legacy defeatbeta URL had gone 404 (upstream moved files under `data/US/`), so the
     keyed source silently returned nothing and every lookup fell through to web search;
  2. a quarter-targeted search, run before the issuer's own call was published, returned
     other companies' calls for the same quarter, and selection checked the quarter only;
  3. admission accepted a body that mentioned "Microsoft" once, as a partner, 25k chars in;
  4. the event package picked the NEWEST transcript, so the web page displaced the keyed
     defeatbeta transcript of the same quarter;
  5. the actuals model noticed ("所附纪要实为 Estée Lauder") and the review was published anyway.

Each layer has a test here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from ats.data import admission, defeatbeta, transcript
from ats.data.products.unstructured import earnings_document_package

FOOL = "https://www.fool.com/earnings/call-transcripts"
SEAGATE = f"{FOOL}/2026/10/28/seagate-stx-q1-2027-earnings-call-transcript"
ESTEE = f"{FOOL}/2026/11/02/estee-lauder-el-q1-2027-earnings-call-transcript"
MARVELL = f"{FOOL}/2026/08/31/marvell-mrvl-q2-2027-earnings-call-transcript"
AMBARELLA = ("https://www.investing.com/news/transcripts/"
             "earnings-call-transcript-ambarella-beats-q2-2027-estimates-shares-fall-11-93CH-4888612")
NVIDIA = f"{FOOL}/2026/08/31/nvidia-nvda-q2-2027-earnings-call-transcript"
MICROSOFT = f"{FOOL}/2026/10/28/microsoft-msft-q1-2027-earnings-call-transcript"
ALPHABET = f"{FOOL}/2026/10/29/alphabet-googl-q3-2026-earnings-call-transcript"

FILLER = ("Operator: thank you. Next question please. Chief Financial Officer: margins. "
          "Question-and-Answer session continues. ") * 300


# --------------------------------------------------------------------------- 1. keyed source
def test_legacy_defeatbeta_urls_point_where_the_managed_jobs_read() -> None:
    catalog = yaml.safe_load(Path("config/data/unstructured.yaml").read_text(encoding="utf-8"))
    policy = catalog["sources"]["defeatbeta_earnings_transcript"]["policy"]
    assert defeatbeta.TRANSCRIPTS.endswith("/" + policy["file"])
    assert defeatbeta.NEWS.endswith("/data/US/stock_news.parquet")


def test_dataset_snapshot_reads_the_relocated_spec_key(tmp_path: Path, monkeypatch) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text('{"files": {"US/stock_news.parquet": "2026-11-03T04:35:18Z"}}', encoding="utf-8")
    monkeypatch.setenv("ATS_DEFEATBETA_SPEC_URI", str(spec))
    snap = defeatbeta.dataset_snapshot(dataset_file="stock_news.parquet",
                                       now=datetime(2026, 11, 3, 6, tzinfo=timezone.utc))
    assert snap.updated_at.startswith("2026-11-03T04:35:18")


def test_platform_admitted_keyed_transcript_is_read_before_any_provider(monkeypatch) -> None:
    from ats.data import source_cache
    from ats.data.products import unstructured as products

    monkeypatch.setattr(source_cache, "load", lambda *a, **k: None)
    monkeypatch.setattr(transcript, "manual_path", lambda *a: Path("/nonexistent/x.txt"))
    item = products.EarningsDocument(
        role="earnings_transcript", document_id="MSFT:defeatbeta", version_id="v1",
        source="defeatbeta_earnings_transcript", source_url="", published_at="2026-10-28",
        title="MSFT Q1 FY2027 earnings call", text="microsoft call body", period="Q1 FY2027")
    monkeypatch.setattr(products, "platform_earnings_document_package", lambda **k: products.EarningsDocumentPackage(
        entity="MSFT", period="Q1 FY2027", documents=(item,), repository="fake"))

    def forbidden(*_a, **_k):
        raise AssertionError("a provider was asked although the platform holds the keyed transcript")

    monkeypatch.setattr(defeatbeta, "fetch", forbidden)
    monkeypatch.setattr(transcript, "_from_search", forbidden)
    assert transcript.fetch("MSFT", "Q1 FY2027") == ("microsoft call body", "defeatbeta_earnings_transcript")


def test_an_admitted_web_page_does_not_short_circuit_the_providers(monkeypatch) -> None:
    from ats.data.products import unstructured as products

    item = products.EarningsDocument(
        role="earnings_transcript", document_id="MSFT:web", version_id="v1",
        source=f"tavily:{MICROSOFT}", source_url=MICROSOFT, published_at="",
        title="MSFT Q1 FY2027 earnings call transcript", text="web body", period="Q1 FY2027")
    monkeypatch.setattr(products, "platform_earnings_document_package", lambda **k: products.EarningsDocumentPackage(
        entity="MSFT", period="Q1 FY2027", documents=(item,), repository="fake"))
    assert transcript._admitted_transcript("MSFT", "Q1 FY2027") is None


# --------------------------------------------------------------------------- 2. identity
@pytest.mark.parametrize("url,symbol", [
    (SEAGATE, "MSFT"), (ESTEE, "MSFT"), (MARVELL, "NVDA"), (AMBARELLA, "NVDA"),
    (f"{FOOL}/2026/10/23/intel-intc-q3-2026-earnings-call-transcript", "EL"),  # "el" is not "intel"
])
def test_the_observed_mismatches_are_not_identified_by_their_locator(url: str, symbol: str) -> None:
    assert not admission.locator_names_entity(url, symbol)


@pytest.mark.parametrize("url,symbol", [(NVIDIA, "NVDA"), (MICROSOFT, "MSFT"), (ALPHABET, "GOOG")])
def test_the_issuers_own_page_is_identified(url: str, symbol: str) -> None:
    assert admission.locator_names_entity(url, symbol)


def test_a_passing_mention_deep_in_another_call_is_not_identity() -> None:
    estee_body = ("The Estee Lauder Companies fiscal 2027 first quarter conference call. " + FILLER
                  + " partners such as Microsoft and Google power our cloud. " + FILLER)
    assert admission.mentions_entity(estee_body, "MSFT")            # the old, too-wide check passes it
    assert not admission.transcript_opening_names_entity(estee_body, "MSFT")


def test_search_selection_drops_same_quarter_calls_of_other_companies(monkeypatch) -> None:
    from types import SimpleNamespace

    from ats import config

    monkeypatch.setattr(config, "get_config",
                        lambda: SimpleNamespace(secrets=SimpleNamespace(tavily_api_key="k")))
    results = [{"url": SEAGATE, "title": "Seagate (STX) Q1 2027 Earnings Call Transcript",
                "raw_content": "Seagate Q1 2027 " + FILLER},
               {"url": ESTEE, "title": "Estee Lauder (EL) Q1 2027 Earnings Call Transcript",
                "raw_content": "Estee Lauder Q1 2027 " + FILLER}]
    monkeypatch.setattr(transcript, "_tavily_search", lambda key, query: list(results))
    assert transcript._from_search("MSFT", "Q1 2027", "Microsoft") == ("", "")

    own = {"url": MICROSOFT, "title": "Microsoft (MSFT) Q1 2027 Earnings Call Transcript",
           "raw_content": "Microsoft Q1 2027 " + FILLER}
    monkeypatch.setattr(transcript, "_tavily_search", lambda key, query: [*results, own])
    text, src = transcript._from_search("MSFT", "Q1 2027", "Microsoft")
    assert src == f"tavily:{MICROSOFT}"


# --------------------------------------------------------------------------- 3. selection
class _Repository:
    def __init__(self, rows, versions):
        self.rows, self.versions = rows, versions

    def documents(self, entity=None, **_kwargs):
        return [row for row in self.rows if row["entity"] == entity]

    def latest_document_version(self, document_id):
        return self.versions[document_id]


def test_a_newer_web_transcript_does_not_displace_the_keyed_one(tmp_path: Path) -> None:
    keyed, web = tmp_path / "keyed.md", tmp_path / "web.md"
    keyed.write_text("defeatbeta microsoft call", encoding="utf-8")
    web.write_text("estee lauder call", encoding="utf-8")
    base = {"entity": "MSFT", "doc_type": "earnings_transcript", "period": "Q1 FY2027",
            "published_at": "", "title": "MSFT Q1 FY2027 earnings call"}
    rows = [{**base, "document_id": "MSFT:keyed", "source": "defeatbeta_earnings_transcript",
             "fetched_at": "2026-10-29T09:23:30+00:00"},
            {**base, "document_id": "MSFT:web", "source": f"tavily:{ESTEE}",
             "fetched_at": "2026-11-04T22:06:34+00:00"}]
    versions = {"MSFT:keyed": {"version_id": "k1", "local_path": str(keyed),
                               "fetched_at": "2026-10-29T09:23:30+00:00"},
                "MSFT:web": {"version_id": "w1", "local_path": str(web),
                             "fetched_at": "2026-11-04T22:06:34+00:00"}}
    package = earnings_document_package(_Repository(rows, versions), entity="MSFT", period="Q1 FY2027")
    assert package.transcript.document_id == "MSFT:keyed"


# --------------------------------------------------------------------------- 4. model flag
def test_actuals_model_flagging_a_foreign_document_blocks_the_review(monkeypatch) -> None:
    from ats.agents.pead import score
    from ats.agents.pead.outputs import ActualsView
    from ats.schemas.pead import PeadConfig
    from ats.workflow.read_scope import ReadUnavailable

    view = ActualsView.model_validate({
        "reported_eps": 4.13, "metrics": [{"dim_key": "revenue", "actual": "$77.7B"}],
        "document_identity_issues": [{"document": "transcript", "problem": "other_company",
                                      "detail": "实为 Estée Lauder FY2027 Q1 电话会"}]})
    monkeypatch.setattr(score, "run_structured", lambda *a, **k: view)
    config = PeadConfig.model_validate({"symbol": "MSFT", "fiscal_label": "Q1 FY2027"})
    with pytest.raises(score.DocumentIdentityMismatch) as caught:
        score.extract_actuals(config, None, "estee lauder call", "financials",
                              datetime(2026, 11, 4, tzinfo=timezone.utc), f"tavily:{ESTEE}")
    assert isinstance(caught.value, ReadUnavailable)
    assert "Estée Lauder" in str(caught.value)


def test_no_identity_issue_leaves_extraction_unchanged(monkeypatch) -> None:
    from ats.agents.pead import score
    from ats.agents.pead.outputs import ActualsView
    from ats.schemas.pead import PeadConfig

    view = ActualsView.model_validate({"reported_eps": 4.13,
                                       "metrics": [{"dim_key": "revenue", "actual": "$77.7B"}]})
    monkeypatch.setattr(score, "run_structured", lambda *a, **k: view)
    config = PeadConfig.model_validate({"symbol": "MSFT", "fiscal_label": "Q1 FY2027"})
    actuals = score.extract_actuals(config, None, "microsoft call", "financials",
                                    datetime(2026, 11, 4, tzinfo=timezone.utc), "defeatbeta")
    assert actuals.reported_eps == 4.13


# --------------------------------------------------------------------------- 5. no re-run on a copy
def _reconcile_with(monkeypatch, confirmed_kinds, package_docs):
    from ats.data import calendar_refresh
    from ats.data.products import unstructured as products
    from ats.data.stores import unstructured as stores

    event = {"event_id": "earnings:AMZN:FY2026Q3:release", "event_version": 5, "event_type": "earnings",
             "status": "released", "event_date": "2026-10-29", "reference_period": "FY2026Q3",
             "payload": {"metadata": {"entity": "AMZN", "fiscal_label": "FY2026Q3"}},
             "release_confirmations": [{"materials": [
                 {"kind": kind, "ref": f"old-{kind}@v1"} for kind in confirmed_kinds]}]}
    confirmed: list = []

    class _Calendar:
        def latest_events(self, limit):
            return [event]

        def confirm_release(self, event_id, **kwargs):
            confirmed.append(kwargs["admitted_materials"])
            return {**event, "event_version": 6}

    class _Docs:
        def close(self):
            pass

    monkeypatch.setattr(stores, "get_platform_unstructured_repository", lambda: _Docs())
    monkeypatch.setattr(products, "earnings_document_package", lambda *a, **k: products.EarningsDocumentPackage(
        entity="AMZN", period="FY2026Q3", documents=tuple(package_docs), repository="fake"))
    calendar_refresh.reconcile_earnings_release_materials(store=_Calendar())
    return confirmed


def _doc(role, document_id):
    from ats.data.products.unstructured import EarningsDocument

    return EarningsDocument(role=role, document_id=document_id, version_id="v1", source="x", source_url="",
                            published_at="", title="", text="body")


def test_a_second_copy_of_a_confirmed_call_does_not_reopen_the_event(monkeypatch) -> None:
    docs = [_doc("earnings_release", "old-company_release"), _doc("earnings_transcript", "AMZN:defeatbeta")]
    assert _reconcile_with(monkeypatch, ["company_release", "transcript"], docs) == []


def test_the_first_transcript_of_an_event_still_reopens_it(monkeypatch) -> None:
    docs = [_doc("earnings_release", "old-company_release"), _doc("earnings_transcript", "AMZN:defeatbeta")]
    assert len(_reconcile_with(monkeypatch, ["company_release"], docs)) == 1


def test_a_new_filing_still_reopens_the_event(monkeypatch) -> None:
    docs = [_doc("earnings_release", "old-company_release"), _doc("regulatory_filing", "AMZN:10-Q")]
    assert len(_reconcile_with(monkeypatch, ["company_release", "transcript"], docs)) == 1


# --------------------------------------------------------------------------- 6. withdrawal
def test_a_withdrawn_document_disappears_from_current_and_later_as_of_reads(tmp_path: Path) -> None:
    import hashlib
    from datetime import timedelta
    from types import SimpleNamespace

    from ats.data.stores.unstructured.platform import PlatformUnstructuredRepository

    repo = PlatformUnstructuredRepository(tmp_path / "data.sqlite", writable=True)
    body = "Estee Lauder call body " * 200
    admitted_at = datetime(2026, 11, 4, 22, 6, tzinfo=timezone.utc)
    repo.save_document(SimpleNamespace(
        document_id="MSFT:Q1 FY2027:earnings_transcript", symbol="MSFT", period="Q1 FY2027",
        doc_type="earnings_transcript", source=f"tavily:{ESTEE}", source_url=ESTEE,
        path=tmp_path / "t.md", sha256=hashlib.sha256(body.encode()).hexdigest(), text=body,
        fetched_at=admitted_at.isoformat(), related_entities=("MSFT",)))
    withdrawn_at = admitted_at + timedelta(hours=10)
    repo._withdraw_document("MSFT:Q1 FY2027:earnings_transcript", reason="Estee Lauder call",
                            known_at=withdrawn_at)

    assert repo.documents(entity="MSFT") == []
    assert repo.document_materials_at(as_of=withdrawn_at + timedelta(minutes=1)) == []
    before = repo.document_materials_at(as_of=withdrawn_at - timedelta(minutes=1))
    assert [row["document_id"] for row in before] == ["MSFT:Q1 FY2027:earnings_transcript"]
    with pytest.raises(PermissionError):
        # the public entry keeps the production write guard
        import ats.data.stores.unstructured.platform as platform

        original = platform.PlatformUnstructuredRepository._require_managed_writer
        try:
            platform.PlatformUnstructuredRepository._require_managed_writer = (
                lambda self: (_ for _ in ()).throw(PermissionError("lease")))
            repo.withdraw_document("MSFT:Q1 FY2027:earnings_transcript", reason="again")
        finally:
            platform.PlatformUnstructuredRepository._require_managed_writer = original
    repo.close()
