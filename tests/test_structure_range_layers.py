import unittest

from scripts.potential_ranges import _cluster_levels, _select_horizon_core, _statistical_envelope


class StructureRangeLayerTests(unittest.TestCase):
    def test_cluster_keeps_auditable_evidence_without_probability(self):
        levels = [
            {"label": "SMA50", "price": 100.0, "source": "moving_average", "anchor_date": "2026-09-30"},
            {"label": "Fib 61.8%", "price": 100.8, "source": "fibonacci", "anchor_date": "2026-09-30"},
            {"label": "Gap", "price": 108.0, "source": "gap", "anchor_date": "2026-08-20"},
        ]
        clusters = _cluster_levels(levels, spot=110.0, atr=4.0, side="bottom")
        self.assertTrue(clusters)
        core = next(cluster for cluster in clusters if cluster["level_count"] == 2)
        self.assertIn("anchor_dates", core)
        self.assertIn("evidence", core)
        self.assertIn("不转化为顶底概率", core["note"])
        self.assertNotIn("probability", core)

    def test_statistical_envelope_expands_with_horizon(self):
        widths = []
        for horizon in (5, 10, 21):
            envelope = _statistical_envelope(100.0, 0.40, horizon)
            self.assertEqual(envelope["status"], "observed")
            self.assertIn("descriptive range only", envelope["method"])
            widths.append(envelope["one_sigma"][1] - envelope["one_sigma"][0])
        self.assertLess(widths[0], widths[1])
        self.assertLess(widths[1], widths[2])

    def test_horizon_core_does_not_repeat_nearest_level_when_deeper_structure_exists(self):
        clusters = [
            {"center": 98.0, "band": [97.5, 98.5], "level_count": 3},
            {"center": 94.0, "band": [93.5, 94.5], "level_count": 2},
            {"center": 90.5, "band": [90.0, 91.0], "level_count": 2},
        ]
        five = _select_horizon_core(clusters, spot=100.0, atr=5.0, horizon=5, side="bottom")
        twenty_one = _select_horizon_core(clusters, spot=100.0, atr=5.0, horizon=21, side="bottom")
        self.assertEqual(five["center"], 94.0)
        self.assertEqual(twenty_one["center"], 90.5)
        self.assertIn("不进入方向概率", twenty_one["note"])


if __name__ == "__main__":
    unittest.main()
