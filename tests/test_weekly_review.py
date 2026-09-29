import csv
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from scripts.weekly_review import compute_point_in_time_popularity, latest_eligible_snapshot


def write_snapshot(path: Path, base_date: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["symbol", "base_date"])
        writer.writeheader()
        writer.writerow({"symbol": "MU", "base_date": base_date})


class WeeklyReviewTests(unittest.TestCase):
    def test_next_day_build_directory_is_eligible_for_prior_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_snapshot(root / "2026-09-26" / "all_metrics.csv", "2026-09-25")
            write_snapshot(root / "2026-09-29" / "all_metrics.csv", "2026-09-28")
            path, rows = latest_eligible_snapshot(root, "2026-09-28")
        self.assertEqual(path.parent.name, "2026-09-29")
        self.assertEqual({row["base_date"] for row in rows}, {"2026-09-28"})

    def test_future_base_date_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_snapshot(root / "2026-10-01" / "all_metrics.csv", "2026-09-30")
            write_snapshot(root / "2026-09-29" / "all_metrics.csv", "2026-09-28")
            path, rows = latest_eligible_snapshot(root, "2026-09-28")
        self.assertEqual(path.parent.name, "2026-09-29")
        self.assertEqual(len(rows), 1)

    def test_popularity_uses_point_in_time_medians_and_active_rank(self):
        dates = pd.bdate_range("2026-05-01", periods=79)

        def chart(close: float, volume: float) -> pd.DataFrame:
            return pd.DataFrame(
                {
                    "date": dates,
                    "close": [close] * len(dates),
                    "volume": [volume] * len(dates),
                }
            )

        charts = {
            "AAA": chart(100.0, 2_000_000.0),
            "BBB": chart(50.0, 2_000_000.0),
            "LOW": chart(2.0, 100_000_000.0),
        }
        metadata = {
            symbol: {"symbol": symbol, "name": symbol, "exchange": "Nasdaq", "cik": str(i)}
            for i, symbol in enumerate(charts, start=1)
        }
        result = compute_point_in_time_popularity(
            charts,
            metadata,
            dates[-1].strftime("%Y-%m-%d"),
            active_top_n=1,
        ).set_index("symbol")

        self.assertTrue(result.loc["AAA", "qualified"])
        self.assertTrue(result.loc["BBB", "qualified"])
        self.assertFalse(result.loc["LOW", "qualified"])
        self.assertTrue(result.loc["AAA", "popularity_complete"])
        self.assertEqual(result.loc["AAA", "median_dollar_20d"], 200_000_000.0)
        self.assertEqual(result.loc["BBB", "median_dollar_60d"], 100_000_000.0)
        self.assertEqual(result.loc["AAA", "active_top_ratio"], 1.0)
        self.assertEqual(result.loc["BBB", "active_top_ratio"], 0.0)
        self.assertLess(result.loc["AAA", "issuer_popularity_rank"], result.loc["BBB", "issuer_popularity_rank"])

    def test_known_issuer_is_deduplicated(self):
        dates = pd.bdate_range("2026-05-01", periods=79)
        charts = {
            "ONE": pd.DataFrame({"date": dates, "close": 100.0, "volume": 2_000_000.0}),
            "TWO": pd.DataFrame({"date": dates, "close": 90.0, "volume": 2_000_000.0}),
        }
        metadata = {
            "ONE": {"symbol": "ONE", "cik": "123", "exchange": "NYSE"},
            "TWO": {"symbol": "TWO", "cik": "123", "exchange": "NYSE"},
        }
        result = compute_point_in_time_popularity(
            charts, metadata, dates[-1].strftime("%Y-%m-%d"), active_top_n=2
        ).set_index("symbol")

        self.assertTrue(result.loc["ONE", "issuer_selected"])
        self.assertFalse(result.loc["TWO", "issuer_selected"])
        self.assertTrue(pd.isna(result.loc["TWO", "issuer_popularity_rank"]))

    def test_empty_qualified_set_does_not_create_a_fake_rank(self):
        dates = pd.bdate_range("2026-05-01", periods=79)
        charts = {
            "LOW": pd.DataFrame({"date": dates, "close": 2.0, "volume": 100_000_000.0})
        }
        metadata = {"LOW": {"symbol": "LOW", "cik": "456", "exchange": "NYSE"}}
        result = compute_point_in_time_popularity(
            charts, metadata, dates[-1].strftime("%Y-%m-%d")
        ).set_index("symbol")

        self.assertFalse(result.loc["LOW", "qualified"])
        self.assertFalse(result.loc["LOW", "issuer_selected"])
        self.assertTrue(pd.isna(result.loc["LOW", "popularity_score"]))


if __name__ == "__main__":
    unittest.main()
