"""Append-only local prediction snapshots and separate mature settlements."""
import json
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
try:
    from audit_upgrade import ROOT, digest, freeze, features, labels
except ImportError:
    from scripts.audit_upgrade import ROOT, digest, freeze, features, labels


def archive(data, frames):
    now=datetime.now(timezone.utc).isoformat()
    root=ROOT/'state/frozen'
    run_id=data['run_id']
    if not run_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for c in run_id):
        raise ValueError('invalid run id')
    source={s: digest({'csv':f.to_csv()}) for s,f in frames.items()}
    snapshot={'schema':'frozen-predictions-v2','run_id':run_id,'as_of':data['as_of'],
              'generated_at':data.get('generated_at'),'model_version':data['model_version'],
              'feature_version':data.get('feature_version'),'universe_version':data.get('universe_version'),
              'source_hashes':source,'records':data['records'],'temporary':data.get('temporary',[]),
              'indices':data.get('index_forecasts',{}).get('records',[])}
    path=root/'predictions'/f'{run_id}.json'
    # Old run IDs already stored are read, never rebuilt from revised price data.
    if path.exists():
        frozen=json.loads(path.read_text(encoding='utf-8'))
        if frozen['records'] != snapshot['records'] or frozen['model_version']!=snapshot['model_version']:
            raise ValueError('existing run prediction changed')
        sha=digest(frozen)
    else:
        sha=freeze(path,snapshot)
        freeze(root/'receipts'/f'{run_id}.json',{'run_id':run_id,'archived_at':now,'snapshot_sha256':sha,
               'kind':'imported_existing_run' if data.get('generated_at','')[:10]<now[:10] else 'current_run_archive'})
        # Membership is known when collected. Do not backdate to price as_of.
        freeze(root/'memberships'/f'{run_id}.json',{'known_at':now,'price_as_of':data['as_of'],
               'members':[{k:r.get(k) for k in ('symbol','issuer_id','research_group_id','business_tags')} for r in data['records']]})
        freeze(root/'options'/f'{run_id}.json',{'archived_at':now,'records':[{'symbol':r['symbol'],'snapshot':r.get('potential_ranges')} for r in data['records']]})
    summaries=[]
    shadow=data.get('audit_upgrade',{})
    if shadow.get('latest'):
        shadow_path=root/'shadow'/f"{run_id}-{shadow['version']}.json"
        payload={'version':shadow['version'],'run_id':run_id,'forecasts':shadow['latest'],
                 'label':shadow['label'],'source_sha256':shadow['source_sha256']}
        freeze(shadow_path,payload)
    shadow_settled=0
    for saved in sorted((root/'shadow').glob('*.json')):
        prediction=json.loads(saved.read_text(encoding='utf-8'))
        for horizon, forecasts in prediction['forecasts'].items():
            for row in forecasts:
                frame=frames.get(row['symbol']);date=pd.Timestamp(row['as_of']);h=int(horizon)
                if frame is None or date not in frame.index:continue
                i=frame.index.get_loc(date)
                if len(frame)<=i+h:continue
                # Future ratios use same refreshed corporate-action basis as as_of;
                # frozen ATR/ref ratio remains unchanged across splits.
                future=frame.iloc[i+1:i+h+1]/float(frame.iloc[i].close)
                lo,hi=float(future.low.min()),float(future.high.max());atr=row['atr_pct']
                il,ih=int(future.low.argmin()),int(future.high.argmax())
                bottom=bool(lo<=1-.75*atr and future.close.iloc[il:].max()>=lo+(.8 if row['bull'] else 1)*atr)
                top=bool(hi>=1+.75*atr and future.close.iloc[ih:].min()<=hi-(1.5 if row['bull'] else 1)*atr)
                target=root/'shadow-settlements'/f"{saved.stem}-{row['symbol']}-{h}.json"
                if not target.exists():freeze(target,{'prediction_sha256':digest(prediction),'bottom':bottom,'top':top,'label_end':str(future.index[-1].date()),'source_sha256':source[row['symbol']]})
                shadow_settled+=1
    for saved in sorted((root/'predictions').glob('*.json')):
        frozen=json.loads(saved.read_text(encoding='utf-8'))
        receipt=json.loads((root/'receipts'/saved.name).read_text(encoding='utf-8'))
        settled=0; pending=0; unavailable=0
        for record in frozen['records']+frozen.get('temporary',[]):
            frame=frames.get(record['symbol']); date=pd.Timestamp(record.get('as_of') or frozen['as_of'])
            if frame is None or date not in frame.index:
                unavailable+=3;continue
            # Settle original prediction against extrema only. Event labels require
            # original frozen ATR and label version, unavailable in legacy runs.
            i=frame.index.get_loc(date)
            for h in (5,10,21):
                if len(frame)<=i+h:
                    pending+=1;continue
                outcome_path=root/'settlements'/f"{frozen['run_id']}-{record['symbol']}-{h}.json"
                future=frame.iloc[i+1:i+h+1]
                outcome={'prediction_sha256':receipt['snapshot_sha256'],'run_id':frozen['run_id'],'symbol':record['symbol'],'horizon':h,
                         'label_end':str(future.index[-1].date()),'actual_low':float(future.low.min()),'actual_high':float(future.high.max()),
                         'event_evaluation':'BLOCKED: legacy ATR and label version not frozen',
                         'settlement_source_sha256':source[record['symbol']]}
                if not outcome_path.exists(): freeze(outcome_path,outcome)
                settled+=1
        summaries.append({'run_id':frozen['run_id'],'as_of':frozen['as_of'],'archived_at':receipt['archived_at'],'kind':receipt['kind'],
                          'sha256':receipt['snapshot_sha256'],'settled':settled,'pending':pending,'unavailable':unavailable})
    return {'runs':summaries,'shadow_settled':shadow_settled,'storage':'local append-only snapshots; authenticated encrypted release archive, restored before each cloud build',
            'integrity':'SHA256 receipts detect revisions; not a trusted timestamp or WORM guarantee',
            'event_settlement':'legacy runs cannot reconstruct frozen ATR/label; no event win-rate claim'}
