"""Fresh dual-PDF replay; independent inventory input, never golden values."""
from datetime import datetime, timezone
from pathlib import Path
import json
import tempfile

import yaml

from ats.data.sources.factset_earnings_insight import FactSetFetch, inspect_pdf, STABLE_URL
from ats.data.sources.factset_contracts import GroupIdentity, validate_group
from ats.data.sources.factset_report_layout import load_layout_policy, policy_hash
from ats.data.sources.factset_semantic import extract_sector_packages

inventory = yaml.safe_load(Path('tests/fixtures/factset_earnings_insight/acceptance/core-inventory.yaml').read_text())
policy = load_layout_policy()
out = Path(tempfile.mkdtemp(prefix='factset-sector-core-', dir='/private/tmp'))
print('evidence_directory', out, flush=True)
for date, plan in inventory['reports'].items():
    pdf_hash = plan['pdf_hash']
    body = (Path('var/data_artifacts')/pdf_hash[:2]/(pdf_hash+'.bin')).read_bytes()
    source = FactSetFetch(STABLE_URL,STABLE_URL,200,'','','application/pdf',body,
                          datetime.now(timezone.utc))
    document = inspect_pdf(source)
    assert str(document.report_date) == date and document.pdf_hash == pdf_hash
    groups = [GroupIdentity(chart_id=name,period=str(item['period']),period_basis=item['basis'],
        scope_id='technology',scope_version=policy['scope_policy']['version'],entity_ids=('GICS_45',))
        for name, items in plan['groups'].items() for item in items]
    packages, charts = extract_sector_packages(document,document_version=f'isolated:{pdf_hash}',
                                                expected_groups=groups,policy=policy)
    output = {'report_date':date,'pdf_hash':pdf_hash,'policy_hash':policy_hash(policy),
        'source_only':True,'production_approval':False,
        'discovery': [chart.summary() for chart in charts],
        'packages':[p.model_dump(mode='json') for p in packages],
        'validation':[validate_group(p,policy) for p in packages]}
    (out/(date+'.json')).write_text(json.dumps(output,indent=2))
    print('report',date,'groups',len(packages),'cells',sum(len(p.candidates) for p in packages),
          'errors',[(p.group.chart_id,p.group.period,validate_group(p,policy)) for p in packages
                    if validate_group(p,policy)],flush=True)
