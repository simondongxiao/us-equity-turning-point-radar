"""Render evidence page. Public summaries only; private row ledgers stay local."""
import json
import argparse
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def render(input_path=None, output_path=None):
    data=json.loads((input_path or ROOT/'site/data.json').read_text(encoding='utf-8'))
    audit=data.get('audit_upgrade',{})
    public={'run_id':data['run_id'],'as_of':data['as_of'],'audit':audit}
    encoded=json.dumps(public,ensure_ascii=False,allow_nan=False).replace('<','\\u003c')
    template=(ROOT/'assets/validation.html').read_text(encoding='utf-8')
    (output_path or ROOT/'site/validation.html').write_text(template.replace('__AUDIT_DATA__',encoded),encoding='utf-8')
    # A compact receipt can be copied to permanent public storage without prices,
    # raw chains, model weights or the full prediction ledger.
    receipt={'run_id':data['run_id'],'as_of':data['as_of'],'version':audit.get('version'),
             'source_sha256':audit.get('source_sha256'),'archive_manifest_sha256':audit.get('archive_manifest_sha256')}
    if output_path is None:
        (ROOT/'site/audit-receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--input',type=Path);p.add_argument('--output',type=Path);args=p.parse_args()
    render(args.input,args.output)
