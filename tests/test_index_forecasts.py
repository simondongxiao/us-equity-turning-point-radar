import unittest
import numpy as np
import pandas as pd
from scripts import radar_engine as engine
from scripts.index_forecasts import (
    build_index_forecasts,
    build_structure_matrix_record,
    nyse_horizon_end,
    prepare_asset_frame,
)


class IndexForecastTests(unittest.TestCase):
    def test_expected_nyse_horizon_dates_keep_columbus_day_open(self):
        self.assertEqual(nyse_horizon_end('2026-10-07', 5), '2026-10-14')
        self.assertEqual(nyse_horizon_end('2026-10-07', 10), '2026-10-21')
        self.assertEqual(nyse_horizon_end('2026-10-07', 21), '2026-11-05')

    def test_structure_matrix_separates_price_zones_from_probability(self):
        row = {'symbol':'SPY', 'name':'SPY', 'kind':'etf_proxy', 'level':100.0, 'unit':'adjusted_usd'}
        metric = {
            'status':'calibrated', 'bottom_band':[92.0, 95.0], 'top_band':[106.0, 109.0],
            'terminal_p10':90.0, 'terminal_p90':111.0,
            'expected_return_p05':-0.12, 'expected_return_p95':0.15,
            'p_bottom_rebound_first':0.55, 'p_top_reversal_first':0.20,
            'p_no_directional_turn':0.25, 'confidence_level':'medium', 'confidence_label':'中确信',
        }
        result = build_structure_matrix_record(row, metric, 10, pd.Timestamp('2026-10-07'))
        self.assertEqual(result['core_bottom_zone'], [92.0, 95.0])
        self.assertEqual(result['pressure_bottom_zone'], [88.0, 92.0])
        self.assertEqual(result['pressure_top_zone'][0], 109.0)
        self.assertAlmostEqual(result['pressure_top_zone'][1], 115.0)
        self.assertEqual(result['dominant_direction'], 'bottom_rebound_first')
        self.assertAlmostEqual(result['dominant_probability'], 0.55)
        self.assertIn('不是方向概率', result['semantic_note'])
        self.assertEqual(result['event_status'], 'not_ingested')

    def test_leveraged_etf_uses_adjusted_ohlc(self):
        raw = pd.DataFrame({'open':[9.0], 'high':[11.0], 'low':[8.0], 'close':[10.0], 'adj_close':[5.0]})
        frame = prepare_asset_frame(raw, 'leveraged_etf')
        self.assertEqual(frame.loc[0, 'open'], 4.5)
        self.assertEqual(frame.loc[0, 'high'], 5.5)
        self.assertEqual(frame.loc[0, 'low'], 4.0)
        self.assertEqual(frame.loc[0, 'close'], 5.0)

    def test_own_history_joint_probabilities_and_purged_dates(self):
        rng = np.random.default_rng(751)
        dates = pd.bdate_range('2021-01-01', periods=1300)
        close = 100*np.exp(np.cumsum(rng.normal(0.0001, .013, len(dates))))
        opening = np.r_[close[0], close[:-1]] * np.exp(rng.normal(0,.003,len(dates)))
        frame = pd.DataFrame({'close':close,'adj_close':close,'open':opening,
            'high':np.maximum(close,opening)*1.004,'low':np.minimum(close,opening)*.996,'volume':100},index=dates)
        frames = {'SPY':frame,'QQQ':frame,'^SOX':frame}
        context = {'rows':[{'symbol':'^SOX','name':'SOX','kind':'price_index','as_of':str(dates[-1].date())}]}
        result = build_index_forecasts(engine, frames, context, dates[-1])
        record = result['records'][0]
        matrix = result['structure_matrix']['records']
        self.assertEqual(len(matrix), 3)
        self.assertTrue(all(x['symbol'] == '^SOX' for x in matrix))
        self.assertTrue(all(x['horizon_end_date'] > str(dates[-1].date()) for x in matrix))
        calibrated = 0
        for h, metric in record['metrics'].items():
            v = record['validation'][h]['directional_turn_exclusive']
            self.assertLess(v['train_label_end'], v['calibration_start'])
            self.assertLess(v['calibration_label_end'], v['test_start'])
            if metric['status'] in {'calibrated','calibrated_low_confidence'}:
                calibrated += 1
                for field in ('p_bottom','p_top','p_upfirst','p_downfirst','p_unhit','p_bottom_rebound_first','p_top_reversal_first','p_no_directional_turn','p_two_way_wash'):
                    self.assertTrue(0 <= metric[field] <= 1)
                self.assertAlmostEqual(sum(metric[k] for k in ('p_upfirst','p_downfirst','p_unhit')),1)
                self.assertAlmostEqual(sum(metric[k] for k in ('p_bottom_rebound_first','p_top_reversal_first','p_no_directional_turn')),1)
                self.assertLessEqual(metric['bottom_band'][0],metric['bottom_band'][1])
                self.assertLessEqual(metric['volatility_bottom_band'][0],metric['volatility_bottom_band'][1])
                self.assertIn(metric['confidence_level'], {'high','medium','low'})
                self.assertTrue(0 <= metric['confidence_score'] <= 100)
                self.assertTrue(0 <= metric['path_similarity_score'] <= 100)
                if metric['confidence_level'] == 'high':
                    self.assertGreaterEqual(metric['validation_accuracy_lift'], .03)
                    self.assertFalse(metric['regime_shift_flag'])
                self.assertNotIn('expected_return', metric)
        self.assertGreater(calibrated, 0)


if __name__ == '__main__':
    unittest.main()
