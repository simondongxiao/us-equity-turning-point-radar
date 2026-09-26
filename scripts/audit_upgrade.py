"""Auditable shadow evaluation. Historical replay is never live track record."""
from __future__ import annotations
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.isotonic import IsotonicRegression

ROOT = Path(__file__).resolve().parents[1]
VERSION = "shadow-purged-joint-v2.2"
TECH = ["ret5", "ret20", "ma20", "ma50", "atr_pct", "rv20"]
CONTEXT = ["market5", "market20", "qqq5", "sox5", "vix", "beta", "residual5"]
CLASSES = ["00", "01", "10", "11"]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def freeze(path, value):
    """Exclusive creation: refuse conflicting revisions, allow identical replay."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical(value)
    try:
        with path.open("xb") as handle:
            handle.write(payload)
    except FileExistsError:
        if path.read_bytes() != payload:
            raise ValueError(f"Frozen artifact conflict: {path}")
    return hashlib.sha256(payload).hexdigest()


def read_frames(cutoff):
    result = {}
    for path in (ROOT / "data/raw_prices").glob("*.csv"):
        raw = pd.read_csv(path, index_col="date", parse_dates=True).sort_index().loc[:cutoff]
        if len(raw) < 80:
            continue
        # All OHLC must share a corporate-action basis.
        factor = raw.adj_close / raw.close
        for column in ("open", "high", "low"):
            raw[column] = raw[column] * factor
        raw["close"] = raw.adj_close
        result[path.stem] = raw
    return result


def features(frame, frames):
    c = frame.close
    ret = c.pct_change(fill_method=None)
    market = frames["SPY"].close.pct_change(fill_method=None).reindex(frame.index)
    x = pd.DataFrame(index=frame.index)
    x["atr"] = pd.concat([frame.high-frame.low, (frame.high-c.shift()).abs(), (frame.low-c.shift()).abs()], axis=1).max(axis=1).rolling(14).mean()
    x["ref"] = c
    x["ret5"], x["ret20"] = c.pct_change(5), c.pct_change(20)
    x["ma20"], x["ma50"] = c/c.rolling(20).mean()-1, c/c.rolling(50).mean()-1
    x["atr_pct"], x["rv20"] = x.atr/c, ret.rolling(20).std()*np.sqrt(252)
    x["market5"] = frames["SPY"].close.pct_change(5)
    x["market20"] = frames["SPY"].close.pct_change(20)
    x["qqq5"] = frames["QQQ"].close.pct_change(5)
    x["sox5"] = frames["^SOX"].close.pct_change(5) if "^SOX" in frames else np.nan
    x["vix"] = frames["^VIX"].close if "^VIX" in frames else np.nan
    x["beta"] = (ret.rolling(60).cov(market)/market.rolling(60).var()).shift(1).clip(-3, 5)
    x["residual5"] = (ret-x.beta*market).rolling(5).sum()
    return x


def labels(frame, x, horizon):
    """Frozen asymmetric label; stages independent, first-touch exclusive.

    Bull state uses stronger top reversal confirmation (1.5 ATR); bottom
    rebound 0.8 ATR. Other states use 1 ATR. No parameter search on test data.
    """
    result = []
    highs,lows,closes,opens=(frame[c].to_numpy() for c in ('high','low','close','open'))
    feature_rows=x.to_dict('records')
    for i in range(60, len(frame)-horizon):
        row = feature_rows[i];atr=row['atr'];ref=row['ref']
        if not np.isfinite(atr) or atr <= 0:
            continue
        low,high,close=lows[i+1:i+horizon+1],highs[i+1:i+horizon+1],closes[i+1:i+horizon+1]
        lo, hi = float(low.min()), float(high.max())
        il, ih = int(low.argmin()), int(high.argmax())
        bull = row['market20'] > 0
        bottom = lo <= ref-.75*atr and close[il:].max() >= lo+(.8 if bull else 1)*atr
        top = hi >= ref+.75*atr and close[ih:].min() <= hi-(1.5 if bull else 1)*atr
        up, down = np.flatnonzero(high >= ref+atr), np.flatnonzero(low <= ref-atr)
        u, d = (int(up[0]) if len(up) else horizon), (int(down[0]) if len(down) else horizon)
        touch = "unhit" if min(u,d)==horizon else ("ambiguous" if u==d else ("upfirst" if u<d else "downfirst"))
        result.append({**row, "date": frame.index[i], "label_end": frame.index[i+horizon], "joint": f"{int(bottom)}{int(top)}", "first_touch": touch,
                       "low_ratio": lo/ref, "high_ratio": hi/ref,
                       "net_hold": float(close[-1]/opens[i+1]-1-.0015)})
    return pd.DataFrame(result)


def fit(train, cal, cols):
    if len(train)<300 or len(cal)<100 or train.joint.nunique()<2:
        return None
    med = train[cols].median().fillna(0)
    scale = StandardScaler().fit(train[cols].fillna(med))
    model = LogisticRegression(C=.3, max_iter=400, random_state=17).fit(scale.transform(train[cols].fillna(med)), train.joint)
    raw = model.predict_proba(scale.transform(cal[cols].fillna(med)))
    calibrators = [IsotonicRegression(out_of_bounds="clip").fit(raw[:, i], (cal.joint==c).astype(int)) for i,c in enumerate(model.classes_)]
    return cols, med, scale, model, calibrators


def predict(bundle, rows):
    cols, med, scale, model, calibrators = bundle
    raw = model.predict_proba(scale.transform(rows[cols].fillna(med)))
    prob = np.column_stack([c.predict(raw[:, i]) for i,c in enumerate(calibrators)])
    empty = prob.sum(axis=1)==0
    prob[empty] = raw[empty]
    prob /= prob.sum(axis=1, keepdims=True)
    output = np.zeros((len(rows), 4))
    for i,c in enumerate(model.classes_):
        output[:, CLASSES.index(c)] = prob[:, i]
    return output


def split_at_origin(samples, origin):
    cal_start = origin-pd.DateOffset(months=6)
    train = samples[(samples.date<cal_start)&(samples.label_end<cal_start)]
    cal = samples[(samples.date>=cal_start)&(samples.date<origin)&(samples.label_end<origin)]
    return train, cal


def conditional_bands(history, probabilities, atr_pct):
    """Common four-state mixture; conditional extreme quantiles, no future test prices.

    Empirical state distributions use only matured calibration history. A fixed
    257-knot CDF approximation is recorded; this is not a technical target fit.
    """
    result={}
    for side,index,field in [('bottom',0,'low_ratio'),('top',1,'high_ratio')]:
        states=[j for j,c in enumerate(CLASSES) if c[index]=='1']
        values=[]
        for j in states:
            subset=history[history.joint==CLASSES[j]]
            v=((subset[field]-1)/subset.atr_pct).replace([np.inf,-np.inf],np.nan).dropna().to_numpy()
            values.append(np.sort(v))
        if any(len(v)<20 for v in values):
            result[side]=np.full((len(probabilities),3),np.nan);continue
        grid=np.unique(np.quantile(np.concatenate(values),np.linspace(0,1,257)))
        cdfs=np.array([np.searchsorted(v,grid,side='right')/len(v) for v in values])
        weights=probabilities[:,states];mass=weights.sum(axis=1)
        mix=(weights/np.maximum(mass[:,None],1e-12))@cdfs
        qs=np.column_stack([grid[np.argmax(mix>=q,axis=1)] for q in (.25,.5,.75)])
        bands=1+qs*np.asarray(atr_pct)[:,None]
        bands[(mass<=1e-10)|(bands[:,0]<=0)]=np.nan
        result[side]=bands
    return result


def metrics(rows):
    output = {"n":len(rows), "dates":len({r['date'] for r in rows})}
    for side in ("bottom", "top"):
        p = np.array([r['p_'+side] for r in rows]); y = np.array([r[side] for r in rows])
        bins = []
        for lo,hi in ((0,.2),(.2,.4),(.4,.6),(.6,.75),(.75,1.00001)):
            mask = (p>=lo)&(p<hi)
            bins.append({"low":lo,"high":min(hi,1),"n":int(mask.sum()),"predicted":float(p[mask].mean()) if mask.any() else None,"observed":float(y[mask].mean()) if mask.any() else None})
        high = p>=.75
        output[side] = {"brier":float(np.mean((p-y)**2)), "base_rate":float(y.mean()), "high_signal_n":int(high.sum()), "high_precision":float(y[high].mean()) if high.any() else None,"coverage":float(high.mean()),"calibration":bins}
        if side+'_band' in rows[0]:
            errors=[];centers=[];widths=[];covered=[]
            for r in rows:
                b=r[side+'_band'];actual=r['actual_'+side]
                if b is None or not r[side]:continue
                lo,mid,hi=b
                errors.append(max(lo-actual,actual-hi,0)/actual)
                centers.append(abs(mid-actual)/actual)
                widths.append(hi-lo)
                covered.append(lo<=actual<=hi)
            output[side]['conditional_band']={'n':len(errors),'band_distance_le_3pct':float(np.mean(np.array(errors)<=.03)) if errors else None,
                'median_error_le_3pct':float(np.mean(np.array(centers)<=.03)) if centers else None,
                'coverage':float(np.mean(covered)) if covered else None,'mean_width_relative_reference':float(np.mean(widths)) if widths else None,
                'method':'conditional event; 25/50/75% common-state empirical extreme distribution; width and median error prevent wide-band gaming'}
        if side=='bottom' and 'net_hold' in rows[0]:
            net=np.array([r['net_hold'] for r in rows])
            output['high_bottom_next_open_hold']={'n':int(high.sum()),'mean_net_return':float(net[high].mean()) if high.any() else None,
                'positive_return_rate':float((net[high]>0).mean()) if high.any() else None,
                'execution':'next adjusted open to horizon close; 15bp total cost; overlapping signals, no portfolio win-rate claim'}
    # Report all rows, and date-block uncertainty rather than IID stock-day CI.
    daily = pd.DataFrame(rows).groupby('date').apply(lambda g: ((g.p_bottom-g.bottom)**2+(g.p_top-g.top)**2).mean(), include_groups=False).to_numpy()
    rng = np.random.default_rng(17)
    blocks = [daily[i:i+21] for i in range(0,len(daily),21)]
    draws = [np.concatenate([blocks[i] for i in rng.integers(0,len(blocks),len(blocks))]).mean() for _ in range(300)]
    output['joint_marginal_brier_block_ci'] = np.quantile(draws,[.025,.975]).tolist()
    return output


def run_shadow(data, frames, folder):
    reports = {}; latest = {}; all_audits = []
    symbols = [r['symbol'] for r in data['records'] if r['symbol'] in frames]
    feature_map = {s:features(frames[s],frames) for s in symbols}
    for h in (5,10,21):
        samples = pd.concat([labels(frames[s],feature_map[s],h).assign(symbol=s) for s in symbols],ignore_index=True)
        samples = samples.dropna(subset=TECH+['market5','market20','qqq5','beta','residual5'])
        end = samples.date.max(); start = end-pd.DateOffset(years=1)
        results = {'technical':[], 'market_residual':[]}; folds=[]
        for k in range(4):
            origin=start+pd.DateOffset(months=3*k); until=start+pd.DateOffset(months=3*(k+1)) if k<3 else end+pd.Timedelta(days=1)
            train,cal=split_at_origin(samples,origin)
            test=samples[(samples.date>=origin)&(samples.date<until)].sort_values(['date','symbol'])
            if test.empty: continue
            audit={'horizon':h,'fold':k+1,'origin':str(origin.date()),'train_n':len(train),'cal_n':len(cal),'test_n':len(test),
                   'train_label_end':str(train.label_end.max().date()),'cal_label_end':str(cal.label_end.max().date()),'status':'observed'}
            for name,cols in [('technical',TECH),('market_residual',TECH+CONTEXT)]:
                bundle=fit(train,cal,cols)
                if bundle is None:
                    audit['status']='BLOCKED'; continue
                probabilities=predict(bundle,test[cols])
                bands=conditional_bands(cal,probabilities,test.atr_pct)
                _,med,scale,model,calibrators=bundle
                model_state={'features':cols,'medians':med.to_dict(),'scale_mean':scale.mean_.tolist(),'scale_std':scale.scale_.tolist(),
                             'classes':model.classes_.tolist(),'coef':model.coef_.tolist(),'intercept':model.intercept_.tolist(),
                             'calibrators':[{'x':c.X_thresholds_.tolist(),'y':c.y_thresholds_.tolist()} for c in calibrators]}
                audit[name+'_model_sha256']=freeze(folder/f'h{h}-fold{k+1}-{name}-model.json',model_state)
                # Prediction file has no future outcomes. Persist BEFORE reading truth.
                frozen=[{'date':str(r.date.date()),'symbol':r.symbol,'horizon':h,'p_bottom':float(p[2]+p[3]),'p_top':float(p[1]+p[3])} for r,p in zip(test[['date','symbol']].itertuples(),probabilities)]
                for i,pred in enumerate(frozen):
                    for side in ('bottom','top'):
                        b=bands[side][i]
                        pred[side+'_band']=b.tolist() if np.isfinite(b).all() else None
                sha=freeze(folder/f'h{h}-fold{k+1}-{name}-predictions.json',{'version':VERSION,'origin':audit['origin'],'rows':frozen})
                audit[name+'_prediction_sha256']=sha
                scored=[]
                for pred,truth in zip(frozen,test[['joint','label_end','net_hold','low_ratio','high_ratio']].itertuples()):
                    scored.append({**pred,'bottom':int(truth.joint[0]),'top':int(truth.joint[1]),'label_end':str(truth.label_end.date()),'net_hold':float(truth.net_hold),
                                   'actual_bottom':float(truth.low_ratio),'actual_top':float(truth.high_ratio)})
                freeze(folder/f'h{h}-fold{k+1}-{name}-settlement.json',{'prediction_sha256':sha,'rows':scored})
                results[name].extend(scored)
            folds.append(audit)
        reports[str(h)]={'folds':folds,'models':{name:metrics(rows) for name,rows in results.items() if rows}}
        # Calibrate latest shadow using only labels mature before current as_of.
        train,cal=split_at_origin(samples,pd.Timestamp(data['as_of']))
        bundle=fit(train,cal,TECH+CONTEXT)
        if bundle:
            current=pd.DataFrame([feature_map[s].iloc[-1] for s in symbols])
            probs=predict(bundle,current)
            latest[str(h)]=[{'symbol':s,'p_bottom':float(p[2]+p[3]),'p_top':float(p[1]+p[3]),'status':'shadow_only',
                            'as_of':str(feature_map[s].index[-1].date()),'reference':float(feature_map[s].iloc[-1].ref),
                            'atr_pct':float(feature_map[s].iloc[-1].atr_pct),'bull':bool(feature_map[s].iloc[-1].market20>0)} for s,p in zip(symbols,probs)]
        all_audits.extend(folds)
        print(f'[audit] {h}d frozen replay completed',flush=True)
    return {'version':VERSION,'status':'shadow_only','horizons':reports,'latest':latest,
            'index_coverage':{s:s in frames for s in ('SPY','QQQ','^SOX','^VIX')},
            'universe_status':'current_pool_conditional_selection_bias',
            'label':'asymmetric-excursion-v2: bull bottom 0.8 ATR rebound, top 1.5 ATR reversal; otherwise 1 ATR; excursion 0.75 ATR',
            'limitations':['Historical replay created now; not past live forecasts.','Current pool selection bias; no invented historical membership.','Market/residual conditional model; sector PIT layer BLOCKED pending historical membership.','No options features or promotion; first-touch production engine retained.','Labels differ from B3; compare technical versus context using same v2 labels only.']}


def run(input_path):
    data=json.loads(Path(input_path).read_text(encoding='utf-8'))
    frames=read_frames(data['as_of'])
    fingerprint=digest({'version':VERSION,'as_of':data['as_of'],'universe':[r['symbol'] for r in data['records']],
                       'code_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                       'sources':{s:hashlib.sha256(f.to_csv().encode()).hexdigest() for s,f in frames.items()}})
    folder=ROOT/'state/audit'/fingerprint[:20]
    manifest={'version':VERSION,'as_of':data['as_of'],'source_sha256':fingerprint,'kind':'retrospective_replay','universe':[r['symbol'] for r in data['records']]}
    freeze(folder/'manifest.json',manifest)
    report_path=folder/'report.json'
    report=json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else run_shadow(data,frames,folder)
    freeze(report_path,report)
    # Preserve original output; publish a separate, versioned enhanced rendering input.
    data['audit_upgrade']={**report,'source_sha256':fingerprint,'archive_manifest_sha256':digest(manifest),'as_of':data['as_of'],
                          'created_at':datetime.now(timezone.utc).isoformat(),
                          'blocked':{'historical_membership':'No dated historical constituent archive available', 'options_b4':'No continuous point-in-time chain history', 'sector_layer':'Historical issuer membership unavailable','promotion':'Not approved: requires same-target full-path validation'}}
    try:
        from frozen_ledger import archive
    except ImportError:
        from scripts.frozen_ledger import archive
    data['audit_upgrade']['ledger']=archive(data,frames)
    out=ROOT/'outputs'/f"audited-{data['run_id']}-{fingerprint[:10]}.json"
    out.write_bytes(canonical(data))
    print(str(out),flush=True)
    return out


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--input',type=Path,required=True);parser.add_argument('--output',type=Path)
    args=parser.parse_args();out=run(args.input)
    if args.output: args.output.write_bytes(out.read_bytes())
