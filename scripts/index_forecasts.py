"""Index targets through the shared labeling, training and scenario engine."""
import pandas as pd

VERSION = 'index-exclusive-directional-turn-purged-v3-leveraged-etf'
ETF_KINDS = {'etf_proxy', 'leveraged_etf'}


def prepare_asset_frame(raw, kind):
    frame = raw.copy()
    if kind not in ETF_KINDS:
        frame['adj_close'] = frame['close']
        return frame
    factor = frame['adj_close'] / frame['close']
    for field in ('open', 'high', 'low'):
        frame[field] *= factor
    frame['close'] = frame['adj_close']
    return frame


def build_index_forecasts(engine, frames, context, as_of):
    results = []
    for row in context['rows']:
        symbol = row['symbol']
        leveraged = row.get('kind') == 'leveraged_etf'
        record = {**row, 'model_version': VERSION, 'metrics': {}, 'validation': {},
                  'label_version': 'atr-excursion-0.75-reversal-1.0-v1',
                  'note': ('杠杆/反向ETF按自身点时复权OHLC独立训练，不把基准指数概率乘以杠杆倍数或镜像；每日重置、复利衰减、融资费用与跳空风险使多日表现可能显著偏离每日目标。' if leveraged else '') + '方向性阶段底、阶段顶与无有效拐点按首达边界及同窗反转确认做互斥三分类；原同窗顶底事件只作双向洗盘诊断。不是精确极值命中率，也不是交易胜率。各品种独立训练与校准，不占100股名额。'}
        raw = frames.get(symbol)
        if raw is None or as_of not in raw.index:
            record['metrics'] = {str(h):{'status':'data_error'} for h in engine.HORIZONS}
            results.append(record)
            continue
        frame = prepare_asset_frame(raw.loc[:as_of], row['kind'])
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
                # This panel reports directional structure only. Even tradable ETFs do not
                # receive an unvalidated net-return/opportunity score here.
                for field in ('opportunity_value','expected_return','mu_lcb','cost_assumption'):
                    metric.pop(field, None)
                record['validation'][str(horizon)] = {'first_touch':bundle.test_metrics, 'joint_turning_diagnostic':event_bundle.test_metrics,
                    'directional_turn_exclusive':directional_bundle.test_metrics,
                    'limitations':'single chronological holdout; overlapping daily outcomes are correlated; no 70% claim; ambiguous same-bar paths excluded'}
            record['metrics'][str(horizon)] = metric
        results.append(record)
        print(f'[radar] index forecast {symbol} ready', flush=True)
    return {'model_version':VERSION, 'as_of':str(as_of.date()), 'records':results,
            'status':'independently_trained_confidence_tiered',
            'note':'每个指数或ETF均按自身历史单独训练、校准、测试；杠杆/反向ETF不由基准概率倍乘或镜像。方向性阶段底/顶/无有效拐点互斥并归一，原四态联合事件仅保留为双向洗盘诊断。信度按品种自身路径支持与独立测试分级，不改变概率。'}
