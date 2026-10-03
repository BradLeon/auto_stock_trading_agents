"""Zero-network Task 3 qualification using existing artifacts and isolated stores.

uv run --offline --no-sync python scripts/replay_target_dataflow.py
This replays the post-adapter boundary, not a fresh provider/parser acceptance.
Source-specific parser acceptance remains the Task 2 source evidence. Synthetic
failure/revision probes are labelled and never written to production databases.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sqlite3
import tempfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ats.data.consumer_api import assert_research_payload, input_contract, read_input
from ats.data.contract_validation import EXPECTED_PRODUCTS, validate_target_contract
from ats.data.products.base import DataProducts
from ats.data.products.unstructured import admitted_documents, earnings_document_package
from ats.data.source_cache import CachedDoc, _split_frontmatter
from ats.data.stores.unstructured import PlatformUnstructuredRepository
from ats.data.structured import (
    AdapterArtifact,
    AdapterBatch,
    FetchRequest,
    IngestionPipeline,
    IngestionStatus,
    NativeRecord,
    SQLiteStructuredRepository,
    StructuredCatalog,
)


def point(value):
    stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


class CachedAdapter:
    def __init__(self, batch=None, error=None):
        self.batch, self.error = batch, error

    def fetch(self, request):
        if self.error:
            raise self.error
        return self.batch


def no_network(sock, address):
    raise AssertionError(f"cache replay attempted a network connection: {address}")


def structured_replay(cache, root):
    repo = SQLiteStructuredRepository(root / "structured.sqlite", artifact_root=root / "artifacts")
    repo.bootstrap_catalog(StructuredCatalog.load())
    products = DataProducts(structured_repository=repo)
    pipeline = IngestionPipeline(repo)
    output = []
    datasets = ("company_financials", "regional_tw_exports", "regional_kr_exports",
                "industry_dram_contract_price", "market_consensus", "sp500_earnings_insight")
    for dataset in datasets:
        row = cache.execute(
            "SELECT o.*,s.source_id,s.dataset_id,s.entity_id,s.metric_id,s.unit,s.currency,"
            "s.period_basis,s.adjustment,s.dimensions_json,a.source_url,a.source_version,"
            "a.media_type,b.relative_path,b.content_hash blob_hash FROM structured_observations o "
            "JOIN structured_series s ON s.series_id=o.series_id "
            "JOIN structured_artifacts a ON a.artifact_id=o.artifact_id "
            "JOIN structured_artifact_blobs b ON b.blob_id=a.blob_id "
            "WHERE s.dataset_id=? AND o.quality_status='accepted' "
            "ORDER BY o.known_at DESC LIMIT 1", (dataset,)).fetchone()
        assert row, f"cache_missing:{dataset}"
        row = dict(row)
        raw_path = Path("var/data_artifacts") / row["relative_path"]
        raw = raw_path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row["blob_hash"], f"corrupt_raw:{dataset}"
        stamp = point(row["known_at"])
        record = NativeRecord(
            entity_id=row["entity_id"], provider_field=row["metric_id"], period=row["period"],
            value=row["value"], unit=row["unit"], currency=row["currency"],
            period_basis=row["period_basis"], adjustment=row["adjustment"],
            period_start=row["period_start"], period_end=row["period_end"],
            event_time=point(row["event_time"]) if row["event_time"] else None,
            published_at=point(row["published_at"]) if row["published_at"] else None,
            dimensions=json.loads(row["dimensions_json"]), raw=json.loads(row["raw_payload"]),
        )
        request = FetchRequest(source_id=row["source_id"], dataset_id=dataset,
                               entities=[row["entity_id"]], periods=[row["period"]])

        def batch(rec=record, at=stamp, payload=raw, status=IngestionStatus.SUCCEEDED):
            return AdapterBatch(source_id=row["source_id"], dataset_id=dataset, status=status,
                                fetched_at=at, records=[rec] if rec else [],
                                artifacts=[AdapterArtifact(payload=payload, source_url=row["source_url"],
                                                           source_version=row["source_version"],
                                                           media_type=row["media_type"])])

        normal = pipeline.run(CachedAdapter(batch()), request)
        assert normal["accepted"] == 1, (dataset, normal)
        duplicate = pipeline.run(CachedAdapter(batch()), request)
        assert duplicate["unchanged"] == 1, duplicate
        failed = pipeline.run(CachedAdapter(error=ConnectionError("synthetic_transport_failure")), request)
        permission = pipeline.run(CachedAdapter(error=PermissionError("synthetic_access_failure")), request)
        stale = pipeline.run(CachedAdapter(batch(rec=None, status=IngestionStatus.STALE)), request)
        rejected = pipeline.run(CachedAdapter(batch(rec=record.model_copy(update={"unit": ""}))), request)
        assert failed["status"] == "unreachable" and permission["status"] == "unauthorized"
        assert stale["status"] == "stale" and rejected["quarantined"] == 1
        assert normal["results"][0]["candidate_id"] != rejected["results"][0]["candidate_id"]
        later = stamp + timedelta(hours=1)
        revision = record.model_copy(update={"value": float(record.value) + 1,
                                             "raw": {"synthetic_revision_probe": True}})
        revised = pipeline.run(CachedAdapter(batch(rec=revision, at=later,
                                                  payload=b"synthetic revision probe")), request)
        assert revised["accepted"] == 1, revised
        if dataset == "sp500_earnings_insight":
            # Real publication fence remains on. Replay one manifest's selected
            # observation subset in the disposable store; no full-report approval.
            quality = {"package_hash": record.dimensions["package_hash"],
                       "replay_subset_only": True}
            observations = repo.observations(dataset_id=dataset, accepted_only=False, latest_only=False)
            for index, at in enumerate((stamp, later)):
                visible = [item["observation_id"] for item in observations if point(item["known_at"]) <= at]
                repo.save_release_manifest(source_id=row["source_id"], dataset_id=dataset,
                    partition="replay", report_date=row["period"], document_id="cached-factset",
                    version_id=f"replay-{index}", artifact_id=observations[0]["artifact_id"],
                    known_at=at, extractor_version="isolated-replay", status="platform", passed=True,
                    quality=quality, observation_ids=visible)
        scope = {"entity": row["entity_id"], "metric": row["metric_id"], "dataset": dataset}
        old = products.metric_series(**scope, as_of=stamp)
        new = products.metric_series(**scope, as_of=later)
        assert old["rows"], (dataset, old)
        assert old["rows"][-1]["value"] == record.value
        assert new["rows"][-1]["value"] == revision.value
        role, product = (("fundamental", "COMPANY_DATA") if dataset in {"company_financials", "market_consensus"}
                         else ("macro", "MACRO_DATA"))
        packet = read_input(role, product, scope=scope, as_of=stamp, products=products)
        assert packet.status == "complete" and packet.payload["rows"] == old["rows"]
        hierarchy_roles = []
        if dataset == "industry_dram_contract_price":
            for hierarchy_role in ("layer", "sector", "fundamental"):
                hierarchy = read_input(hierarchy_role, "HIER_DATA", scope=scope, as_of=stamp, products=products)
                assert hierarchy.status == "complete" and hierarchy.payload["rows"] == old["rows"]
                hierarchy_roles.append(hierarchy_role)
        output.append({"dataset": dataset, "source": row["source_id"], "entity": row["entity_id"],
                       "metric": row["metric_id"], "as_of": stamp.isoformat(),
                       "cached_observation": row["observation_id"], "raw_hash": row["blob_hash"],
                       "normal": normal, "duplicate": duplicate, "transport": failed,
                       "permission": permission, "stale": stale, "quarantine": rejected,
                       "synthetic_revision": revised, "historical_equal": True,
                       "native_packet_equal": True, "hierarchy_roles_equal": hierarchy_roles})
    repo.close()
    readonly_root = root / "must-not-be-created"
    reader = SQLiteStructuredRepository(root / "structured.sqlite", artifact_root=readonly_root, readonly=True)
    try:
        assert not readonly_root.exists()
        try:
            reader.conn.execute("CREATE TABLE forbidden_reader_write(value TEXT)")
        except sqlite3.OperationalError:
            pass
        else:
            raise AssertionError("consumer database handle can write")
        try:
            reader.artifacts.put(b"forbidden reader artifact")
        except PermissionError:
            pass
        else:
            raise AssertionError("consumer artifact handle can write")
    finally:
        reader.close()
    with patch.dict(os.environ, {"ATS_DATA_DB_PATH": str(root / "structured.sqlite"),
                                "ATS_DATA_ARTIFACT_ROOT": str(readonly_root)}):
        sample = output[1]
        packet = read_input("macro", "MACRO_DATA", scope={"entity": sample["entity"],
            "dataset": sample["dataset"], "metric": sample["metric"]}, as_of=point(sample["as_of"]))
        assert packet.status == "complete" and not readonly_root.exists(), packet
    return output


def document_replay(cache, root):
    from ats.schemas.chain import Observation
    repo = PlatformUnstructuredRepository(root / "documents.sqlite", writable=True)
    now = datetime.now(timezone.utc)
    output = []
    sources = ("ai_hardware_knowledge_corpus", "defeatbeta_earnings_transcript",
               "sec_edgar_filing_body", "factset_earnings_insight_doc", "ibkr_news",
               "semianalysis", "trendforce_news")
    for source in sources:
        row = cache.execute(
            "SELECT d.*,v.local_path body_path,v.content_hash body_hash,v.version_id cached_version "
            "FROM data_documents d JOIN data_document_versions v ON v.document_id=d.document_id "
            "AND v.content_hash=d.sha256 WHERE d.source=? AND d.ok=1 "
            "ORDER BY d.fetched_at DESC LIMIT 1", (source,)).fetchone()
        assert row, f"cache_missing:{source}"
        row = dict(row)
        _, body = _split_frontmatter(Path(row["body_path"]).read_text(encoding="utf-8"))
        body = body.strip()
        assert hashlib.sha256(body.encode()).hexdigest() == row["body_hash"], f"corrupt_body:{source}"
        # Same canonical identity and cached body, fresh isolated publication clock.
        # Never backdate a new publication or pretend these are new provider bytes.
        path = root / (hashlib.sha256(source.encode()).hexdigest() + ".txt")
        path.write_text(body, encoding="utf-8")
        doc = CachedDoc(symbol=row["entity"], period=row["period"], doc_type=row["doc_type"],
                        text=body, path=path, source=source, source_url=row["source_url"],
                        fetched_at=now.isoformat(), sha256=row["body_hash"],
                        title=row["title"], published_at=row["published_at"],
                        completeness=row["completeness"], document_key=row["document_id"])
        # CachedDoc identity uses document_key when supplied; verify rather than assume.
        repo.save_document(doc)
        doc_id = doc.document_id
        repo.save_document(doc)
        assert len(repo.document_materials_at(as_of=now)) == len(output) + 1
        first = admitted_documents(repository=repo, entities=[row["entity"]], as_of=now)
        original = next(item for item in first if item.document_id == doc_id)
        assert original.text == body and original.content_hash == row["body_hash"]
        from ats.data.document_assets import read_document
        assert read_document(doc_id, store=repo) == original.text
        # Reuse a cited body span, not LLM-generated text. Profile direction is a
        # deliberately separate projection and must not appear in neutral facts.
        observation = Observation(document_id=doc_id, source_url=doc.source_url,
                                  entity=row["entity"], metric="cached_span_probe", period=row["period"],
                                  observation_type="reported_actual", stance="incumbent", direction="up",
                                  evidence_span=body[:200], observed_at=now)
        repo.save_evidence_observation(observation, projection_profile="layer", projection_version="replay-v1")
        neutral = DataProducts(unstructured_repository=repo).neutral_evidence(entity=row["entity"], as_of=now)
        fact = next(item for item in neutral["rows"] if item["document_id"] == doc_id)
        assert "direction" not in fact and "stance" not in fact
        later = now + timedelta(hours=1)
        revised_body = body + "\nSynthetic revision probe; not provider content."
        revised_path = root / (hashlib.sha256(source.encode()).hexdigest() + "-revision.txt")
        revised_path.write_text(revised_body, encoding="utf-8")
        revised = replace(doc, text=revised_body, path=revised_path,
                          sha256=hashlib.sha256(revised_body.encode()).hexdigest(),
                          fetched_at=later.isoformat(), title="Synthetic revised title")
        repo.save_document(revised)
        repo.save_evidence_observation(observation.model_copy(update={"observed_at": later, "value": 2}),
                                       projection_profile="layer", projection_version="replay-v1")
        historical_facts = repo.facts(entity=row["entity"], document_id=doc_id, as_of=now)
        current_facts = repo.facts(entity=row["entity"], document_id=doc_id, as_of=later)
        assert historical_facts[0]["document_version_id"] == original.version_id
        assert current_facts[0]["value"] == 2 and current_facts[0]["fact_id"] != fact["fact_id"]
        historical = next(item for item in admitted_documents(repository=repo, as_of=now)
                          if item.document_id == doc_id)
        latest = next(item for item in admitted_documents(repository=repo, as_of=later)
                      if item.document_id == doc_id)
        assert historical.text == body and historical.title == row["title"]
        assert historical.version_id != latest.version_id and latest.text == revised_body
        revised_path.write_text("corrupt body", encoding="utf-8")
        assert doc_id not in {item.document_id for item in admitted_documents(repository=repo, as_of=later)}
        assert doc_id in {item.document_id for item in admitted_documents(repository=repo, as_of=now)}
        packet = read_input("information", "DOC_DATA", scope={"entity": row["entity"]},
                            as_of=now, products=DataProducts(unstructured_repository=repo))
        assert any(item["version_id"] == historical.version_id for item in packet.payload)
        layer = read_input("layer", "DOC_DATA", scope={"entity": row["entity"]},
                           as_of=now, products=DataProducts(unstructured_repository=repo))
        assert layer.payload == packet.payload
        if source == "defeatbeta_earnings_transcript":
            package = earnings_document_package(repo, entity=row["entity"], period=row["period"], as_of=now)
            assert package.transcript and package.transcript.text == body
            assert not earnings_document_package(repo, entity=row["entity"], period="FY1900Q1", as_of=now).scoreable
        output.append({"source": source, "cached_document": row["document_id"],
                       "cached_version": row["cached_version"], "content_hash": row["body_hash"],
                       "published_id": doc_id, "normal": "passed", "duplicate": "passed",
                       "synthetic_revision_as_of": "passed", "corrupt_body_fail_closed": "passed",
                       "native_packet_equal": True, "neutral_fact_version_and_opinion_isolation": "passed"})
    repo.close()
    return output


def source_gate_replay(cache, root):
    """Each registered article gets its own actual policy/budget negative probes.

    Payload perturbations are diagnostics, not assertions of live entitlement.
    FactSet's PDF/derived-product gate is covered separately by its native suite.
    """
    from ats.data.pipelines.unstructured import article_ingest as articles
    from ats.data.pipelines.unstructured.source_acceptance import fallback_plan, load_policy
    from ats.schemas.chain import ArticleRef

    now = datetime.now(timezone.utc)
    results = []
    for source in articles._article_sources().values():
        policy = load_policy(source.id)
        if source.id == "factset_earnings_insight_doc":
            import yaml

            from ats.config import REPO_ROOT
            from ats.data.sources.factset_report_layout import load_layout_policy
            registry = yaml.safe_load((REPO_ROOT / "config/data/structured.yaml").read_text())
            registry["datasets"]["sp500_earnings_insight"]["extraction_policy"]["sector_aliases"]["GICS_15"].append("Energy")
            invalid = root / "invalid-factset-registry.json"
            invalid.write_text(yaml.safe_dump(registry), encoding="utf-8")
            try:
                load_layout_policy(invalid)
            except ValueError as exc:
                assert "alias_conflict" in str(exc)
            else:
                raise AssertionError("FactSet alias conflict was admitted")
            results.append({"source": source.id, "gate": "native_pdf_and_semantic_group_suite",
                            "complete_registry_alias_conflict": "rejected",
                            "html_budget": "not_applicable_pdf_transport"})
            continue
        row = cache.execute("SELECT title,source_url,published_at FROM data_documents "
                            "WHERE source=? AND ok=1 ORDER BY fetched_at DESC LIMIT 1", (source.id,)).fetchone()
        # Yahoo candidate is deliberately not a released document. It receives a
        # labelled boundary fixture; never claim it is cached accepted coverage.
        ref = ArticleRef(url=row["source_url"] if row else "https://example.invalid/yahoo-replay",
                         slug="cached-replay", title=row["title"] if row else "Synthetic NVDA fallback",
                         published_at=now.date())
        material = articles.Material(ref, "diagnostic body " * 400, {
            "native_id": "replay", "canonical_url": ref.url, "entity_association": "title_verified",
            "title_verified_entities": "NVDA", "completeness": "full"})

        def evaluate(m, *, approved=True, eligible=True):
            candidate = articles._candidate(source, m, m.body, discovered_at=now, policy=policy)
            return articles._admission(candidate, m, source=source, policy=policy,
                                       human_review_approved=approved, source_eligible=eligible)[0]

        assert evaluate(material).accepted
        short = evaluate(replace(material, body="subscription required"))
        failed = evaluate(replace(material, status="body_unavailable", reason="synthetic transport failure"))
        permission = evaluate(material, eligible=False)
        partial = evaluate(replace(material, provenance={**material.provenance, "completeness": "partial"}))
        pending = evaluate(material, approved=False)
        assert not short.accepted and not failed.accepted and not permission.accepted
        assert partial.accepted == bool(policy.get("policy", {}).get("allow_partial_bodies"))
        assert (pending.status == "pending_human_review") == bool(
            policy.get("policy", {}).get("release_requires_human_title_url_review"))
        # Exercise the transport budget against every adapter without importing or
        # querying it. Both refs match the real source's topic rule if present.
        budget_policy = {**policy, "request_budget": {"max_body_requests": 1},
                         "policy": {**policy.get("policy", {}), "lookback_days": 0}}
        title = " ".join(source.match) or ref.title
        refs = [ArticleRef(url="https://example.invalid/" + str(i), slug=str(i),
                           title=title, published_at=now.date()) for i in range(2)]
        adapter = SimpleNamespace(discover_with_status=lambda **_: (refs, {"status": "succeeded"}),
                                  fetch_body=lambda _: material.body)
        with patch.object(articles.importlib, "import_module", return_value=adapter):
            materials, _, _ = articles._materials_from_adapter(source, budget_policy, now=now)
        assert len(materials) == 2 and materials[-1].status == "budget_deferred", source.id
        results.append({"source": source.id, "normal_policy_gate": "passed",
                        "short_paywall": list(short.reason_codes), "transport_failure": list(failed.reason_codes),
                        "source_permission": list(permission.reason_codes), "partial": partial.status,
                        "human_review": pending.status, "budget": materials[-1].status,
                        "fixture_kind": "synthetic_gate_probe_with_real_source_policy"})
    ibkr = load_policy("ibkr_news")
    fallback = fallback_plan(source_id="ibkr_news", outcome="unreachable",
                             discovery={"status": "unreachable"}, policy=ibkr)
    assert fallback["activate"] and fallback["source_id"] == "yfinance_live_news"
    return {"sources": results, "ibkr_yahoo_fallback": fallback}


def guard_negative_replay(root):
    from ats.workflow.architecture_guards import scan_module
    cases = {
        "provider_relative": "from ...data.sources import factset\n",
        "provider_http": "import requests\n",
        "provider_dynamic": "from importlib import import_module\np = import_module('ats.data.sources.factset')\n",
        "table": "def f(conn):\n    return conn.execute('SELECT * FROM data_documents')\n",
        "memory": "def f(memory):\n    return memory.documents()\n",
        "memory_alias": "def f(memory):\n    read = memory.documents\n    return read()\n",
        "projection": "def f(store):\n    return store.task_projection_envelopes(agent_role='macro_review')\n",
        "projection_alias": "def f(store):\n    read = getattr(store, 'task_projection_envelopes')\n    return read(agent_role='macro_review')\n",
        "dynamic": "def f(store, role):\n    return store.task_projection_envelopes(agent_role=role)\n",
        "fact_write": "def f(store):\n    return store.save_evidence_observation({})\n",
    }
    output = []
    for name, code in cases.items():
        path = root / "src/ats/agents/sector" / (name + ".py")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(code, encoding="utf-8")
        violations = scan_module(path, root=root)
        assert violations, f"negative_guard_missed:{name}"
        output.append({"case": name, "violations": [item.kind for item in violations]})
    return output


def fixed_source_replay(cache, root):
    """Pinned raw row replay through the actual SEC/transcript normalizers."""
    from ats.data.pipelines.unstructured import document_ingest as fixed

    now = datetime.now(timezone.utc)
    output = []
    for source_id in ("defeatbeta_sec_filing_index", "defeatbeta_earnings_transcript"):
        cached = cache.execute("SELECT * FROM data_fixed_source_rows WHERE source_id=? AND status='accepted' "
                               "ORDER BY observed_at DESC LIMIT 1", (source_id,)).fetchone()
        assert cached, f"cache_missing:{source_id}"
        row = json.loads(cached["raw_json"])
        snapshot = dict(cache.execute("SELECT * FROM data_fixed_source_snapshots WHERE snapshot_id=?",
                                      (cached["snapshot_id"],)).fetchone())
        for key in ("snapshot_id", "source_id", "run_id", "checked_at"):
            snapshot.pop(key, None)
        repo = PlatformUnstructuredRepository(root / (source_id + ".sqlite"), writable=True)
        with patch.dict(os.environ, {"ATS_DOCS_ROOT": str(root / (source_id + "-docs"))}), \
                patch.object(fixed, "_hf_snapshot", return_value=snapshot), \
                patch.object(fixed, "_query_hf", return_value=[row]):
            normal = fixed.ingest_documents(source_id, entity=cached["entity"], now=now, store=repo)
            repeat = fixed.ingest_documents(source_id, entity=cached["entity"], now=now, store=repo)
            assert normal["accepted"] == 1, normal
            assert repeat["no_change"] == 1, repeat
            if source_id == "defeatbeta_earnings_transcript":
                malformed = {**row, "transcripts": []}
            else:
                malformed = {**row, "filing_url": "https://example.invalid/not-sec"}
            with patch.object(fixed, "_query_hf", return_value=[malformed]):
                refused = fixed.ingest_documents(source_id, entity=cached["entity"], now=now, store=repo)
                assert refused["quarantined"] == 1 and not refused["document_ids"], refused
            with patch.object(fixed, "_query_hf", return_value=[]):
                partial = fixed.ingest_documents(source_id, entity=cached["entity"], now=now, store=repo)
                assert partial["status"] == "partial" and partial["coverage_missing_entities"]
            for error in (PermissionError("synthetic access denial"), ConnectionError("synthetic network outage")):
                with patch.object(fixed, "_hf_snapshot", side_effect=error):
                    try:
                        fixed.ingest_documents(source_id, entity=cached["entity"], now=now, store=repo)
                    except type(error):
                        pass
                    else:
                        raise AssertionError("provider error was silently accepted")
            policy = fixed._rows()[source_id]
            count = policy["request_budget"]["max_rows_per_run"] // policy["request_budget"]["max_rows_per_symbol"] + 1
            with patch.object(fixed, "_universe", return_value=[f"REPLAY{i}" for i in range(count)]), \
                    patch.object(fixed, "_hf_snapshot", side_effect=AssertionError("budget queried provider")):
                try:
                    fixed.ingest_documents(source_id, now=now, store=repo)
                except ValueError as exc:
                    assert str(exc) == "fixed_universe_exceeds_row_budget"
                else:
                    raise AssertionError("fixed source budget not enforced")
        output.append({"source": source_id, "cached_row": cached["row_id"],
                       "snapshot": cached["snapshot_id"], "upstream_revision": snapshot["revision"],
                       "normal": normal, "duplicate": repeat, "bad_material": refused,
                       "partial": partial, "permission_transport_failures": "passed", "budget": "passed",
                       "human_review": "not_applicable_fixed_source_automatic_gate"})
        repo.close()
    return output


def fixed_publication_gate_replay(cache, root):
    from ats.data.admission import CandidateDocument, ValidationIssue
    from ats.data.pipelines.unstructured.document_ingest import _publish_document

    output = []
    for source in ("ai_hardware_knowledge_corpus", "sec_edgar_filing_body", "defeatbeta_earnings_transcript"):
        row = dict(cache.execute("SELECT d.*,v.local_path body_path FROM data_documents d "
                                "JOIN data_document_versions v ON v.document_id=d.document_id AND v.content_hash=d.sha256 "
                                "WHERE d.source=? AND d.ok=1 ORDER BY d.fetched_at DESC LIMIT 1", (source,)).fetchone())
        _, text = _split_frontmatter(Path(row["body_path"]).read_text(encoding="utf-8"))
        candidate = CandidateDocument(expected_entity=row["entity"], claimed_entity=row["entity"],
            target_period=row["period"], claimed_period=row["period"],
            expected_semantic=row["doc_type"], claimed_semantic=row["doc_type"],
            text=text.strip(), source=source, source_url=row["source_url"],
            title=row["title"], external_id="replay:" + row["external_id"], min_chars=100)
        repo = PlatformUnstructuredRepository(root / (source + "-gate.sqlite"), writable=True)
        with patch.dict(os.environ, {"ATS_DOCS_ROOT": str(root / (source + "-gate-docs"))}):
            normal, doc_id = _publish_document(repo, candidate)
            assert normal == "accepted", (source, normal)
            duplicate, repeated_id = _publish_document(repo, candidate)
            assert duplicate == "no_change" and repeated_id == doc_id
            for name, bad in (("short", replace(candidate, text="paywall")),
                              ("partial", replace(candidate, completeness="partial"))):
                result, bad_id = _publish_document(repo, bad)
                assert result == "quarantined" and not bad_id, (source, name)
            denied, bad_id = _publish_document(repo, candidate, source_issues=[
                ValidationIssue("source", "synthetic_permission_or_transport_denial")])
            assert denied == "quarantined" and not bad_id
            assert repo.latest_document_version(doc_id)
        output.append({"source": source, "normal": normal, "duplicate": duplicate,
                       "short_paywall": "quarantined", "partial": "quarantined",
                       "source_failure": denied, "human_review": "not_applicable_automatic_fixed_gate",
                       "preserve_last_good_version": True})
        repo.close()
    return output


def legacy_fact_classification(cache):
    """Classify missing historical lineage without editing/backfilling old rows."""
    rows = cache.execute(
        "SELECT CASE WHEN source_url IN ('tw_mof','kr_ecos','trendforce') "
        "THEN 'legacy_numeric_without_vintage_link' "
        "WHEN source_url LIKE 'tavily:%' THEN 'legacy_search_document_without_version' "
        "ELSE 'legacy_document_without_version' END category,COUNT(*) n "
        "FROM data_evidence_facts WHERE COALESCE(document_version_id,'')='' GROUP BY category").fetchall()
    return {"action": "record_only_no_backfill", "owner": "Data Platform legacy evidence migration",
            "groups": [dict(row) for row in rows],
            "target_disposition": "not_returned_as_verified_neutral_document_facts",
            "impact": "explicit_coverage_gap_not_a_new_path_publication_failure"}


def calendar_replay(calendar_root, root):
    from ats.data.calendar_refresh import refresh_schedule_calendar
    from ats.data.products.calendar import ScheduleCalendarProduct
    from ats.data.stores.schedule_calendar import ScheduleCalendarStore, ScheduleEventCandidate
    from ats.data.stores.structured.artifacts import ArtifactStore

    source_cache = sqlite3.connect(f"file:{calendar_root / 'data.sqlite'}?mode=ro", uri=True)
    source_cache.row_factory = sqlite3.Row
    output = []
    for source_id in ("federal_reserve_fomc", "bls_release_calendar", "bea_release_schedule"):
        run = source_cache.execute("SELECT * FROM schedule_calendar_source_runs WHERE source_id=? "
                                   "AND status='complete' ORDER BY finished_at DESC LIMIT 1", (source_id,)).fetchone()
        assert run, f"calendar_raw_cache_missing:{source_id}"
        provenance = json.loads(run["provenance_json"])
        descriptor = provenance["raw_artifact"]
        raw = (calendar_root / "artifacts" / descriptor["relative_path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == descriptor["content_hash"]
        now = point(run["started_at"])
        store = ScheduleCalendarStore(root / (source_id + ".sqlite"))
        artifacts = ArtifactStore(root / (source_id + "-artifacts"))
        with patch("ats.data.calendar_refresh._fetch_text", return_value=raw.decode("utf-8")):
            first = refresh_schedule_calendar(source_ids=[source_id], now=now, store=store, artifact_store=artifacts)
            duplicate = refresh_schedule_calendar(source_ids=[source_id], now=now + timedelta(minutes=1), store=store, artifact_store=artifacts)
        assert first["sources"][0]["status"] == "complete", first
        assert len(store.events()) == first["sources"][0]["published"]
        with patch("ats.data.calendar_refresh._fetch_text", side_effect=ConnectionError("synthetic transport failure")):
            failed = refresh_schedule_calendar(source_ids=[source_id], now=now + timedelta(minutes=2), store=store, artifact_store=artifacts)
        assert failed["sources"][0]["status"] == "failed"
        product = ScheduleCalendarProduct(store)
        assert product.snapshot(as_of=now)["quality"]["status"] == "ok"
        assert product.snapshot(as_of=now + timedelta(minutes=2))["quality"]["status"] == "degraded"
        assert product.snapshot(as_of=now + timedelta(days=30))["quality"]["status"] == "stale"
        event = store.events(as_of=now)[0]
        allowed = set(ScheduleEventCandidate.model_fields)
        payload = {k: v for k, v in event["payload"].items() if k in allowed}
        payload.pop("utc_at", None)
        payload.update(source_id=source_id, source_url=provenance["url"],
                       fetched_at=(now + timedelta(minutes=3)).isoformat(),
                       event_date=(point(event["event_date"]) + timedelta(days=1)).date())
        changed = store.submit_candidate(ScheduleEventCandidate(**payload), at=now + timedelta(minutes=3))
        store.publish_candidate(changed["candidate_id"], at=now + timedelta(minutes=3))
        original = next(e for e in store.events(as_of=now) if e["event_id"] == event["event_id"])
        revised = next(e for e in store.events(as_of=now + timedelta(minutes=3)) if e["event_id"] == event["event_id"])
        assert original["event_date"] != revised["event_date"]
        payload.update(source_id="synthetic-calendar-mirror", event_date=(
            point(revised["event_date"]) + timedelta(days=1)).date(),
            fetched_at=(now + timedelta(minutes=4)).isoformat())
        conflict = store.submit_candidate(ScheduleEventCandidate(**payload), at=now + timedelta(minutes=4))
        assert conflict["review_status"] == "conflict"
        override_at = now + timedelta(minutes=5)
        store.override_event(event["event_id"], patch={"event_date": revised["event_date"]},
                             actor="isolated-replay", reason="synthetic adjudication",
                             evidence={"scope": "non-production"}, at=override_at)
        assert any(e["quality_status"] == "conflict" for e in store.events(as_of=now + timedelta(minutes=4)))
        assert all(e["quality_status"] != "conflict" for e in store.events(as_of=override_at))
        unknown = store.submit_candidate(ScheduleEventCandidate(source_id=source_id, event_type="earnings",
                                                                 event_date="2026-10-20", time_precision="date"))
        assert unknown["review_status"] == "pending_identity"
        output.append({"source": source_id, "raw_hash": descriptor["content_hash"],
                       "cached_run": run["source_run_id"], "normal": first["sources"][0],
                       "duplicate": duplicate["sources"][0], "failure": failed["sources"][0],
                       "stale": "passed", "rejected_identity": "passed", "revision_as_of": "passed",
                       "historical_conflict_after_override": "passed"})
    source_cache.close()
    return output


def edge_replay(root):
    from ats.data.runtime.broker import portfolio_snapshot
    from ats.data.runtime.market_data import RuntimeCloseHistory
    from ats.decision.repository import DecisionAuditRepository
    from ats.execution.state_api import get_internal_state
    from ats.memory.store import TradingMemory
    from ats.workflow.architecture_guards import scan_agents

    now = datetime.now(timezone.utc)
    store = TradingMemory(root / "memory.sqlite")
    audit = DecisionAuditRepository(store)
    audit.create_cycle(cycle_id="replay", trigger_source="manual")
    revision = audit.append_revision(cycle_id="replay", orders=[{"symbol": "NVDA", "action": "buy", "notional_usd": 1000}], rationale="isolated replay; no order submission")
    audit.record_review(review_id="review", cycle_id="replay", revision_no=revision["revision_no"],
                        decision_hash=revision["decision_hash"], ruleset_version="replay-v1",
                        portfolio_snapshot_id="replay-snapshot", market_as_of=now.isoformat(), verdict="approved")
    audit.record_approval(approval_id="approval", cycle_id="replay", revision_no=revision["revision_no"],
                          decision_hash=revision["decision_hash"], decision="approved", reviewer="isolated-test",
                          idempotency_key="replay")
    audit.transition("replay", to_status="pending_approval", actor="risk_gate", revision_no=revision["revision_no"])
    broker = SimpleNamespace(get_portfolio=lambda: {"as_of": now.isoformat(), "net_liquidation": 1000},
                             completed_orders=lambda: [], get_fills=lambda: [])
    result = []
    for role, products in EXPECTED_PRODUCTS.items():
        for product in products:
            contract = input_contract(role, product)
            assert contract["schema_version"] == "target-consumer-input-v1"
            result.append({"consumer": role, "product": product, "owner": contract["owner"],
                           "input_mode": contract["input_mode"], "schema_version": contract["schema_version"],
                           "fallback": contract["fallback"], "legacy": contract["legacy"]})
    for role in ("chief", "risk"):
        packet = read_input(role, "PORTFOLIO_DATA", scope={}, store=store)
        assert packet.payload == get_internal_state(store).portfolio and packet.status != "complete"
    history = read_input("chief", "HISTORY_DATA", scope={}, store=store)
    assert history.status == "partial" and history.gaps
    old_history = get_internal_state(store)
    assert history.payload["trades"] == old_history.trades
    assert history.payload["fills"] == old_history.fills
    assert history.payload["performance"] == old_history.performance
    market = RuntimeCloseHistory("NVDA", (100, 101), now.date(), now, "succeeded")
    with patch("ats.data.runtime.market_data.fetch_close_history_many", return_value={"NVDA": market}):
        for role in ("technical", "risk"):
            packet = read_input(role, "MARKET_DATA", scope={"entity": "NVDA"})
            assert packet.status == "complete" and packet.payload["NVDA"]["closes"] == [100, 101]
    with patch("ats.data.runtime.options.fetch_runtime", return_value={"payload": None, "status": "unavailable", "source_as_of": None, "reason": "synthetic provider failure"}):
        assert read_input("technical", "MARKET_DATA", scope={"entity": "NVDA", "kind": "options"}).status == "unavailable"
    assert read_input("risk", "RISK_RULES", scope={}, risk_config={"max": 10}).status == "complete"
    clerk = read_input("clerk", "BROKER_STATE", scope={}, broker=broker)
    assert clerk.status == "complete" and clerk.payload["payload"] == portfolio_snapshot(broker)["payload"]
    broker.get_fills = lambda: None
    assert read_input("clerk", "BROKER_STATE", scope={}, broker=broker).status == "partial"
    assert read_input("clerk", "DECISION_APPROVAL_CONTEXT", scope={"cycle_id": "replay"}, audit=audit).status == "complete"
    auth = read_input("trader", "APPROVED_EXECUTION_AUTHORIZATION", scope={"cycle_id": "replay", "snapshot_as_of": now}, audit=audit)
    assert auth.status == "complete", auth
    from ats.execution.authorization import build_authorization
    assert auth.payload == build_authorization(audit, "replay").model_dump(mode="json")
    audit.append_revision(cycle_id="replay", orders=[], rationale="supersede approval")
    assert read_input("trader", "APPROVED_EXECUTION_AUTHORIZATION", scope={"cycle_id": "replay"}, audit=audit).status != "complete"
    for role, product in (("sector", "DOC_DATA"), ("macro", "COMPANY_DATA"), ("risk", "DOC_DATA")):
        try:
            input_contract(role, product)
        except PermissionError:
            pass
        else:
            raise AssertionError("undeclared edge accepted")
    for payload in ({"agent_role": "macro"}, {"input_mode": "runtime"}, {"nested": {"decision_hash": "x"}}):
        try:
            assert_research_payload(payload)
        except ValueError:
            pass
        else:
            raise AssertionError("non-research material accepted")
    assert not scan_agents(), scan_agents()
    assert validate_target_contract()["valid"], validate_target_contract()
    store.close()
    return {"edges": result, "runtime_internal_failures": "passed", "authorization_revision": "passed",
            "undeclared_edge_rejection": "passed", "research_boundary": "passed", "architecture_scan": "passed"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path("var/data.sqlite"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--calendar-cache", type=Path,
                        help="Existing isolated live calendar root containing data.sqlite and artifacts")
    args = parser.parse_args()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # A failed rerun must never leave yesterday's success as its output.
        args.output.write_text(json.dumps({"schema_version": "target-dataflow-replay-v1",
                                          "passed": False, "status": "running_or_incomplete",
                                          "started_at": datetime.now(timezone.utc).isoformat()}) + "\n",
                               encoding="utf-8")
    root = Path(tempfile.mkdtemp(prefix="ats-task3-replay-"))
    cache = sqlite3.connect(f"file:{args.cache.resolve()}?mode=ro", uri=True)
    cache.row_factory = sqlite3.Row
    cache.execute("BEGIN")
    calendar_root = args.calendar_cache
    if calendar_root is None:
        roots = sorted(Path(tempfile.gettempdir()).glob("ats-target-calendar-*"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        calendar_root = next((p for p in roots if (p / "data.sqlite").is_file()), None)
    if calendar_root is None:
        raise ValueError("missing_calendar_raw_cache; supply --calendar-cache; no implicit network fallback")
    with patch.object(socket.socket, "connect", no_network):
        report = {"schema_version": "target-dataflow-replay-v1", "passed": True,
                  "network_calls": 0, "isolated_root": str(root), "cache": str(args.cache.resolve()),
                  "structured": structured_replay(cache, root),
                  "documents": document_replay(cache, root), "edges": edge_replay(root),
                  "source_gates": source_gate_replay(cache, root), "negative_guards": guard_negative_replay(root)}
        report["calendars"] = calendar_replay(calendar_root, root)
        report["fixed_sources"] = fixed_source_replay(cache, root)
        report["fixed_publication_gates"] = fixed_publication_gate_replay(cache, root)
        report["legacy_fact_classification"] = legacy_fact_classification(cache)
    cache.close()
    encoded = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "network_calls": 0,
                      "structured_domains": len(report["structured"]), "document_sources": len(report["documents"]),
                      "calendar_sources": len(report["calendars"]), "fixed_raw_sources": len(report["fixed_sources"]),
                      "consumer_edges": len(report["edges"]["edges"]),
                      "negative_guards": len(report["negative_guards"]), "isolated_root": str(root),
                      "report": str(args.output) if args.output else "stdout_only"}, ensure_ascii=False, indent=2))
    if not args.output:
        print(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
