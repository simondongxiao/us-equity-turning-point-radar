"""Interface checks only. Passing these checks is not evidence of predictive accuracy."""
import argparse
import csv
import json
import math
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
PROBS=['p_bottom','p_top','p_upfirst','p_downfirst','p_unhit','p_bottom_rebound_first','p_top_reversal_first','p_no_directional_turn','p_two_way_wash']
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
        alerts=data.get('boundary_breach_alerts')
        require(isinstance(alerts,dict) and alerts.get('status')=='observed','Production requires prior-frozen boundary breach evaluation')
        require(bool(alerts.get('basis_as_of')) and alerts['basis_as_of']<data['as_of'],'Boundary alert basis must be an earlier trading date')
        require(isinstance(alerts.get('records'),list),'Boundary alert records missing')
        for alert in alerts['records']:
            require(alert.get('horizon_sessions') in {5,10,21},'Boundary alert horizon invalid')
            require(alert.get('side') in {'below_lower','above_upper'},'Boundary alert side invalid')
            require(all(num(alert.get(key)) and alert[key]>0 for key in ('current_price','frozen_lower_bound','frozen_upper_bound','boundary_value')),'Boundary alert price invalid')
            require(alert['frozen_lower_bound']<=alert['frozen_upper_bound'],'Boundary alert bounds reversed')
            if alert['side']=='below_lower':
                require(alert['current_price']<alert['frozen_lower_bound'] and alert['boundary_value']==alert['frozen_lower_bound'] and num(alert.get('breach_pct')) and alert['breach_pct']<0,'False lower-bound breach alert')
            else:
                require(alert['current_price']>alert['frozen_upper_bound'] and alert['boundary_value']==alert['frozen_upper_bound'] and num(alert.get('breach_pct')) and alert['breach_pct']>0,'False upper-bound breach alert')
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
            directional=[m.get(k) for k in ['p_bottom_rebound_first','p_top_reversal_first','p_no_directional_turn']]
            if calibrated:
                require(all(num(x) for x in directional),f'{r["symbol"]}: missing exclusive directional-turn probabilities')
                require(abs(sum(directional)-1)<1e-6,f'{r["symbol"]}: directional-turn probabilities must sum to 1')
                require(m.get('confidence_level') in {'high','medium','low'},f'{r["symbol"]}: missing confidence tier')
                require(num(m.get('confidence_score')) and 0<=m['confidence_score']<=100,f'{r["symbol"]}: invalid confidence score')
                require(num(m.get('path_similarity_score')) and 0<=m['path_similarity_score']<=100,f'{r["symbol"]}: invalid path similarity')
                require(isinstance(m.get('regime_shift_flag'),bool),f'{r["symbol"]}: invalid regime-shift flag')
                if m.get('confidence_level')=='high':
                    require(num(m.get('validation_accuracy_lift')) and m['validation_accuracy_lift']>=.03,f'{r["symbol"]}: high confidence lacks baseline lift')
                    require(not m['regime_shift_flag'],f'{r["symbol"]}: high confidence cannot be regime-shifted')
                    require((m.get('validation_test_n') or 0)>=60 and (m.get('validation_unique_dates') or 0)>=40,f'{r["symbol"]}: high confidence lacks independent-date support')
            for key in ['opportunity_score','risk_score']:
                x=m.get(key)
                if x is not None:require(num(x) and 0<=x<=100 and calibrated,f'{r["symbol"]}: invalid {key}')
            for key in ['bottom_band','top_band','volatility_bottom_band','volatility_top_band']:
                a=m.get(key)
                if a is not None:require(isinstance(a,list) and len(a)==2 and all(num(v) and v>0 for v in a) and a[0]<=a[1],f'{r["symbol"]}: invalid {key}')
    for h,result in (data.get('backtest',{}).get('one_year_directional_turn',{}).get('horizons',{}) or {}).items():
        if result.get('status')!='observed':continue
        require(num(result.get('classification_accuracy')) and num(result.get('majority_baseline_accuracy')),f'{h}d: invalid validation accuracy')
        require(num(result.get('calibration_ece')) and 0<=result['calibration_ece']<=1,f'{h}d: invalid calibration ECE')
        curves=result.get('calibration_curve') or {}
        require(set(curves)=={'bottom_rebound_first','top_reversal_first','no_directional_turn'},f'{h}d: incomplete calibration curves')
        for event,bins in curves.items():
            require(bool(bins),f'{h}d {event}: empty calibration curve')
            for row in bins:
                require(num(row.get('predicted_mean')) and 0<=row['predicted_mean']<=1,f'{h}d {event}: invalid predicted bin')
                require(num(row.get('observed_rate')) and 0<=row['observed_rate']<=1,f'{h}d {event}: invalid observed bin')
                require(isinstance(row.get('n'),int) and row['n']>0,f'{h}d {event}: invalid bin support')
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
