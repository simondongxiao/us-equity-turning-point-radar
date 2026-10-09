"""Index targets through the shared labeling, training and scenario engine."""
from datetime import date, timedelta
import math

import pandas as pd

VERSION = 'index-exclusive-directional-turn-purged-v3-leveraged-etf'
ETF_KINDS = {'etf_proxy', 'leveraged_etf'}
STRUCTURE_MATRIX_VERSION = 'index-structure-matrix-v1.4.0'
HORIZON_LABELS = {5: '1周', 10: '2周', 21: '约1月'}
WIDE_RANGE_THRESHOLDS = {5: 0.12, 10: 0.18, 21: 0.30}


def _observed_holiday(day):
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _nth_weekday(year, month, weekday, n):
    day = date(year, month, 1)
    return day + timedelta(days=(weekday - day.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year, month, weekday):
    boundary = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    day = boundary - timedelta(days=1)
    return day - timedelta(days=(day.weekday() - weekday) % 7)


def _easter_sunday(year):
    """Gregorian Easter date; used only to derive the regular Good Friday closure."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = (h + ell - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def _nyse_regular_holidays(year):
    holidays = {
        _observed_holiday(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _easter_sunday(year) - timedelta(days=2),
        _last_weekday(year, 5, 0),
        _observed_holiday(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _observed_holiday(date(year, 12, 25)),
    }
    if year >= 2022:
        holidays.add(_observed_holiday(date(year, 6, 19)))
    holidays.add(_observed_holiday(date(year + 1, 1, 1)))
    return holidays


def nyse_horizon_end(as_of, sessions):
    """Expected regular-session end date; unplanned exchange closures require a rerun."""
    current = pd.Timestamp(as_of).date()
    holidays = set()
    last_year = (current + timedelta(days=sessions * 3 + 20)).year
    for year in range(current.year, last_year + 2):
        holidays.update(_nyse_regular_holidays(year))
    completed = 0
    while completed < sessions:
        current += timedelta(days=1)
        if current.weekday() < 5 and current not in holidays:
            completed += 1
    return current.isoformat()


def _finite(value):
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _valid_band(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2 or not all(_finite(v) and float(v) > 0 for v in value):
        return None
    return sorted([float(value[0]), float(value[1])])


def _pct_band(value, anchor):
    band = _valid_band(value)
    if band is None or not _finite(anchor) or float(anchor) <= 0:
        return None
    return [(point / float(anchor)) - 1 for point in band]


def _distance_to_band(anchor, value):
    band = _valid_band(value)
    if band is None or not _finite(anchor) or float(anchor) <= 0:
        return None
    anchor = float(anchor)
    if band[0] <= anchor <= band[1]:
        return 0.0
    return min(abs(anchor - band[0]), abs(anchor - band[1])) / anchor


def _pressure_zones(metric, anchor, bottom, top):
    if not _finite(anchor) or float(anchor) <= 0:
        return None, None
    anchor = float(anchor)
    p05 = anchor * (1 + float(metric['expected_return_p05'])) if _finite(metric.get('expected_return_p05')) else None
    p95 = anchor * (1 + float(metric['expected_return_p95'])) if _finite(metric.get('expected_return_p95')) else None
    lower_candidates = [bottom[0]] if bottom else []
    upper_candidates = [top[1]] if top else []
    for value in (metric.get('terminal_p10'), p05):
        if _finite(value) and float(value) > 0:
            lower_candidates.append(float(value))
    for value in (metric.get('terminal_p90'), p95):
        if _finite(value) and float(value) > 0:
            upper_candidates.append(float(value))
    pressure_bottom = [min(lower_candidates), bottom[0]] if bottom and lower_candidates else None
    pressure_top = [top[1], max(upper_candidates)] if top and upper_candidates else None
    return pressure_bottom, pressure_top


def _location_state(anchor, bottom, top, pressure_bottom, pressure_top):
    if not _finite(anchor):
        return 'unavailable', '数据不可用'
    anchor = float(anchor)
    in_bottom = bool(bottom and bottom[0] <= anchor <= bottom[1])
    in_top = bool(top and top[0] <= anchor <= top[1])
    if in_bottom and in_top:
        return 'core_zones_overlap', '核心底顶区重叠，双向波动'
    if in_bottom:
        return 'in_core_bottom', '已进入核心条件底区'
    if in_top:
        return 'in_core_top', '已进入核心条件顶区'
    if pressure_bottom and anchor < pressure_bottom[0]:
        return 'below_lower_pressure', '已跌破下方压力扩展，原矩阵失效'
    if pressure_bottom and pressure_bottom[0] <= anchor < pressure_bottom[1]:
        return 'in_lower_pressure', '处于核心底区下方的尾部压力扩展'
    if pressure_top and anchor > pressure_top[1]:
        return 'above_upper_pressure', '已突破上方压力扩展，原矩阵失效'
    if pressure_top and pressure_top[0] < anchor <= pressure_top[1]:
        return 'in_upper_pressure', '处于核心顶区上方的尾部压力扩展'
    bottom_distance = _distance_to_band(anchor, bottom)
    top_distance = _distance_to_band(anchor, top)
    if bottom_distance is not None and bottom_distance <= 0.015:
        return 'near_core_bottom', '接近核心条件底区'
    if top_distance is not None and top_distance <= 0.015:
        return 'near_core_top', '接近核心条件顶区'
    return 'between_core_zones', '位于核心底顶区之间'


def build_structure_matrix_record(row, metric, horizon, as_of):
    anchor = row.get('level')
    ready = metric.get('status') in {'calibrated', 'calibrated_low_confidence'}
    metric_bottom = _valid_band(metric.get('bottom_band'))
    metric_top = _valid_band(metric.get('top_band'))
    bottom = metric_bottom or _valid_band(metric.get('volatility_bottom_band'))
    top = metric_top or _valid_band(metric.get('volatility_top_band'))
    bottom_source = 'turning_point_conditional_path' if metric_bottom else ('atr_volatility_fallback' if bottom else 'unavailable')
    top_source = 'turning_point_conditional_path' if metric_top else ('atr_volatility_fallback' if top else 'unavailable')
    pressure_bottom, pressure_top = _pressure_zones(metric, anchor, bottom, top)
    location_state, location_label = _location_state(anchor, bottom, top, pressure_bottom, pressure_top)
    probabilities = {
        'bottom_rebound_first': metric.get('p_bottom_rebound_first'),
        'top_reversal_first': metric.get('p_top_reversal_first'),
        'no_directional_turn': metric.get('p_no_directional_turn'),
    }
    valid_probabilities = {key: float(value) for key, value in probabilities.items() if _finite(value)}
    dominant_key = max(valid_probabilities, key=valid_probabilities.get) if ready and valid_probabilities else None
    dominant_probability = valid_probabilities.get(dominant_key) if dominant_key else None
    dominant_labels = {
        'bottom_rebound_first': '方向模型偏阶段底',
        'top_reversal_first': '方向模型偏阶段顶',
        'no_directional_turn': '方向模型偏无有效拐点',
    }
    lower = pressure_bottom[0] if pressure_bottom else (bottom[0] if bottom else None)
    upper = pressure_top[1] if pressure_top else (top[1] if top else None)
    width = (upper - lower) / float(anchor) if _finite(anchor) and float(anchor) > 0 and _finite(lower) and _finite(upper) else None
    wide = bool(width is not None and width >= WIDE_RANGE_THRESHOLDS[horizon])
    probability_text = f"{dominant_labels.get(dominant_key, '方向模型不可用')} {dominant_probability:.1%}" if dominant_probability is not None else '方向模型不可用'
    confidence_label = metric.get('confidence_label') or '信度未评估'
    main_judgment = f"{probability_text}；{location_label}；{confidence_label}。"
    if wide:
        main_judgment += ' 尾部压力范围较宽，仅用于风控分层，不应直接据此挂单。'
    if dominant_key == 'bottom_rebound_first':
        trigger = f'触及核心条件底区后，须在同一{horizon}交易日窗口完成模型定义的反弹确认。'
    elif dominant_key == 'top_reversal_first':
        trigger = f'触及核心条件顶区后，须在同一{horizon}交易日窗口完成模型定义的回落确认。'
    else:
        trigger = '等待方向模型与价位触达形成一致证据；价位本身不构成确认。'
    invalidation_parts = []
    if _finite(lower):
        invalidation_parts.append(f'下破{float(lower):,.2f}')
    if _finite(upper):
        invalidation_parts.append(f'上破{float(upper):,.2f}')
    invalidation = ('或'.join(invalidation_parts) + '后原矩阵重算；') if invalidation_parts else '价位数据不足；'
    invalidation += '跳空或新事件出现时同样失效。'
    return {
        'symbol': row.get('symbol'), 'name': row.get('name'), 'kind': row.get('kind'),
        'status': 'observed' if ready and bottom and top else 'unavailable',
        'horizon_sessions': horizon, 'horizon_label': HORIZON_LABELS[horizon],
        'horizon_end_date': nyse_horizon_end(as_of, horizon),
        'calendar_method': 'nyse_regular_session_calendar_v1_excludes_unplanned_closures',
        'anchor_price': float(anchor) if _finite(anchor) else None,
        'anchor_as_of': str(pd.Timestamp(as_of).date()), 'anchor_unit': row.get('unit'),
        'core_bottom_zone': bottom, 'core_top_zone': top,
        'core_bottom_source': bottom_source, 'core_top_source': top_source,
        'pressure_bottom_zone': pressure_bottom, 'pressure_top_zone': pressure_top,
        'pressure_definition': 'conditional-turn core extended to the lower/upper of terminal P10/P90 and net-return P05/P95; terminal-tail risk display, not an intraday extreme guarantee',
        'core_bottom_relative_to_anchor_pct': _pct_band(bottom, anchor),
        'core_top_relative_to_anchor_pct': _pct_band(top, anchor),
        'pressure_bottom_relative_to_anchor_pct': _pct_band(pressure_bottom, anchor),
        'pressure_top_relative_to_anchor_pct': _pct_band(pressure_top, anchor),
        'distance_to_core_bottom_pct': _distance_to_band(anchor, bottom),
        'distance_to_core_top_pct': _distance_to_band(anchor, top),
        'price_location_state': location_state, 'price_location_label': location_label,
        'dominant_direction': dominant_key, 'dominant_probability': dominant_probability,
        'confidence_level': metric.get('confidence_level'), 'confidence_label': confidence_label,
        'range_width_pct': width, 'wide_range_flag': wide,
        'trigger_condition': trigger, 'invalidation_condition': invalidation,
        'main_judgment': main_judgment,
        'event_status': 'not_ingested', 'event_source': None, 'event_observed_at': None,
        'event_note': '本层未接入已核验事件台账；缺失不视为中性，也不改写方向概率。',
        'semantic_note': '核心条件区、尾部压力扩展和相对锚点涨跌都是价格区间，不是方向概率、胜率或收益承诺。',
    }


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
    structure_matrix = []
    for row in context['rows']:
        symbol = row['symbol']
        leveraged = row.get('kind') == 'leveraged_etf'
        record = {**row, 'model_version': VERSION, 'metrics': {}, 'validation': {},
                  'label_version': 'atr-excursion-0.75-reversal-1.0-v1',
                  'note': ('杠杆/反向ETF按自身点时复权OHLC独立训练，不把基准指数概率乘以杠杆倍数或镜像；每日重置、复利衰减、融资费用与跳空风险使多日表现可能显著偏离每日目标。' if leveraged else '') + '方向性阶段底、阶段顶与无有效拐点按首达边界及同窗反转确认做互斥三分类；原同窗顶底事件只作双向洗盘诊断。不是精确极值命中率，也不是交易胜率。各品种独立训练与校准，不占100股名额。'}
        raw = frames.get(symbol)
        if raw is None or as_of not in raw.index:
            record['metrics'] = {str(h):{'status':'data_error'} for h in engine.HORIZONS}
            for horizon in engine.HORIZONS:
                structure_matrix.append(build_structure_matrix_record(record, record['metrics'][str(horizon)], horizon, as_of))
            results.append(record)
            continue
        frame = prepare_asset_frame(raw.loc[:as_of], row['kind'])
        record['session_ohlc'] = engine.session_ohlc(frame, as_of)
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
            structure_matrix.append(build_structure_matrix_record(record, metric, horizon, as_of))
        results.append(record)
        print(f'[radar] index forecast {symbol} ready', flush=True)
    return {'model_version':VERSION, 'as_of':str(as_of.date()), 'records':results,
            'status':'independently_trained_confidence_tiered',
            'note':'每个指数或ETF均按自身历史单独训练、校准、测试；杠杆/反向ETF不由基准概率倍乘或镜像。方向性阶段底/顶/无有效拐点互斥并归一，原四态联合事件仅保留为双向洗盘诊断。信度按品种自身路径支持与独立测试分级，不改变概率。',
            'structure_matrix': {
                'version': STRUCTURE_MATRIX_VERSION, 'as_of': str(as_of.date()),
                'records': structure_matrix,
                'calendar_method': 'nyse_regular_session_calendar_v1_excludes_unplanned_closures',
                'note': '核心条件区来自方向模型条件路径，缺失时才用ATR波动率带；尾部压力区扩展到期限末P10/P90与净收益P05/P95。全部为价位与相对锚点距离，不是概率。事件台账缺失时明确标记not_ingested，不假设中性。'
            }}
