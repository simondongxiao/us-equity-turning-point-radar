import tempfile
import unittest
from datetime import datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

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
    def test_regular_session_aggregation_rejects_partial_and_captures_intraday_low(self):
        start = datetime(2026, 10, 8, 9, 30, tzinfo=ZoneInfo("America/New_York"))
        stamps = [int((start + timedelta(minutes=5 * i)).timestamp()) for i in range(79)]
        close = [1669.0 - i * .75 for i in range(79)]
        low = [value - 1 for value in close]
        low[74] = 1584.15
        payload = {"chart": {"result": [{"timestamp": stamps, "indicators": {"quote": [{
            "open": close, "high": [value + 1 for value in close], "low": low,
            "close": close[:-1] + [1609.46], "volume": [100_000] * 79,
        }]}}]}}
        aggregate = engine.aggregate_regular_session_chart(payload, "2026-10-08")
        self.assertEqual(aggregate["date"], "2026-10-08")
        self.assertEqual(aggregate["bar_count"], 79)
        self.assertAlmostEqual(aggregate["low"], 1584.15)
        self.assertAlmostEqual(aggregate["close"], 1609.46)
        partial = {"chart": {"result": [{"timestamp": stamps[:20], "indicators": {"quote": [{key: values[:20] for key, values in payload["chart"]["result"][0]["indicators"]["quote"][0].items()}]}}]}}
        self.assertIsNone(engine.aggregate_regular_session_chart(partial, "2026-10-08"))

    def test_intraday_reconciliation_replaces_stale_daily_bar(self):
        session = pd.Timestamp("2026-10-08")
        frame = pd.DataFrame({"open": [1669.49], "high": [1675.0], "low": [1633.0], "close": [1648.03], "adj_close": [1648.03], "volume": [2_378_355]}, index=pd.DatetimeIndex([session], name="date"))
        corrected = {"symbol": "SNDK", "status": "observed", "date": "2026-10-08", "open": 1669.49,
                     "high": 1675.0, "low": 1584.15, "close": 1609.46, "volume": 9_218_054,
                     "bar_count": 79, "first_bar_ny": "2026-10-08T09:30:00-04:00", "last_bar_ny": "2026-10-08T16:00:00-04:00"}
        with patch.object(engine, "fetch_intraday_regular_session", return_value=corrected):
            frames, audit = engine.reconcile_intraday_sessions({"SNDK": frame}, "2026-10-08", max_workers=1)
        self.assertAlmostEqual(frames["SNDK"].loc[session, "low"], 1584.15)
        self.assertAlmostEqual(frames["SNDK"].loc[session, "close"], 1609.46)
        self.assertEqual(frames["SNDK"].loc[session, "volume"], 9_218_054)
        self.assertEqual(audit["symbols"]["SNDK"]["status"], "corrected")

    def test_settled_daily_row_requires_complete_ohlc(self):
        stamp = int(datetime(2026, 10, 8, 9, 30, tzinfo=ZoneInfo("America/New_York")).timestamp())
        payload = {"chart": {"result": [{"timestamp": [stamp], "indicators": {"quote": [{
            "open": [1669.49], "high": [1675.0], "low": [1584.15], "close": [1609.46], "volume": [9_218_054],
        }]}}]}}
        row = engine.daily_chart_session(payload, "2026-10-08")
        self.assertEqual(row["close"], 1609.46)
        self.assertEqual(row["volume"], 9_218_054)
        payload["chart"]["result"][0]["indicators"]["quote"][0]["close"] = [None]
        self.assertIsNone(engine.daily_chart_session(payload, "2026-10-08"))

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
            with patch.object(engine.yf, "download", return_value=downloaded), patch.object(
                engine, "fetch_intraday_regular_session",
                return_value={"symbol": "TEST", "status": "unavailable", "reason": "unit_test"},
            ):
                frames, manifest = engine.download_prices(["TEST"], refresh=True)
        engine.RAW_DIR = old_raw_dir
        self.assertEqual(frames["TEST"].index.max(), cached_dates.max())
        self.assertEqual(len(frames["TEST"]), len(cached_dates))
        self.assertIn("TEST", manifest["refresh_regressions"])
        self.assertEqual(manifest["common_latest_date"], cached_dates.max().strftime("%Y-%m-%d"))


if __name__ == "__main__":
    unittest.main()
