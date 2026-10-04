"""Compare AFTER extraction and exercise isolated publication with test approvals."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
import argparse
import json
import tempfile

import yaml

from ats.data.core.structured_models import ArtifactDescriptor
from ats.data.sources.factset_contracts import GroupPackage, validate_group
from ats.data.sources.factset_report_layout import load_layout_policy, policy_hash
from ats.data.stores.structured.repository import SQLiteStructuredRepository
from ats.data.stores.structured.factset_reviews import FactSetReviews
from ats.data.products.base import DataProducts
from ats.data.pipelines.factset_groups import FactSetGroupPipeline
import ats.data.pipelines.factset_groups as publisher

parser = argparse.ArgumentParser()
parser.add_argument('evidence_directory', type=Path)
args = parser.parse_args()
policy = load_layout_policy()
root = Path(tempfile.mkdtemp(prefix='factset-core-chain-', dir='/private/tmp'))
repo = SQLiteStructuredRepository(root/'isolated.sqlite', artifact_root=root/'artifacts')
reviews = FactSetReviews(repo)
products = DataProducts(structured_repository=repo)
golden = yaml.safe_load(Path('tests/fixtures/factset_earnings_insight/acceptance/cross-report-visual-partial.yaml').read_text())
expected = {}
for table in golden['tables']:
    if table.get('source_row') != 'Today':
        continue
    value = next(v for label, v in table['values'].items() if label in policy['sector_aliases']['GICS_45'])
    expected[(table['report_date'], table['page'], table['image'])] = Decimal(str(value))
checked, results = set(), []
now = datetime.now(timezone.utc)
# Isolated DB only. This process-local test switch changes no deployment config.
publisher.source_mode = lambda _: 'platform'
for path in sorted(args.evidence_directory.glob('[0-9]*.json')):
    raw = json.loads(path.read_text())
    assert raw['policy_hash'] == policy_hash(policy)
    packages = tuple(GroupPackage.model_validate(p) for p in raw['packages'])
    assert len(packages) == 4 and not any(raw['validation'])
    pdf_hash = raw['pdf_hash']
    body = (Path('var/data_artifacts')/pdf_hash[:2]/f'{pdf_hash}.bin').read_bytes()
    artifact = repo.put_artifact(body, ArtifactDescriptor(source_id='factset_earnings_insight_metrics',
        dataset_id='sp500_earnings_insight', fetched_at=now, media_type='application/pdf'))
    pipeline = FactSetGroupPipeline(repo, policy=policy, clock=lambda:now)
    for p in packages:
        assert validate_group(p, policy) == []
        for c in p.candidates:
            e = c.value_evidence[0]
            key = (p.report_date.isoformat(), e.page_number, e.image_number)
            assert key not in checked and c.value == expected[key], (key, c.value)
            checked.add(key)
        pending = pipeline.run(p, artifact_id=artifact.id, document_id='isolated-source')
        assert not pending['passed'] and not pending['observation_ids']
        rid = reviews.decide(p.package_hash, decision='approve', reviewer='isolated-test-only',
            evidence_refs=[f'local-golden:{p.pdf_hash}'],
            note='Test approval only; not production operator approval', policy=policy, at=now)
        released = pipeline.run(p, artifact_id=artifact.id, document_id='isolated-source', review_id=rid)
        assert released['passed'] and len(released['observation_ids']) == 2
        repeat = pipeline.run(p, artifact_id=artifact.id, document_id='isolated-source', review_id=rid)
        assert repeat['status'] == 'no_change' and repeat['observation_ids'] == released['observation_ids']
    view = products.earnings_insight_groups(version_id=packages[0].document_version,
        report_date=packages[0].report_date, expected_groups=[p.group for p in packages], as_of=now+timedelta(seconds=1))
    assert view['published_groups'] == 4
    assert sum(len(g['observations']) for g in view['current'].values()) == 8
    results.append({'report_date': str(packages[0].report_date), 'groups': 4, 'matched_cells': 8})
assert checked == set(expected) and len(checked) == 16
repo.close()
summary = {'policy_hash':policy_hash(policy), 'results':results, 'isolated_database':str(root/'isolated.sqlite'),
    'production_approval':False, 'production_cutover':False, 'matched_cells':len(checked)}
(args.evidence_directory/'verification-summary.json').write_text(json.dumps(summary,indent=2))
print(json.dumps(summary,indent=2))
