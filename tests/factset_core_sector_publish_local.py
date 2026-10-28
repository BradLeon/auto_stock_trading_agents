"""Isolated test approval and product readback; no production write or approval."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import argparse
import hashlib
import json
import tempfile

from ats.data.core.structured_models import ArtifactDescriptor
from ats.data.pipelines.factset_groups import FactSetGroupPipeline
from ats.data.products.base import DataProducts
from ats.data.sources.factset_contracts import GroupPackage, validate_group
from ats.data.sources.factset_report_layout import load_layout_policy, policy_hash
from ats.data.stores.structured.factset_reviews import FactSetReviews
from ats.data.stores.structured.repository import SQLiteStructuredRepository
import ats.data.pipelines.factset_groups as publisher

parser = argparse.ArgumentParser()
parser.add_argument('evidence_directory', type=Path)
args = parser.parse_args()
comparison = json.loads((args.evidence_directory/'golden-comparison.json').read_text())
assert comparison['expected_cells'] == comparison['extracted_cells'] == 70
assert not any(comparison[key] for key in ('missing','extra','different','blocked_groups'))
policy = load_layout_policy()
assert comparison['policy_hash'] == policy_hash(policy)
root = Path(tempfile.mkdtemp(prefix='factset-sector-publish-',dir='/private/tmp'))
repo = SQLiteStructuredRepository(root/'isolated.sqlite',artifact_root=root/'artifacts')
reviews = FactSetReviews(repo)
products = DataProducts(structured_repository=repo)
publisher.source_mode = lambda _: 'platform'  # process-local isolated test switch only
now = datetime.now(timezone.utc)
summary = []
for path in sorted(args.evidence_directory.glob('[0-9]*.json')):
    raw = json.loads(path.read_text())
    packages = [GroupPackage.model_validate(p) for p in raw['packages']]
    body = (Path('var/data_artifacts')/raw['pdf_hash'][:2]/(raw['pdf_hash']+'.bin')).read_bytes()
    assert hashlib.sha256(body).hexdigest() == raw['pdf_hash']
    artifact = repo.put_artifact(body,ArtifactDescriptor(source_id='factset_earnings_insight_metrics',
        dataset_id='sp500_earnings_insight',fetched_at=now,media_type='application/pdf'))
    pipeline = FactSetGroupPipeline(repo,policy=policy,clock=lambda:now)
    for package in packages:
        assert validate_group(package,policy) == []
        pending = pipeline.run(package,artifact_id=artifact.id,document_id='isolated-source')
        assert not pending['passed'] and not pending['observation_ids']
        rid = reviews.decide(package.package_hash,decision='approve',reviewer='isolated-test-only',
            evidence_refs=['local-independent-golden:'+raw['pdf_hash']],
            note='Isolated test, not an operator production approval',policy=policy,at=now)
        released = pipeline.run(package,artifact_id=artifact.id,document_id='isolated-source',review_id=rid)
        assert released['passed'] and len(released['observation_ids']) == len(package.candidates)
        again = pipeline.run(package,artifact_id=artifact.id,document_id='isolated-source',review_id=rid)
        assert again['status'] == 'no_change' and again['observation_ids'] == released['observation_ids']
    view = products.earnings_insight_groups(version_id=packages[0].document_version,
        report_date=packages[0].report_date,expected_groups=[p.group for p in packages],
        as_of=now+timedelta(seconds=1))
    assert view['published_groups'] == 12
    assert sum(len(group['observations']) for group in view['current'].values()) == 35
    summary.append({'report_date':raw['report_date'],'published_groups':12,'readback_cells':35})
repo.close()
output = {'reports':summary,'isolated_database':str(root/'isolated.sqlite'),
          'production_approval':False,'production_cutover':False}
(args.evidence_directory/'publication-comparison.json').write_text(json.dumps(output,indent=2))
print(json.dumps(output,indent=2))
