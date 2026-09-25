import unittest
import numpy as np
import pandas as pd
from scripts import radar_engine as engine
from scripts.index_forecasts import build_index_forecasts


class IndexForecastTests(unittest.TestCase):
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
        calibrated = 0
        for h, metric in record['metrics'].items():
            v = record['validation'][h]['joint_turning']
            self.assertLess(v['train_label_end'], v['calibration_start'])
            self.assertLess(v['calibration_label_end'], v['test_start'])
            if metric['status']=='calibrated_low_confidence':
                calibrated += 1
                for field in ('p_bottom','p_top','p_upfirst','p_downfirst','p_unhit'):
                    self.assertTrue(0 <= metric[field] <= 1)
                self.assertAlmostEqual(sum(metric[k] for k in ('p_upfirst','p_downfirst','p_unhit')),1)
                self.assertLessEqual(metric['bottom_band'][0],metric['bottom_band'][1])
                self.assertNotIn('expected_return', metric)
        self.assertGreater(calibrated, 0)


if __name__ == '__main__':
    unittest.main()
