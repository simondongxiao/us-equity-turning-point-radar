"""Render the UI template. Does not fetch prices, train models or fabricate predictions."""
from pathlib import Path
import argparse
import csv
import json
from datetime import datetime, timezone
ROOT=Path(__file__).resolve().parents[1]
def render(data: dict, target: Path) -> None:
    template=(ROOT/'assets/dashboard.html').read_text(encoding='utf-8')
    serialized=json.dumps(data,ensure_ascii=False,allow_nan=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    utils=(ROOT/'assets/sort.cjs').read_text(encoding='utf-8')
    html=template.replace('__RADAR_DATA__',serialized).replace('__SORT_UTILS__',utils)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(html,encoding='utf-8')
def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input',type=Path,help='Actual data JSON, otherwise render explicit uncalibrated seed preview')
    p.add_argument('--output',type=Path,default=ROOT/'preview.html')
    args=p.parse_args()
    if args.input:
        data=json.loads(args.input.read_text(encoding='utf-8'))
    else:
        with (ROOT/'assets/universe_seed.csv').open(encoding='utf-8-sig',newline='') as f: seeds=list(csv.DictReader(f))
        records=[]
        for seed in seeds:
            for field in ['business_tags','industry_tags']:
                seed[field]=[v for v in seed.get(field,'').split('|') if v]
            seed['peer_context']=None
            records.append({**seed,'as_of':None,'reference_price':None,'stage':'待分析','potential_ranges':{},'metrics':{str(h):{'status':'uncalibrated','opportunity_value':None,'risk_value':None,'opportunity_score':None,'risk_score':None,'bottom_band':None,'top_band':None,'p_bottom':None,'p_top':None,'p_upfirst':None,'p_downfirst':None,'p_unhit':None,'expected_return':None,'es95':None,'positive_edge':None} for h in [5,10,21]}})
        data={'build_mode':'preview','generated_at':datetime.now(timezone.utc).isoformat(),'as_of':None,'run_id':'seed-ui-preview-not-a-forecast','model_version':None,'potential_range_version':'three-layer-descriptive-v1','universe_version':'curated-seed-20260915-v1.1','taxonomy_version':'research-taxonomy-20260915-v1.1','research_taxonomy':json.loads((ROOT/'assets/research_taxonomy.json').read_text(encoding='utf-8')),'storage_rotation':None,'public_config':{'api_base_url':None},'records':records,'temporary':[]}
        (ROOT/'assets/preview-data.json').write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    render(data,args.output)
    print(f'Rendered {args.output}; mode={data.get("build_mode")}; this command does not calculate financial predictions.')
if __name__=='__main__':main()
