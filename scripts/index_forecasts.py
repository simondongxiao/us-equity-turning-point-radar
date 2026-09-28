"""Index targets through the shared labeling, training and scenario engine."""
import pandas as pd

VERSION = 'index-exclusive-directional-turn-purged-v2'


def build_index_forecasts(engine, frames, context, as_of):
    results = []
    for row in context['rows']:
        symbol = row['symbol']
        record = {**row, 'model_version': VERSION, 'metrics': {}, 'validation': {},
                  'label_version': 'atr-excursion-0.75-reversal-1.0-v1',
                  'note': '方向性阶段底、阶段顶与无有效拐点按首达边界及同窗反转确认做互斥三分类；原同窗顶底事件只作双向洗盘诊断。不是精确极值命中率，也不是交易胜率。指数模型独立训练与校准，不占100股名额。'}
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
            samples = engine.add_directional_turn_label(samples)
            samples['date'] = samples.index
            samples['label_end'] = pd.Series(samples.index, index=samples.index).shift(-horizon)
            samples['symbol'] = symbol
            excluded_ambiguous = int(samples['ambiguous'].sum())
            samples = samples.dropna(subset=['label','label_end','terminal_return','min_price','max_price']).reset_index(drop=True)
            bundle = engine.train_bundle(samples, feature_names, horizon, strict_time=True)
            directional_bundle = engine.train_bundle(samples, feature_names, horizon, strict_time=True, target_col='turn_label')
            joint = samples.copy()
            joint['label'] = joint['bottom_event'].astype(int).astype(str) + joint['top_event'].astype(int).astype(str)
            event_bundle = engine.train_bundle(joint, feature_names, horizon, strict_time=True)
            if bundle is None or event_bundle is None or directional_bundle is None:
                metric = {'status':'uncalibrated', 'reason':'insufficient chronological training/calibration/test support'}
            else:
                bundle.test_metrics['ambiguous_excluded'] = excluded_ambiguous
                event_bundle.test_metrics['ambiguous_excluded'] = excluded_ambiguous
                metric = engine.build_scenario_metric(symbol, features.loc[as_of], samples, bundle, horizon, features,
                                                       event_bundle=event_bundle, relative_atr=True,
                                                       directional_bundle=directional_bundle)
                metric['model_version'] = VERSION
                for side in ('bottom', 'top'):
                    if metric.get('p_'+side, 0) <= 1e-8:
                        metric[side+'_band'] = None
                # Indices and VIX spot are not directly executable instruments.
                for field in ('opportunity_value','expected_return','mu_lcb','cost_assumption'):
                    metric.pop(field, None)
                record['validation'][str(horizon)] = {'first_touch':bundle.test_metrics, 'joint_turning_diagnostic':event_bundle.test_metrics,
                    'directional_turn_exclusive':directional_bundle.test_metrics,
                    'limitations':'single chronological holdout; overlapping daily outcomes are correlated; no 70% claim; ambiguous same-bar paths excluded'}
            record['metrics'][str(horizon)] = metric
        results.append(record)
        print(f'[radar] index forecast {symbol} ready', flush=True)
    return {'model_version':VERSION, 'as_of':str(as_of.date()), 'records':results,
            'status':'independently_trained_low_confidence',
            'note':'每个指数单独训练、校准、测试；方向性阶段底/顶/无有效拐点互斥并归一，原四态联合事件仅保留为双向洗盘诊断。'}
