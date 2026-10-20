"""Read-only Phase F stage-one inventory, failure attribution and final preflight.

Preparation never grants qualification, signs reports or changes production.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sqlite3
import tempfile
import math
from contextlib import closing
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import yaml

from ats.config import REPO_ROOT
from ats.agent.task_projection import ProjectionScope
from ats.workflow.exit_requirements import required_plan_uses
from ats.workflow.phase_e import build_plan
from ats.workflow.run_contracts import TriggerContext
from ats.workflow.runtime_reads import task_identity


def file_ref(path):
    path = Path(path)
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def inventory(as_of, *, config_dir=None):
    point = datetime.fromisoformat(as_of)
    if point.tzinfo is None:
        raise ValueError("inventory as_of requires timezone")
    root = Path(config_dir or REPO_ROOT / "config")
    load = lambda name: yaml.safe_load((root / name).read_text())
    owners = load("workflow/workflow_owners.yaml")["workflows"]
    schedules = load("workflow/research_schedules.yaml")["workflows"]
    profiles = load("workflow/decision_profiles.yaml")["profiles"]
    coverage = load("data/target_dataflow_coverage.yaml")
    contracts = {c["id"]: c for c in coverage["consumers"]}
    groups = []
    def add(name, requested, scope, *, profile="", mode=""):
        plan = build_plan(requested_tasks=requested, scope=ProjectionScope(**scope),
            trigger=TriggerContext(kind="manual", workflow_id=name, trigger_id="inventory"),
            as_of=as_of, run_id="inventory:" + name, config_dir=root,
            profile_id=profile, enter_decision_cycle=bool(profile))
        rows = required_plan_uses(plan)
        for row, task in zip(rows, plan.tasks):
            contract = contracts[row["consumer"]]
            row.update(read_identity=task_identity(plan, task).as_row(),
                product_allowlist=contract["products"], required_evidence=contract["required_evidence"],
                evidence_ttl_days=contract["evidence_ttl_days"],
                required_inputs=coverage["qualification_policy"].get("required_inputs", {}).get(row["consumer"], []),
                report_status="not_bound", qualification_status="not_queried")
        groups.append({"workflow": name, "request_scope": scope, "profile": profile,
            "selected_fundamental_mode": mode, "required_uses": rows,
            "decision_chain_required": bool(profile), "execution_claim": False})
    for name, owner in owners.items():
        scope = schedules.get(name, {}).get("scope") or (
            {"kind": "sector", "id": "ai_hardware"} if name in {"layer-review", "sector-review"}
            else {"kind": "portfolio", "id": "pead-targets"})
        add(name, owner["task_ids"], scope)
    for name, profile in profiles.items():
        for mode in profile["fundamental_modes"]:
            add("profile:" + name + ":" + mode,
                ["layer-review", "information-brief", "sector-review", "fundamental-" + mode,
                 "macro-review", "technical-review"], profile["scope"], profile=name, mode=mode)
    downstream = []
    conditions = {"chief": "selected complete decision workflow; all required analysis successful",
        "risk": "actionable immutable decision revision; No Action bypasses risk",
        "trader": "Risk passed and Boss approved exact revision; governed quote required; real writes separately authorized",
        "clerk": "selected broker reconciliation/history/rebuild; includes manual and unattributed orders"}
    for role, condition in conditions.items():
        c = contracts[role]
        downstream.append({"consumer": role, "condition": condition, "domain": c["domain"],
            "contract_version": c["contract_version"], "product_allowlist": c["products"],
            "required_evidence": c["required_evidence"], "evidence_ttl_days": c["evidence_ttl_days"],
            "actual_invocation_scope": "bind from actual selected decision/approval/broker records",
            "scope_status": "template_not_execution", "required_by": "TARGET 4/10/11/15.2/15.4"})
    return {"kind": "phase-f-stage-one-inventory-v1", "status": "prepared_not_verified",
        "as_of": as_of, "authority": file_ref(REPO_ROOT / "docs/ARCHITECTURE.md"),
        "sources": [file_ref(root / n) for n in ("workflow/workflow_owners.yaml",
            "workflow/research_schedules.yaml", "workflow/decision_profiles.yaml",
            "workflow/event_routes.yaml", "data/target_dataflow_coverage.yaml")],
        "groups": groups, "downstream_uses": downstream,
        "event_rules": load("workflow/event_routes.yaml")["events"],
        "optional_input_policy": coverage["qualification_policy"]["optional_inputs"],
        "task_scope_occurrences": sum(len(g["required_uses"]) for g in groups),
        "criterion": "each selected group requires its own dependency closure; groups and Fundamental alternatives are inventory, not simultaneous admission prerequisites",
        "production_claim": False}


# These describe observed causes, not exemptions or assertions of successful repair.
CAUSES = {
 "batch_cli": ("retired product-list/fallback-label fixture omits exact request and authorization; obsolete verdict/JSON expectations", "14.6/15.5", "read/schedule control", "tests/test_phase_f_batch_executor.py"),
 "batch_manifest": ("legacy fixture supplies summary report and fallback labels rather than current exact deployment metadata", "14.6/15.5", "read/schedule control", "tests/test_phase_f_batch_executor.py"),
 "read_cutover": ("old product-list request/global-pairing fixture cannot enter current exact workflow/scope coordinator", "14.6/15.5", "read/schedule control", "tests/test_phase_f_read_executor.py"),
 "chief_snapshot": ("fixture lacks current frozen selected dependencies; either-mode and dual-read assertions also predate user ruling", "8.2/10/15.2/15.3", "Chief and dependent trading", "tests/test_phase_f_snapshot_dependencies.py"),
 "chief": ("legacy CLI fixture lacks complete current qualified frozen research inputs", "8.2/10/15.2", "Chief", "tests/test_phase_f_research_gate.py"),
 "chief_loop": ("literal place_orders scan counts isolated simulation and actual broker-refusal probes as live writers", "10/15.5", "single real submission authority", "tests/test_phase_f_broker_enforcement.py"),
 "cutover_cli": ("fixture bootstraps now-incompatible global boundary combinations before the tested scoped operation", "14.6/15.5", "shared startup/control safety", "tests/test_phase_f_startup_admission.py"),
 "anthropic_consumer_boundaries": ("fingerprint paths and retired-consumer dispositions mistaken for active trading configuration", "3/4/12", "Layer/data boundary", "inspect semantic configuration; retain active-route negative guard"),
 "chain_regional_consumer": ("legacy Chain refresh fixture reaches acquisition without current managed-queue lease", "3/4", "governed regional inputs", "verify new Layer product reads; never disable lease guard"),
 "fact_projections": ("same factual hash remains superseded after identical re-extraction; current fact/history semantics need resolution", "3/12/15.4", "neutral facts and history", "tests/test_fact_projections.py"),
 "factset_values_local": ("partial alias-conflict fixture omits current core_metrics before policy validation", "3/12", "shared admission policy", "complete policy fixture; retain alias-conflict refusal"),
 "managed_cache_miss_local": ("SimpleNamespace broker result is not a valid schema payload; runtime returns unavailable", "4/10", "Risk/Trader broker input", "typed broker fixture plus malformed payload refusal"),
 "workflow_data_cutover": ("literal retired research_pipeline import expected instead of current governed CLI entry", "3/8/13", "scheduler/data producer boundary", "actual guarded scheduler/CLI entry proof"),
 "factset_core_guidance_count_local": ("local original PDF/image assets missing; cannot certify printed-value extraction", "3/12", "FactSet governed facts", "supply original artifacts or keep missing evidence explicitly pending"),
 "factset_core_surprise_local": ("local original PDF/image assets missing; cannot certify printed-value extraction", "3/12", "FactSet governed facts", "supply original artifacts or keep missing evidence explicitly pending"),
 "factset_earnings_insight": ("catalog/freshness/lineage and failed-refresh fixtures predate current source contract; shared behavior needs per-case recheck", "3/4/12", "Macro/Sector shared FactSet inputs", "tests/test_factset_earnings_insight.py"),
 "factset_consumer_semantic_local": ("current source-policy test needs final real report and historical release references", "3/12", "shared FactSet admission/history", "tests/test_factset_consumer_semantic_local.py"),
 "evidence_work_adoption": ("standalone evidence_observer expectation survives role retirement into Layer", "4/12", "Layer and preserved source lineage", "validate replacement Layer contract, not retired role restoration"),
 "phase_d_e2e": ("fixture lacks current physical isolation, qualification and frozen requirements for new full funnel", "10/15.2", "complete decision/trade chain", "tests/test_phase_f_independent_runner.py"),
 "phase_e_dispatcher": ("fake complete projection fixture lacks current frozen qualified selected closure", "8.2/15.3", "Dispatcher to Chief", "tests/test_phase_f_research_gate.py"),
 "phase_f_independent_runner": ("discovery overlapped protected source edits; capture/replay fingerprints drifted", "14.6/15.5", "full new/replay proof", "rerun independent runner after final closure freeze"),
 "phase_f_snapshot_dependencies": ("old callback fixture escaped physical isolation through a gateway alias; corrected stage requires final recheck", "10/15.2/15.5", "approval and Trader shared safety", "tests/test_phase_f_snapshot_dependencies.py"),
 "phase_f_acceptance_reports": ("historical test expected ready without the exact formal deployment protocol; revised fixture needs current recheck", "14.6/15.5", "report applicability and activation", "tests/test_phase_f_acceptance_reports.py"),
 "research": ("retired article-processing fixture does not satisfy current managed producer/admitted-document path", "3/4", "Information document lineage", "verify new admitted document pipeline and producer fencing"),
 "sector_allocation": ("old input_refs expected instead of current governed product references", "4/7", "Sector output lineage", "tests/test_sector_allocation.py"),
 "sector_viz": ("synthetic test_sector lacks configured entities; strict scope resolver refuses before view rendering", "7/8.1", "Sector scope and historical read", "configured sector fixture with actual projection refs"),
 "structured_docs_consistency": ("operations matrix drifted from current catalog source/dataset status; focused recheck now passes", "3/12", "catalog and operations consistency", "tests/test_structured_docs_consistency.py"),
}


def failures(junit, previous):
    root = ET.parse(junit).getroot()
    old = {r["test"]: r for r in json.loads(Path(previous).read_text())["failures"]}
    rows = []
    counts = Counter()
    for case in root.iter("testcase"):
        tags = [tag for tag in ("failure", "error", "skipped") if case.find(tag) is not None]
        kind = tags[0] if tags else "passed"
        counts[kind] += 1
        if kind not in {"failure", "error"}:
            continue
        test = case.attrib["classname"].replace(".", "/") + ".py::" + case.attrib["name"]
        prior = old.get(test, {})
        module = Path(test.split("::")[0]).stem.removeprefix("test_")
        cause = CAUSES.get(module)
        established = cause is not None or prior.get("classification", "").startswith("resolved")
        if cause is None:
            cause = (prior.get("classification", "cause not established; source/trace review required"),
                     "3/4/7/8/10/11/12/15", "shared contract or runtime guard", "recheck this actual case after 5.16")
        path = REPO_ROOT / test.split("::")[0]
        function = case.attrib["name"].split("[")[0]
        tree = ast.parse(path.read_text()) if path.exists() else None
        node = next((n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == function), None) if tree else None
        rows.append({"test": test, "original_kind": kind,
            "original_message": case.find(kind).attrib.get("message", ""),
            "previous_case_matched": bool(prior), "historical_stage_classification": prior.get("classification"),
            "cause": cause[0], "required_by": "TARGET " + cause[1], "affected_new_scope": cause[2],
            "new_recheck": cause[3], "source": {**file_ref(path), "line": node.lineno if node else None} if path.exists() else None,
            "new_path_reachability": "not excluded; required/shared impact must be tested",
            "disposition": "attributed_requires_final_recheck" if established else "unattributed",
            "scope_exclusion_proven": False, "gate_pass_claim": False})
    missing = sorted(set(old) - {r["test"] for r in rows})
    if missing:
        raise ValueError("original attribution cases missing from discovery JUnit: " + str(missing))
    return {"kind": "phase-f-stage-one-attribution-v1", "status": "prepared_not_final_gate",
        "discovery": file_ref(junit), "previous_attribution": file_ref(previous),
        "authority": file_ref(REPO_ROOT / "docs/ARCHITECTURE.md"),
        "discovery_counts": dict(counts), "failures": rows,
        "excluded_count": 0, "final_gate_requires": ["5.16", "final protected closure", "current required/shared safety rechecks"],
        "production_claim": False}


def preflight(scopes, freeze, bindings, *, source_ledger):
    from ats.workflow.scoped_intake import check_freeze, qualification_report
    from ats.workflow.scoped_routes import RouteIdentity
    from ats.workflow.shadow_reports import check_batch_report
    check_freeze(freeze)
    result = qualification_report(scopes, db_path=source_ledger)
    required = {RouteIdentity(**row).key for row in scopes}
    if set(bindings) - required:
        raise ValueError("report bindings escape selected exact scopes")
    for row in result["scopes"]:
        who = RouteIdentity(**row["identity"])
        binding = bindings.get(who.key)
        if not binding:
            row["report"] = {"passed": False, "problems": ["missing report binding"]}
            continue
        batch = SimpleNamespace(shadow_report_id=binding["report_id"], scope=who.scope,
            batch_class=binding["batch_class"], consumers=(who.consumer_id,),
            required_surfaces=tuple(binding["required_surfaces"]))
        ok, problems = check_batch_report(batch, path=binding.get("report_db"))
        row["report"] = {"passed": ok, "problems": problems}
    check_freeze(freeze)
    result.update(status="passed" if all(r["qualification"]["status"] == "eligible" and r["report"]["passed"]
        for r in result["scopes"]) else "pending", production_claim=False,
        permission_checked=False, criterion="current exact-scope qualification and applicable signed reports only; deployment executor still checks permission and all other gates")
    return result


def cache_audit(inventory_path, data_cache, internal_cache):
    """Reopen candidates and immutable history without creating or modifying stores.

    A product allowlist is not a required-read list. This diagnostic deliberately
    does not confer per-use coverage, qualification or deployment permission.
    """
    from ats.data.products.base import DataProducts
    from ats.data.products.unstructured import admitted_documents, _read_version_text
    from ats.data.stores.structured.repository import SQLiteStructuredRepository
    from ats.data.stores.unstructured.platform import PlatformUnstructuredRepository

    for path in (inventory_path, data_cache, internal_cache):
        if not Path(path).is_file():
            raise ValueError("existing inventory and read-only stores required: " + str(path))
    manifest = json.loads(Path(inventory_path).read_text())
    point = datetime.now(UTC)
    entities = sorted({entity for group in manifest['groups']
        for use in group['required_uses'] for entity in use['read_identity']['scope']['entities']})
    structured = SQLiteStructuredRepository(data_cache, readonly=True,
        artifact_root=Path(data_cache).parent / 'data_artifacts')
    unstructured = PlatformUnstructuredRepository(data_cache, writable=False)
    internal = sqlite3.connect(Path(internal_cache).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        for conn in (structured.conn, unstructured.conn, internal):
            conn.execute('BEGIN')
        products = DataProducts(structured_repository=structured, unstructured_repository=unstructured)

        def summarize(action):
            try:
                result = action()
                rows = result['rows']
                return {'rows': len(rows), 'content_sha256': hashlib.sha256(
                    json.dumps(result, sort_keys=True, default=str).encode()).hexdigest(),
                    'refs': [row.get('observation_id') or row.get('fact_id') for row in rows],
                    'rejected': result.get('rejected', []),
                    'status': result.get('status', 'nonempty' if rows else 'no_coverage')}
            except (KeyError, TypeError, ValueError, OSError, sqlite3.Error) as exc:
                return {'rows': 0, 'status': 'unavailable', 'reason': str(exc), 'refs': []}

        candidates = {}
        selected = set()
        for entity in entities:
            diagnostics = []
            documents = admitted_documents(entities=[entity], as_of=point,
                repository=unstructured, limit=10000, diagnostics=diagnostics)
            refs = [doc.document_id + '@' + doc.version_id for doc in documents]
            selected.update(refs)
            candidates[entity] = {
                'financials': summarize(lambda: products.company_financial_snapshot(entity=entity, as_of=point)),
                'consensus': summarize(lambda: products.consensus_snapshot(entity=entity, as_of=point)),
                'neutral_evidence': summarize(lambda: products.neutral_evidence(entity=entity, as_of=point)),
                'admitted_documents': {'rows': len(documents), 'refs': refs,
                    'types': sorted({doc.document_type for doc in documents}), 'diagnostics': diagnostics}}

        history = []
        unresolved = []
        for raw in unstructured.conn.execute('SELECT * FROM data_document_versions'):
            version = dict(raw)
            resolved = bool(_read_version_text(version))
            row = {'version_id': version['version_id'], 'document_id': version['document_id'],
                'expected_hash': version['content_hash'], 'status': 'resolved' if resolved else 'unresolved'}
            if not resolved:
                row.update(local_path=version['local_path'],
                    selected_admitted_candidate=version['document_id'] + '@' + version['version_id'] in selected,
                    fact_references=unstructured.conn.execute(
                        'SELECT COUNT(*) FROM data_evidence_facts WHERE document_version_id=?',
                        (version['version_id'],)).fetchone()[0], internal_reference_hits=[])
                unresolved.append(row)
            history.append(row)
        # Conservative search includes bare document IDs: ambiguous old references
        # remain visible, rather than being silently relabelled as unreachable.
        for (table,) in internal.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            quoted = '"' + table.replace('"', '""') + '"'
            hits = {row['version_id']: 0 for row in unresolved}
            for record in internal.execute('SELECT * FROM ' + quoted):
                text_values = [value for value in record if isinstance(value, str)]
                for row in unresolved:
                    if any(row['version_id'] in value or row['document_id'] in value for value in text_values):
                        hits[row['version_id']] += 1
            for row in unresolved:
                if hits[row['version_id']]:
                    row['internal_reference_hits'].append({'table': table, 'rows': hits[row['version_id']]})

        uses = [{**use, 'workflow': group['workflow'], 'status': 'pending',
            'actual_workflow_evidence': 'not_bound'} for group in manifest['groups'] for use in group['required_uses']]
        return {'kind': 'phase-f-required-cache-audit-v2', 'as_of': point.isoformat(),
            'inventory': file_ref(inventory_path), 'source_paths': {'data': str(data_cache), 'internal': str(internal_cache)},
            'status': 'pending', 'qualification_granted': False, 'production_claim': False, 'network_calls': 0,
            'criterion': 'candidate diagnostics only; exact selected-use admission/lineage/completeness/report binding still required',
            'entities': candidates, 'historical_document_versions': history, 'required_uses': uses,
            'conditional_downstream_uses': manifest['downstream_uses']}
    finally:
        structured.close()
        unstructured.close()
        internal.close()


def financial_use_audit(requirements_path, data_cache, artifact_root):
    """Measure explicitly selected financial uses, not workflow/release eligibility.

    Requirements fix entity, source, period, currency and metric semantics before
    execution. Source databases/artifacts remain read-only; control-plane writes
    from the native read guard are redirected into a disposable isolated root.
    Missing data or history fails the selected use; no source/period substitution.
    """
    from ats.data.consumer_api import read_input
    from ats.data.products.base import DataProducts
    from ats.data.stores.structured.repository import SQLiteStructuredRepository, _json
    from ats.data.stores.unstructured.platform import PlatformUnstructuredRepository
    from ats.workflow.isolation import isolated_run
    from ats.workflow.runtime_reads import business_identity

    for path in (requirements_path, data_cache):
        if not Path(path).is_file():
            raise ValueError('existing requirements and read-only data store required')
    manifest = json.loads(Path(requirements_path).read_text())
    point = datetime.fromisoformat(manifest['as_of'])
    uses = manifest['uses']
    if point.tzinfo is None or not uses or len({u['use'] for u in uses}) != len(uses):
        raise ValueError('timezone and nonempty unique predeclared uses required')
    for use in uses:
        if not use['metrics'] or not all(m['metric_id'] and m['period_basis'] and m['unit']
                                        for m in use['metrics']):
            raise ValueError('explicit required metric semantics required')
    structured = SQLiteStructuredRepository(data_cache, readonly=True, artifact_root=artifact_root)
    unstructured = PlatformUnstructuredRepository(data_cache, writable=False)
    results, blobs, backups = [], {}, {}
    try:
        for conn in (structured.conn, unstructured.conn):
            conn.execute('BEGIN')
        products = DataProducts(structured_repository=structured, unstructured_repository=unstructured)

        def observation_hash(row):
            normalized = {key: row[key] for key in ('period', 'period_start', 'period_end', 'event_time',
                'value', 'published_at', 'quality_status')}
            normalized.update(quality=json.loads(row['quality_json']), raw=json.loads(row['raw_payload']))
            return hashlib.sha256(_json(normalized).encode()).hexdigest()

        def migrated_history(row, *, historical):
            # Older recorded semantic repairs retained original hashes/IDs when
            # moving series and resolving conflicts. Prove that original content
            # from the real backup plus the exact recorded semantic transition;
            # a migration label alone never excuses a hash mismatch.
            if 'semantic_reclassification' not in json.loads(row['quality_json']).get('reason_codes', []):
                return None
            for repair in structured.conn.execute('SELECT * FROM data_company_financial_semantic_repairs '
                    "WHERE status='reconciled' AND dry_run=0"):
                details = json.loads(repair['details_json'])
                changes = [change for change in details.get('series', [])
                    if change['new_series_id'] == row['series_id'] and change['entity_id'] == row['entity_id']]
                scoped_changes = [change for change in details.get('series', [])
                    if change['entity_id'] == row['entity_id']]
                if not changes and not (historical and scoped_changes):
                    continue
                backup = Path(repair['backup_path']).resolve()
                if not backup.is_file():
                    continue
                with closing(sqlite3.connect(backup.as_uri() + '?mode=ro', uri=True)) as conn:
                    conn.row_factory = sqlite3.Row
                    prior = conn.execute('SELECT * FROM structured_observations WHERE observation_id=?',
                        (row['observation_id'],)).fetchone()
                    if prior is None or observation_hash(prior) != prior['content_hash']:
                        continue
                    immutable = ('observation_id', 'period', 'period_start', 'period_end', 'event_time',
                        'value', 'published_at', 'known_at', 'fetched_at', 'artifact_id', 'content_hash', 'raw_payload')
                    if any(row[key] != prior[key] for key in immutable):
                        continue
                    old_series = conn.execute('SELECT * FROM structured_series WHERE series_id=?',
                        (prior['series_id'],)).fetchone()
                    current_series = structured.conn.execute('SELECT * FROM structured_series WHERE series_id=?',
                        (row['series_id'],)).fetchone()
                    if old_series is None:
                        continue
                    transition = next((change for change in changes if change['old_series_id'] == prior['series_id']
                        and all(old_series[key] == change['old_' + key] and current_series[key] == change['new_' + key]
                            for key in ('metric_id', 'adjustment', 'unit'))), None)
                    if transition is None and historical and prior['series_id'] == row['series_id']:
                        # Reopening old content is distinct from approving changed
                        # quality metadata for a new required input. This branch
                        # proves historical readability only, never admission.
                        transition = {'kind': 'quality_metadata_only',
                            'before': {'status': prior['quality_status'], 'quality': json.loads(prior['quality_json'])},
                            'after': {'status': row['quality_status'], 'quality': json.loads(row['quality_json'])},
                            'admission_reapproved': False}
                    if transition is None:
                        continue
                    if str(backup) not in backups:
                        backups[str(backup)] = file_ref(backup)
                    return {'repair_id': repair['repair_id'], 'backup': backups[str(backup)],
                        'original_content_hash_verified': True, 'transition': transition}
            return None

        def reopen(row, *, historical=False):
            if not historical and row['quality_status'] != 'accepted':
                raise ValueError('required financial row not accepted: ' + row['observation_id'])
            artifact = structured.conn.execute('SELECT a.*, b.relative_path, b.content_hash AS blob_hash, '
                'b.bytes FROM structured_artifacts a JOIN structured_artifact_blobs b ON b.blob_id=a.blob_id '
                'WHERE a.artifact_id=?', (row['artifact_id'],)).fetchone()
            if artifact is None or artifact['source_id'] != row['source_id'] or artifact['dataset_id'] != row['dataset_id']:
                raise ValueError('missing or mismatched artifact lineage: ' + row['observation_id'])
            if artifact['blob_id'] not in blobs:
                raw = structured.artifacts.read(artifact['relative_path'])
                if len(raw) != artifact['bytes'] or hashlib.sha256(raw).hexdigest() != artifact['blob_hash']:
                    raise ValueError('historical artifact hash/size differs: ' + row['artifact_id'])
                blobs[artifact['blob_id']] = {'artifact_id': row['artifact_id'], 'sha256': artifact['blob_hash'],
                    'path': str(Path(artifact_root) / artifact['relative_path']), 'bytes': len(raw)}
            migration = None
            if observation_hash(row) != row['content_hash']:
                migration = migrated_history(row, historical=historical)
            if observation_hash(row) != row['content_hash'] and migration is None:
                raise ValueError('historical observation content hash differs: ' + row['observation_id'])
            if not math.isfinite(row['value']):
                raise ValueError('nonfinite financial value')
            return {'observation_id': row['observation_id'], 'artifact_id': row['artifact_id'],
                'content_hash': row['content_hash'], 'metric_id': row['metric_id'], 'period': row['period'],
                'period_basis': row['period_basis'], 'unit': row['unit'], 'currency': row['currency'],
                'value': row['value'], 'quality_status': row['quality_status'], 'known_at': row['known_at'],
                'published_at': row['published_at'], 'source_id': row['source_id'],
                'historical_migration': migration}

        with tempfile.TemporaryDirectory(prefix='phase-f-financial-audit-') as root, isolated_run(root):
            for use in uses:
                result = {**use, 'status': 'failed', 'problems': [], 'selected_rows': []}
                results.append(result)
                try:
                    who = business_identity('fundamental', kind='entity', scope_id=use['entity'],
                        entities=[use['entity']], as_of=point)
                    packet = read_input('fundamental', 'COMPANY_DATA', scope={'kind': 'financials',
                        'entity': use['entity']}, as_of=point, products=products, business_scope=who)
                    result.update(read_identity=who.as_row(), packet_status=packet.status,
                        packet_rows=len(packet.payload['rows']), input_refs=packet.input_refs,
                        payload_sha256=hashlib.sha256(_json(packet.payload).encode()).hexdigest())
                    if packet.status != 'complete':
                        raise ValueError('required financial input unavailable: ' + packet.status)
                    if len(packet.payload['rows']) >= 100_000:
                        raise ValueError('financial packet may be truncated')
                    for metric in use['metrics']:
                        matches = [row for row in packet.payload['rows'] if row['source_id'] == use['source_id']
                            and row['period'] == use['period'] and row['currency'] == use['currency']
                            and all(row[key] == metric[key] for key in ('metric_id', 'period_basis', 'unit'))]
                        if not matches:
                            raise ValueError('missing required metric/period/unit: ' + metric['metric_id'])
                        # Keep all applicable dimensions/vintages, never choose a convenient duplicate.
                        result['selected_rows'].extend(reopen(row) for row in matches)
                    # Reopen all historical financial vintages for this entity, including superseded ones.
                    history = structured.observations(dataset_id='company_financials', entity_id=use['entity'],
                        as_of=point, latest_only=False, accepted_only=True, limit=100_001)
                    if len(history) >= 100_001:
                        raise ValueError('historical query truncated')
                    history_proofs = [reopen(row, historical=True) for row in history]
                    result.update(status='passed', historical_rows=len(history_proofs),
                        historical_refs=[r['observation_id'] for r in history_proofs],
                        migrated_history=[r for r in history_proofs if r['historical_migration']],
                        history_sha256=hashlib.sha256(_json(history_proofs).encode()).hexdigest())
                except (OSError, sqlite3.Error, KeyError, TypeError, ValueError) as exc:
                    result['problems'].append(str(exc))
        return {'kind': 'phase-f-selected-financial-use-audit-v1',
            'status': 'passed' if all(r['status'] == 'passed' for r in results) else 'failed',
            'as_of': point.isoformat(), 'requirements': file_ref(requirements_path),
            'authority': file_ref(REPO_ROOT / 'docs/ARCHITECTURE.md'),
            'implementation': file_ref(Path(__file__)), 'uses': results, 'raw_blobs': list(blobs.values()),
            'historical_backups': list(backups.values()),
            'scope': 'selected financial data uses only; workflow outputs and qualification require 6.4/6.6',
            'network_calls': 0, 'qualification_granted': False, 'production_claim': False,
            'historical_data_deleted': False}
    finally:
        structured.close()
        unstructured.close()


def summarize_selected_audit(required, checks):
    """Audit completion never means missing data was accepted for retirement."""
    expected = {row['use'] for row in required}
    actual = [row['use'] for row in checks]
    if not expected or len(expected) != len(required) or len(set(actual)) != len(actual) or set(actual) != expected:
        raise ValueError('every predeclared required use must be audited exactly once')
    for row in checks:
        if row['status'] not in {'passed', 'rejected'} or (row['status'] == 'rejected' and not row['problems']):
            raise ValueError('explicit measured result and rejection reasons required')
    gaps = [row for row in checks if row['status'] == 'rejected']
    return {'audit_status': 'completed', 'coverage_status': 'pending' if gaps else 'passed',
        'required_uses': required, 'checks': checks, 'gaps': gaps,
        'production_claim': False, 'qualification_granted': False,
        'retirement_allowed': False, 'workflow_evidence_owner': ['6.4', '6.6', '12.8']}


def selected_use_audit(requirements_path, data_cache, artifact_root, internal_cache):
    """Task 12.2: inventory and evaluate data, preserving rejected dependencies.

    Old financial periods are legitimate audit inputs. This never changes a
    runtime quote TTL, model/projection reuse policy or production qualification.
    Workflow execution/signoff is measured separately by 6.4/6.6.
    """
    from ats.data.consumer_api import read_input
    from ats.data.products.base import DataProducts
    from ats.data.stores.structured.repository import SQLiteStructuredRepository
    from ats.data.stores.unstructured.platform import PlatformUnstructuredRepository
    from ats.memory.store import TradingMemory
    from ats.workflow.isolation import isolated_run
    from ats.workflow.runtime_reads import business_identity

    if not Path(internal_cache).is_file():
        raise ValueError('existing read-only internal store required')
    manifest = json.loads(Path(requirements_path).read_text())
    point = datetime.fromisoformat(manifest['as_of'])
    entities = [row['entity'] for row in manifest['uses']]
    if len(set(entities)) != len(entities):
        raise ValueError('one explicit financial use per selected entity required')
    plans = [build_plan(requested_tasks=['fundamental-routine'], scope=ProjectionScope(kind='entity', id=entity),
        trigger=TriggerContext(kind='manual', workflow_id='fundamental-routine', trigger_id='12.2-data-audit'),
        as_of=point.isoformat(), run_id='12.2-audit:' + entity) for entity in entities]
    required = [row for plan in plans for row in required_plan_uses(plan)]
    financial = financial_use_audit(requirements_path, data_cache, artifact_root)
    structured = SQLiteStructuredRepository(data_cache, readonly=True, artifact_root=artifact_root)
    unstructured = PlatformUnstructuredRepository(data_cache, writable=False)
    checks = []
    try:
        with closing(sqlite3.connect(Path(internal_cache).resolve().as_uri() + '?mode=ro', uri=True)) as internal:
            internal.row_factory = sqlite3.Row
            for conn in (structured.conn, unstructured.conn, internal):
                conn.execute('BEGIN')
            products = DataProducts(structured_repository=structured, unstructured_repository=unstructured)
            store = object.__new__(TradingMemory)
            store.path, store._data, store._closed, store.conn = str(internal_cache), None, False, internal
            from ats.agents.fundamental.routine import load_baseline
            with tempfile.TemporaryDirectory(prefix='phase-f-data-use-') as root, isolated_run(root):
                for entity in entities:
                    who = business_identity('information', kind='entity', scope_id=entity,
                        entities=[entity], as_of=point)
                    packet = read_input('information', 'DOC_DATA', scope={'entity': entity, 'limit': 60},
                        as_of=point, products=products, business_scope=who)
                    selected = [row for row in required if row['task_scope']['id'] == entity]
                    information = next(row for row in selected if row['consumer'] == 'information')
                    checks.append({'use': information['use'],
                        'status': 'passed' if packet.payload and packet.status == 'complete' else 'rejected',
                        'problems': [] if packet.payload and packet.status == 'complete' else ['required admitted documents missing/incomplete'],
                        'actual_read': packet.model_dump(mode='json', exclude={'payload'}),
                        'documents': [{'document_id': doc['document_id'], 'version_id': doc['version_id'],
                            'published_at': doc['published_at'], 'content_hash': doc['content_hash']}
                            for doc in packet.payload or []],
                        'criterion': 'admitted historical documents; no invented 7-day financial-data rule'})
                    fundamental = next(row for row in selected if row['consumer'] == 'fundamental')
                    rows = store.task_projection_envelopes(agent_role='information_brief',
                        scope_kind='entity', scope_id=entity, status='published', limit=1)
                    brief = rows[0] if rows else None
                    problems = []
                    if not brief:
                        problems.append('required Information projection missing')
                    else:
                        # This audit does not qualify even a nonempty projection;
                        # runtime schema/vintage/freshness/report gates stay intact.
                        for key in ('input_refs', 'data_vintage_refs'):
                            value = brief.get(key) or []
                            if isinstance(value, str): value = json.loads(value)
                            if not value: problems.append('Information projection has no ' + key)
                        if not brief.get('agent_run_id'):
                            problems.append('Information projection has no agent attempt binding')
                        # Nonempty identifiers also need real remeasurement; this
                        # inventory must not approve a projection by its labels.
                        if not problems:
                            problems.append('Information dependency lineage requires actual workflow remeasurement in 6.4')
                    f = next(row for row in financial['uses'] if row['entity'] == entity)
                    problems.extend(f['problems'])
                    baseline = load_baseline(store, entity)
                    baseline['kpi_words'] = sorted(baseline.get('kpi_words') or [])
                    if not baseline.get('fiscal_label'):
                        problems.append('previous pre-event baseline fiscal label missing; validate for selected invocation')
                    checks.append({'use': fundamental['use'], 'status': 'rejected' if problems else 'passed',
                        'problems': problems, 'financial_data_status': f['status'],
                        'information_projection': brief, 'baseline': baseline,
                        'follow_up': '6.4 actual Information/Fundamental input and output evidence; 6.6 qualification',
                        'required_history_not_deleted': True})
        result = summarize_selected_audit(required, checks)
        result.update(kind='phase-f-selected-required-use-audit-v1', financial_audit=financial,
            as_of=point.isoformat(), requirements=file_ref(requirements_path),
            implementation=file_ref(Path(__file__)), source_mutated=False,
            scope='four selected entities, Routine analysis dependencies; no Chief/trade or Event claim',
            financial_age_policy='audit historical reporting periods; no fixed recent-fetch threshold')
        return result
    finally:
        structured.close()
        unstructured.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inv = commands.add_parser("inventory"); inv.add_argument("--as-of", required=True)
    triage = commands.add_parser("triage"); triage.add_argument("--junit", type=Path, required=True)
    triage.add_argument("--previous", type=Path, required=True)
    probe = commands.add_parser("preflight")
    for flag in ("scopes", "freeze", "bindings", "source-ledger"):
        probe.add_argument("--" + flag, type=Path, required=True)
    cache = commands.add_parser('cache-audit')
    cache.add_argument('--inventory', type=Path, required=True)
    cache.add_argument('--data-cache', type=Path, required=True)
    cache.add_argument('--internal-cache', type=Path, required=True)
    financial = commands.add_parser('financial-audit')
    financial.add_argument('--requirements', type=Path, required=True)
    financial.add_argument('--data-cache', type=Path, required=True)
    financial.add_argument('--artifact-root', type=Path, required=True)
    selected = commands.add_parser('selected-use-audit')
    for flag in ('requirements', 'data-cache', 'artifact-root', 'internal-cache'):
        selected.add_argument('--' + flag, type=Path, required=True)
    for sub in (inv, triage, probe, cache, financial, selected):
        sub.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "inventory": result = inventory(args.as_of)
    elif args.command == "triage": result = failures(args.junit, args.previous)
    elif args.command == 'cache-audit': result = cache_audit(args.inventory, args.data_cache, args.internal_cache)
    elif args.command == 'financial-audit': result = financial_use_audit(args.requirements, args.data_cache, args.artifact_root)
    elif args.command == 'selected-use-audit': result = selected_use_audit(args.requirements,
        args.data_cache, args.artifact_root, args.internal_cache)
    else:
        read = lambda path: json.loads(path.read_text())
        result = preflight(read(args.scopes), read(args.freeze), read(args.bindings), source_ledger=args.source_ledger)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x") as stream: json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({"output": str(args.out), "status": result.get('status', result.get('audit_status')),
        "coverage_status": result.get('coverage_status'), "production_mutated": False}))
    return 1 if args.command in {"preflight", "financial-audit"} and result["status"] != "passed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
