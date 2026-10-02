import unittest

import numpy as np
import pandas as pd

from scripts.radar_engine import _decision_fields, _risk_and_return_audit


def metric(bottom, top, no_turn, *, zone=True, wash=0.0, valid=True, margin=0.05):
    return {
        "status": "calibrated",
        "p_bottom_rebound_first": bottom,
        "p_top_reversal_first": top,
        "p_no_directional_turn": no_turn,
        "p_two_way_wash": wash,
        "two_way_wash_status": "scenario_diagnostic_not_direction_probability",
        "risk_metric_valid": valid,
        "effective_weighted_sample_size": 100,
        "history_source": "own_history",
        "validation_ece": 0.08,
        "regime_shift_flag": False,
        "bottom_zone": [95, 105] if zone else None,
        "top_zone": [105, 115] if zone else None,
        "directional_baseline_rates": {"bottom_rebound_first": 0.30, "top_reversal_first": 0.30, "no_directional_turn": 0.40},
    }


class DecisionConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.current = pd.Series({"adj_close": 100.0, "dist_ma20": 0.02, "ret_20": 0.08})

    def test_case_a_already_near_bottom_then_rises(self):
        out = _decision_fields(metric(.62, .18, .20), self.current, .08)
        self.assertEqual(out["stage_primary_judgment"], "bottom_reversal_candidate")

    def test_case_b_touch_bottom_no_confirm_then_top(self):
        out = _decision_fields(metric(.18, .60, .22), self.current, .08)
        self.assertEqual(out["stage_primary_judgment"], "top_reversal_warning")

    def test_case_c_touch_bottom_confirm_then_new_low_pending_if_unit_bad(self):
        out = _decision_fields(metric(.62, .20, .18, valid=False), self.current, .08)
        self.assertEqual(out["stage_primary_judgment"], "data_model_pending_review")
        self.assertIn("metric_unit_anomaly", out["decision_block_reason"])

    def test_case_d_sustained_uptrend(self):
        out = _decision_fields(metric(.10, .12, .78), self.current, .08)
        self.assertEqual(out["stage_primary_judgment"], "trend_continuation_up")

    def test_case_e_both_sides_same_window_is_wash_diagnostic(self):
        out = _decision_fields(metric(.31, .30, .39, wash=.55), self.current, .08)
        self.assertEqual(out["stage_primary_judgment"], "two_way_high_volatility_wash")

    def test_invalid_negative_price_is_not_clamped(self):
        out = _risk_and_return_audit(
            np.array([100.0, 110.0]), np.array([-20.0, 95.0]), 100.0,
            np.array([.5, .5]), np.array([0.0, .1])
        )
        self.assertFalse(out["risk_metric_valid"])
        self.assertEqual(out["risk_metric_validation_reason"], "negative_modeled_price")
        self.assertGreater(out["risk_value_raw"], 1.0)


if __name__ == "__main__":
    unittest.main()
