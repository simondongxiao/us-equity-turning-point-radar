import unittest
import pandas as pd
from scripts.index_context import build_index_context


class IndexContextTests(unittest.TestCase):
    def setUp(self):
        self.dates = pd.bdate_range('2026-08-01', periods=30)
        self.frame = pd.DataFrame({'close': range(100, 130), 'adj_close': range(100, 130)}, index=self.dates)

    def test_index_does_not_change_pool_or_probabilities(self):
        records = [{'symbol':'MU', 'metrics':{'5':{'p_bottom':.8, 'p_top':.7}}}]
        frames = {'SPY':self.frame, 'MU':self.frame, '^SOX':self.frame}
        result = build_index_context(frames, records, self.dates[-1])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['metrics']['5'], {'p_bottom':.8, 'p_top':.7})
        self.assertEqual(records[0]['index_context']['comparisons'][0]['relative_returns']['5'], 0)
        self.assertEqual(result['rows'][0]['status'], 'observed')

    def test_missing_or_incomplete_window_never_filled(self):
        sox = self.frame.copy()
        sox.loc[self.dates[-3], 'close'] = float('nan')
        result = build_index_context({'SPY':self.frame, '^SOX':sox}, [], self.dates[-1])
        self.assertIsNone(result['rows'][0]['returns']['5'])
        self.assertEqual(result['rows'][1]['status'], 'unavailable')

    def test_future_rows_excluded(self):
        result = build_index_context({'SPY':self.frame, '^SOX':self.frame}, [], self.dates[-2])
        self.assertEqual(result['rows'][0]['level'], 128)


if __name__ == '__main__':
    unittest.main()
