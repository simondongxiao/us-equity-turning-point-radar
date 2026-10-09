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
        "terminal_band": [91, 109],
        "expected_return_p05": p05,
        "expected_return_p95": p95,
    }


def potential(structure_bottom=(92, 95), structure_top=(105, 108)):
    return {"horizons": {str(h): {"layer1_price_structure": {
        "status": "observed", "support_band": list(structure_bottom),
        "resistance_band": list(structure_top),
    }} for h in (5, 10, 21)}}


class BoundaryAlertTests(unittest.TestCase):
    def test_compares_current_price_with_latest_earlier_frozen_bounds(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as folder:
            root = Path(folder)
            older = {
                "run_id": "prior-20261006",
                "as_of": "2026-10-06",
                "generated_at": "2026-10-07T01:00:00Z",
                "records": [
                    {"symbol": "LOW", "name_zh": "下破样本", "reference_price": 100, "potential_ranges": potential(), "metrics": {str(h): metric() for h in (5, 10, 21)}},
                    {"symbol": "HIGH", "name_zh": "上破样本", "reference_price": 100, "potential_ranges": potential(), "metrics": {str(h): metric() for h in (5, 10, 21)}},
                ],
                "indices": [
                    {"symbol": "SPY", "name": "SPY", "level": 100, "metrics": {str(h): metric() for h in (5, 10, 21)}}
                ],
            }
            same_day = {**older, "run_id": "same-day", "as_of": "2026-10-07", "records": []}
            (root / "older.json").write_text(json.dumps(older), encoding="utf-8")
            (root / "same.json").write_text(json.dumps(same_day), encoding="utf-8")
            current = [
                {"symbol": "LOW", "name_zh": "下破样本", "reference_price": 89, "session_ohlc": {"open": 94, "high": 96, "low": 88, "close": 89}},
                {"symbol": "HIGH", "name_zh": "上破样本", "reference_price": 111, "session_ohlc": {"open": 106, "high": 112, "low": 105, "close": 111}},
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
            self.assertEqual(len(result["tiered_records"]), 18)
            self.assertTrue(all(row["trigger_state"] == "close_confirmed" for row in result["tiered_records"]))

    def test_sndk_intraday_and_close_confirmations_are_separate(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as folder:
            root = Path(folder)
            metrics = {
                "5": metric(bottom=(1414, 1541), top=(1800, 1900), p05=-.20, p95=.25),
                "10": metric(bottom=(1208, 1509), top=(1900, 2100), p05=-.30, p95=.40),
                "21": metric(bottom=(1208, 1491), top=(2000, 2300), p05=-.35, p95=.60),
            }
            metrics["5"].update(terminal_band=[1590.08, 1947.91], terminal_p10=1590.08, terminal_p90=1947.91)
            metrics["10"].update(terminal_band=[1630.24, 2186.92], terminal_p10=1630.24, terminal_p90=2186.92)
            metrics["21"].update(terminal_band=[1552.92, 3322.91], terminal_p10=1552.92, terminal_p90=3322.91)
            prior = {
                "run_id": "radar-20261007", "as_of": "2026-10-07", "generated_at": "2026-10-08T01:00:00Z",
                "records": [{
                    "symbol": "SNDK", "name_zh": "闪迪", "reference_price": 1692.42,
                    "potential_ranges": potential((1628.94, 1693.15), (1780, 1840)), "metrics": metrics,
                }], "indices": [],
            }
            (root / "prior.json").write_text(json.dumps(prior), encoding="utf-8")
            current = [{"symbol": "SNDK", "name_zh": "闪迪", "reference_price": 1609.46,
                        "session_ohlc": {"open": 1669.49, "high": 1675.0, "low": 1584.15, "close": 1609.46}}]
            result = build_boundary_breach_alerts(current, [], "2026-10-08", "current", root)
            rows = result["tiered_records"]
            structure = [row for row in rows if row["boundary_layer"] == "structure" and row["side"] == "below_lower"]
            terminal = [row for row in rows if row["boundary_layer"] == "terminal" and row["side"] == "below_lower"]
            self.assertEqual({row["horizon_sessions"] for row in structure}, {5, 10, 21})
            self.assertTrue(all(row["trigger_state"] == "close_confirmed" for row in structure))
            self.assertEqual([(row["horizon_sessions"], row["trigger_state"]) for row in terminal], [(5, "intraday_only"), (10, "close_confirmed")])
            self.assertFalse([row for row in rows if row["boundary_layer"] == "extreme_tail"])
            self.assertEqual(result["records"], [])

    def test_no_earlier_snapshot_fails_closed(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "outputs") as folder:
            result = build_boundary_breach_alerts([], [], "2026-10-07", "current", Path(folder))
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["reason"], "no_earlier_frozen_prediction")
            self.assertEqual(result["records"], [])
            self.assertEqual(result["tiered_records"], [])


if __name__ == "__main__":
    unittest.main()

