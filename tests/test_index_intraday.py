import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from scripts.index_intraday import _profile, _regular, _mapping, _stale_copy


class IndexIntradayProfileTests(unittest.TestCase):
    def setUp(self):
        ny = ZoneInfo("America/New_York")
        idx = pd.date_range(datetime(2026, 10, 20, 9, 30, tzinfo=ny), periods=12, freq="5min")
        close = np.array([100, 100.2, 100.4, 101, 101.1, 101.2, 100.9, 100.7, 100.5, 100.3, 100.1, 99.9])
        self.frame = pd.DataFrame({"open": close - .1, "high": close + .2, "low": close - .2, "close": close, "volume": [10, 20, 50, 80, 100, 120, 100, 70, 40, 30, 20, 10]}, index=idx)

    def test_profile_has_contiguous_value_area_and_fixed_bins(self):
        p = _profile(self.frame, "today", self.frame.index[0].date(), self.frame.index[-1].date())
        self.assertEqual(p["status"], "observed")
        self.assertLessEqual(p["val"], p["poc"])
        self.assertLessEqual(p["poc"], p["vah"])
        self.assertGreaterEqual(p["value_area_actual"], .70)
        self.assertTrue(all("is_poc" in b and "in_value_area" in b for b in p["bins"]))

    def test_mapping_never_converts_qqq_to_ixic(self):
        item = _mapping({"price": 20000, "as_of": "a"}, {"price": 500, "as_of": "a"}, {"key": "nasdaq", "mapping": "QQQ→NDX only"})
        self.assertFalse(item["allowed"])
        self.assertIsNone(item["ratio"])

    def test_refresh_failure_keeps_last_successful_card_as_stale(self):
        previous = {"status": "observed", "quality": {"status": "observed"}, "session_date": "2026-10-21"}
        stale = _stale_copy(previous, "rate limited")
        self.assertEqual(stale["status"], "observed")
        self.assertEqual(stale["quality_status"], "stale")
        self.assertEqual(stale["refresh_status"], "failed_using_previous_snapshot")
        self.assertEqual(stale["missing_reason"], "rate limited")


if __name__ == "__main__":
    unittest.main()
