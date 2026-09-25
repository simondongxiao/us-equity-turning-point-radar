"""Index targets through the shared labeling, training and scenario engine."""
import pandas as pd

VERSION = 'index-joint-turning-purged-v1'


def build_index_forecasts(engine, frames, context, as_of):
    results = []
    for row in context['rows']:
        symbol = row['symbol']
        record = {**row, 'model_version': VERSION, 'metrics': {}, 'validation': {},
                  'label_version': 'atr-excursion-0.75-reversal-1.0-v1',
                  'note': '分别计算未来窗口内下探后反转、上冲后反转；允许同窗同时发生。不是精确极值命中率，也不是交易胜率。指数模型独立训练与校准，不占100股名额。'}
        raw = frames.get(symbol)
        if raw is None or as_of not in raw.index:
            record['metrics'] = {str(h):{'status':'data_error'} for h in engine.HORIZONS}
            results.append(record)
            continue
        frame = raw.loc[:as_of].copy()
        if row['kind'] != 'etf_proxy':
            frame['adj_close'] = frame['close']
        else:
            factor = frame['adj_close'] / frame['close']
            for field in ('open','high','low'):
                frame[field] *= factor
        market = frames['SPY']['adj_close'].loc[:as_of]
        features = engine.add_features(frame, market, market, None)
        features['qqq_rs_5'] = features['ret_5'] - frames['QQQ']['adj_close'].loc[:as_of].pct_change(5)
        feature_names = engine.TECHNICAL_FEATURES  # same technical engine, own-asset calibration
        for horizon in engine.HORIZONS:
            samples = engine.label_paths(features, horizon).join(engine.path_summary_frame(features, horizon))
            samples['date'] = samples.index
            samples['label_end'] = pd.Series(samples.index, index=samples.index).shift(-horizon)
            samples['symbol'] = symbol
            excluded_ambiguous = int(samples['ambiguous'].sum())
            samples = samples.dropna(subset=['label','label_end','terminal_return','min_price','max_price']).reset_index(drop=True)
            bundle = engine.train_bundle(samples, feature_names, horizon, strict_time=True)
            joint = samples.copy()
            joint['label'] = joint['bottom_event'].astype(int).astype(str) + joint['top_event'].astype(int).astype(str)
            event_bundle = engine.train_bundle(joint, feature_names, horizon, strict_time=True)
            if bundle is None or event_bundle is None:
                metric = {'status':'uncalibrated', 'reason':'insufficient chronological training/calibration/test support'}
            else:
                bundle.test_metrics['ambiguous_excluded'] = excluded_ambiguous
                event_bundle.test_metrics['ambiguous_excluded'] = excluded_ambiguous
                metric = engine.build_scenario_metric(symbol, features.loc[as_of], samples, bundle, horizon, features,
                                                       event_bundle=event_bundle, relative_atr=True)
                metric['model_version'] = VERSION
                for side in ('bottom', 'top'):
                    if metric.get('p_'+side, 0) <= 1e-8:
                        metric[side+'_band'] = None
                # Indices and VIX spot are not directly executable instruments.
                for field in ('opportunity_value','expected_return','mu_lcb','cost_assumption'):
                    metric.pop(field, None)
                record['validation'][str(horizon)] = {'first_touch':bundle.test_metrics, 'joint_turning':event_bundle.test_metrics,
                    'limitations':'single chronological holdout; overlapping daily outcomes are correlated; no 70% claim; ambiguous same-bar paths excluded'}
            record['metrics'][str(horizon)] = metric
        results.append(record)
        print(f'[radar] index forecast {symbol} ready', flush=True)
    return {'model_version':VERSION, 'as_of':str(as_of.date()), 'records':results,
            'status':'independently_trained_low_confidence',
            'note':'每个指数单独训练、校准、测试；顶与底为四态联合事件的独立边际，概率和价带来自同一组重加权路径。'}
