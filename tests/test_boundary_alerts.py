import json
import tempfile
import unittest
from pathlib import Path

from scripts.boundary_alerts import build_boundary_breach_alerts

ROOT = Path(__file__).resolve().parents[1]


def metric():
    return {
        "status": "calibrated",
        "bottom_zone": [92, 95], "top_zone": [105, 108],
        "terminal_p10": 91, "terminal_p90": 109,
        "expected_return_p05": -0.10, "expected_return_p95": 0.10,
        "volatility_bottom_band": [95, 97], "volatility_top_band": [103, 105],
    }


def potential(atr14=4.0, structure_bottom=(92, 95), structure_top=(105, 108)):
    return {"horizons": {str(h): {"layer1_price_structure": {
        "status": "observed", "atr14": atr14,
        "support_band": list(structure_bottom), "resistance_band": list(structure_top),
    }} for h in (5, 10, 21)}}


class BoundaryAlertTests(unittest.TestCase):
    def test_structure_and_terminal_crossings_do_not_reach_homepage(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as folder:
            root = Path(folder)
            prior = {"run_id": "radar-20261008", "as_of": "2026-10-08", "records": [{
                "symbol": "TEST", "reference_price": 100, "potential_ranges": potential(),
                "metrics": {str(h): metric() for h in (5, 10, 21)},
            }], "indices": []}
            (root / "prior.json").write_text(json.dumps(prior), encoding="utf-8")
            current = [{"symbol": "TEST", "reference_price": 107,
                        "session_ohlc": {"open": 100, "high": 107.9, "low": 99, "close": 107}}]
            result = build_boundary_breach_alerts(current, [], "2026-10-09", "current", root)
            self.assertEqual(result["tiered_records"], [])
            self.assertEqual(result["records"], [])

    def test_only_two_atr_five_day_breaks_are_reported(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as folder:
            root = Path(folder)
            prior = {"run_id": "prior-20261006", "as_of": "2026-10-06", "records": [
                {"symbol": "LOW", "reference_price": 100, "potential_ranges": potential(), "metrics": {str(h): metric() for h in (5, 10, 21)}},
                {"symbol": "HIGH", "reference_price": 100, "potential_ranges": potential(), "metrics": {str(h): metric() for h in (5, 10, 21)}},
            ], "indices": []}
            (root / "prior.json").write_text(json.dumps(prior), encoding="utf-8")
            current = [
                {"symbol": "LOW", "reference_price": 91, "session_ohlc": {"open": 94, "high": 96, "low": 90, "close": 91}},
                {"symbol": "HIGH", "reference_price": 109, "session_ohlc": {"open": 106, "high": 110, "low": 105, "close": 109}},
            ]
            result = build_boundary_breach_alerts(current, [], "2026-10-07", "current", root)
            rows = result["tiered_records"]
            self.assertEqual(len(rows), 2)
            self.assertEqual({row["horizon_sessions"] for row in rows}, {5})
            self.assertEqual({row["side"] for row in rows}, {"below_lower", "above_upper"})
            self.assertTrue(all(row["boundary_layer"] == "extreme_atr" for row in rows))
            self.assertTrue(all(row["extreme_atr_multiplier"] == 2.0 for row in rows))
            self.assertTrue(all(row["trigger_state"] == "close_confirmed" for row in rows))
            self.assertEqual(len(result["records"]), 2)
            self.assertEqual(result["summary"]["5"], {"below_lower": 1, "above_upper": 1})
            self.assertEqual(result["summary"]["10"], {"below_lower": 0, "above_upper": 0})

    def test_intraday_extreme_atr_break_is_separate_from_close_confirmation(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as folder:
            root = Path(folder)
            prior = {"run_id": "prior", "as_of": "2026-10-08", "records": [{
                "symbol": "TEST", "reference_price": 100, "potential_ranges": potential(),
                "metrics": {str(h): metric() for h in (5, 10, 21)},
            }], "indices": []}
            (root / "prior.json").write_text(json.dumps(prior), encoding="utf-8")
            current = [{"symbol": "TEST", "reference_price": 95,
                        "session_ohlc": {"open": 99, "high": 101, "low": 91, "close": 95}}]
            result = build_boundary_breach_alerts(current, [], "2026-10-09", "current", root)
            self.assertEqual(len(result["tiered_records"]), 1)
            self.assertEqual(result["tiered_records"][0]["trigger_state"], "intraday_only")
            self.assertEqual(result["records"], [])

    def test_index_atr_is_inferred_from_frozen_volatility_band(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as folder:
            root = Path(folder)
            prior = {"run_id": "prior", "as_of": "2026-10-08", "records": [],
                     "indices": [{"symbol": "SPY", "level": 100,
                                  "metrics": {str(h): metric() for h in (5, 10, 21)}}]}
            (root / "prior.json").write_text(json.dumps(prior), encoding="utf-8")
            current = [{"symbol": "SPY", "level": 109,
                        "session_ohlc": {"open": 101, "high": 109, "low": 100, "close": 109}}]
            result = build_boundary_breach_alerts([], current, "2026-10-09", "current", root)
            row = result["tiered_records"][0]
            self.assertEqual(row["scope"], "index_etf")
            self.assertAlmostEqual(row["frozen_atr14"], 4.0)
            self.assertAlmostEqual(row["frozen_upper_bound"], 108.0)
            self.assertEqual(row["boundary_source"], "prior_frozen_atr14_inferred_from_volatility_outer_band")

    def test_no_earlier_snapshot_fails_closed(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as folder:
            result = build_boundary_breach_alerts([], [], "2026-10-07", "current", Path(folder))
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["reason"], "no_earlier_frozen_prediction")
            self.assertEqual(result["records"], [])
            self.assertEqual(result["tiered_records"], [])


if __name__ == "__main__":
    unittest.main()
