import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from scripts import radar_engine as engine


def price_frame(dates: pd.DatetimeIndex) -> pd.DataFrame:
    close = np.linspace(90.0, 110.0, len(dates))
    return pd.DataFrame({
        "open": close - .2,
        "high": close + .5,
        "low": close - .5,
        "close": close,
        "adj_close": close,
        "volume": 1_000_000,
    }, index=dates.rename("date"))


class PriceRefreshTests(unittest.TestCase):
    def test_truncated_refresh_keeps_newer_verified_cache(self):
        old_raw_dir = engine.RAW_DIR
        with tempfile.TemporaryDirectory() as tmp:
            engine.RAW_DIR = Path(tmp)
            cached_dates = pd.bdate_range("2025-10-01", periods=120)
            cached = price_frame(cached_dates)
            cached.to_csv(engine.RAW_DIR / "TEST.csv", date_format="%Y-%m-%d")
            downloaded = price_frame(cached_dates[:-2]).rename(columns={
                "open": "Open", "high": "High", "low": "Low", "close": "Close",
                "adj_close": "Adj Close", "volume": "Volume",
            })
            with patch.object(engine.yf, "download", return_value=downloaded):
                frames, manifest = engine.download_prices(["TEST"], refresh=True)
        engine.RAW_DIR = old_raw_dir
        self.assertEqual(frames["TEST"].index.max(), cached_dates.max())
        self.assertEqual(len(frames["TEST"]), len(cached_dates))
        self.assertIn("TEST", manifest["refresh_regressions"])
        self.assertEqual(manifest["common_latest_date"], cached_dates.max().strftime("%Y-%m-%d"))


if __name__ == "__main__":
    unittest.main()
