import json
import tempfile
import unittest
from pathlib import Path

from scripts.boundary_alerts import build_boundary_breach_alerts

ROOT = Path(__file__).resolve().parents[1]


def metric(bottom=(92, 95), top=(105, 108), p05=-0.10, p95=0.10):
    return {
        "status": "calibrated",
        "bottom_zone": list(bottom),
        "top_zone": list(top),
        "terminal_p10": 91,
        "terminal_p90": 109,
        "expected_return_p05": p05,
        "expected_return_p95": p95,
    }


class BoundaryAlertTests(unittest.TestCase):
    def test_compares_current_price_with_latest_earlier_frozen_bounds(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as folder:
            root = Path(folder)
            older = {
                "run_id": "prior-20261006",
                "as_of": "2026-10-06",
                "generated_at": "2026-10-07T01:00:00Z",
                "records": [
                    {"symbol": "LOW", "name_zh": "下破样本", "reference_price": 100, "metrics": {str(h): metric() for h in (5, 10, 21)}},
                    {"symbol": "HIGH", "name_zh": "上破样本", "reference_price": 100, "metrics": {str(h): metric() for h in (5, 10, 21)}},
                ],
                "indices": [
                    {"symbol": "SPY", "name": "SPY", "level": 100, "metrics": {str(h): metric() for h in (5, 10, 21)}}
                ],
            }
            same_day = {**older, "run_id": "same-day", "as_of": "2026-10-07", "records": []}
            (root / "older.json").write_text(json.dumps(older), encoding="utf-8")
            (root / "same.json").write_text(json.dumps(same_day), encoding="utf-8")
            current = [
                {"symbol": "LOW", "name_zh": "下破样本", "reference_price": 89},
                {"symbol": "HIGH", "name_zh": "上破样本", "reference_price": 111},
            ]
            indices = [{"symbol": "SPY", "name": "SPY", "level": 100}]
            result = build_boundary_breach_alerts(current, indices, "2026-10-07", "current", root)
            self.assertEqual(result["status"], "observed")
            self.assertEqual(result["basis_run_id"], "prior-20261006")
            self.assertEqual(result["basis_as_of"], "2026-10-06")
            self.assertEqual(result["evaluated_symbol_horizons"], 9)
            self.assertEqual(len(result["records"]), 6)
            self.assertEqual({row["side"] for row in result["records"]}, {"below_lower", "above_upper"})
            self.assertEqual(result["summary"]["5"], {"below_lower": 1, "above_upper": 1})
            low = next(row for row in result["records"] if row["symbol"] == "LOW" and row["horizon_sessions"] == 5)
            self.assertEqual(low["frozen_lower_bound"], 90)
            self.assertAlmostEqual(low["breach_pct"], 89 / 90 - 1)

    def test_no_earlier_snapshot_fails_closed(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as folder:
            result = build_boundary_breach_alerts([], [], "2026-10-07", "current", Path(folder))
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["reason"], "no_earlier_frozen_prediction")
            self.assertEqual(result["records"], [])


if __name__ == "__main__":
    unittest.main()

