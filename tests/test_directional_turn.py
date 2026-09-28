import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from scripts import radar_engine as engine


class DirectionalTurnTests(unittest.TestCase):
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
