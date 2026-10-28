"""Append-only, hash-bound FactSet review audit in the existing data database."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from decimal import Decimal

from ...sources.factset_contracts import (
    CellEvidence,
    GroupPackage,
    SectorCandidate,
    digest,
    validate_group,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS factset_review_packages (
    package_hash TEXT PRIMARY KEY, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS factset_group_reviews (
    review_id TEXT PRIMARY KEY, package_hash TEXT NOT NULL,
    reviewer TEXT NOT NULL, decision TEXT NOT NULL,
    evidence_json TEXT NOT NULL, note TEXT NOT NULL, reviewed_at TEXT NOT NULL,
    FOREIGN KEY(package_hash) REFERENCES factset_review_packages(package_hash)
);
CREATE INDEX IF NOT EXISTS factset_group_review_lookup
ON factset_group_reviews(package_hash,reviewed_at);
CREATE TABLE IF NOT EXISTS factset_group_corrections (
    package_hash TEXT PRIMARY KEY, parent_hash TEXT NOT NULL,
    reviewer TEXT NOT NULL, note TEXT NOT NULL, corrected_at TEXT NOT NULL,
    FOREIGN KEY(package_hash) REFERENCES factset_review_packages(package_hash),
    FOREIGN KEY(parent_hash) REFERENCES factset_review_packages(package_hash)
);
CREATE TABLE IF NOT EXISTS factset_index_review_packages (
    package_hash TEXT PRIMARY KEY, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS factset_index_reviews (
    review_id TEXT PRIMARY KEY, package_hash TEXT NOT NULL,
    reviewer TEXT NOT NULL, decision TEXT NOT NULL,
    evidence_json TEXT NOT NULL, note TEXT NOT NULL, reviewed_at TEXT NOT NULL,
    FOREIGN KEY(package_hash) REFERENCES factset_index_review_packages(package_hash)
);
CREATE INDEX IF NOT EXISTS factset_index_review_lookup
ON factset_index_reviews(package_hash,reviewed_at);
"""


class FactSetReviews:
    def __init__(self, repository):
        self.repository = repository
        self.conn = repository.conn
        self.lock = repository._lock
        with self.lock, self.conn:
            self.conn.executescript(SCHEMA)

    def register(self, package: GroupPackage, *, at: datetime | None = None) -> str:
        stamp = self._stamp(at)
        payload = json.dumps(package.identity_payload, sort_keys=True, ensure_ascii=False)
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO factset_review_packages VALUES (?,?,?)",
                (package.package_hash, payload, stamp),
            )
        return package.package_hash

    @staticmethod
    def _stamp(at):
        at = at or datetime.now(timezone.utc)
        if at.tzinfo is None:
            raise ValueError("review_time_must_be_aware")
        return at.astimezone(timezone.utc).isoformat(timespec="microseconds")

    def package(self, package_hash: str) -> GroupPackage:
        with self.lock:
            row = self.conn.execute(
                "SELECT payload_json FROM factset_review_packages WHERE package_hash=?",
                (package_hash,),
            ).fetchone()
        if row is None:
            raise ValueError("review_package_not_found")
        package = GroupPackage.model_validate_json(row[0])
        if package.package_hash != package_hash:
            raise ValueError("review_package_hash_mismatch")
        return package

    def latest_group_decision(self, package_hash: str) -> tuple[str, str] | None:
        with self.lock:
            row = self.conn.execute(
                'SELECT review_id,decision FROM factset_group_reviews '
                'WHERE package_hash=? ORDER BY reviewed_at DESC,rowid DESC LIMIT 1',
                (package_hash,)).fetchone()
        return tuple(row) if row else None

    def latest_index_decision(self, package_hash: str) -> tuple[str, str] | None:
        with self.lock:
            row = self.conn.execute(
                'SELECT review_id,decision FROM factset_index_reviews '
                'WHERE package_hash=? ORDER BY reviewed_at DESC,rowid DESC LIMIT 1',
                (package_hash,)).fetchone()
        return tuple(row) if row else None

    def decide(
        self,
        package_hash: str,
        *,
        decision: str,
        reviewer: str,
        evidence_refs: list[str],
        note: str,
        policy: dict,
        at: datetime | None = None,
    ) -> str:
        if decision not in {"approve", "reject", "unreadable"}:
            raise ValueError("invalid_review_decision")
        if (
            not reviewer.strip()
            or not note.strip()
            or not evidence_refs
            or any(not ref.strip() for ref in evidence_refs)
        ):
            raise ValueError("reviewer_and_source_evidence_required")
        stamp = self._stamp(at)
        package = self.package(package_hash)
        if decision == "approve":
            failures = validate_group(package, policy)
            if failures:
                raise ValueError("review_group_invalid:" + ",".join(failures))
        payload = {
            "package_hash": package_hash,
            "decision": decision,
            "reviewer": reviewer,
            "evidence_refs": sorted(set(evidence_refs)),
            "note": note,
            "reviewed_at": stamp,
        }
        review_id = digest(payload)
        with self.lock, self.conn:
            created = self.conn.execute(
                "SELECT created_at FROM factset_review_packages WHERE package_hash=?",
                (package_hash,),
            ).fetchone()[0]
            if stamp < created:
                raise ValueError("review_predates_package")
            self.conn.execute(
                "INSERT OR IGNORE INTO factset_group_reviews VALUES (?,?,?,?,?,?,?)",
                (
                    review_id,
                    package_hash,
                    reviewer,
                    decision,
                    json.dumps(payload["evidence_refs"]),
                    note,
                    stamp,
                ),
            )
        return review_id

    def require_approval(
        self, package: GroupPackage, review_id: str, *, policy: dict, as_of: datetime | None = None
    ) -> dict:
        if not isinstance(review_id, str) or not review_id:
            raise ValueError("bound_review_id_required")
        stamp = self._stamp(as_of)
        if validate_group(package, policy):
            raise ValueError("review_group_not_publishable")
        with self.lock:
            row = self.conn.execute(
                "SELECT review_id,decision,reviewed_at,reviewer,evidence_json "
                "FROM factset_group_reviews WHERE package_hash=? AND reviewed_at<=? "
                "ORDER BY reviewed_at DESC,rowid DESC LIMIT 1",
                (package.package_hash, stamp),
            ).fetchone()
        if row is None or row[0] != review_id or row[1] != "approve":
            raise ValueError("review_missing_stale_or_rejected")
        return {
            "review_id": row[0],
            "reviewed_at": row[2],
            "reviewer": row[3],
            "evidence_refs": json.loads(row[4]),
            "package_hash": package.package_hash,
        }

    def list_packages(self, limit: int = 50) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT package_hash,payload_json,created_at "
                "FROM factset_review_packages ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 500)),),
            ).fetchall()
        return [
            {"package_hash": row[0], "package": json.loads(row[1]), "created_at": row[2]}
            for row in rows
        ]

    def correct(
        self,
        package_hash: str,
        *,
        corrections: list[dict],
        reviewer: str,
        note: str,
        at: datetime | None = None,
    ) -> str:
        """Append a human-sourced revision; never manufacture an approval."""
        if not reviewer.strip() or not note.strip() or not corrections:
            raise ValueError("correction_actor_note_and_cells_required")
        stamp = self._stamp(at)
        parent = self.package(package_hash)
        changes = {}
        for change in corrections:
            if set(change) != {"entity_id", "column", "value", "evidence"}:
                raise ValueError("invalid_correction_schema")
            key = (change["entity_id"], change["column"])
            if key in changes:
                raise ValueError("duplicate_correction_cell")
            evidence = CellEvidence.model_validate(change["evidence"])
            if evidence.method != "manual:" + reviewer:
                raise ValueError("correction_evidence_actor_mismatch")
            changes[key] = (Decimal(str(change["value"])), evidence)
        candidates = []
        for cell in parent.candidates:
            change = changes.pop((cell.entity_id, cell.column), None)
            if change is None:
                candidates.append(cell)
                continue
            value, evidence = change
            data = cell.model_dump()
            data.update(
                value=value,
                status="manual_reviewed",
                corrected_from=digest(cell.model_dump(mode="json")),
                value_evidence=cell.value_evidence + (evidence,),
            )
            candidates.append(SectorCandidate.model_validate(data))
        if changes:
            raise ValueError("correction_unknown_cell")
        revised = GroupPackage.model_validate(
            {**parent.model_dump(), "candidates": tuple(candidates)}
        )
        with self.lock, self.conn:
            created = self.conn.execute(
                "SELECT created_at FROM factset_review_packages WHERE package_hash=?",
                (package_hash,),
            ).fetchone()[0]
            if stamp < created:
                raise ValueError("correction_predates_package")
            self.conn.execute(
                "INSERT OR IGNORE INTO factset_review_packages VALUES (?,?,?)",
                (revised.package_hash, json.dumps(revised.identity_payload, sort_keys=True), stamp),
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO factset_group_corrections VALUES (?,?,?,?,?)",
                (revised.package_hash, package_hash, reviewer, note, stamp),
            )
        return revised.package_hash

    def register_index(self, context: dict, *, expected_cells: list[dict],
                       not_disclosed: list[dict], evidence_refs: list[str],
                       at: datetime | None = None) -> str:
        """Register an independently declared aggregate inventory.

        The expected cells and source references come from a reviewer, never
        from the extractor's successful candidate subset.
        """
        required = {'pdf_hash', 'document_version', 'policy_hash',
                    'extractor_version', 'scope_id', 'scope_version',
                    'metric_group', 'entity_ids', 'registered_metric_ids',
                    'candidate_set_hash', 'cells'}
        if set(context) != required or context['scope_id'] != 'index' \
                or context['entity_ids'] != ['SP500'] \
                or context['metric_group'] != 'index_core':
            raise ValueError('invalid_index_review_context')
        if digest(context['cells']) != context['candidate_set_hash']:
            raise ValueError('index_candidate_set_hash_mismatch')
        if not expected_cells or not evidence_refs or any(not ref.strip() for ref in evidence_refs):
            raise ValueError('independent_index_inventory_and_evidence_required')
        def key(row):
            if set(row) != {'metric_id', 'period', 'period_basis'}:
                raise ValueError('invalid_index_cell_key')
            return (row['metric_id'], row['period'], row['period_basis'])
        expected = sorted(set(key(row) for row in expected_cells))
        absent = sorted(set(key(row) for row in not_disclosed))
        if len(expected) != len(expected_cells) or len(absent) != len(not_disclosed) \
                or set(expected) & set(absent):
            raise ValueError('index_inventory_duplicate_or_overlap')
        payload = {
            **context,
            'expected_cells': [dict(zip(('metric_id', 'period', 'period_basis'), item))
                               for item in expected],
            'not_disclosed': [dict(zip(('metric_id', 'period', 'period_basis'), item))
                              for item in absent],
            'source_evidence_refs': sorted(set(evidence_refs)),
        }
        package_hash = digest(payload)
        with self.lock, self.conn:
            self.conn.execute(
                'INSERT OR IGNORE INTO factset_index_review_packages VALUES (?,?,?)',
                (package_hash, json.dumps(payload, sort_keys=True), self._stamp(at)))
        return package_hash

    def index_package(self, package_hash: str) -> dict:
        with self.lock:
            row = self.conn.execute(
                'SELECT payload_json FROM factset_index_review_packages WHERE package_hash=?',
                (package_hash,)).fetchone()
        if row is None:
            raise ValueError('index_review_package_not_found')
        payload = json.loads(row[0])
        if digest(payload) != package_hash:
            raise ValueError('index_review_package_hash_mismatch')
        return payload

    def list_index_packages(self, limit: int = 50) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                'SELECT package_hash,payload_json,created_at '
                'FROM factset_index_review_packages ORDER BY created_at DESC LIMIT ?',
                (max(1, min(limit, 500)),)).fetchall()
        return [{'package_hash': row[0], 'package': json.loads(row[1]),
                 'created_at': row[2]} for row in rows]

    @staticmethod
    def index_package_reasons(package: dict) -> list[str]:
        def key(row):
            return (row['metric_id'], row['period'], row['period_basis'])
        expected = {key(row) for row in package['expected_cells']}
        declared_metric_ids = {row[0] for row in expected} | {
            row['metric_id'] for row in package['not_disclosed']}
        cells = package['cells']
        actual = {key(row) for row in cells if row['status'] == 'accepted'}
        reasons = []
        if len(cells) != len({key(row) for row in cells}):
            reasons.append('index_duplicate_or_conflicting_cells')
        if expected - actual:
            reasons.append('index_required_cells_missing')
        if declared_metric_ids != set(package['registered_metric_ids']):
            reasons.append('index_registered_metrics_not_accounted_for')
        if actual - expected:
            reasons.append('index_unreviewed_extra_cells')
        if any(row['status'] != 'accepted' for row in cells):
            reasons.append('index_quarantined_or_conflicting_cells')
        if any(not row['evidence_hash'] for row in cells):
            reasons.append('index_candidate_evidence_missing')
        return reasons

    def decide_index(self, package_hash: str, *, decision: str, reviewer: str,
                     evidence_refs: list[str], note: str,
                     at: datetime | None = None) -> str:
        if decision not in {'approve', 'reject', 'unreadable'}:
            raise ValueError('invalid_review_decision')
        if not reviewer.strip() or not note.strip() or not evidence_refs \
                or any(not ref.strip() for ref in evidence_refs):
            raise ValueError('reviewer_and_source_evidence_required')
        package = self.index_package(package_hash)
        if decision == 'approve' and (reasons := self.index_package_reasons(package)):
            raise ValueError('index_review_package_invalid:' + ','.join(reasons))
        stamp = self._stamp(at)
        payload = {'package_hash': package_hash, 'decision': decision,
                   'reviewer': reviewer, 'evidence_refs': sorted(set(evidence_refs)),
                   'note': note, 'reviewed_at': stamp}
        review_id = digest(payload)
        with self.lock, self.conn:
            created = self.conn.execute(
                'SELECT created_at FROM factset_index_review_packages WHERE package_hash=?',
                (package_hash,)).fetchone()[0]
            if stamp < created:
                raise ValueError('review_predates_package')
            self.conn.execute(
                'INSERT OR IGNORE INTO factset_index_reviews VALUES (?,?,?,?,?,?,?)',
                (review_id, package_hash, reviewer, decision,
                 json.dumps(payload['evidence_refs']), note, stamp))
        return review_id

    def require_index_approval(self, context: dict, review_id: str, *,
                               as_of: datetime | None = None) -> dict:
        if not review_id:
            raise ValueError('bound_review_id_required')
        with self.lock:
            row = self.conn.execute(
                'SELECT package_hash,decision,reviewed_at,reviewer FROM factset_index_reviews '
                'WHERE review_id=?', (review_id,)).fetchone()
        if row is None or row[1] != 'approve' or row[2] > self._stamp(as_of):
            raise ValueError('index_review_missing_rejected_or_future')
        package = self.index_package(row[0])
        if any(package.get(key) != value for key, value in context.items()):
            raise ValueError('index_review_context_mismatch')
        if self.index_package_reasons(package):
            raise ValueError('index_review_package_invalid')
        with self.lock:
            latest = self.conn.execute(
                'SELECT review_id FROM factset_index_reviews '
                'WHERE package_hash=? AND reviewed_at<=? '
                'ORDER BY reviewed_at DESC,rowid DESC LIMIT 1',
                (row[0], self._stamp(as_of))).fetchone()
        if latest is None or latest[0] != review_id:
            raise ValueError('index_review_stale')
        return {'package_hash': row[0], 'review_id': review_id,
                'reviewed_at': row[2], 'reviewer': row[3],
                'expected_cells': package['expected_cells'],
                'not_disclosed': package['not_disclosed']}
