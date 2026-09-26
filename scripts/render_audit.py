"""Render evidence page. Public summaries only; private row ledgers stay local."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def render():
    data=json.loads((ROOT/'site/data.json').read_text(encoding='utf-8'))
    audit=data.get('audit_upgrade',{})
    public={'run_id':data['run_id'],'as_of':data['as_of'],'audit':audit}
    encoded=json.dumps(public,ensure_ascii=False,allow_nan=False).replace('<','\\u003c')
    template=(ROOT/'assets/validation.html').read_text(encoding='utf-8')
    (ROOT/'site/validation.html').write_text(template.replace('__AUDIT_DATA__',encoded),encoding='utf-8')
    # A compact receipt can be copied to permanent public storage without prices,
    # raw chains, model weights or the full prediction ledger.
    receipt={'run_id':data['run_id'],'as_of':data['as_of'],'version':audit.get('version'),
             'source_sha256':audit.get('source_sha256'),'archive_manifest_sha256':audit.get('archive_manifest_sha256')}
    (ROOT/'site/audit-receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')

if __name__=='__main__':render()
