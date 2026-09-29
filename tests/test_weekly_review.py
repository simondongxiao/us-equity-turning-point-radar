import csv
import tempfile
import unittest
from pathlib import Path

from scripts.weekly_review import latest_eligible_snapshot


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


if __name__ == "__main__":
    unittest.main()
