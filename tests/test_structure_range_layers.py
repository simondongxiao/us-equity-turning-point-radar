import unittest

from scripts.potential_ranges import _cluster_levels, _statistical_envelope


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


if __name__ == "__main__":
    unittest.main()
