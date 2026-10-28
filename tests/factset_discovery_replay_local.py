"""Real PDF discovery evidence only: this output is not independent golden data."""
from datetime import datetime, timezone
from pathlib import Path
import json
from ats.data.sources.factset_earnings_insight import FactSetFetch, inspect_pdf, STABLE_URL
from ats.data.sources.factset_report_layout import discover_charts, load_layout_policy, policy_hash

paths = [
    'var/data_artifacts/ae/ae262138891244678730deaaef8607466b919f058038578fbea3b5454d95f381.bin',
    'var/data_artifacts/f6/f679661e1aaf0dc3eaa181e7fb44230a4c5c9c98ac6bea46eeea2af52edecc08.bin',
]
policy=load_layout_policy()
for path in paths:
    source=FactSetFetch(STABLE_URL,STABLE_URL,200,'','','application/pdf',Path(path).read_bytes(),datetime.now(timezone.utc))
    doc=inspect_pdf(source)
    results=discover_charts(doc,policy=policy)
    output={'pdf_hash':doc.pdf_hash,'policy_hash':policy_hash(policy),
            'report_date':str(doc.report_date),'discovery_only':True,
            'images':[r.summary() for r in results]}
    out=Path('tests/fixtures/factset_earnings_insight/discovery')
    out.mkdir(parents=True,exist_ok=True)
    (out/f'{doc.report_date}.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report_date':str(doc.report_date), 'policy_hash':output['policy_hash'],
                      'charts':[r.summary() for r in results if r.groups]},ensure_ascii=False),flush=True)
