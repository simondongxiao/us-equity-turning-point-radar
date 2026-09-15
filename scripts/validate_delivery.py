"""Interface checks only. Passing these checks is not evidence of predictive accuracy."""
import argparse
import csv
import json
import math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PROBS=['p_bottom','p_top','p_upfirst','p_downfirst','p_unhit']
CALIBRATED={'calibrated','calibrated_low_confidence'}
def require(test, message):
    if not test: raise ValueError(message)
def num(x):return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)
def validate_seed(path):
    with path.open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
    require(len(rows)==100,'Seed must contain exactly 100 rows')
    require(len({r['symbol'] for r in rows})==100,'Duplicate symbols')
    require(sum(r['coverage_bucket']=='科技与成长主题' for r in rows)==60,'Expected 60 priority seeds')
    require(sum(r['coverage_bucket']=='非科技主题' for r in rows)==40,'Expected 40 non-tech seeds')
    require(not {'GOOG','GOOGL'}.issubset({r['symbol'] for r in rows}),'Duplicate Alphabet issuer')
    storage={'MU','SNDK','WDC','STX'}
    require({r['symbol'] for r in rows if r.get('research_group_id')=='storage-memory'}==storage,'Storage primary group must contain the four original seeds')
    require(all(r['research_group']=='存储与内存' for r in rows if r['symbol'] in storage),'Storage display label mismatch')
    require(sum(r['research_group']=='半导体与设备' for r in rows)==13,'Expected 13 remaining semiconductor seeds')
    require(sum(r['research_group']=='算力网络与光通信' for r in rows)==7,'Expected 7 remaining infrastructure seeds')
    lookup={r['symbol']:r for r in rows}
    expected={'MU':{'DRAM','HBM','NAND','SSD'},'SNDK':{'NAND','SSD'},'WDC':{'HDD'},'STX':{'HDD','HAMR'}}
    for sym,tags in expected.items():require(set(lookup[sym]['business_tags'].split('|'))==tags,f'{sym} business tag mismatch')
    return len(rows)
def validate_dashboard(data, production=False):
    if production:
        require(data.get('build_mode')=='live','Preview cannot pass production check')
        require(bool(data.get('as_of')) and bool(data.get('model_version')),'Missing live data/model timestamps')
    rows=data.get('records',[])
    require(len(rows)==100,'Regular board must retain 100 members or be explicitly handled as failed/incomplete before this production interface')
    require(len({r['symbol'] for r in rows})==len(rows),'Duplicate regular symbols')
    for r in rows+data.get('temporary',[]):
        for h in ['5','10','21']:
            m=r.get('metrics',{}).get(h)
            require(isinstance(m,dict),f'{r["symbol"]} missing {h} day result/status')
            calibrated=m.get('status') in CALIBRATED
            for key in PROBS:
                x=m.get(key)
                if x is not None:
                    require(num(x) and 0<=x<=1,f'{r["symbol"]}: invalid {key}')
                    require(calibrated,f'{r["symbol"]}: uncalibrated probability must not be exposed as a valid prediction')
            triple=[m.get(k) for k in ['p_upfirst','p_downfirst','p_unhit']]
            if any(x is not None for x in triple):
                require(all(num(x) for x in triple),f'{r["symbol"]}: incomplete competing-risk probabilities')
                require(abs(sum(triple)-1)<1e-6,f'{r["symbol"]}: competing risks must sum to 1')
            for key in ['opportunity_score','risk_score']:
                x=m.get(key)
                if x is not None:require(num(x) and 0<=x<=100 and calibrated,f'{r["symbol"]}: invalid {key}')
            for key in ['bottom_band','top_band']:
                a=m.get(key)
                if a is not None:require(isinstance(a,list) and len(a)==2 and all(num(v) and v>0 for v in a) and a[0]<=a[1],f'{r["symbol"]}: invalid {key}')
    return len(rows)
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--seed',type=Path,default=ROOT/'assets/universe_seed.csv')
    p.add_argument('--dashboard',type=Path)
    p.add_argument('--production',action='store_true')
    args=p.parse_args()
    count=validate_seed(args.seed)
    print(f'PASS: {count} unique curated seeds; identity/liquidity NOT verified by this test.')
    if args.dashboard:
        data=json.loads(args.dashboard.read_text(encoding='utf-8'),parse_constant=lambda x:(_ for _ in ()).throw(ValueError('Nonfinite JSON')))
        n=validate_dashboard(data,args.production)
        print(f'PASS: {n} record interface; financial model accuracy NOT evaluated by this test.')
if __name__=='__main__':main()
