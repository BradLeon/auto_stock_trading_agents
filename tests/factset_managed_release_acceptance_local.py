"""Issue test-only approvals for an isolated managed FactSet queue replay.

Never run against a production database. This file is intentionally git-ignored.
"""

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path

import yaml

from ats.data.sources.factset_contracts import GroupIdentity
from ats.data.sources.factset_report_layout import load_layout_policy
from ats.data.stores.structured.factset_reviews import FactSetReviews
from ats.data.stores.structured.repository import SQLiteStructuredRepository


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', required=True)
    parser.add_argument('--run-queue', action='store_true')
    parser.add_argument('--readback', action='store_true')
    parser.add_argument('--pdf', default='')
    parser.add_argument('--artifact-root', default='')
    args = parser.parse_args()
    db = Path(args.db).resolve()
    if not db.is_relative_to(Path('/private/tmp')):
        raise ValueError('isolated_tmp_database_required')
    if args.readback:
        from ats.data.products.base import DataProducts
        from ats.data.sources.factset_report_layout import declared_report_groups
        from ats.data.stores.unstructured.platform import PlatformUnstructuredRepository

        structured = SQLiteStructuredRepository(db, artifact_root=args.artifact_root or None)
        documents = PlatformUnstructuredRepository(db, writable=False)
        products = DataProducts(structured_repository=structured,
                                unstructured_repository=documents)
        snapshot = products.earnings_insight_snapshot()
        group_view = products.earnings_insight_groups(
            version_id=snapshot.report.version_id,
            report_date=snapshot.report.report_date,
            expected_groups=declared_report_groups(snapshot.report.report_date))
        print(json.dumps({'version_id': snapshot.report.version_id,
                          'index_state': snapshot.status.index_release.state,
                          'index_periods': len(snapshot.index),
                          'core_acceptance': group_view['core_acceptance'],
                          'published_groups': group_view['published_groups'],
                          'expected_groups': group_view['expected_groups'],
                          'report_coverage': group_view['report_coverage']}))
        structured.close()
        documents.close()
        return
    repository = SQLiteStructuredRepository(db)
    releases = repository.release_manifests(
        dataset_id='sp500_earnings_insight', passed_only=False, limit=1000)
    index = next(row for row in releases if row['partition_name'] == 'index_core'
                 and row['report_date'] == '2026-09-18')
    groups = [row for row in releases if row['partition_name'].startswith('sector_group:')
              and row['report_date'] == '2026-09-18']
    assert len(groups) == 12
    golden = yaml.safe_load(Path(
        'tests/fixtures/factset_earnings_insight/acceptance/index-core-golden.yaml'
    ).read_text())['reports']['2026-09-18']
    cells = [{'metric_id': metric, 'period': row['period'],
              'period_basis': row.get('basis', 'target_quarter')}
             for row in golden['groups'] for metric in row['values']]
    reviews = FactSetReviews(repository)
    now = datetime.now(timezone.utc)
    package = reviews.register_index(
        index['quality']['index_review_context'], expected_cells=cells,
        not_disclosed=[{'metric_id': 'earnings.reporting.coverage',
                        'period': '2026Q3', 'period_basis': 'target_quarter'}],
        evidence_refs=['isolated_independent_source_pdf'], at=now)
    index_review = reviews.decide_index(
        package, decision='approve', reviewer='isolated_test',
        evidence_refs=['independent_index_core_golden'],
        note='isolated managed queue acceptance, not production approval')
    sector_reviews = {}
    policy = load_layout_policy()
    for row in groups:
        identity = GroupIdentity.model_validate(row['quality']['group'])
        sector_reviews[identity.key] = reviews.decide(
            row['quality']['package_hash'], decision='approve',
            reviewer='isolated_test', evidence_refs=['independent_sector_visual_golden'],
            note='isolated managed queue acceptance, not production approval',
            policy=policy)
    scope = {'index_review_id': index_review, 'review_ids': sector_reviews,
             'url': 'https://advantage.factset.com/hubfs/Website/Resources%20Section/Research%20Desk/Earnings%20Insight/EarningsInsight_091826.pdf'}
    repository.close()
    if not args.run_queue:
        print(json.dumps(scope, sort_keys=True))
        return
    if not args.pdf or not args.artifact_root:
        raise ValueError('pdf_and_artifact_root_required')
    from ats.runtime.cli import main as ats_main

    output = StringIO()
    with redirect_stdout(output):
        code = ats_main([
            'data', 'factset-semantic-refresh',
            '--source', 'factset_earnings_insight_doc',
            '--db', str(db), '--artifact-root', args.artifact_root,
            '--report-path', args.pdf, '--query-scope', json.dumps(scope),
        ])
    result = json.loads(output.getvalue())
    ingestion = result.get('ingestion_result') or {}
    print(json.dumps({'exit_code': code, 'task_id': result.get('task_id'),
                      'queue_status': result.get('status'),
                      'core_acceptance': ingestion.get('core_acceptance'),
                      'report_coverage': ingestion.get('report_coverage'),
                      'index_release_id': (ingestion.get('index_core') or {}).get('release_id'),
                      'sector_group_release_ids': [row.get('release_id') for row in
                         (ingestion.get('sector_groups') or {}).values()]}, sort_keys=True))


if __name__ == '__main__':
    main()
