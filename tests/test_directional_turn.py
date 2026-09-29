import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from scripts import radar_engine as engine


class DirectionalTurnTests(unittest.TestCase):
    def test_calibration_curve_uses_observed_bins_and_date_blocks(self):
        dates = np.repeat(pd.bdate_range("2026-01-02", periods=60), 2)
        probabilities = np.tile(np.array([
            [.72, .18, .10],
            [.15, .70, .15],
            [.12, .18, .70],
        ]), (40, 1))
        truths = np.resize(np.array([
            "bottom_rebound_first", "top_reversal_first", "no_directional_turn",
        ]), len(probabilities))
        result = engine.calibration_diagnostics(
            probabilities,
            truths,
            ["bottom_rebound_first", "top_reversal_first", "no_directional_turn"],
            dates,
            block_ci=True,
        )
        self.assertGreaterEqual(result["calibration_ece"], 0)
        self.assertLessEqual(result["calibration_ece"], 1)
        rows = [row for curve in result["calibration_curve"].values() for row in curve]
        self.assertTrue(rows)
        self.assertEqual(sum(row["n"] for row in result["calibration_curve"]["bottom_rebound_first"]), len(truths))
        self.assertTrue(any("date_block_ci_low" in row for row in rows))
        for row in rows:
            self.assertTrue(0 <= row["predicted_mean"] <= 1)
            self.assertTrue(0 <= row["observed_rate"] <= 1)

    def test_confidence_tiers_require_validation_skill_and_regime_support(self):
        high = engine.confidence_from_path_validation(np.full(40, .10), {
            "calibration_ece": .04,
            "classification_accuracy": .66,
            "majority_baseline_accuracy": .55,
            "accuracy_lift_vs_majority_baseline": .11,
            "test_n": 180,
            "unique_test_dates": 80,
        })
        medium = engine.confidence_from_path_validation(np.full(40, .50), {
            "calibration_ece": .08,
            "classification_accuracy": .55,
            "majority_baseline_accuracy": .55,
            "accuracy_lift_vs_majority_baseline": 0.0,
            "test_n": 180,
            "unique_test_dates": 80,
        })
        low = engine.confidence_from_path_validation(np.full(40, 3.0), {
            "calibration_ece": .04,
            "classification_accuracy": .66,
            "majority_baseline_accuracy": .55,
            "accuracy_lift_vs_majority_baseline": .11,
            "test_n": 180,
            "unique_test_dates": 80,
        })
        self.assertEqual(high["confidence_level"], "high")
        self.assertEqual(medium["confidence_level"], "medium")
        self.assertEqual(low["confidence_level"], "low")
        self.assertTrue(low["regime_shift_flag"])

    def test_mutually_exclusive_labels(self):
        source = pd.DataFrame({
            "label": ["downfirst", "upfirst", "unhit", "downfirst", None],
            "ambiguous": [False, False, False, False, False],
            "bottom_event": [True, False, True, False, False],
            "top_event": [False, True, True, True, False],
        })
        result = engine.add_directional_turn_label(source)
        self.assertEqual(result["turn_label"].tolist(), [
            "bottom_rebound_first", "top_reversal_first", "no_directional_turn",
            "no_directional_turn", None,
        ])

    def test_calibrated_directional_probabilities_sum_to_one(self):
        rng = np.random.default_rng(17)
        count = 1000
        samples = pd.DataFrame(rng.normal(size=(count, len(engine.BASE_FEATURES))), columns=engine.BASE_FEATURES)
        samples["date"] = pd.bdate_range("2022-01-03", periods=count)
        samples["ambiguous"] = False
        samples["turn_label"] = np.resize(np.array([
            "bottom_rebound_first", "no_directional_turn", "top_reversal_first",
        ]), count)
        bundle = engine.train_bundle(samples, engine.BASE_FEATURES, 10, target_col="turn_label")
        self.assertIsNotNone(bundle)
        probability = bundle.predict(samples.iloc[-1])
        self.assertAlmostEqual(float(probability.sum()), 1.0)
        self.assertEqual(set(bundle.model.classes_), {
            "bottom_rebound_first", "no_directional_turn", "top_reversal_first",
        })

    def test_options_gate_blocks_single_snapshot(self):
        old_state = engine.STATE_DIR
        with tempfile.TemporaryDirectory() as tmp:
            engine.STATE_DIR = Path(tmp)
            current = {"TEST": {"horizons": {"5": {
                "as_of": "2026-09-25",
                "layer2_options_distribution": {"status": "observed", "atm_iv": .5},
                "layer3_skew_event": {"rv_minus_iv": -.1, "put_call_skew": .02, "term_structure_vs_1w": 0.0},
            }}}}
            gate = engine.options_model_gate(current, pd.Timestamp("2026-09-25"))
        engine.STATE_DIR = old_state
        self.assertEqual(gate["status"], "BLOCKED")
        self.assertFalse(gate["training_eligible"])
        self.assertEqual(gate["current_integration"], "not_in_champion_or_directional_probability_model")


if __name__ == "__main__":
    unittest.main()
